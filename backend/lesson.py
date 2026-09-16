import json
import logging
import random
import threading
import time
from enum import Enum, auto
from typing import Any, Callable, Iterable, NamedTuple, Optional

import websocket

from ai_provider import create_provider
from config import api_url, get_account, get_ai_config, http_request, make_headers

logger = logging.getLogger(__name__)

# API URLs
URL_WSS = "wss://{domain}/wsapp/"
URL_CHECKIN = "https://{domain}/api/v3/lesson/checkin"
URL_BASIC_INFO = "https://{domain}/api/v3/lesson/basic-info"
URL_DANMU_SEND = "https://{domain}/api/v3/lesson/danmu/send"
URL_PROBLEM_ANSWER = "https://{domain}/api/v3/lesson/problem/answer"
URL_PRESENTATION_FETCH = "https://{domain}/api/v3/lesson/presentation/fetch?presentation_id={presentation_id}"
URL_REDENVELOPE_PREPARE = "https://{domain}/api/v3/lesson/redenvelope/prepare"

# Answering is deadline-bound, so these calls trade retry depth for speed:
# a slow retry chain would push the submission past the teacher's cut-off.
_ANSWER_ATTEMPTS = 3
_ANSWER_TIMEOUT = 3
# Upper bound on how long we wait for an AI answer when the problem carries no
# deadline of its own. Without it a hung provider means no answer at all.
_AI_MAX_WAIT = 120.0
# Rejected submissions before we stop retrying a problem. Transient errors
# clear in a try or two; retrying forever would hammer Yuketang on every frame.
_MAX_SUBMIT_FAILS = 3


class AIKeyAttempt(NamedTuple):
    provider: str
    api_key: str
    name: str


