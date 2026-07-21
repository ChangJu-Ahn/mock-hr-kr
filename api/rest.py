from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import require_api_key
from hr_core import db

router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])

EmploymentType = Literal["정규직", "계약직", "인턴"]
EmployeeStatus = Literal["재직", "휴직", "퇴직"]
AppointmentType = Literal["입사", "승진", "부서이동", "휴직", "복직", "퇴직"]


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class ApiIndex(BaseModel):
    name: str
    docs: str
    endpoints: list[str]


class HealthResponse(BaseModel):
    status: str
    db: str
    counts: dict[str, int]


class DepartmentRow(BaseModel):
    dept_code: str
    dept_name: str
    parent_dept_code: str | None = None
    manager_emp_id: str | None = None
    manager_name: str | None = None
    cost_center: str | None = None
    headcount: int | None = None
    depth: int | None = None


class PositionRow(BaseModel):
    position_code: str
    position_name: str
    level_no: int
    min_leave_days: float
    headcount: int | None = None


class EmployeeRow(BaseModel):
    emp_id: str
    name: str
    dept_code: str | None = None
    dept_name: str | None = None
    position_code: str | None = None
    position_name: str | None = None
    level_no: int | None = None
    email: str | None = None
    phone: str | None = None
    employment_type: str | None = None
    status: str | None = None
    hire_date: str | None = None
    birth_date: str | None = None
    manager_emp_id: str | None = None
    manager_name: str | None = None


class CourseRow(BaseModel):
    course_code: str
    course_name: str
    category: str | None = None
    delivery: str | None = None
    hours: float | None = None
    capacity: int | None = None
    is_mandatory: int = 0


class TrainingRow(BaseModel):
    id: int
    emp_id: str
    emp_name: str | None = None
    dept_code: str | None = None
    course_code: str
    course_name: str | None = None
    category: str | None = None
    is_mandatory: int | None = None
    enroll_date: str | None = None
    complete_date: str | None = None
    status: str | None = None
    score: float | None = None


class AppointmentRow(BaseModel):
    id: int
    emp_id: str
    emp_name: str | None = None
    effective_date: str | None = None
    type: str
    from_dept: str | None = None
    to_dept: str | None = None
    from_position: str | None = None
    to_position: str | None = None
    note: str | None = None


class EmployeeCreate(BaseModel):
    name: str
    dept_code: str
    position_code: str
    employment_type: EmploymentType = "정규직"
    email: str | None = None
    phone: str | None = None
    hire_date: str | None = None
    birth_date: str | None = None
    manager_emp_id: str | None = None


class TrainingCreate(BaseModel):
    emp_id: str
    course_code: str
    enroll_date: str | None = None
    status: str = "수강중"


class TrainingComplete(BaseModel):
    status: Literal["이수", "미이수", "수강중"] = "이수"
    score: float | None = None
    complete_date: str | None = None


class AppointmentCreate(BaseModel):
    emp_id: str
    type: AppointmentType
    effective_date: str | None = None
    to_dept: str | None = None
    to_position: str | None = None
    note: str | None = None


# --------------------------------------------------------------------------- #
# Index / health
# --------------------------------------------------------------------------- #

@router.get("", response_model=ApiIndex, tags=["Index"])
@router.get("/", response_model=ApiIndex, tags=["Index"], include_in_schema=False)
def api_index() -> dict[str, Any]:
    return {
        "name": "Mock HR REST API (Employee · Department · Position · Course · Training · Appointment)",
        "docs": "/api/docs",
        "endpoints": [
            "GET /api/health",
            "GET /api/departments",
            "GET /api/departments/tree",
            "GET /api/departments/{dept_code}",
            "GET /api/positions",
            "GET /api/employees",
            "GET /api/employees/{emp_id}",
            "POST /api/employees",
            "GET /api/courses",
            "GET|POST /api/training-records",
            "POST /api/training-records/{id}/complete",
            "GET|POST /api/appointments",
        ],
    }


@router.get("/health", response_model=HealthResponse, tags=["Health"])
def health() -> dict[str, Any]:
    return {"status": "ok", "db": db.get_db_path(), "counts": db.counts()}


# --------------------------------------------------------------------------- #
# 부서 (department) · 직급 (position)
# --------------------------------------------------------------------------- #

@router.get("/departments", response_model=list[DepartmentRow], tags=["Department"])
def get_departments(parent_dept_code: str | None = None) -> list[dict[str, Any]]:
    return db.list_departments(parent_dept_code=parent_dept_code)


@router.get("/departments/tree", response_model=list[DepartmentRow], tags=["Department"])
def get_departments_tree() -> list[dict[str, Any]]:
    """조직도 순서(부모→자식)로 평탄화한 부서 목록. 각 행에 ``depth`` 포함."""
    return db.get_org_tree()


