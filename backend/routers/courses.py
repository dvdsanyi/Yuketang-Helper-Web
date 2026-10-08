from fastapi import APIRouter

import state
from config import (
    CourseSettingsModel,
    get_account, get_course_config, update_course_config,
)
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
    cached = acc.get("course_list", [])
    m = state.get_monitor(account_id)
    active_classroom_ids: set[str] = (
        {str(lesson["classroomid"]) for lesson in m.get_active_lessons()} if m else set()
    )
    return [
        {
            "classroom_id": c["classroom_id"],
            "name": c["name"],
            "classroom_name": c["classroom_name"],
            "teacher_name": c["teacher_name"],
            "active": c["classroom_id"] in active_classroom_ids,
        }
        for c in cached
    ]


@router.get("/api/accounts/{account_id}/courses/defaults")
async def get_course_defaults(_: str = AccountDep):
    return CourseSettingsModel().model_dump()


@router.get("/api/accounts/{account_id}/courses/settings")
async def get_all_course_settings(account_id: str = AccountDep):
    acc = get_account(account_id) or {}
    courses = acc.get("courses", {})
    return {cid: get_course_config(account_id, cid) for cid in courses}


@router.get("/api/accounts/{account_id}/courses/settings/{course_id}")
async def get_course_settings(course_id: str, account_id: str = AccountDep):
    return get_course_config(account_id, course_id)


@router.put("/api/accounts/{account_id}/courses/settings/{course_id}")
async def update_course_settings(course_id: str, body: CourseSettingsModel, account_id: str = AccountDep):
    data = body.model_dump()
    update_course_config(account_id, course_id, data)

    m = state.get_monitor(account_id)
    if m:
        m.update_lesson_config(course_id, data)

    return {"ok": True, "course_id": course_id, "config": data}
