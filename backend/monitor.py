import asyncio
import logging
import threading
import time
from typing import Callable, Dict, Optional

import event_log
import pushdeer
from config import (
    DEFAULT_POLL_INTERVAL, MAX_POLL_INTERVAL, MIN_POLL_INTERVAL,
    api_url, get_account, get_course_config, get_domain, get_poll_interval,
    get_sessionid, http_request, make_headers, update_course_config,
)
from lesson import Lesson

logger = logging.getLogger(__name__)

URL_ON_LESSON = "https://{domain}/api/v3/classroom/on-lesson-upcoming-exam"


class Monitor:
    """One Monitor per account — polls that account's active lessons and manages
    the per-lesson WebSocket threads for it."""

    def __init__(
        self,
        account_id: str,
        event_queue: asyncio.Queue,
        on_session_expired: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.account_id = account_id
        self.event_queue = event_queue
        self._on_session_expired = on_session_expired
        self._active_lessons: Dict[int, Lesson] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Wakes the poll loop early when poll_interval is reduced.
        self._wake_event = threading.Event()

    def wake(self) -> None:
        # Poll interval is re-read from config each cycle; kick the loop so a
        # config change takes effect without waiting for the current sleep.
        self._wake_event.set()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._running:
            return
        self._loop = loop
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"monitor-{self.account_id}")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        with self._lock:
            for lesson in list(self._active_lessons.values()):
                lesson.stop_lesson()
            self._active_lessons.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def get_active_lessons(self) -> list:
        with self._lock:
            return [
                {
                    "lessonid": lesson.lessonid,
                    "lessonname": lesson.lessonname,
                    "classroomid": lesson.classroomid,
                    "teacher_name": lesson.teacher_name,
                }
                for lesson in self._active_lessons.values()
            ]

    def _current_credentials(self) -> tuple[str, str]:
        return get_domain(self.account_id), get_sessionid(self.account_id)

    def _run(self) -> None:
        while self._running:
            try:
                domain, sessionid = self._current_credentials()
                if not sessionid:
                    return
                headers = make_headers(domain, sessionid)
                r = http_request("GET", api_url(domain, URL_ON_LESSON), headers=headers)
                data = r.json()
                if data.get("code") != 0:
                    logger.warning("[%s] Session expired: %s", self.account_id, data.get("msg", ""))
                    self._emit("session_expired", {"message": data.get("msg", "Session expired")})
                    # Stop all per-lesson WS threads so they don't keep hammering
                    # Yuketang with an expired sessionid.
                    with self._lock:
                        for lesson in list(self._active_lessons.values()):
                            lesson.stop_lesson()
                        self._active_lessons.clear()
                    if self._on_session_expired:
                        self._on_session_expired(self.account_id)
                    self._running = False
                    return
                lesson_list = data["data"]["onLessonClassrooms"]
                logger.info("[%s] Monitor poll: %d active lesson(s)", self.account_id, len(lesson_list))
                self._sync_lessons(lesson_list)
            except Exception as e:
                logger.warning("[%s] Monitor poll failed: %s", self.account_id, e)
            interval = get_poll_interval(self.account_id)
            self._wake_event.clear()
            # Tick once per second so a stop() / interval change takes effect
            # within ~1s instead of waiting the whole interval.
            for _ in range(interval):
                if not self._running:
                    return
                if self._wake_event.is_set():
                    break
                time.sleep(1)

    def _sync_lessons(self, lesson_list: list) -> None:
        incoming_ids = set()
        domain, sessionid = self._current_credentials()

        for item in lesson_list:
            lesson_id = item["lessonId"]
            incoming_ids.add(lesson_id)

            with self._lock:
                already_tracked = lesson_id in self._active_lessons

            if not already_tracked:
                lesson_name = item.get("courseName", "Unknown")
                lesson_data = {
                    "lessonid": lesson_id,
                    "lessonname": lesson_name,
                    "classroomid": item["classroomId"],
                }
                classroom_id = str(item["classroomId"])
                course_config = get_course_config(self.account_id, classroom_id)
                if course_config.get("name") != lesson_name:
                    course_config["name"] = lesson_name
                    update_course_config(self.account_id, classroom_id, {"name": lesson_name})
                if not course_config.get("course_enabled", True):
                    logger.info(
                        "[%s] Skipping lesson %s (%s): course disabled",
                        self.account_id, lesson_id, lesson_name,
                    )
                    # Drop from incoming so we re-evaluate next poll (cheap)
                    # but don't emit lesson_start.
                    incoming_ids.discard(lesson_id)
                    continue
                lesson = Lesson(
                    account_id=self.account_id,
                    lesson_data=lesson_data,
                    sessionid=sessionid,
                    domain=domain,
                    course_config=course_config,
                    on_event=self._emit,
                )

                with self._lock:
                    self._active_lessons[lesson_id] = lesson

                self._emit("lesson_start", {
                    "lesson": lesson.lessonname,
                    "lessonid": lesson_id,
                    "message": "Started monitoring: %s" % lesson.lessonname,
                })

                threading.Thread(
                    target=self._lesson_thread,
                    args=(lesson,),
                    daemon=True,
                    name="lesson-%s-%s" % (self.account_id, lesson_id),
                ).start()

        with self._lock:
            ended = [lid for lid in self._active_lessons if lid not in incoming_ids]
        for lid in ended:
            with self._lock:
                lesson = self._active_lessons.pop(lid, None)
            if lesson:
                lesson.stop_lesson()
                self._emit("lesson_end", {
                    "lesson": lesson.lessonname,
                    "lessonid": lesson.lessonid,
                    "message": "Lesson ended: %s" % lesson.lessonname,
                })

    def _lesson_thread(self, lesson: Lesson) -> None:
        lesson.start_lesson()
        with self._lock:
            self._active_lessons.pop(lesson.lessonid, None)

    def _emit(self, event_type: str, data: dict) -> None:
        event = {"type": event_type, "account_id": self.account_id, **data}
        event_log.append(self.account_id, event)

        lesson_id = data.get("lessonid")
        if lesson_id is not None:
            with self._lock:
                lesson = self._active_lessons.get(lesson_id)
            if lesson is not None:
                pushdeer.dispatch(self.account_id, str(lesson.classroomid), event_type, data)

        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.event_queue.put(event), self._loop)
