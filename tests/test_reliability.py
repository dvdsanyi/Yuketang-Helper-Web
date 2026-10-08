"""Regression tests for the paths that must never drop a check-in or an answer.

Runs against a fake Yuketang — no network, no config, no pytest:

    python tests/test_reliability.py
"""

import json
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
_store = tempfile.TemporaryDirectory(prefix="yuketang-test-")  # removed at exit
os.environ.setdefault("YUKETANG_STORE_DIR", _store.name)

import config          # noqa: E402
import lesson as lesson_mod  # noqa: E402
import monitor as monitor_mod  # noqa: E402
import pushdeer  # noqa: E402
from ai_provider import QwenProvider  # noqa: E402
from lesson import Lesson     # noqa: E402

PROB, SLIDE, PRES = "prob-1", "slide-1", "pres-1"

HELLO = {"op": "hello", "timeline": [{"type": "slide", "pres": PRES}], "presentation": PRES}
UNLOCK = {"op": "unlockproblem", "unlockedproblem": [PROB],
          "problem": {"prob": PROB, "sid": SLIDE, "pres": PRES, "limit": 61}}

failures = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   <-- {extra}"))
    if not cond:
        failures.append(name)


class Fake:
    """Fake Yuketang. The flags steer one failure mode each."""

    def __init__(self, result=None, fail_fetch=0, answer_code=0, checkin_ok=True,
                 limit=0, send_ms=0, always_reject=False, cover="http://cover", ptype=1):
        self.result, self.fail_fetch, self.answer_code = result, fail_fetch, answer_code
        self.checkin_ok, self.limit, self.send_ms = checkin_ok, limit, send_ms
        self.always_reject, self.cover, self.ptype = always_reject, cover, ptype
        self.submissions, self.fetches = [], 0

    def __call__(self, method, url, **kw):
        if "checkin" in url:
            if not self.checkin_ok:
                raise RuntimeError("network down")
            return SimpleNamespace(headers={}, json=lambda: {"code": 0, "data": {"lessonToken": "tok"}})
        if "basic-info" in url:
            return SimpleNamespace(headers={}, json=lambda: {"code": 0, "data": {"teacher": {"name": "T"}}})
        if "presentation/fetch" in url:
            self.fetches += 1
            if self.fetches <= self.fail_fetch:
                raise RuntimeError("fetch failed")
            return SimpleNamespace(headers={}, json=lambda: {"code": 0, "data": {"slides": [
                {"id": SLIDE, "cover": self.cover, "problem": {
                    "problemId": PROB, "problemType": self.ptype, "limit": self.limit,
                    "sendTime": self.send_ms, "result": self.result,
                    "options": [{"key": k, "value": k} for k in "ABCD"]}}]}})
        if "problem/answer" in url:
            self.submissions.append(kw["json"])
            code = 1 if self.always_reject else self.answer_code
            self.answer_code = 0  # only the first submit fails
            return SimpleNamespace(headers={}, json=lambda: {"code": code, "msg": "busy" if code else "ok"})
        raise AssertionError("unexpected url " + url)


def make_lesson(fake, **course_config):
    config.http_request = fake
    lesson_mod.http_request = fake
    lesson_mod.get_account = lambda aid: {"user": {"id": 1, "name": "u"}}
    events = []
    cfg = {"type1": "random", "answer_last5s": False, **course_config}
    les = Lesson("acct", {"lessonid": 9, "lessonname": "L", "classroomid": 3},
                 "sid", "pro.yuketang.cn", cfg, lambda t, d: events.append((t, d)))
    les.auth = "tok"
    return les, events


def settle():
    for t in threading.enumerate():
        if t.name.startswith("answer-"):
            t.join(timeout=10)
    time.sleep(0.05)


def stub_ai(behaviour):
    class Provider:
        def answer_options(self, *a, **k): return behaviour()
        def answer_short(self, *a, **k): return behaviour()
    lesson_mod.create_provider = lambda *a, **k: Provider()
    lesson_mod.get_ai_config = lambda aid: {"keys": [{"name": "k", "provider": "google", "key": "x"}],
                                            "active_key": 0, "fallback_keys": True}


