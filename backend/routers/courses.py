from fastapi import APIRouter

import state
from config import CourseSettingsModel, get_account, update_course_config
from routers._common import AccountDep

router = APIRouter()


@router.get("/api/accounts/{account_id}/courses/active")
async def get_active_courses(account_id: str = AccountDep):
    m = state.get_monitor(account_id)
    if not m:
        return {"lessons": []}
    return {"lessons": m.get_active_lessons()}


@router.get("/api/accounts/{account_id}/courses/all")
async def get_all_courses_route(account_id: str = AccountDep):
    acc = get_account(account_id) or {}
    m = state.get_monitor(account_id)
    active_classroom_ids = {str(lesson["classroomid"]) for lesson in m.get_active_lessons()} if m else set()
    return [
        {**c, "active": c["classroom_id"] in active_classroom_ids}
        for c in acc.get("course_list", [])
    ]


@router.get("/api/accounts/{account_id}/courses/defaults")
async def get_course_defaults(_: str = AccountDep):
    return CourseSettingsModel().model_dump()


@router.get("/api/accounts/{account_id}/courses/settings")
async def get_all_course_settings(account_id: str = AccountDep):
    """Settings of the courses that have been changed; the rest use the defaults."""
    return (get_account(account_id) or {}).get("courses", {})


@router.put("/api/accounts/{account_id}/courses/settings/{course_id}")
async def update_course_settings(course_id: str, body: CourseSettingsModel, account_id: str = AccountDep):
    data = body.model_dump()
    update_course_config(account_id, course_id, data)

    m = state.get_monitor(account_id)
    if m:
        m.update_lesson_config(course_id, data)

    return {"ok": True, "course_id": course_id, "config": data}
