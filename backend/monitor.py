import asyncio
import logging
import threading
import time
from typing import Dict, Optional

import event_log
from config import get_course_config, update_course_config
from lesson import Lesson
from utils import get_on_lesson

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30


class Monitor:
    def __init__(self, sessionid: str, event_queue: asyncio.Queue) -> None:
        self.sessionid = sessionid
        self.event_queue = event_queue
        self._active_lessons: Dict[int, Lesson] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._first_poll_done = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._running:
            return
        self._loop = loop
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="monitor")
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

    def _run(self) -> None:
        while self._running:
            try:
                lesson_list = get_on_lesson(self.sessionid)
            except Exception:
                logger.exception("Failed to poll lessons")
                lesson_list = []
            self._sync_lessons(lesson_list)
            for _ in range(POLL_INTERVAL):
                if not self._running:
                    return
                time.sleep(1)

    def _sync_lessons(self, lesson_list: list) -> None:
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
                course_config = get_course_config(classroom_id)
                if course_config.get("name") != lesson_name:
                    course_config["name"] = lesson_name
                    update_course_config(classroom_id, course_config)
                lesson = Lesson(
                    lesson_data=lesson_data,
                    sessionid=self.sessionid,
                    course_config=course_config,
                    on_event=self._on_lesson_event,
                )

                with self._lock:
                    self._active_lessons[lesson_id] = lesson

                if self._first_poll_done:
                    self._emit("lesson_start", {
                        "lesson": lesson.lessonname,
                        "lessonid": lesson_id,
                        "message": "Started monitoring: %s" % lesson.lessonname,
                    })

                threading.Thread(
                    target=self._lesson_thread,
                    args=(lesson,),
                    daemon=True,
                    name="lesson-%s" % lesson_id,
                ).start()

        with self._lock:
            ended = [lid for lid in self._active_lessons if lid not in incoming_ids]
        for lid in ended:
            with self._lock:
                lesson = self._active_lessons.pop(lid, None)
            if lesson:
                lesson.stop_lesson()

        self._first_poll_done = True

    def _lesson_thread(self, lesson: Lesson) -> None:
        lesson.start_lesson()
        with self._lock:
            self._active_lessons.pop(lesson.lessonid, None)

    def _on_lesson_event(self, event_type: str, data: dict) -> None:
        self._emit(event_type, data)

    def _emit(self, event_type: str, data: dict) -> None:
        event = {"type": event_type, **data}
        event_log.append(event)
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.event_queue.put(event), self._loop)