# ---------------------------------------------------------------- answering

def test_answering():
    f = Fake(); l, ev = make_lesson(f)
    l._dispatch(HELLO); l._dispatch(UNLOCK); settle()
    check("unlockproblem answers once", len(f.submissions) == 1, f.submissions)
    check("submits the problemId, not the slide id",
          f.submissions and f.submissions[0]["problemId"] == PROB, f.submissions)

    f = Fake(fail_fetch=1); l, ev = make_lesson(f)
    l._dispatch(HELLO)
    check("hello survives a failed presentation fetch", l._problems == {})
    l._dispatch(UNLOCK); settle()
    check("unlockproblem re-fetches and still answers", len(f.submissions) == 1, f.submissions)

    f = Fake(); l, ev = make_lesson(f)
    l._dispatch({"op": "showpresentation", "presentation": PRES, "unlockedproblem": [PROB]}); settle()
    check("problem whose unlock frame we missed is still answered", len(f.submissions) == 1, f.submissions)

    f = Fake(result=["A"]); l, ev = make_lesson(f)
    l._dispatch(HELLO); l._dispatch(UNLOCK); l._dispatch(UNLOCK); settle()
    check("an answer made elsewhere is never overwritten", f.submissions == [], f.submissions)

    f = Fake(); l, ev = make_lesson(f)
    l._dispatch(HELLO); l._dispatch(UNLOCK); settle()
    l._dispatch(UNLOCK); l._dispatch(HELLO); settle()
    check("repeated frames submit exactly once", len(f.submissions) == 1, f.submissions)

    f = Fake(answer_code=1); l, ev = make_lesson(f)
    l._dispatch(HELLO); l._dispatch(UNLOCK); settle()
    statuses = [d["status"] for t, d in ev if t == "problem"]
    check("a rejected submit is reported and retried", statuses == ["error", "success"], statuses)

    f = Fake(always_reject=True); l, ev = make_lesson(f)
    for _ in range(8):
        l._dispatch({**HELLO, "unlockedproblem": [PROB]}); settle()
    check("a hopeless problem stops at the retry cap", len(f.submissions) == lesson_mod._MAX_SUBMIT_FAILS,
          f.submissions)

    f = Fake(); l, ev = make_lesson(f)
    for _ in range(6):
        l._dispatch({"op": "showpresentation", "presentation": PRES, "unlockedproblem": ["ghost"]})
    errors = [d for t, d in ev if t == "problem" and d.get("status") == "error"]
    check("an unresolvable problem is reported, then dropped",
          len(errors) == lesson_mod._MAX_SUBMIT_FAILS and f.submissions == [], (errors, f.fetches))

    f = Fake(limit=60, send_ms=int((time.time() - 30) * 1000)); l, ev = make_lesson(f)
    l._dispatch(HELLO)
    remaining = l._remaining_limit(l._problems[PROB])
    check("deadline recovered from the problem's own clock", 28 <= remaining <= 30, remaining)

    f = Fake(); l, ev = make_lesson(f, answer_last5s=True)
    l._dispatch(HELLO)
    started = time.time()
    l._dispatch({**UNLOCK, "problem": {**UNLOCK["problem"], "limit": 31}})
    time.sleep(0.2)
    l.stop_lesson()  # Monitor evicts the lesson while the answer is still holding
    settle()
    check("a lesson ending mid-hold submits instead of dropping the answer",
          len(f.submissions) == 1 and time.time() - started < 3, f.submissions)

    l, ev = make_lesson(Fake(), answer_last5s=True)
    hold, _ = l._submit_window(60, "random")
    check("near-deadline submits 10-15s before the deadline", 45 <= hold <= 50, hold)

    f = Fake(); l, ev = make_lesson(f, answer_last5s=True)
    l._dispatch(HELLO)
    l._dispatch({**UNLOCK, "problem": {**UNLOCK["problem"], "limit": 31}})
    time.sleep(0.2)
    f.result = ["C"]  # the user answers by hand while we hold
    l.stop_lesson()
    settle()
    skipped = [d.get("message") for t, d in ev if t == "problem" and d.get("status") == "skipped"]
    check("an answer given by hand during the hold is not replaced",
          f.submissions == [] and skipped == ["answered"], (f.submissions, skipped))

    f = Fake(); l, ev = make_lesson(f, type1="off")
    l._dispatch(HELLO); l._dispatch(UNLOCK); l._dispatch(UNLOCK); l._dispatch(HELLO); settle()
    skipped = [d.get("message") for t, d in ev if t == "problem" and d.get("status") == "skipped"]
    check("a turned-off type is reported once, not answered", f.submissions == [] and skipped == ["off"],
          (f.submissions, skipped))

    f = Fake(ptype=4); l, ev = make_lesson(f)
    l._dispatch(HELLO); l._dispatch(UNLOCK); settle()
    skipped = [d.get("message") for t, d in ev if t == "problem" and d.get("status") == "skipped"]
    check("an unsupported type is reported, not silently dropped", skipped == ["unsupported"], skipped)

    f = Fake(limit=60, send_ms=int(time.time() * 1000)); l, ev = make_lesson(f)
    l._dispatch(HELLO)
    l._dispatch({"op": "showpresentation", "dt": (time.time() + 30) * 1000})  # server clock 30s ahead
    l._dispatch({"op": "showpresentation", "dt": (time.time() - 600) * 1000})  # a stale timestamp
    remaining = l._remaining_limit(l._problems[PROB])
    check("deadlines follow the server clock, never a stale timestamp", 28 <= remaining <= 30, remaining)


