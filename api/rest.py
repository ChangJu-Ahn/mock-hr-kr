from typing import Any, Literal
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

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
    parent_dept_name: str | None = None
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
    from_dept_name: str | None = None
    to_dept_name: str | None = None
    from_position: str | None = None
    to_position: str | None = None
    from_position_name: str | None = None
    to_position_name: str | None = None
    note: str | None = None


class MasterInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DepartmentCreate(MasterInput):
    dept_code: str
    dept_name: str
    parent_dept_code: str | None = None
    manager_emp_id: str | None = None
    cost_center: str | None = None


class DepartmentUpdate(MasterInput):
    dept_name: str | None = None
    parent_dept_code: str | None = None
    manager_emp_id: str | None = None
    cost_center: str | None = None


class PositionCreate(MasterInput):
    position_code: str
    position_name: str
    level_no: int = Field(default=1, ge=1)
    min_leave_days: float = Field(default=15, ge=0)


class PositionUpdate(MasterInput):
    position_name: str | None = None
    level_no: int | None = Field(default=None, ge=1)
    min_leave_days: float | None = Field(default=None, ge=0)


class CourseCreate(MasterInput):
    course_code: str
    course_name: str
    category: str | None = None
    delivery: str | None = None
    hours: float | None = Field(default=None, ge=0)
    capacity: int | None = Field(default=None, ge=0)
    is_mandatory: bool = False


class CourseUpdate(MasterInput):
    course_name: str | None = None
    category: str | None = None
    delivery: str | None = None
    hours: float | None = Field(default=None, ge=0)
    capacity: int | None = Field(default=None, ge=0)
    is_mandatory: bool | None = None


class EmployeeCreate(MasterInput):
    emp_id: str | None = None
    name: str
    dept_code: str
    position_code: str
    employment_type: EmploymentType = "정규직"
    email: str | None = None
    phone: str | None = None
    hire_date: str | None = None
    birth_date: str | None = None
    manager_emp_id: str | None = None


class EmployeeUpdate(MasterInput):
    name: str | None = None
    dept_code: str | None = None
    position_code: str | None = None
    employment_type: EmploymentType | None = None
    status: EmployeeStatus | None = None
    email: str | None = None
    phone: str | None = None
    hire_date: str | None = None
    birth_date: str | None = None
    manager_emp_id: str | None = None


def _master_write(action: Callable[..., dict[str, Any]], /, *args, **kwargs) -> dict[str, Any]:
    try:
        return action(*args, **kwargs)
    except db.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except db.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
            "POST /api/departments",
            "PATCH|DELETE /api/departments/{dept_code}",
            "GET /api/positions",
            "GET /api/positions/{position_code}",
            "POST /api/positions",
            "PATCH|DELETE /api/positions/{position_code}",
            "GET /api/employees",
            "GET /api/employees/{emp_id}",
            "POST /api/employees",
            "PATCH|DELETE /api/employees/{emp_id}",
            "GET /api/courses",
            "GET /api/courses/{course_code}",
            "POST /api/courses",
            "PATCH|DELETE /api/courses/{course_code}",
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


@router.post("/departments", response_model=DepartmentRow, status_code=201, tags=["Department"])
def create_department(payload: DepartmentCreate):
    return _master_write(db.create_department, **payload.model_dump())


@router.patch("/departments/{dept_code}", response_model=DepartmentRow, tags=["Department"])
def update_department(dept_code: str, payload: DepartmentUpdate):
    return _master_write(db.update_department, dept_code, **payload.model_dump(exclude_unset=True))


@router.delete("/departments/{dept_code}", tags=["Department"])
def delete_department(dept_code: str):
    return _master_write(db.delete_department, dept_code)


@router.get("/positions", response_model=list[PositionRow], tags=["Position"])
def get_positions() -> list[dict[str, Any]]:
    return db.list_positions()


@router.get("/positions/{position_code}", response_model=PositionRow, tags=["Position"])
def get_position(position_code: str):
    row = db.get_position(position_code)
    if row is None:
        raise HTTPException(status_code=404, detail=f"position {position_code} not found")
    return row


@router.post("/positions", response_model=PositionRow, status_code=201, tags=["Position"])
def create_position(payload: PositionCreate):
    return _master_write(db.create_position, **payload.model_dump())


@router.patch("/positions/{position_code}", response_model=PositionRow, tags=["Position"])
def update_position(position_code: str, payload: PositionUpdate):
    return _master_write(db.update_position, position_code, **payload.model_dump(exclude_unset=True))


@router.delete("/positions/{position_code}", tags=["Position"])
def delete_position(position_code: str):
    return _master_write(db.delete_position, position_code)


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
    return _master_write(db.hire_employee, **payload.model_dump())


@router.patch("/employees/{emp_id}", response_model=EmployeeRow, tags=["Employee"])
def update_employee(emp_id: str, payload: EmployeeUpdate):
    return _master_write(db.update_employee, emp_id, **payload.model_dump(exclude_unset=True))


@router.delete("/employees/{emp_id}", tags=["Employee"])
def delete_employee(emp_id: str):
    return _master_write(db.delete_employee, emp_id)


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


@router.get("/courses/{course_code}", response_model=CourseRow, tags=["Course"])
def get_course(course_code: str):
    row = db.get_course(course_code)
    if row is None:
        raise HTTPException(status_code=404, detail=f"course {course_code} not found")
    return row


@router.post("/courses", response_model=CourseRow, status_code=201, tags=["Course"])
def create_course(payload: CourseCreate):
    return _master_write(db.create_course, **payload.model_dump())


@router.patch("/courses/{course_code}", response_model=CourseRow, tags=["Course"])
def update_course(course_code: str, payload: CourseUpdate):
    return _master_write(db.update_course, course_code, **payload.model_dump(exclude_unset=True))


@router.delete("/courses/{course_code}", tags=["Course"])
def delete_course(course_code: str):
    return _master_write(db.delete_course, course_code)


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
