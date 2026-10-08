import asyncio
import logging
import threading
from typing import Callable, Optional

import event_log
import pushdeer
from config import (
    api_url, get_course_config, get_domain, get_poll_interval,
    get_sessionid, http_request, make_headers, update_course_config,
)
from lesson import Lesson

logger = logging.getLogger(__name__)

URL_ON_LESSON = "https://{domain}/api/v3/classroom/on-lesson-upcoming-exam"

# Consecutive rejected polls before we declare the sessionid dead. One bad
# response (maintenance, rate limit) must not take the monitor offline.
MAX_REJECTED_POLLS = 3


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
        self._active_lessons: dict[int, Lesson] = {}
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
        self._wake_event.set()  # break the poll loop's wait immediately
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
                }
                for lesson in self._active_lessons.values()
            ]

    def update_lesson_config(self, classroom_id: str, patch: dict) -> None:
        """Apply a course-settings patch to any currently-running lesson(s) for
        the given classroom. Called from the HTTP layer so settings take effect
        without waiting for the next poll."""
        with self._lock:
            for lesson in self._active_lessons.values():
                if str(lesson.classroomid) == classroom_id:
                    lesson.course_config.update(patch)

    def _current_credentials(self) -> tuple[str, str]:
        return get_domain(self.account_id), get_sessionid(self.account_id)

    def _run(self) -> None:
        rejected = 0  # consecutive polls Yuketang answered with a non-zero code
        while self._running:
            try:
                domain, sessionid = self._current_credentials()
                if not sessionid:
                    self._running = False
                    return
                headers = make_headers(domain, sessionid)
                r = http_request("GET", api_url(domain, URL_ON_LESSON), headers=headers)
                data = r.json()
                if data.get("code") != 0:
                    # One rejection is more often a hiccup on their side than a
                    # dead session, and tearing the monitor down here would
                    # silently stop every later lesson — so count them first.
                    rejected += 1
                    msg = data.get("msg", "")
                    logger.warning(f"[{self.account_id}] poll rejected ({rejected}/{MAX_REJECTED_POLLS}): {msg}")
                    if rejected >= MAX_REJECTED_POLLS:
                        self._expire_session(msg)
                        return
                else:
                    rejected = 0
                    lesson_list = data["data"]["onLessonClassrooms"]
                    logger.info(f"[{self.account_id}] Monitor poll: {len(lesson_list)} active lesson(s)")
                    self._sync_lessons(lesson_list, domain, sessionid)
            except Exception:
                logger.exception(f"[{self.account_id}] Monitor poll failed")
            # wake() and stop() both set _wake_event, so a config change or
            # shutdown takes effect immediately instead of waiting the
            # whole interval.
            self._wake_event.clear()
            self._wake_event.wait(timeout=get_poll_interval(self.account_id))

    def _expire_session(self, message: str) -> None:
        """Give up on this account's sessionid: stop every lesson WS so they
        don't keep hammering Yuketang, and tell the app to clear the login."""
        logger.warning(f"[{self.account_id}] Session expired: {message}")
        self._emit("session_expired", {"message": message or "Session expired"})
        pushdeer.send_session_expired(self.account_id)
        with self._lock:
            lessons = list(self._active_lessons.values())
            self._active_lessons.clear()
        for lesson in lessons:
            lesson.stop_lesson()
        if self._on_session_expired:
            self._on_session_expired(self.account_id)
        self._running = False

    def _sync_lessons(self, lesson_list: list, domain: str, sessionid: str) -> None:
        incoming_ids = set()

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
                        f"[{self.account_id}] Skipping lesson {lesson_id} ({lesson_name}): course disabled"
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
                    "message": f"Started monitoring: {lesson.lessonname}",
                })

                threading.Thread(
                    target=self._lesson_thread,
                    args=(lesson,),
                    daemon=True,
                    name=f"lesson-{self.account_id}-{lesson_id}",
                ).start()

        with self._lock:
            ended = [lid for lid in self._active_lessons if lid not in incoming_ids]
        for lid in ended:
            self._evict(lid)

    def _evict(self, lesson_id: int) -> None:
        """Pop a lesson from the registry and tear it down. stop_lesson() may
        block on WS close, so we release self._lock before calling it."""
        with self._lock:
            lesson = self._active_lessons.pop(lesson_id, None)
        if lesson is None:
            return
        lesson.stop_lesson()
        self._emit("lesson_end", {
            "lesson": lesson.lessonname,
            "lessonid": lesson.lessonid,
            "message": f"Lesson ended: {lesson.lessonname}",
        })

    def _lesson_thread(self, lesson: Lesson) -> None:
        try:
            lesson.start_lesson()
        except Exception as e:
            # Check-in or the WS loop blew up. Freeing the slot in `finally`
            # lets the next poll rebuild this lesson from scratch — otherwise
            # the class sits "tracked" but unattended until it ends.
            logger.warning(f"[{self.account_id}] lesson {lesson.lessonid} aborted, retrying next poll: {e}")
        finally:
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