# ----------------------------------------------------------------- AI paths

def test_ai():
    stub_ai(lambda: ["B"])
    f = Fake(); l, ev = make_lesson(f, type1="ai")
    l._dispatch(HELLO); l._dispatch(UNLOCK); settle()
    check("AI answer is submitted", [s["result"] for s in f.submissions] == [["B"]], f.submissions)
    check("source recorded as ai", [d["source"] for t, d in ev if t == "problem"] == ["ai"])

    def boom(): raise RuntimeError("provider down")
    stub_ai(boom)
    f = Fake(); l, ev = make_lesson(f, type1="ai")
    l._dispatch(HELLO); l._dispatch(UNLOCK); settle()
    problem_events = [d for t, d in ev if t == "problem"]
    check("AI failure falls back to a generated answer",
          len(f.submissions) == 1 and problem_events[-1].get("source") == "random", problem_events)
    check("AI failure is notified with its reason",
          any(d.get("status") == "ai_failed" and "provider down" in d.get("message", "")
              for d in problem_events), problem_events)

    calls = []
    stub_ai(lambda: calls.append(1) or ["A"])
    f = Fake(cover=""); l, ev = make_lesson(f, type1="ai")
    l._dispatch(HELLO); l._dispatch(UNLOCK); settle()
    problem_events = [d for t, d in ev if t == "problem"]
    check("a slide without a cover skips AI and falls back",
          calls == [] and len(f.submissions) == 1 and problem_events[-1].get("source") == "random",
          (calls, problem_events))
    check("a missing cover is the reported reason",
          any(d.get("message") == "slide has no cover image" for d in problem_events), problem_events)

    calls = []
    stub_ai(lambda: calls.append(1) or ["A"])
    f = Fake(cover=""); l, ev = make_lesson(f, type1="ai")
    l._dispatch(HELLO)
    f.cover = "http://cover"  # the slide image finished rendering after our first fetch
    l._dispatch(UNLOCK); settle()
    check("a cover missing from the cached slide is re-read before giving up on AI",
          calls == [1] and [s["result"] for s in f.submissions] == [["A"]], (calls, f.submissions))

    qwen = QwenProvider.__new__(QwenProvider)
    qwen.model = "m"
    null_reply = SimpleNamespace(choices=None)  # what ModelScope sends for some failures
    qwen.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: null_reply)))
    try:
        qwen.answer_options("http://cover", ["A", "B"], 1, 1)
        reason = ""
    except RuntimeError as e:
        reason = str(e)
    check("a ModelScope reply without choices raises a readable error", "choices is null" in reason, reason)

    stub_ai(lambda: time.sleep(30) or ["A"])  # provider hangs past the deadline
    f = Fake(); l, ev = make_lesson(f, type1="ai", answer_last5s=True)
    l._dispatch(HELLO)
    started = time.time()
    l._dispatch({**UNLOCK, "problem": {**UNLOCK["problem"], "limit": 7}})
    settle()
    check("a hung AI still submits a fallback inside the deadline",
          len(f.submissions) == 1 and time.time() - started < 6.5, round(time.time() - started, 1))


