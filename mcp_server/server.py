"""MCP server exposing the mock HR Attendance & Leave tools."""

from __future__ import annotations

import os
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
    "decide_leave_request",
    "get_attendance_summary",
    "get_employee",
    "get_leave_balance",
    "list_attendance",
    "list_employees",
    "list_leave_requests",
    "log_attendance",
    "main",
    "mcp",
    "submit_leave_request",
]
