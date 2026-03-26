import requests

from config import get_domain


def _make_headers(sessionid: str) -> dict:
    domain = get_domain()
    return {
        "Cookie": "sessionid=%s" % sessionid,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:97.0) "
            "Gecko/20100101 Firefox/97.0"
        ),
        "Referer": "https://%s/" % domain,
        "xt-agent": "web",
    }


def get_user_info(sessionid: str) -> dict:
    headers = _make_headers(sessionid)
    r = requests.get(
        url="https://%s/api/v3/user/basic-info" % get_domain(),
        headers=headers,
        proxies={"http": None, "https": None},
        timeout=10,
    )
    return r.json()["data"]


def get_all_courses(sessionid: str) -> list:
    headers = _make_headers(sessionid)
    r = requests.get(
        url="https://%s/v2/api/web/courses/list?identity=2" % get_domain(),
        headers=headers,
        proxies={"http": None, "https": None},
        timeout=10,
    )
    return r.json()["data"]["list"]


def get_on_lesson(sessionid: str) -> list:
    headers = _make_headers(sessionid)
    r = requests.get(
        url="https://%s/api/v3/classroom/on-lesson-upcoming-exam" % get_domain(),
        headers=headers,
        proxies={"http": None, "https": None},
        timeout=10,
    )
    return r.json()["data"]["onLessonClassrooms"]
