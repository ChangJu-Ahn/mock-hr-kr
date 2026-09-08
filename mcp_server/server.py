"""MCP server exposing Attendance, Leave and master-data CRUD."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import FastMCP
from hr_core import db

mcp = FastMCP(
    "mock-hr-mcp",
    host="0.0.0.0",
    port=8001,
    streamable_http_path="/mcp",
    stateless_http=True,
)


# --------------------------------------------------------------------------- #
# 근태 (attendance)
# --------------------------------------------------------------------------- #

@mcp.tool(
    description=(
        "근태 기록(log_attendance): upsert a day's attendance for an employee. "
        "Provide work_date (YYYY-MM-DD) with optional check_in/check_out (HH:MM). "
        "work_hours and overtime_hours are derived (minus a 1h lunch) and the "
        "status is auto-classified (지각 if check_in>09:00, 조퇴 if check_out<18:00, "
        "결근 if no check_in, else 정상) unless you pass an explicit status "
        "(휴가/재택/etc.). One row per (emp_id, work_date). Returns the row."
    )
)
def log_attendance(
    emp_id: str,
    work_date: str,
    check_in: str | None = None,
    check_out: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    try:
        return db.log_attendance(
            emp_id=emp_id, work_date=work_date, check_in=check_in,
            check_out=check_out, status=status,
        )
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool(
    description=(
        "근태 조회(list_attendance): attendance rows. Filter by emp_id, work_date, "
        "status (정상/지각/조퇴/결근/휴가/재택), or a date range (date_from/date_to, "
        "inclusive YYYY-MM-DD)."
    )
)
def list_attendance(
    emp_id: str | None = None,
    work_date: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return db.list_attendance(
        emp_id=emp_id, work_date=work_date, status=status,
        date_from=date_from, date_to=date_to, limit=limit,
    )


@mcp.tool(
    description=(
        "근태 요약(get_attendance_summary): roll up attendance by status with record "
        "count, distinct employees, total work hours and overtime. Filter by month "
        "(YYYY-MM), a single work_date (YYYY-MM-DD), and/or dept_code."
    )
)
def get_attendance_summary(
    month: str | None = None,
    dept_code: str | None = None,
    work_date: str | None = None,
) -> list[dict[str, Any]]:
    return db.get_attendance_summary(month=month, dept_code=dept_code, work_date=work_date)


# --------------------------------------------------------------------------- #
# 연차·휴가 (leave)
# --------------------------------------------------------------------------- #

@mcp.tool(
    description=(
        "휴가 신청(submit_leave_request): file a leave request (status 신청). "
        "leave_type is 연차/반차/병가/경조사/공가. days is computed from the inclusive "
        "start_date..end_date span (반차 = 0.5); end_date defaults to start_date. "
        "Returns the request plus a non-blocking 'balance_warning' if the current "
        "remaining balance is below the requested days."
    )
)
def submit_leave_request(
    emp_id: str,
    leave_type: str,
    start_date: str,
    end_date: str | None = None,
    reason: str | None = None,
    days: float | None = None,
) -> dict[str, Any]:
    try:
        return db.submit_leave_request(
            emp_id=emp_id, leave_type=leave_type, start_date=start_date,
            end_date=end_date, reason=reason, days=days,
        )
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool(
    description=(
        "휴가 승인/반려(decide_leave_request): decide a pending (신청) request. "
        "decision is 승인/반려/취소. On 승인 the employee's remaining annual-leave "
        "balance is decremented; if the balance is insufficient the approval is "
        "BLOCKED (returns an error) and nothing changes. Only 신청 requests can be "
        "decided. Returns the request plus the updated leave_balance."
    )
)
def decide_leave_request(
    request_id: int,
    decision: str,
    approver_emp_id: str | None = None,
) -> dict[str, Any]:
    try:
        return db.decide_leave_request(
            request_id=request_id, decision=decision, approver_emp_id=approver_emp_id,
        )
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool(
    description=(
        "연차현황 조회(get_leave_balance): the annual-leave balance (entitled/used/"
        "remaining days) for an employee in a given year (defaults to the current year)."
    )
)
def get_leave_balance(emp_id: str, year: int | None = None) -> dict[str, Any]:
    bal = db.get_leave_balance(emp_id, year=year)
    if bal is None:
        return {"not_found": f"No leave balance for employee '{emp_id}'."}
    return bal


@mcp.tool(
    description=(
        "휴가 신청 조회(list_leave_requests): leave requests. Filter by emp_id, status "
        "(신청/승인/반려/취소), leave_type, or start_date range (date_from/date_to)."
    )
)
def list_leave_requests(
    emp_id: str | None = None,
    status: str | None = None,
    leave_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return db.list_leave_requests(
        emp_id=emp_id, status=status, leave_type=leave_type,
        date_from=date_from, date_to=date_to, limit=limit,
    )


# --------------------------------------------------------------------------- #
# 사원 조회 (employee lookup — context for the operational tools)
# --------------------------------------------------------------------------- #

@mcp.tool(
    description=(
        "사원 조회(list_employees): employees for picking an emp_id. Filter by "
        "dept_code, position_code, status (재직/휴직/퇴직), employment_type, "
        "manager_emp_id, or q (search sabun/name/email)."
    )
)
def list_employees(
    dept_code: str | None = None,
    position_code: str | None = None,
    status: str | None = None,
    employment_type: str | None = None,
    manager_emp_id: str | None = None,
    q: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return db.list_employees(
        dept_code=dept_code, position_code=position_code, status=status,
        employment_type=employment_type, manager_emp_id=manager_emp_id, q=q, limit=limit,
    )


@mcp.tool(
    description=(
        "사원 상세(get_employee): one employee with leave balance, recent attendance, "
        "training records, leave requests, and appointment history."
    )
)
def get_employee(emp_id: str) -> dict[str, Any]:
    emp = db.get_employee(emp_id)
    if emp is None:
        return {"not_found": f"Employee '{emp_id}' was not found."}
    return emp


def _master_write(action: Callable[..., dict[str, Any]], /, *args, **kwargs) -> dict[str, Any]:
    try:
        return action(*args, **kwargs)
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool(description=(
    "사원 등록(create_employee): hire an employee, grant annual leave and create an 입사 "
    "appointment atomically. emp_id is optional (auto-assigned) and immutable afterward. "
    "References must exist. Returns the employee or an error object."
))
def create_employee(name: str, dept_code: str, position_code: str,
                    employment_type: str = "정규직", email: str | None = None,
                    phone: str | None = None, hire_date: str | None = None,
                    birth_date: str | None = None, manager_emp_id: str | None = None,
                    emp_id: str | None = None) -> dict[str, Any]:
    return _master_write(
        db.hire_employee, name=name, dept_code=dept_code, position_code=position_code,
        employment_type=employment_type, email=email, phone=phone, hire_date=hire_date,
        birth_date=birth_date, manager_emp_id=manager_emp_id, emp_id=emp_id,
    )


@mcp.tool(description=(
    "사원 수정(update_employee): patch fields using changes. Allowed: name, dept_code, "
    "position_code, email, phone, employment_type, status, hire_date, birth_date, manager_emp_id. "
    "Omitted fields stay unchanged; null clears nullable fields. emp_id is immutable. "
    "Changing hire_date also updates 입사 appointments. Returns a row or error object."
))
def update_employee(emp_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    return _master_write(db.update_employee, emp_id, **changes)


@mcp.tool(description=(
    "사원 삭제(delete_employee): delete only an unreferenced employee. Reports/managers, "
    "training, leave balances/requests/approvals, attendance and appointments block deletion. "
    "A newly hired employee has balance and appointment references too. No cascades; "
    "returns an error object naming all blockers and counts instead of raising."
))
def delete_employee(emp_id: str) -> dict[str, Any]:
    return _master_write(db.delete_employee, emp_id)


@mcp.tool(description="부서 조회(list_departments): list departments, optionally by parent_dept_code.")
def list_departments(parent_dept_code: str | None = None) -> list[dict[str, Any]]:
    return db.list_departments(parent_dept_code=parent_dept_code)


@mcp.tool(description="부서 상세(get_department): get one department with parent and manager context.")
def get_department(dept_code: str) -> dict[str, Any]:
    row = db.get_department(dept_code)
    return row if row is not None else {"not_found": f"Department '{dept_code}' was not found."}


@mcp.tool(description=(
    "부서 생성(create_department): create a department with a unique immutable dept_code. "
    "Optional parent department and manager must exist; hierarchy cycles are rejected."
))
def create_department(dept_code: str, dept_name: str, parent_dept_code: str | None = None,
                      manager_emp_id: str | None = None, cost_center: str | None = None) -> dict[str, Any]:
    return _master_write(db.create_department, dept_code=dept_code, dept_name=dept_name,
                         parent_dept_code=parent_dept_code, manager_emp_id=manager_emp_id,
                         cost_center=cost_center)


@mcp.tool(description=(
    "부서 수정(update_department): changes may contain dept_name, parent_dept_code, "
    "manager_emp_id, cost_center. Null clears optional fields; dept_code is immutable. "
    "Invalid references/cycles return an error object."
))
def update_department(dept_code: str, changes: dict[str, Any]) -> dict[str, Any]:
    return _master_write(db.update_department, dept_code, **changes)


@mcp.tool(description=(
    "부서 삭제(delete_department): only delete an unreferenced department. Employees "
    "(including retired), child departments and appointments block deletion. No cascades; "
    "returns an error object naming the blocking records and counts."
))
def delete_department(dept_code: str) -> dict[str, Any]:
    return _master_write(db.delete_department, dept_code)


@mcp.tool(description="직급 조회(list_positions): list positions with level, leave allowance and headcount.")
def list_positions() -> list[dict[str, Any]]:
    return db.list_positions()


@mcp.tool(description="직급 상세(get_position): get one position by its immutable position_code.")
def get_position(position_code: str) -> dict[str, Any]:
    row = db.get_position(position_code)
    return row if row is not None else {"not_found": f"Position '{position_code}' was not found."}


@mcp.tool(description=(
    "직급 생성(create_position): create a unique immutable position_code. level_no is an "
    "integer >=1 and min_leave_days is finite and >=0. Returns a row or an error object."
))
def create_position(position_code: str, position_name: str, level_no: int = 1,
                    min_leave_days: float = 15.0) -> dict[str, Any]:
    return _master_write(db.create_position, position_code=position_code, position_name=position_name,
                         level_no=level_no, min_leave_days=min_leave_days)


@mcp.tool(description=(
    "직급 수정(update_position): changes may contain position_name, level_no, min_leave_days. "
    "position_code is immutable. Existing employee leave grants are not retroactively changed."
))
def update_position(position_code: str, changes: dict[str, Any]) -> dict[str, Any]:
    return _master_write(db.update_position, position_code, **changes)


@mcp.tool(description=(
    "직급 삭제(delete_position): only delete when no employees or appointments reference "
    "the position. Returns an error object with blocker counts; never cascades."
))
def delete_position(position_code: str) -> dict[str, Any]:
    return _master_write(db.delete_position, position_code)


@mcp.tool(description="과정 조회(list_courses): list courses; filter by category, is_mandatory or q.")
def list_courses(category: str | None = None, is_mandatory: bool | None = None,
                 q: str | None = None) -> list[dict[str, Any]]:
    return db.list_courses(category=category, is_mandatory=is_mandatory, q=q)


@mcp.tool(description="과정 상세(get_course): get one course by its immutable course_code.")
def get_course(course_code: str) -> dict[str, Any]:
    row = db.get_course(course_code)
    return row if row is not None else {"not_found": f"Course '{course_code}' was not found."}


@mcp.tool(description=(
    "과정 생성(create_course): create a course with a unique immutable course_code. "
    "Optional hours/capacity are nonnegative; capacity is integral. Returns a row or error object."
))
def create_course(course_code: str, course_name: str, category: str | None = None,
                  delivery: str | None = None, hours: float | None = None,
                  capacity: int | None = None, is_mandatory: bool = False) -> dict[str, Any]:
    return _master_write(db.create_course, course_code=course_code, course_name=course_name,
                         category=category, delivery=delivery, hours=hours,
                         capacity=capacity, is_mandatory=is_mandatory)


@mcp.tool(description=(
    "과정 수정(update_course): changes may contain course_name, category, delivery, hours, "
    "capacity, is_mandatory. Null clears nullable fields; course_code is immutable."
))
def update_course(course_code: str, changes: dict[str, Any]) -> dict[str, Any]:
    return _master_write(db.update_course, course_code, **changes)


@mcp.tool(description=(
    "과정 삭제(delete_course): only delete an unreferenced course. Any training record "
    "blocks deletion; returns an error object with its count rather than raising."
))
def delete_course(course_code: str) -> dict[str, Any]:
    return _master_write(db.delete_course, course_code)


def main() -> None:
    import uvicorn

    uvicorn.run(build_asgi_app(), host="0.0.0.0", port=8001)


DEFAULT_API_KEY = "changjuahn"


class _ApiKeyGuard:
    """Pure-ASGI header gate: only requests carrying a valid X-API-Key pass.

    Kept as pure ASGI (not Starlette BaseHTTPMiddleware) so it never buffers the
    streamable-HTTP / SSE responses that the MCP transport relies on. The
    accepted key is read from HR_API_KEY at request time (default 'changjuahn').
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            provided = headers.get(b"x-api-key")
            expected = os.environ.get("HR_API_KEY", DEFAULT_API_KEY).encode()
            if provided is None or provided != expected:
                await send({
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"error":"Invalid or missing API key. Send header X-API-Key."}',
                })
                return
        await self.app(scope, receive, send)


def build_asgi_app() -> _ApiKeyGuard:
    """The streamable-HTTP MCP app wrapped in the API-key guard."""
    return _ApiKeyGuard(mcp.streamable_http_app())


__all__ = [
    "DEFAULT_API_KEY",
    "build_asgi_app",
    "create_course",
    "create_department",
    "create_employee",
    "create_position",
    "delete_course",
    "delete_department",
    "delete_employee",
    "delete_position",
    "decide_leave_request",
    "get_attendance_summary",
    "get_employee",
    "get_course",
    "get_department",
    "get_position",
    "get_leave_balance",
    "list_attendance",
    "list_employees",
    "list_courses",
    "list_departments",
    "list_positions",
    "list_leave_requests",
    "log_attendance",
    "main",
    "mcp",
    "submit_leave_request",
    "update_course",
    "update_department",
    "update_employee",
    "update_position",
]
