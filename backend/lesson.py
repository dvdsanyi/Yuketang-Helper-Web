import json
import logging
import random
import threading
import time
from typing import Callable, Dict, List, Optional, Any

import requests
import websocket

from ai_provider import AIProvider, create_provider
from config import get_active_ai_key
from domains import get_domain
from utils import _make_headers, get_user_info

logger = logging.getLogger(__name__)

def _wss_url() -> str:
    return "wss://%s/wsapp/" % get_domain()


class Lesson:
    def __init__(
        self,
        lesson_data: dict,
        sessionid: str,
        course_config: dict,
        on_event: Callable[[str, dict], None],
    ):
        self.lessonid: int = lesson_data["lessonid"]
        self.lessonname: str = lesson_data["lessonname"]
        self.classroomid: int = lesson_data["classroomid"]
        self.sessionid = sessionid
        self.course_config = course_config
        self.on_event = on_event

        self.headers = _make_headers(sessionid)
        self.auth: Optional[str] = None
        self.wsapp: Optional[websocket.WebSocketApp] = None
        self._running = False

        self.danmu_dict: Dict[str, List[float]] = {}
        self.sent_danmu_dict: Dict[str, float] = {}
        self.problems_ls: List[dict] = []

        self.user_uid: Optional[int] = None
        self.user_uname: Optional[str] = None
        self.teacher_name: Optional[str] = None
        self._stopped_externally = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_lesson(self) -> None:
        self._running = True
        self._checkin()
        self.wsapp = websocket.WebSocketApp(
            url=_wss_url(),
            header=self.headers,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.wsapp.run_forever(ping_interval=30, ping_timeout=10)
        self._running = False
        if not self._stopped_externally:
            self.on_event("lesson_end", {"lesson": self.lessonname, "lessonid": self.lessonid})

    def stop_lesson(self) -> None:
        self._stopped_externally = True
        self._running = False
        if self.wsapp:
            self.wsapp.close()

    def send_danmu(self, content: str) -> None:
        payload = {
            "extra": "",
            "fromStart": "50",
            "lessonId": self.lessonid,
            "message": content,
            "requiredCensor": False,
            "showStatus": True,
            "target": "",
            "userName": "",
            "wordCloud": True,
        }
        r = requests.post(
            url="https://%s/api/v3/lesson/danmu/send" % get_domain(),
            headers=self.headers,
            data=json.dumps(payload),
            proxies={"http": None, "https": None},
            timeout=10,
        )
        self.on_event("danmu", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "content": content,
            "status": "success" if r.json()["code"] == 0 else "error",
        })

    def answer_questions(self, problemid: Any, problemtype: int, answers: Any, limit: int) -> None:
        wait_time = random.uniform(self.course_config["answer_delay_min"], self.course_config["answer_delay_max"])
        if limit != -1 and wait_time >= limit:
            wait_time = max(0, limit - 2)
        if wait_time > 0:
            time.sleep(wait_time)
        if not self._running:
            return

        payload = {
            "problemId": problemid,
            "problemType": problemtype,
            "dt": int(time.time() * 1000),
            "result": answers,
        }
        r = requests.post(
            url="https://%s/api/v3/lesson/problem/answer" % get_domain(),
            headers=self.headers,
            data=json.dumps(payload),
            proxies={"http": None, "https": None},
            timeout=10,
        )
        result = r.json()
        self.on_event("problem", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "problemid": problemid,
            "answers": answers,
            "status": "success" if result["code"] == 0 else "error",
            "message": result.get("msg", ""),
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _checkin(self) -> None:
        r = requests.post(
            url="https://%s/api/v3/lesson/checkin" % get_domain(),
            headers=self.headers,
            data=json.dumps({"source": 5, "lessonId": self.lessonid}),
            proxies={"http": None, "https": None},
            timeout=10,
        )
        set_auth = r.headers.get("Set-Auth")
        if set_auth:
            self.headers["Authorization"] = "Bearer %s" % set_auth

        result = r.json()
        self.auth = result["data"]["lessonToken"]

        user_data = get_user_info(self.sessionid)
        self.user_uid = user_data["id"]
        self.user_uname = user_data["name"]

        info = requests.get(
            url="https://%s/api/v3/lesson/basic-info" % get_domain(),
            headers=self.headers,
            proxies={"http": None, "https": None},
            timeout=10,
        ).json()["data"]
        self.teacher_name = (info.get("teacher") or {}).get("name")

        self.on_event("signin", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "status": "success" if result["code"] == 0 else "error",
            "message": result.get("msg", ""),
        })

    def _get_problems_from_presentation(self, presentation_id: Any) -> List[dict]:
        r = requests.get(
            url="https://%s/api/v3/lesson/presentation/fetch?presentation_id=%s" % (get_domain(), presentation_id),
            headers=self.headers,
            proxies={"http": None, "https": None},
            timeout=10,
        )
        data = r.json()["data"]
        problems = []
        for slide in data.get("slides", []):
            if "problem" in slide:
                problem = slide["problem"]
                problem["_cover"] = slide.get("cover", "")
                problems.append(problem)
        return problems

    def _build_random_answers(self, problem: dict) -> list:
        problemtype = problem["problemType"]
        options = [opt["key"] for opt in problem.get("options", [])]
        if problemtype == 1:
            return [random.choice(options)]
        elif problemtype == 2:
            k = random.randint(1, len(options))
            return random.sample(options, k)
        elif problemtype == 3:
            count = int(problem.get("pollingCount", 1))
            return random.sample(options, min(count, len(options)))

    def _get_ai_provider(self) -> Optional[AIProvider]:
        provider_name, api_key = get_active_ai_key()
        return create_provider(provider_name, api_key)

    def _build_ai_answers(self, problem: dict) -> list | dict:
        provider = self._get_ai_provider()
        if not provider:
            return self._build_random_answers(problem)

        cover_url = problem.get("_cover", "")

        problemtype = problem["problemType"]
        if problemtype == 5:
            text = provider.answer_short(cover_url)
            return {"content": text, "pics": [{"pic": "", "thumb": ""}]}
        else:
            options = [opt["key"] for opt in problem["options"]]
            return provider.answer_choice(cover_url, options, problemtype)

    def _start_answer_for_problem(self, problemid: Any, limit: int) -> None:
        for problem in self.problems_ls:
            if problem["problemId"] == problemid:
                if problem.get("result") is not None:
                    return
                problemtype = problem["problemType"]
                mode = self.course_config.get("type%d" % problemtype, "off")
                if mode == "off":
                    return
                if mode == "ai":
                    answers = self._build_ai_answers(problem)
                elif mode == "random":
                    answers = self._build_random_answers(problem)
                threading.Thread(
                    target=self.answer_questions,
                    args=(problemid, problemtype, answers, limit),
                    daemon=True,
                ).start()
                return

    def _handle_danmu(self, content: str) -> None:
        if not self.course_config.get("auto_danmu", True):
            return

        key = content.lower().strip()
        now = time.time()
        self.danmu_dict.setdefault(key, [])
        self.danmu_dict[key] = [t for t in self.danmu_dict[key] if now - t <= 60]

        if now - self.sent_danmu_dict.get(key, 0) <= 60:
            return

        danmu_limit = max(1, self.course_config.get("danmu_threshold", 3))
        if len(self.danmu_dict[key]) + 1 >= danmu_limit:
            self.danmu_dict[key] = []
            self.sent_danmu_dict[key] = now
            threading.Thread(target=self.send_danmu, args=(content,), daemon=True).start()
        else:
            self.danmu_dict[key].append(now)

    # ------------------------------------------------------------------
    # WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_open(self, wsapp: websocket.WebSocketApp) -> None:
        wsapp.send(json.dumps({
            "op": "hello",
            "userid": self.user_uid,
            "role": "student",
            "auth": self.auth,
            "lessonid": self.lessonid,
        }))

    def _on_message(self, wsapp: websocket.WebSocketApp, message: str) -> None:
        data = json.loads(message)
        op = data.get("op", "")

        if op == "hello":
            timeline = data.get("timeline", [])
            presentation_ids = list({
                slide["pres"]
                for slide in timeline
                if slide.get("type") == "slide" and "pres" in slide
            })
            current = data.get("presentation")
            if current and current not in presentation_ids:
                presentation_ids.append(current)
            for pid in presentation_ids:
                self.problems_ls.extend(self._get_problems_from_presentation(pid))

        elif op == "unlockproblem":
            problem = data["problem"]
            self._start_answer_for_problem(problem["sid"], problem.get("limit", -1))

        elif op == "lessonfinished":
            wsapp.close()

        elif op in ("presentationupdated", "presentationcreated"):
            pid = data.get("presentation")
            if pid:
                self.problems_ls.extend(self._get_problems_from_presentation(pid))

        elif op == "newdanmu":
            content = data.get("danmu", "")
            if content:
                self._handle_danmu(content)

        elif op == "callpaused":
            if data.get("name") == self.user_uname:
                self.on_event("call", {"lesson": self.lessonname, "lessonid": self.lessonid})

    def _on_error(self, wsapp: websocket.WebSocketApp, error: Exception) -> None:
        pass

    def _on_close(self, wsapp: websocket.WebSocketApp, close_status_code: Any, close_msg: Any) -> None:
        pass

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Lesson):
            return NotImplemented
        return self.lessonid == other.lessonid

    def __hash__(self) -> int:
        return hash(self.lessonid)
