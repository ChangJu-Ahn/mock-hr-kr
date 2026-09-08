from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette import status
from starlette.concurrency import run_in_threadpool
from pydantic import ValidationError

from api import mcp_docs
from api.rest import (
    CourseCreate, CourseUpdate, DepartmentCreate, DepartmentUpdate,
    EmployeeCreate, EmployeeUpdate, PositionCreate, PositionUpdate,
)
from hr_core import db

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter(include_in_schema=False)

LEAVE_TYPES = ["연차", "반차", "병가", "경조사", "공가"]
ATTENDANCE_STATUS = ["정상", "지각", "조퇴", "결근", "휴가", "재택"]
APPOINTMENT_TYPES = ["승진", "부서이동", "휴직", "복직", "퇴직"]
_SEE_OTHER = status.HTTP_303_SEE_OTHER


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _float_or_none(value: str | None) -> float | None:
    value = _blank_to_none(value)
    return float(value) if value is not None else None


def _context(request: Request, **extra: Any) -> dict[str, Any]:
    return {"request": request, **extra}


def _redirect(path: str, *, message: str | None = None, error: str | None = None):
    query = {}
    if message:
        query["message"] = message
    if error:
        query["error"] = error
    return RedirectResponse(path + ("?" + urlencode(query) if query else ""), status_code=_SEE_OTHER)


def _emp_options() -> list[dict[str, Any]]:
    return db.list_employees(limit=1000)


_MASTER_FORMS = {
    "departments": (DepartmentCreate, DepartmentUpdate, "department", "dept_code", "dept_name"),
    "positions": (PositionCreate, PositionUpdate, "position", "position_code", "position_name"),
    "courses": (CourseCreate, CourseUpdate, "course", "course_code", "course_name"),
    "employees": (EmployeeCreate, EmployeeUpdate, "employee", "emp_id", "name"),
}


async def _master_form(request: Request, collection: str, identifier: str | None = None):
    create_model, update_model, entity, key, name = _MASTER_FORMS[collection]
    target = f"/employees/{identifier}" if collection == "employees" and identifier else f"/{collection}"
    try:
        values = {}
        for field, value in (await request.form()).multi_items():
            if field in values:
                raise ValueError(f"duplicate form field: {field}")
            if not isinstance(value, str):
                raise ValueError("master-data forms do not accept file uploads")
            values[field] = _blank_to_none(value)
        model = update_model if identifier is not None else create_model
        payload = model.model_validate(values).model_dump(exclude_unset=True)
        if identifier is None:
            action = db.hire_employee if entity == "employee" else getattr(db, f"create_{entity}")
            row = await run_in_threadpool(action, **payload)
        else:
            row = await run_in_threadpool(getattr(db, f"update_{entity}"), identifier, **payload)
    except ValidationError as exc:
        reason = "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                           for error in exc.errors())
        return _redirect(target, error=reason)
    except db.NotFoundError as exc:
        return _redirect(f"/{collection}", error=str(exc))
    except ValueError as exc:
        return _redirect(target, error=str(exc))
    return _redirect(target, message=f"{row[key]} {row[name]} 저장됨")


def _master_delete(collection: str, identifier: str):
    entity = _MASTER_FORMS[collection][2]
    try:
        getattr(db, f"delete_{entity}")(identifier)
    except db.NotFoundError as exc:
        return _redirect(f"/{collection}", error=str(exc))
    except ValueError as exc:
        target = f"/employees/{identifier}" if collection == "employees" else f"/{collection}"
        return _redirect(target, error=str(exc))
    return _redirect(f"/{collection}", message=f"{identifier} 삭제됨")


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #

@router.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(
        request, "dashboard.html", _context(request, summary=db.get_dashboard_summary()))


# --------------------------------------------------------------------------- #
# 사원 (employees) + 입사
# --------------------------------------------------------------------------- #