# ----------------------------------------------------------------- check-in

def test_checkin():
    f = Fake(checkin_ok=False); l, ev = make_lesson(f)
    try:
        l._checkin()
        raised = False
    except Exception:
        raised = True
    check("a failed check-in raises so the next poll retries", raised)
    check("a failed check-in still notifies",
          ev and ev[0][0] == "signin" and ev[0][1]["status"] == "error", ev)

    m = monitor_mod.Monitor("acct", None)

    class DeadLesson:
        lessonid, lessonname, classroomid = 9, "L", 3
        def start_lesson(self): raise RuntimeError("check-in failed")

    m._active_lessons[9] = DeadLesson()
    m._lesson_thread(m._active_lessons[9])
    check("a dead lesson frees its slot for the next poll", m._active_lessons == {})


# -------------------------------------------------------------- poll loop

def test_poll_loop():
    def run_monitor(codes):
        seq, events, polls = iter(codes), [], []

        def fake(method, url, **kw):
            code = next(seq, 0)
            polls.append(code)
            return SimpleNamespace(headers={}, json=lambda: {
                "code": code, "msg": "server busy", "data": {"onLessonClassrooms": []}})

        monitor_mod.http_request = fake
        monitor_mod.get_domain = lambda aid: "pro.yuketang.cn"
        monitor_mod.get_sessionid = lambda aid: "sid"
        monitor_mod.get_poll_interval = lambda aid: 1
        m = monitor_mod.Monitor("acct", None)
        m._emit = lambda t, d: events.append(t)
        m._running = True
        threading.Thread(target=m._run, daemon=True).start()
        return m, events, polls

    m, events, polls = run_monitor([1, 1, 0, 0])
    time.sleep(3.4)
    check("a transient rejection doesn't stop monitoring",
          m._running and "session_expired" not in events, (events, polls))
    check("polling continues after a rejection", len(polls) >= 3, polls)
    m._running = False
    m._wake_event.set()

    pushed = []
    monitor_mod.pushdeer.send_session_expired = pushed.append
    m, events, polls = run_monitor([1, 1, 1, 1])
    time.sleep(3.4)
    check("a genuinely dead session expires after the cap",
          not m._running and events.count("session_expired") == 1, (events, polls))
    check("an expired session is pushed to the phone", pushed == ["acct"], pushed)


# ---------------------------------------------------------------- push text

def test_push_text():
    title, body = pushdeer.format_event("problem", {"lesson": "L", "problemtype": 2, "status": "success",
                                                    "answers": ["A", "C"], "source": "ai"}, "en")
    check("push names the type once, in the title", title == "Multiple Choice" and body == "L: success, answer(s): A, C [AI]",
          (title, body))
    _, body = pushdeer.format_event("problem", {"lesson": "L", "problemtype": 2, "status": "ai_failed",
                                                "message": "slide has no cover image"}, "en")
    check("push carries the failure reason", body.endswith("\nReason: slide has no cover image"), body)


if __name__ == "__main__":
    test_answering()
    test_ai()
    test_checkin()
    test_poll_loop()
    test_push_text()
    print("\nALL PASS" if not failures else f"\n{len(failures)} FAILED: {failures}")
    sys.exit(1 if failures else 0)