@router.get("/departments/{dept_code}", response_model=DepartmentRow, tags=["Department"])
def get_department(dept_code: str) -> dict[str, Any]:
    row = db.get_department(dept_code)
    if row is None:
        raise HTTPException(status_code=404, detail=f"department {dept_code} not found")
    return row


@router.get("/positions", response_model=list[PositionRow], tags=["Position"])
def get_positions() -> list[dict[str, Any]]:
    return db.list_positions()


# --------------------------------------------------------------------------- #
# 사원 (employee)
# --------------------------------------------------------------------------- #

@router.get("/employees", response_model=list[EmployeeRow], tags=["Employee"])
def get_employees(
    dept_code: str | None = None,
    position_code: str | None = None,
    status: EmployeeStatus | None = None,
    employment_type: EmploymentType | None = None,
    manager_emp_id: str | None = None,
    q: str | None = Query(default=None, description="search emp_id / name / email"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return db.list_employees(
        dept_code=dept_code, position_code=position_code, status=status,
        employment_type=employment_type, manager_emp_id=manager_emp_id, q=q, limit=limit,
    )


@router.get("/employees/{emp_id}", tags=["Employee"])
def get_employee(emp_id: str) -> dict[str, Any]:
    """사원 상세 + 연차현황 · 최근 근태 · 교육이수 · 휴가신청 · 인사발령."""
    emp = db.get_employee(emp_id)
    if emp is None:
        raise HTTPException(status_code=404, detail=f"employee {emp_id} not found")
    return emp


@router.post("/employees", tags=["Employee"])
def create_employee(payload: EmployeeCreate) -> dict[str, Any]:
    """입사 처리: 사원 등록 + 당해연도 연차 부여 + 입사 발령 기록(원자적)."""
    try:
        return db.hire_employee(
            name=payload.name, dept_code=payload.dept_code,
            position_code=payload.position_code, employment_type=payload.employment_type,
            email=payload.email, phone=payload.phone, hire_date=payload.hire_date,
            birth_date=payload.birth_date, manager_emp_id=payload.manager_emp_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------- #
# 교육 (course · training_record)
# --------------------------------------------------------------------------- #

@router.get("/courses", response_model=list[CourseRow], tags=["Course"])
def get_courses(
    category: str | None = None,
    is_mandatory: bool | None = None,
    q: str | None = Query(default=None, description="search course_code / course_name"),
) -> list[dict[str, Any]]:
    return db.list_courses(category=category, is_mandatory=is_mandatory, q=q)


@router.get("/training-records", response_model=list[TrainingRow], tags=["Training"])
def get_training_records(
    emp_id: str | None = None,
    course_code: str | None = None,
    status: str | None = None,
    category: str | None = None,
    date_from: str | None = Query(default=None, description="enroll_date >= YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="enroll_date <= YYYY-MM-DD"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return db.list_training_records(
        emp_id=emp_id, course_code=course_code, status=status, category=category,
        date_from=date_from, date_to=date_to, limit=limit,
    )


@router.post("/training-records", response_model=TrainingRow, tags=["Training"])
def create_training_record(payload: TrainingCreate) -> dict[str, Any]:
    """교육 수강신청."""
    try:
        return db.enroll_training(
            emp_id=payload.emp_id, course_code=payload.course_code,
            enroll_date=payload.enroll_date, status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/training-records/{record_id}/complete", response_model=TrainingRow, tags=["Training"])
def complete_training_record(record_id: int, payload: TrainingComplete) -> dict[str, Any]:
    """교육 이수/미이수 처리."""
    try:
        return db.complete_training(
            record_id=record_id, status=payload.status,
            score=payload.score, complete_date=payload.complete_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------- #
# 인사발령 (appointment)
# --------------------------------------------------------------------------- #

@router.get("/appointments", response_model=list[AppointmentRow], tags=["Appointment"])
def get_appointments(
    emp_id: str | None = None,
    type: AppointmentType | None = None,
    date_from: str | None = Query(default=None, description="effective_date >= YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="effective_date <= YYYY-MM-DD"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return db.list_appointments(
        emp_id=emp_id, type=type, date_from=date_from, date_to=date_to, limit=limit,
    )


@router.post("/appointments", response_model=AppointmentRow, tags=["Appointment"])
def create_appointment(payload: AppointmentCreate) -> dict[str, Any]:
    """인사발령 기록 + 사원 상태/부서/직급 반영(원자적)."""
    try:
        return db.create_appointment(
            emp_id=payload.emp_id, type=payload.type, effective_date=payload.effective_date,
            to_dept=payload.to_dept, to_position=payload.to_position, note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