@router.get("/employees")
def employees(request: Request, dept_code: str | None = None, position_code: str | None = None,
              status_filter: str | None = None, employment_type: str | None = None,
              q: str | None = None, message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(request, "employees.html", _context(
        request,
        rows=db.list_employees(dept_code=_blank_to_none(dept_code),
                               position_code=_blank_to_none(position_code),
                               status=_blank_to_none(status_filter),
                               employment_type=_blank_to_none(employment_type),
                               q=_blank_to_none(q)),
        departments=db.list_departments(),
        positions=db.list_positions(),
        managers=_emp_options(),
        filters={"dept_code": dept_code or "", "position_code": position_code or "",
                 "status_filter": status_filter or "", "employment_type": employment_type or "",
                 "q": q or ""},
        message=message, error=error,
    ))


@router.post("/employees/hire")
def employees_hire(name: str = Form(...), dept_code: str = Form(...),
                   position_code: str = Form(...), employment_type: str = Form("정규직"),
                   email: str | None = Form(None), phone: str | None = Form(None),
                   hire_date: str | None = Form(None), birth_date: str | None = Form(None),
                   manager_emp_id: str | None = Form(None)):
    try:
        emp = db.hire_employee(
            name=name, dept_code=dept_code, position_code=position_code,
            employment_type=employment_type, email=_blank_to_none(email),
            phone=_blank_to_none(phone), hire_date=_blank_to_none(hire_date),
            birth_date=_blank_to_none(birth_date), manager_emp_id=_blank_to_none(manager_emp_id),
        )
    except ValueError as exc:
        return _redirect("/employees", error=str(exc))
    return _redirect("/employees", message=f"{emp['emp_id']} {name} 입사 처리됨")


@router.get("/employees/{emp_id}")
def employee_detail(request: Request, emp_id: str, message: str | None = None,
                    error: str | None = None):
    emp = db.get_employee(emp_id)
    if emp is None:
        raise HTTPException(status_code=404, detail="employee not found")
    return templates.TemplateResponse(request, "employee_detail.html", _context(
        request, emp=emp, departments=db.list_departments(), positions=db.list_positions(),
        managers=_emp_options(), message=message, error=error))


@router.post("/employees/{emp_id}/update")
async def employees_update(request: Request, emp_id: str):
    return await _master_form(request, "employees", emp_id)


@router.post("/employees/{emp_id}/delete")
def employees_delete(emp_id: str):
    return _master_delete("employees", emp_id)


# --------------------------------------------------------------------------- #
# 부서 (departments · org tree) · 직급 (positions)
# --------------------------------------------------------------------------- #

@router.get("/departments")
def departments(request: Request, message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(request, "departments.html", _context(
        request, tree=db.get_org_tree(), headcounts=db.headcount_by_dept(),
        managers=_emp_options(), message=message, error=error))


@router.post("/departments/create")
async def departments_create(request: Request):
    return await _master_form(request, "departments")


@router.post("/departments/{dept_code}/update")
async def departments_update(request: Request, dept_code: str):
    return await _master_form(request, "departments", dept_code)


@router.post("/departments/{dept_code}/delete")
def departments_delete(dept_code: str):
    return _master_delete("departments", dept_code)


@router.get("/positions")
def positions(request: Request, message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(request, "positions.html", _context(
        request, rows=db.list_positions(), message=message, error=error))


@router.post("/positions/create")
async def positions_create(request: Request):
    return await _master_form(request, "positions")


@router.post("/positions/{position_code}/update")
async def positions_update(request: Request, position_code: str):
    return await _master_form(request, "positions", position_code)


@router.post("/positions/{position_code}/delete")
def positions_delete(position_code: str):
    return _master_delete("positions", position_code)


# --------------------------------------------------------------------------- #
# 교육 (courses · training)
# --------------------------------------------------------------------------- #

@router.get("/courses")
def courses(request: Request, category: str | None = None, course_code: str | None = None,
            status_filter: str | None = None, message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(request, "courses.html", _context(
        request,
        rows=db.list_courses(category=_blank_to_none(category)),
        records=db.list_training_records(course_code=_blank_to_none(course_code),
                                         status=_blank_to_none(status_filter), limit=200),
        courses=db.list_courses(),
        employees=_emp_options(),
        completion=db.training_completion_rate(mandatory_only=True),
        filters={"category": category or "", "course_code": course_code or "",
                 "status_filter": status_filter or ""},
        message=message, error=error,
    ))


@router.post("/courses/create")
async def courses_create(request: Request):
    return await _master_form(request, "courses")


@router.post("/courses/{course_code}/update")
async def courses_update(request: Request, course_code: str):
    return await _master_form(request, "courses", course_code)


@router.post("/courses/{course_code}/delete")
def courses_delete(course_code: str):
    return _master_delete("courses", course_code)


@router.post("/courses/enroll")
def courses_enroll(emp_id: str = Form(...), course_code: str = Form(...),
                   enroll_date: str | None = Form(None)):
    try:
        db.enroll_training(emp_id=emp_id, course_code=course_code,
                           enroll_date=_blank_to_none(enroll_date))
    except ValueError as exc:
        return _redirect("/courses", error=str(exc))
    return _redirect("/courses", message="교육 수강신청 완료")


@router.post("/courses/{record_id}/complete")
def courses_complete(record_id: int, status_value: str = Form("이수"),
                     score: str | None = Form(None)):
    try:
        db.complete_training(record_id=record_id, status=status_value,
                             score=_float_or_none(score))
    except ValueError as exc:
        return _redirect("/courses", error=str(exc))
    return _redirect("/courses", message="교육 이수처리 완료")


# --------------------------------------------------------------------------- #
# 연차·휴가 (leave) + 신청/승인
# --------------------------------------------------------------------------- #

@router.get("/leave")
def leave(request: Request, status_filter: str | None = None, emp_id: str | None = None,
          message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(request, "leave.html", _context(
        request,
        requests=db.list_leave_requests(status=_blank_to_none(status_filter),
                                        emp_id=_blank_to_none(emp_id), limit=200),
        balances=db.list_leave_balances(),
        employees=_emp_options(),
        leave_types=LEAVE_TYPES,
        filters={"status_filter": status_filter or "", "emp_id": emp_id or ""},
        message=message, error=error,
    ))


@router.post("/leave/submit")
def leave_submit(emp_id: str = Form(...), leave_type: str = Form(...),
                 start_date: str = Form(...), end_date: str | None = Form(None),
                 reason: str | None = Form(None)):
    try:
        res = db.submit_leave_request(emp_id=emp_id, leave_type=leave_type,
                                      start_date=start_date, end_date=_blank_to_none(end_date),
                                      reason=_blank_to_none(reason))
    except ValueError as exc:
        return _redirect("/leave", error=str(exc))
    note = f"휴가신청 등록됨 ({res['days']}일)"
    if res.get("balance_warning"):
        note += " | 잔여부족 경고"
    return _redirect("/leave", message=note)


@router.post("/leave/{request_id}/decide")
def leave_decide(request_id: int, decision: str = Form(...),
                 approver_emp_id: str | None = Form(None)):
    try:
        db.decide_leave_request(request_id=request_id, decision=decision,
                                approver_emp_id=_blank_to_none(approver_emp_id))
    except ValueError as exc:
        return _redirect("/leave", error=str(exc))
    return _redirect("/leave", message=f"휴가신청 {decision} 처리됨")


# --------------------------------------------------------------------------- #
# 근태 (attendance) + 기록
# --------------------------------------------------------------------------- #

@router.get("/attendance")
def attendance(request: Request, emp_id: str | None = None, work_date: str | None = None,
               status_filter: str | None = None, month: str | None = None,
               message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(request, "attendance.html", _context(
        request,
        rows=db.list_attendance(emp_id=_blank_to_none(emp_id),
                                work_date=_blank_to_none(work_date),
                                status=_blank_to_none(status_filter), limit=200),
        summary=db.get_attendance_summary(month=_blank_to_none(month)),
        employees=_emp_options(),
        statuses=ATTENDANCE_STATUS,
        latest=db.latest_work_date(),
        filters={"emp_id": emp_id or "", "work_date": work_date or "",
                 "status_filter": status_filter or "", "month": month or ""},
        message=message, error=error,
    ))


@router.post("/attendance/log")
def attendance_log(emp_id: str = Form(...), work_date: str = Form(...),
                   check_in: str | None = Form(None), check_out: str | None = Form(None),
                   status_value: str | None = Form(None)):
    try:
        db.log_attendance(emp_id=emp_id, work_date=work_date,
                          check_in=_blank_to_none(check_in), check_out=_blank_to_none(check_out),
                          status=_blank_to_none(status_value))
    except ValueError as exc:
        return _redirect("/attendance", error=str(exc))
    return _redirect("/attendance", message="근태 기록됨")


# --------------------------------------------------------------------------- #
# 인사발령 (appointments)
# --------------------------------------------------------------------------- #

@router.get("/appointments")
def appointments(request: Request, emp_id: str | None = None, type: str | None = None,
                 message: str | None = None, error: str | None = None):
    return templates.TemplateResponse(request, "appointments.html", _context(
        request,
        rows=db.list_appointments(emp_id=_blank_to_none(emp_id), type=_blank_to_none(type),
                                  limit=200),
        employees=_emp_options(),
        departments=db.list_departments(),
        positions=db.list_positions(),
        types=APPOINTMENT_TYPES,
        filters={"emp_id": emp_id or "", "type": type or ""},
        message=message, error=error,
    ))


@router.post("/appointments/create")
def appointments_create(emp_id: str = Form(...), type: str = Form(...),
                        effective_date: str | None = Form(None),
                        to_dept: str | None = Form(None), to_position: str | None = Form(None),
                        note: str | None = Form(None)):
    try:
        db.create_appointment(emp_id=emp_id, type=type,
                              effective_date=_blank_to_none(effective_date),
                              to_dept=_blank_to_none(to_dept),
                              to_position=_blank_to_none(to_position), note=_blank_to_none(note))
    except ValueError as exc:
        return _redirect("/appointments", error=str(exc))
    return _redirect("/appointments", message=f"{type} 발령 처리됨")


@router.get("/guide")
def guide(request: Request):
    return templates.TemplateResponse(request, "guide.html", _context(request))


# --------------------------------------------------------------------------- #
# MCP 문서 (MCP reference — the counterpart of /api/docs)
# --------------------------------------------------------------------------- #

def _public_base_url(request: Request) -> str:
    """Origin as the browser sees it, honouring the ACA ingress / Caddy hops.

    The MCP endpoint lives on the same origin as this page, so the docs must
    advertise the externally reachable scheme+host rather than the internal
    ``http://localhost:8000`` the api container is addressed on.
    """
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    scheme = forwarded_proto or request.url.scheme
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host")
            or request.url.netloc)
    host = host.split(",")[0].strip() or request.url.netloc
    return f"{scheme}://{host}"


@router.get("/mcp-docs")
async def mcp_docs_page(request: Request):
    base_url = _public_base_url(request)
    return templates.TemplateResponse(request, "mcp_docs.html", _context(
        request, spec=await mcp_docs.build_spec(base_url), base_url=base_url))


@router.get("/mcp-docs/spec.json")
async def mcp_docs_spec(request: Request):
    """Machine-readable MCP reference — the MCP analogue of /api/openapi.json."""
    return JSONResponse(await mcp_docs.build_spec(_public_base_url(request)))
