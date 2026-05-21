import json
import logging
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import websocket

from ai_provider import create_provider
from config import api_url, http_request, get_active_ai_key, get_ai_config, get_all_ai_keys, get_account, make_headers

logger = logging.getLogger(__name__)

# API URLs
URL_WSS = "wss://{domain}/wsapp/"
URL_CHECKIN = "https://{domain}/api/v3/lesson/checkin"
URL_BASIC_INFO = "https://{domain}/api/v3/lesson/basic-info"
URL_DANMU_SEND = "https://{domain}/api/v3/lesson/danmu/send"
URL_PROBLEM_ANSWER = "https://{domain}/api/v3/lesson/problem/answer"
URL_PRESENTATION_FETCH = "https://{domain}/api/v3/lesson/presentation/fetch?presentation_id={presentation_id}"
URL_REDENVELOPE_PREPARE = "https://{domain}/api/v3/lesson/redenvelope/prepare"


class Lesson:
    def __init__(
        self,
        account_id: str,
        lesson_data: dict,
        sessionid: str,
        domain: str,
        course_config: dict,
        on_event: Callable[[str, dict], None],
    ):
        self.account_id = account_id
        self.lessonid: int = lesson_data["lessonid"]
        self.lessonname: str = lesson_data["lessonname"]
        self.classroomid: int = lesson_data["classroomid"]
        self.sessionid = sessionid
        self.domain = domain
        self.course_config = course_config
        self.on_event = on_event

        self.headers = make_headers(domain, sessionid)
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
        self._lesson_ended = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_lesson(self) -> None:
        self._running = True
        self._checkin()

        # Yuketang closes the WS every ~40-60s while class is still live.
        # Reconnect on a fixed 1s delay until external stop or `lessonfinished`.
        while not self._stopped_externally and not self._lesson_ended:
            self.wsapp = websocket.WebSocketApp(
                url=api_url(self.domain, URL_WSS),
                header=self.headers,
                on_open=self._on_open,
                on_message=self._on_message,
            )
            self.wsapp.run_forever(ping_interval=30, ping_timeout=10)
            if self._stopped_externally or self._lesson_ended:
                break
            logger.info("[WS %s] disconnected, reconnecting in 1s", self.lessonname)
            time.sleep(1)

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
            "lessonId": self.lessonid,
            "target": "",
            "userName": "",
            "message": content,
            "extra": "",
            "requiredCensor": False,
            "wordCloud": True,
            "showStatus": True,
            "fromStart": "50",
        }
        r = http_request("POST", api_url(self.domain, URL_DANMU_SEND), headers=self.headers, data=json.dumps(payload))
        self.on_event("danmu", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "content": content,
            "status": "success" if r.json()["code"] == 0 else "error",
        })

    def _grab_red_packet(self, red_envelope_id: int) -> None:
        payload = {
            "lessonId": self.lessonid,
            "redEnvelopeId": red_envelope_id,
        }
        r = http_request("POST", api_url(self.domain, URL_REDENVELOPE_PREPARE), headers=self.headers, data=json.dumps(payload))
        result = r.json()
        self.on_event("red_packet", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "redEnvelopeId": red_envelope_id,
            "status": "success" if result.get("code") == 0 else "error",
            "message": result.get("msg", ""),
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _checkin(self) -> None:
        r = http_request("POST", api_url(self.domain, URL_CHECKIN), headers=self.headers, data=json.dumps({"source": 21, "lessonId": self.lessonid}))
        set_auth = r.headers.get("Set-Auth")
        if set_auth:
            self.headers["Authorization"] = "Bearer %s" % set_auth

        result = r.json()
        self.auth = result["data"]["lessonToken"]

        acc = get_account(self.account_id) or {}
        user = acc.get("user") or {}
        self.user_uid = user.get("id")
        self.user_uname = user.get("name")

        info = http_request("GET", api_url(self.domain, URL_BASIC_INFO), headers=self.headers).json()["data"]
        self.teacher_name = (info.get("teacher") or {}).get("name")

        self.on_event("signin", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "status": "success" if result["code"] == 0 else "error",
            "message": result.get("msg", ""),
        })

    def _get_problems_from_presentation(self, presentation_id: Any) -> List[dict]:
        r = http_request("GET", api_url(self.domain, URL_PRESENTATION_FETCH, presentation_id=presentation_id), headers=self.headers)
        data = r.json()["data"]
        problems = []
        for slide in data.get("slides", []):
            if "problem" in slide:
                problem = slide["problem"]
                problem["_cover"] = slide.get("cover", "")
                problems.append(problem)
        return problems

    def _add_problems(self, problems: List[dict]) -> None:
        existing_ids = {p["problemId"] for p in self.problems_ls}
        for p in problems:
            if p["problemId"] not in existing_ids:
                self.problems_ls.append(p)
                existing_ids.add(p["problemId"])

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

    def _ai_keys_to_try(self) -> list[tuple[str, str]]:
        ai_cfg = get_ai_config(self.account_id)
        fallback = ai_cfg.get("fallback_keys", True)

        if fallback:
            return [(provider, key) for provider, key in get_all_ai_keys(self.account_id) if key]

        provider_name, api_key = get_active_ai_key(self.account_id)
        return [(provider_name, api_key)] if api_key else []

    def _build_ai_answers(self, problem: dict, keys_to_try: Optional[list[tuple[str, str]]] = None) -> list | str:
        if keys_to_try is None:
            keys_to_try = self._ai_keys_to_try()
        if not keys_to_try:
            raise RuntimeError("No AI provider available")

        cover_url = problem.get("_cover", "")
        problemtype = problem["problemType"]
        last_error = None

        for provider_name, api_key in keys_to_try:
            provider = create_provider(provider_name, api_key)
            if not provider:
                continue
            try:
                if problemtype == 5:
                    return provider.answer_short(cover_url)
                option_keys = [opt["key"] for opt in problem["options"]]
                count = int(problem.get("pollingCount", 1) or 1) if problemtype == 3 else None
                return provider.answer_choice(cover_url, option_keys, problemtype, count)
            except Exception as e:
                logger.warning("AI call failed with %s key, trying next: %s", provider_name, e)
                last_error = e

        raise RuntimeError("All AI providers failed") from last_error

    # ------------------------------------------------------------------
    # Answer submission
    # ------------------------------------------------------------------
    #
    # Submission timing depends on mode and the `answer_last5s` toggle.
    #
    #   Random / blank modes:
    #     - last5s ON  + deadline → submit in the last 1-5s window.
    #     - last5s OFF or no deadline → submit immediately.
    #
    #   AI mode (see `_compute_ai_window`):
    #     - last5s ON  + deadline → wait for AI up to the last-5s window,
    #       then submit at that target time. If AI didn't return, submit
    #       fallback (random/blank).
    #     - last5s OFF + deadline → submit as soon as AI returns; cap the
    #       wait at `limit - 1s` so a fallback can still be submitted.
    #     - No deadline → wait indefinitely for AI (provider has its own
    #       request timeout).

    def _submit_answer(self, problemid: Any, problemtype: int, real_answer: Any, source: str) -> None:
        if problemtype == 5:
            payload_result = {"content": real_answer, "pics": [{"pic": "", "thumb": ""}]}
        else:
            payload_result = real_answer
        payload = {
            "problemId": problemid,
            "problemType": problemtype,
            "dt": int(time.time() * 1000),
            "result": payload_result,
        }
        r = http_request("POST", api_url(self.domain, URL_PROBLEM_ANSWER), headers=self.headers, data=json.dumps(payload))
        result = r.json()
        self.on_event("problem", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "problemid": problemid,
            "problemtype": problemtype,
            "answers": real_answer,
            "source": source,
            "status": "success" if result["code"] == 0 else "error",
            "message": result.get("msg", ""),
        })

    def _build_fallback_answer(self, problem: dict, problemtype: int):
        if problemtype == 5:
            return " ", "blank"
        else:
            return self._build_random_answers(problem), "random"

    def _compute_delay(self, limit: int) -> float:
        """Submit-delay for random/blank modes.

        With `answer_last5s` enabled and a deadline, submit in the last 1-5s.
        Otherwise (toggle off or no deadline) submit immediately.
        """
        if limit > 0 and self.course_config.get("answer_last5s", True):
            return max(0, limit - random.uniform(1, min(5, limit)))
        return 0

    def _compute_ai_window(self, limit: int) -> tuple[float, Optional[float]]:
        """Submission window for AI mode: ``(min_hold, max_wait)`` seconds.

        - ``min_hold`` — earliest submit time, measured from problem receipt.
        - ``max_wait`` — how long to wait for AI before falling back.
          ``None`` means wait indefinitely.
        """
        if limit <= 0:
            return (0.0, None)
        if self.course_config.get("answer_last5s", True):
            target = max(0.0, limit - random.uniform(1, min(5, limit)))
            return (target, target)
        # last5s OFF: submit ASAP when AI returns; keep ~1s buffer for fallback.
        return (0.0, max(0.5, float(limit - 1)))

    def _wait_for_delay(self, start_time: float, limit: int) -> bool:
        """Wait until the target submit time. Returns False if lesson stopped."""
        delay = self._compute_delay(limit)
        remaining = delay - (time.time() - start_time)
        if remaining > 0:
            time.sleep(remaining)
        return self._running

    def _answer_problem(self, problem: dict, problemid: Any, problemtype: int, mode: str, limit: int) -> None:
        start_time = time.time()

        if mode == "ai":
            keys_to_try = self._ai_keys_to_try()
            if not keys_to_try:
                logger.warning("AI mode selected but no API key configured, using fallback for problem %s", problemid)
                answers, source = self._build_fallback_answer(problem, problemtype)
                if not self._wait_for_delay(start_time, limit):
                    return
                self._submit_answer(problemid, problemtype, answers, source)
                return

            # Start AI call in background thread.
            result_holder = [None]
            ai_done = threading.Event()
            ai_failed_event = threading.Event()

            logger.info("Attempting AI answer for problem %s", problemid)

            def _call_ai():
                try:
                    result_holder[0] = self._build_ai_answers(problem, keys_to_try)
                except Exception:
                    logger.exception("AI answering failed for problem %s", problemid)
                    ai_failed_event.set()
                finally:
                    ai_done.set()

            threading.Thread(target=_call_ai, daemon=True).start()

            min_hold, max_wait = self._compute_ai_window(limit)

            # Wait for AI to return, capped by max_wait (None = forever).
            if max_wait is None:
                ai_done.wait()
            else:
                remaining_wait = max_wait - (time.time() - start_time)
                if remaining_wait > 0:
                    ai_done.wait(timeout=remaining_wait)

            # Fire ai_failed notification as soon as AI raises, so users can intervene before the fallback submit.
            notification_sent = False
            if ai_failed_event.is_set() and result_holder[0] is None:
                self.on_event("problem", {
                    "lesson": self.lessonname,
                    "lessonid": self.lessonid,
                    "problemid": problemid,
                    "problemtype": problemtype,
                    "status": "ai_failed",
                })
                notification_sent = True

            if not self._running:
                return

            # Hold until the earliest allowed submit time (last-5s gate).
            remaining_hold = min_hold - (time.time() - start_time)
            if remaining_hold > 0:
                time.sleep(remaining_hold)
            if not self._running:
                return
            if result_holder[0] is not None:
                self._submit_answer(problemid, problemtype, result_holder[0], "ai")
                return

            # AI failed — emit notification (if not already sent) and submit fallback.
            if not notification_sent:
                self.on_event("problem", {
                    "lesson": self.lessonname,
                    "lessonid": self.lessonid,
                    "problemid": problemid,
                    "problemtype": problemtype,
                    "status": "ai_failed",
                })
            fallback_answer, fallback_source = self._build_fallback_answer(problem, problemtype)
            self._submit_answer(problemid, problemtype, fallback_answer, fallback_source)

        elif mode == "blank":
            if not self._wait_for_delay(start_time, limit):
                return
            self._submit_answer(problemid, problemtype, " ", "blank")

        elif mode == "random":
            answers, source = self._build_random_answers(problem), "random"
            if not self._wait_for_delay(start_time, limit):
                return
            self._submit_answer(problemid, problemtype, answers, source)

        else:
            logger.warning("Unsupported answer mode %r for problem %s, skipping", mode, problemid)

    def _start_answer_for_problem(self, problemid: Any, limit: int) -> None:
        for problem in self.problems_ls:
            if problem["problemId"] == problemid:
                if problem.get("result") is not None:
                    return
                problemtype = problem["problemType"]
                mode = self.course_config.get("type%d" % problemtype, "off")
                if mode == "off":
                    return

                threading.Thread(
                    target=self._answer_problem,
                    args=(problem, problemid, problemtype, mode, limit),
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
        logger.info("[WS %s] op=%s", self.lessonname, op)

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
                self._add_problems(self._get_problems_from_presentation(pid))

        elif op == "unlockproblem":
            problem = data["problem"]
            self.on_event("problem_received", {
                "lesson": self.lessonname,
                "lessonid": self.lessonid,
                "problemid": problem["sid"],
            })
            self._start_answer_for_problem(problem["sid"], problem.get("limit", -1) - 1)

        elif op == "lessonfinished":
            self._lesson_ended = True
            wsapp.close()

        elif op in ("presentationupdated", "presentationcreated", "showpresentation"):
            pid = data.get("presentation")
            if pid:
                self._add_problems(self._get_problems_from_presentation(pid))

        elif op == "newdanmu":
            content = data.get("danmu", "")
            if content:
                self._handle_danmu(content)

        elif op == "gainbonus":
            logger.info("[WS %s] gainbonus raw: %s", self.lessonname, message)
            redpacket = data.get("redpacket", data)
            red_envelope_id = redpacket.get("redEnvelopeId")
            if red_envelope_id and self.course_config.get("auto_redpacket", True):
                threading.Thread(
                    target=self._grab_red_packet,
                    args=(red_envelope_id,),
                    daemon=True,
                ).start()

        elif op == "callpaused":
            if data.get("name") == self.user_uname:
                self.on_event("call", {"lesson": self.lessonname, "lessonid": self.lessonid})