class _Stop(Enum):
    """Lesson lifecycle state. NONE means the lesson is still running."""
    NONE = auto()
    EXTERNAL = auto()   # stop_lesson() called (Monitor evicted us)
    FINISHED = auto()   # `lessonfinished` WS frame received


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
        self._stop_reason: _Stop = _Stop.NONE
        # Set together with _stop_reason: wakes answer threads that are holding
        # for their submission window so they submit now instead of expiring.
        self._stop_event = threading.Event()

        self.danmu_dict: dict[str, list[float]] = {}
        self.sent_danmu_dict: dict[str, float] = {}
        # Known problems, keyed by BOTH problemId and slide id: the WS frames
        # identify a problem by either one depending on the op, and Yuketang's
        # two id namespaces don't overlap.
        self._problems: dict[Any, dict] = {}
        # problemIds that are answered — by us, or already on the server.
        # Guards against double submission; a failed submit is removed again so
        # the next reconcile retries it.
        self._answered: set = set()
        # Attempts we've given up on, keyed by problemId — or by the raw id from
        # `unlockedproblem` when we couldn't even resolve which problem it is.
        self._failures: dict[Any, int] = {}
        self._answered_lock = threading.Lock()

        self.user_uid: Optional[int] = None
        self.user_uname: Optional[str] = None
        self.teacher_name: Optional[str] = None

    def _is_running(self) -> bool:
        return self._stop_reason is _Stop.NONE

    def _stop(self, reason: _Stop) -> None:
        self._stop_reason = reason
        self._stop_event.set()

    def _hold(self, seconds: float) -> None:
        """Wait out a submission window, waking early if the lesson stops —
        the caller submits either way, so a lesson ending mid-hold costs us
        the delay, never the answer."""
        if seconds > 0:
            self._stop_event.wait(seconds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_lesson(self) -> None:
        # Raises if check-in yielded no lesson token; Monitor frees the slot on
        # the way out so the next poll retries instead of leaving the class
        # tracked but unattended.
        self._checkin()

        # Yuketang closes the WS every ~40-60s while class is still live.
        # Reconnect on a fixed 1s delay until external stop or `lessonfinished`.
        while self._is_running():
            self.wsapp = websocket.WebSocketApp(
                url=api_url(self.domain, URL_WSS),
                header=self.headers,
                on_open=self._on_open,
                on_message=self._on_message,
            )
            self.wsapp.run_forever(ping_interval=30, ping_timeout=10)
            if not self._is_running():
                break
            logger.info(f"[{self.account_id}][WS {self.lessonname}] disconnected, reconnecting in 1s")
            time.sleep(1)

        # Monitor._sync_lessons already emits lesson_end on external stop,
        # so only emit it here when we exited because the lesson finished.
        if self._stop_reason is _Stop.FINISHED:
            self.on_event("lesson_end", {"lesson": self.lessonname, "lessonid": self.lessonid})

    def stop_lesson(self) -> None:
        self._stop(_Stop.EXTERNAL)
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
        r = http_request("POST", api_url(self.domain, URL_DANMU_SEND), headers=self.headers, json=payload)
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
        r = http_request("POST", api_url(self.domain, URL_REDENVELOPE_PREPARE), headers=self.headers, json=payload)
        result = r.json()
        self.on_event("red_packet", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "redEnvelopeId": red_envelope_id,
            "status": "success" if result.get("code") == 0 else "error",
            "message": result.get("msg", ""),
        })

    # ------------------------------------------------------------------
    # Check-in
    # ------------------------------------------------------------------

    def _checkin(self) -> None:
        """Sign in and pick up the lesson token the WS needs. Always emits a
        `signin` event, failures included, so a missed check-in reaches the
        user's notifications instead of dying in a log."""
        try:
            r = http_request("POST", api_url(self.domain, URL_CHECKIN), headers=self.headers,
                             json={"source": 21, "lessonId": self.lessonid})
            set_auth = r.headers.get("Set-Auth")
            if set_auth:
                self.headers["Authorization"] = f"Bearer {set_auth}"
            result = r.json()
            token = (result.get("data") or {}).get("lessonToken")
            message = result.get("msg", "")
        except Exception as e:
            logger.warning(f"[{self.account_id}] check-in request failed for lesson {self.lessonid}: {e}")
            token, message = None, str(e)

        self.on_event("signin", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "status": "success" if token else "error",
            "message": message,
        })
        if not token:
            raise RuntimeError(f"check-in failed: {message}")
        self.auth = token

        acc = get_account(self.account_id) or {}
        user = acc.get("user") or {}
        self.user_uid = user.get("id")
        self.user_uname = user.get("name")

        # Cosmetic only (the teacher name shown in notifications) — never let it
        # abort a lesson we have already checked into.
        try:
            info = http_request("GET", api_url(self.domain, URL_BASIC_INFO), headers=self.headers).json()["data"]
            self.teacher_name = (info.get("teacher") or {}).get("name")
        except Exception as e:
            logger.warning(f"[{self.account_id}] basic-info fetch failed for lesson {self.lessonid}: {e}")

    # ------------------------------------------------------------------
    # Problem bookkeeping
    # ------------------------------------------------------------------

    def _fetch_presentation(self, presentation_id: Any) -> None:
        """(Re-)load a presentation's problems into `_problems`. Always re-reads:
        one cheap GET refreshes each problem's `result`, which is how we know a
        problem was answered elsewhere and must not be overwritten."""
        r = http_request("GET", api_url(self.domain, URL_PRESENTATION_FETCH, presentation_id=presentation_id),
                         headers=self.headers, attempts=_ANSWER_ATTEMPTS, timeout=4)
        for slide in r.json()["data"].get("slides", []):
            problem = slide.get("problem")
            if not problem:
                continue
            problem["_cover"] = slide.get("cover", "")
            problem["_pres"] = presentation_id
            self._problems[problem["problemId"]] = problem
            if slide.get("id"):
                self._problems[slide["id"]] = problem

    def _load_presentations(self, presentation_ids: Iterable) -> None:
        """Fetch each presentation independently so one failure doesn't hide
        the problems in the others."""
        for pid in presentation_ids:
            if not pid:
                continue
            try:
                self._fetch_presentation(pid)
            except Exception as e:
                logger.warning(f"[{self.account_id}] presentation {pid} fetch failed: {e}")

    def _mode_for(self, problem: dict) -> str:
        return self.course_config.get(f"type{problem['problemType']}", "off")

    def _remaining_limit(self, problem: dict) -> int:
        """Seconds left on a problem's own clock, from the presentation data.
        0 means "no deadline known" — submit without holding."""
        limit = int(problem.get("limit") or 0)
        sent_ms = int(float(problem.get("sendTime") or 0))
        if limit <= 0 or sent_ms <= 0:
            return 0
        return max(0, int(sent_ms / 1000 + limit - time.time()))

    def _claim(self, problemid: Any) -> bool:
        """Take ownership of answering a problem. False means it's already
        claimed, answered, or hopeless — the guard against double submission."""
        with self._answered_lock:
            if problemid in self._answered or self._failures.get(problemid, 0) >= _MAX_SUBMIT_FAILS:
                return False
            self._answered.add(problemid)
            return True

    def _answer_if_needed(self, problem: Optional[dict], limit: int, context: str) -> None:
        """The one way in to answering: both the live `unlockproblem` frame and
        the reconcile pass go through here, so the guards can't be bypassed."""
        if problem is None:
            logger.warning(f"[{self.account_id}] could not resolve problem ({context})")
            self.on_event("problem", {
                "lesson": self.lessonname,
                "lessonid": self.lessonid,
                "status": "error",
                "message": f"problem not found ({context})",
            })
            return
        problemid = problem["problemId"]
        if problem.get("result") is not None:
            # Answered already (by us on a previous run, or by hand elsewhere).
            self._claim(problemid)
            return
        if self._mode_for(problem) == "off":
            return
        if not self._claim(problemid):
            return
        threading.Thread(
            target=self._answer_problem,
            args=(problem, limit),
            daemon=True,
            name=f"answer-{self.lessonid}-{problemid}",
        ).start()

    def _reconcile_unlocked(self, data: dict) -> None:
        """Answer anything the server reports as unlocked that we haven't.

        Frames carry the full `unlockedproblem` list, the only way to recover a
        problem whose unlock frame we never saw — the WS drops every ~40-60s and
        anything opened in that gap would otherwise go unanswered.
        """
        for raw_id in data.get("unlockedproblem") or []:
            if self._failures.get(raw_id, 0) >= _MAX_SUBMIT_FAILS:
                continue
            problem = self._problems.get(raw_id)
            if problem is not None:
                with self._answered_lock:
                    if problem["problemId"] in self._answered:
                        continue
                if self._mode_for(problem) == "off":
                    continue
            # Unknown, or known-but-unanswered: re-read from the server so we
            # act on a fresh `result` and pick up problems we never fetched.
            self._load_presentations([(problem or {}).get("_pres") or data.get("presentation")])
            problem = self._problems.get(raw_id)
            if problem is None:
                self._failures[raw_id] = self._failures.get(raw_id, 0) + 1
            self._answer_if_needed(
                problem,
                self._remaining_limit(problem) if problem else 0,
                context=f"unlocked {raw_id}",
            )

    # ------------------------------------------------------------------
    # Answer building
    # ------------------------------------------------------------------

    def _max_option_count(self, problem: dict, problemtype: int, option_count: int) -> int:
        if problemtype == 1:
            return 1
        if problemtype == 2:
            return option_count
        if problemtype == 3:
            return max(1, min(int(problem.get("pollingCount", 1)), option_count))
        raise ValueError(f"Unsupported option problem type: {problemtype}")

    def _build_fallback_answer(
        self, problem: dict, problemtype: int,
    ) -> tuple[str | list[str], str]:
        """Return (answer, source) for non-AI submission.
        Short-answer (type 5) yields a single-space string with source='blank';
        choice/vote types yield a randomly-sampled list with source='random'.
        """
        if problemtype == 5:
            return " ", "blank"
        if problemtype not in (1, 2, 3):
            raise ValueError(f"Unsupported problem type for fallback: {problemtype}")
        options = [opt["key"] for opt in problem.get("options", [])]
        if not options:
            raise ValueError("No options available for fallback answer")
        count = random.randint(1, self._max_option_count(problem, problemtype, len(options)))
        return random.sample(options, count), "random"

    def _ai_keys_to_try(self) -> list[AIKeyAttempt]:
        ai_cfg = get_ai_config(self.account_id)
        keys = ai_cfg["keys"]
        active = ai_cfg["active_key"]
        fallback = ai_cfg["fallback_keys"]

        if fallback:
            ordered = []
            if 0 <= active < len(keys):
                ordered.append(keys[active])
            ordered.extend(entry for i, entry in enumerate(keys) if i != active)
        elif 0 <= active < len(keys):
            ordered = [keys[active]]
        else:
            ordered = []

        out: list[AIKeyAttempt] = []
        for entry in ordered:
            api_key = entry.get("key", "")
            if not api_key:
                continue
            provider = entry.get("provider", "")
            name = entry.get("name") or provider or "unnamed"
            out.append(AIKeyAttempt(provider=provider, api_key=api_key, name=name))
        return out

    def _build_ai_answers(self, problem: dict, keys_to_try: list[AIKeyAttempt]) -> list | str:
        if not keys_to_try:
            raise RuntimeError("No AI provider available")

        cover_url = problem.get("_cover", "")
        problemtype = problem["problemType"]
        last_error: Optional[Exception] = None

        for attempt in keys_to_try:
            try:
                provider = create_provider(attempt.provider, attempt.api_key)
                if problemtype == 5:
                    return provider.answer_short(cover_url)
                option_keys = [opt["key"] for opt in problem["options"]]
                max_count = self._max_option_count(problem, problemtype, len(option_keys))
                return provider.answer_options(cover_url, option_keys, problemtype, max_count)
            except Exception as e:
                logger.warning(
                    f"[{self.account_id}] AI call failed with key {attempt.name!r} ({attempt.provider}), trying next: {e}"
                )
                last_error = e

        raise RuntimeError("All AI providers failed") from last_error

    # ------------------------------------------------------------------
    # Answer submission
    # ------------------------------------------------------------------
    #
    # Timing (see `_submit_window`), by mode and the `answer_last5s` toggle:
    #   random/blank — last5s ON + deadline → submit in the last 1-5s window;
    #                  otherwise submit immediately.
    #   ai           — last5s ON + deadline → wait for AI until that window,
    #                  then submit whatever we have (AI answer or fallback);
    #                  last5s OFF + deadline → submit as soon as AI returns,
    #                  capped at `limit - 1s` so the fallback still fits;
    #                  no deadline → wait up to `_AI_MAX_WAIT`.

    def _submit_answer(self, problemid: Any, problemtype: int, real_answer: str | list[str], source: str) -> bool:
        """POST one answer. Returns True once Yuketang accepts it; a False
        return leaves the problem unclaimed so a later reconcile retries."""
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
        try:
            r = http_request("POST", api_url(self.domain, URL_PROBLEM_ANSWER), headers=self.headers,
                             json=payload, attempts=_ANSWER_ATTEMPTS, timeout=_ANSWER_TIMEOUT)
            result = r.json()
        except Exception as e:
            logger.warning(f"[{self.account_id}] answer submit failed for problem {problemid}: {e}")
            result = {"code": -1, "msg": str(e)}

        ok = result.get("code") == 0
        self.on_event("problem", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "problemid": problemid,
            "problemtype": problemtype,
            "answers": real_answer,
            "source": source,
            "status": "success" if ok else "error",
            "message": result.get("msg", ""),
        })
        return ok

    def _submit_window(self, limit: int, mode: str) -> tuple[float, float]:
        """Compute ``(min_hold, max_wait)`` seconds, measured from problem receipt.

        - ``min_hold`` — earliest submit time. Caller must hold this long
          before submitting (so the last-5s gate is honoured).
        - ``max_wait`` — how long to wait for the AI response before falling
          back. Non-AI modes ignore it.
        """
        if limit <= 0:
            # No deadline known: submit immediately for fallback modes; AI mode
            # waits for the provider, bounded so a hang can't eat the answer.
            return (0.0, _AI_MAX_WAIT)
        if self.course_config.get("answer_last5s", True):
            target = max(0.0, limit - random.uniform(1, min(5, limit)))
            return (target, target)
        if mode == "ai":
            # Submit ASAP once AI returns; keep ~1s buffer for fallback.
            return (0.0, max(0.5, float(limit - 1)))
        # Non-AI + last5s off + has deadline: submit immediately.
        return (0.0, 0.0)

    def _answer_problem(self, problem: dict, limit: int) -> None:
        """Answer thread body. Releases the claim if nothing was accepted, so
        the next reconcile pass gets another go at it."""
        problemid = problem["problemId"]
        problemtype = problem["problemType"]
        mode = self._mode_for(problem)
        start_time = time.time()
        submitted = False
        try:
            if mode == "ai":
                submitted = self._answer_via_ai(problem, problemid, problemtype, limit, start_time)
            else:
                submitted = self._submit_fallback(problem, problemid, problemtype, limit, start_time)
        except Exception:
            logger.exception(f"[{self.account_id}] answering problem {problemid} failed")
        finally:
            if not submitted:
                # Drop the claim so the next reconcile pass retries this one.
                with self._answered_lock:
                    self._answered.discard(problemid)
                    self._failures[problemid] = self._failures.get(problemid, 0) + 1

    def _submit_fallback(self, problem: dict, problemid: Any, problemtype: int, limit: int, start_time: float) -> bool:
        """Build and submit a non-AI (random/blank) answer, honoring the
        last-5s hold window. Shared by fallback modes and the AI no-key path."""
        answers, source = self._build_fallback_answer(problem, problemtype)
        min_hold, _ = self._submit_window(limit, source)
        self._hold(min_hold - (time.time() - start_time))
        return self._submit_answer(problemid, problemtype, answers, source)

    def _emit_ai_failed(self, problemid: Any, problemtype: int) -> None:
        self.on_event("problem", {
            "lesson": self.lessonname,
            "lessonid": self.lessonid,
            "problemid": problemid,
            "problemtype": problemtype,
            "status": "ai_failed",
        })

    def _answer_via_ai(self, problem: dict, problemid: Any, problemtype: int, limit: int, start_time: float) -> bool:
        keys_to_try = self._ai_keys_to_try()
        if not keys_to_try:
            logger.warning(f"[{self.account_id}] AI mode selected but no API key configured, using fallback for problem {problemid}")
            return self._submit_fallback(problem, problemid, problemtype, limit, start_time)

        result_holder: list[Any] = [None]
        ai_done = threading.Event()
        ai_failed_event = threading.Event()

        logger.info(f"[{self.account_id}] Attempting AI answer for problem {problemid}")

        def _call_ai():
            try:
                result_holder[0] = self._build_ai_answers(problem, keys_to_try)
            except Exception:
                logger.exception(f"[{self.account_id}] AI answering failed for problem {problemid}")
                ai_failed_event.set()
            finally:
                ai_done.set()

        threading.Thread(target=_call_ai, daemon=True).start()

        min_hold, max_wait = self._submit_window(limit, "ai")
        remaining_wait = max_wait - (time.time() - start_time)
        if remaining_wait > 0:
            ai_done.wait(timeout=remaining_wait)

        # Fire ai_failed notification as soon as AI raises, so users can intervene before the fallback submit.
        notification_sent = False
        if ai_failed_event.is_set() and result_holder[0] is None:
            self._emit_ai_failed(problemid, problemtype)
            notification_sent = True

        # Hold until the earliest allowed submit time (last-5s gate).
        self._hold(min_hold - (time.time() - start_time))
        if result_holder[0] is not None:
            return self._submit_answer(problemid, problemtype, result_holder[0], "ai")

        # AI failed — emit notification (if not already sent) and submit fallback.
        if not notification_sent:
            self._emit_ai_failed(problemid, problemtype)
        fallback_answer, fallback_source = self._build_fallback_answer(problem, problemtype)
        return self._submit_answer(problemid, problemtype, fallback_answer, fallback_source)

    # ------------------------------------------------------------------
    # Danmu
    # ------------------------------------------------------------------

    def _handle_danmu(self, content: str) -> None:
        if not self.course_config.get("auto_danmu", True):
            return

        key = content.lower().strip()
        now = time.time()
        self.danmu_dict[key] = [t for t in self.danmu_dict.get(key, []) if now - t <= 60]

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
        try:
            self._dispatch(json.loads(message))
        except Exception:
            # websocket-client swallows callback errors; log them here so a bad
            # frame never disappears silently.
            logger.exception(f"[{self.account_id}][WS {self.lessonname}] frame handling failed: {message[:200]}")

    def _dispatch(self, data: dict) -> None:
        op = data.get("op", "")
        logger.info(f"[{self.account_id}][WS {self.lessonname}] op={op}")

        if op == "hello":
            # Re-read every presentation on each (re)connect: refreshes `result`
            # and repairs anything a failed fetch left missing.
            timeline = data.get("timeline", [])
            presentation_ids = {
                slide["pres"] for slide in timeline
                if slide.get("type") == "slide" and "pres" in slide
            }
            presentation_ids.add(data.get("presentation"))
            self._load_presentations(presentation_ids)

        elif op == "unlockproblem":
            problem_ref = data["problem"]
            # The frame names the problem by `prob` and its slide by `sid`;
            # either can be the key we hold it under.
            raw_id = problem_ref.get("prob") or problem_ref.get("sid")
            self.on_event("problem_received", {
                "lesson": self.lessonname,
                "lessonid": self.lessonid,
                "problemid": raw_id,
            })
            problem = self._problems.get(raw_id) or self._problems.get(problem_ref.get("sid"))
            if problem is None:
                # Its presentation is named right here — fetch on demand rather
                # than dropping the problem.
                self._load_presentations({problem_ref.get("pres"), data.get("presentation")})
                problem = self._problems.get(raw_id) or self._problems.get(problem_ref.get("sid"))
            self._answer_if_needed(problem, problem_ref.get("limit", -1) - 1, context=f"unlockproblem {raw_id}")

        elif op == "lessonfinished":
            self._stop(_Stop.FINISHED)
            if self.wsapp:
                self.wsapp.close()
            return

        elif op in ("presentationupdated", "presentationcreated", "showpresentation"):
            self._load_presentations([data.get("presentation")])

        elif op == "newdanmu":
            content = data.get("danmu", "")
            if content:
                self._handle_danmu(content)

        elif op == "gainbonus":
            logger.info(f"[{self.account_id}][WS {self.lessonname}] gainbonus raw: {data}")
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

        # Any frame may carry the full unlocked list — the safety net that
        # catches problems whose unlock frame we missed.
        self._reconcile_unlocked(data)
