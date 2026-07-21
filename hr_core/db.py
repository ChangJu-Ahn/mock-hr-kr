"""SQLite schema, connection management, and data-access foundation for the mock HR system.

Design notes
------------
* ONE shared SQLite file, path from ``HR_DB_PATH`` (default ``/data/hr.db``).
* WAL journal mode + ``busy_timeout`` make it safe for several processes in the
  same replica (init/api/mcp) to read and write concurrently.
* Every data-access function opens its own short-lived connection. This keeps
  callers simple and avoids sharing connection objects across threads/processes.
* Rows are returned as plain ``dict`` objects so both FastAPI (Pydantic/JSON)
  and the MCP server can serialize them trivially.

9-table schema: department, position, employee, course, training_record,
                leave_balance, leave_request, attendance, appointment.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any

DEFAULT_DB_PATH = "/data/hr.db"


def get_db_path() -> str:
    """Return the shared DB path (env ``HR_DB_PATH`` or the container default)."""
    return os.environ.get("HR_DB_PATH", DEFAULT_DB_PATH)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS department (
    dept_code        TEXT PRIMARY KEY,
    dept_name        TEXT NOT NULL,
    parent_dept_code TEXT,
    manager_emp_id   TEXT,
    cost_center      TEXT
);

CREATE TABLE IF NOT EXISTS position (
    position_code  TEXT PRIMARY KEY,
    position_name  TEXT NOT NULL,
    level_no       INTEGER NOT NULL DEFAULT 1,
    min_leave_days REAL NOT NULL DEFAULT 15
);

CREATE TABLE IF NOT EXISTS employee (
    emp_id          TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    dept_code       TEXT,
    position_code   TEXT,
    email           TEXT,
    phone           TEXT,
    employment_type TEXT,
    status          TEXT,
    hire_date       TEXT,
    birth_date      TEXT,
    manager_emp_id  TEXT
);

CREATE TABLE IF NOT EXISTS course (
    course_code  TEXT PRIMARY KEY,
    course_name  TEXT NOT NULL,
    category     TEXT,
    delivery     TEXT,
    hours        REAL,
    capacity     INTEGER,
    is_mandatory INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS training_record (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id        TEXT NOT NULL,
    course_code   TEXT NOT NULL,
    enroll_date   TEXT,
    complete_date TEXT,
    status        TEXT,
    score         REAL
);

CREATE TABLE IF NOT EXISTS leave_balance (
    emp_id         TEXT NOT NULL,
    year           INTEGER NOT NULL,
    entitled_days  REAL NOT NULL DEFAULT 0,
    used_days      REAL NOT NULL DEFAULT 0,
    remaining_days REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (emp_id, year)
);

CREATE TABLE IF NOT EXISTS leave_request (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id          TEXT NOT NULL,
    leave_type      TEXT,
    start_date      TEXT,
    end_date        TEXT,
    days            REAL,
    reason          TEXT,
    status          TEXT,
    approver_emp_id TEXT,
    applied_date    TEXT,
    decided_date    TEXT
);

CREATE TABLE IF NOT EXISTS attendance (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id         TEXT NOT NULL,
    work_date      TEXT NOT NULL,
    check_in       TEXT,
    check_out      TEXT,
    work_hours     REAL,
    overtime_hours REAL,
    status         TEXT,
    UNIQUE (emp_id, work_date)
);

CREATE TABLE IF NOT EXISTS appointment (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id         TEXT NOT NULL,
    effective_date TEXT,
    type           TEXT,
    from_dept      TEXT,
    to_dept        TEXT,
    from_position  TEXT,
    to_position    TEXT,
    note           TEXT
);

CREATE INDEX IF NOT EXISTS idx_emp_dept  ON employee (dept_code);
CREATE INDEX IF NOT EXISTS idx_emp_mgr   ON employee (manager_emp_id);
CREATE INDEX IF NOT EXISTS idx_tr_emp    ON training_record (emp_id);
CREATE INDEX IF NOT EXISTS idx_tr_course ON training_record (course_code);
CREATE INDEX IF NOT EXISTS idx_lr_emp    ON leave_request (emp_id);
CREATE INDEX IF NOT EXISTS idx_att_emp   ON attendance (emp_id, work_date);
CREATE INDEX IF NOT EXISTS idx_apt_emp   ON appointment (emp_id);
"""

TABLES = (
    "appointment",
    "attendance",
    "leave_request",
    "leave_balance",
    "training_record",
    "employee",
    "course",
    "position",
    "department",
)


# --------------------------------------------------------------------------- #
# Connection handling
# --------------------------------------------------------------------------- #

def connect() -> sqlite3.Connection:
    """Open a WAL-mode connection to the shared DB, creating its dir if needed."""
    path = get_db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # isolation_level=None -> autocommit; we manage transactions explicitly when needed.
    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


@contextmanager
def get_conn():
    """Context-managed connection that always closes."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _today() -> str:
    return date.today().isoformat()


def _current_year() -> int:
    return date.today().year


def init_db() -> None:
    """Create all tables/indexes if they do not exist."""
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)


def reset_db() -> None:
    """Drop all rows so seeding produces a clean, reproducible dataset."""
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        for table in TABLES:
            conn.execute(f"DELETE FROM {table};")
        try:
            conn.execute("DELETE FROM sqlite_sequence;")
        except sqlite3.OperationalError:
            pass


def counts() -> dict[str, int]:
    """Row counts per table (for the seed summary + dashboard)."""
    with get_conn() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _calc_work_hours(check_in: str | None, check_out: str | None,
                     lunch_hours: float = 1.0) -> tuple[float | None, float | None]:
    """Derive (work_hours, overtime_hours) from HH:MM check-in/out, minus lunch."""
    if not check_in or not check_out:
        return None, None

    def to_min(t: str) -> int:
        h, m = (t.split(":") + ["0"])[:2]
        return int(h) * 60 + int(m)

    mins = to_min(check_out) - to_min(check_in)
    if mins <= 0:
        return 0.0, 0.0
    work = round(max(0.0, mins / 60.0 - lunch_hours), 2)
    overtime = round(max(0.0, work - 8.0), 2)
    return work, overtime


def _derive_attendance_status(check_in: str | None, check_out: str | None,
                              status: str | None = None) -> str:
    """Standard (지각/조퇴/결근/정상) status when the caller does not force one."""
    if status:
        return status
    if not check_in:
        return "결근"
    if check_in > "09:00":
        return "지각"
    if check_out and check_out < "18:00":
        return "조퇴"
    return "정상"


def _leave_days(leave_type: str | None, start_date: str, end_date: str) -> float:
    """Inclusive calendar-day span; a 반차 (half day) counts as 0.5."""
    if leave_type == "반차":
        return 0.5
    s = date.fromisoformat(start_date)
    e = date.fromisoformat(end_date)
    return float((e - s).days + 1)


# --------------------------------------------------------------------------- #
# 부서 (department)
# --------------------------------------------------------------------------- #

_DEPT_SELECT = (
    "SELECT d.*, parent.dept_name AS parent_dept_name, m.name AS manager_name, "
    "  (SELECT COUNT(*) FROM employee e WHERE e.dept_code = d.dept_code AND e.status='재직') "
    "    AS headcount "
    "FROM department d "
    "LEFT JOIN department parent ON parent.dept_code = d.parent_dept_code "
    "LEFT JOIN employee m ON m.emp_id = d.manager_emp_id "
)


def list_departments(parent_dept_code=None):
    sql = _DEPT_SELECT + "WHERE 1=1"
    args: list[Any] = []
    if parent_dept_code is not None:
        sql += " AND d.parent_dept_code = ?"; args.append(parent_dept_code)
    sql += " ORDER BY d.dept_code"
    with get_conn() as conn:
        return _rows(conn.execute(sql, args))


def get_department(dept_code):
    with get_conn() as conn:
        row = conn.execute(_DEPT_SELECT + "WHERE d.dept_code = ?", (dept_code,)).fetchone()
        return dict(row) if row else None


def list_department_codes() -> list[str]:
    with get_conn() as conn:
        return [r[0] for r in conn.execute("SELECT dept_code FROM department ORDER BY dept_code")]


def get_org_tree() -> list[dict[str, Any]]:
    """Departments flattened in tree order, each row carrying a ``depth`` field."""
    rows = list_departments()
    by_parent: dict[Any, list[dict[str, Any]]] = {}
    for r in rows:
        by_parent.setdefault(r["parent_dept_code"], []).append(r)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def walk(parent, depth):
        for r in sorted(by_parent.get(parent, []), key=lambda x: x["dept_code"]):
            if r["dept_code"] in seen:
                continue
            seen.add(r["dept_code"])
            r2 = dict(r)
            r2["depth"] = depth
            out.append(r2)
            walk(r["dept_code"], depth + 1)

    walk(None, 0)
    for r in rows:  # any orphan whose parent is missing
        if r["dept_code"] not in seen:
            r2 = dict(r)
            r2["depth"] = 0
            out.append(r2)
    return out


# --------------------------------------------------------------------------- #
# 직급 (position)
# --------------------------------------------------------------------------- #

def list_positions():
    sql = (
        "SELECT p.*, "
        "  (SELECT COUNT(*) FROM employee e WHERE e.position_code = p.position_code "
        "     AND e.status='재직') AS headcount "
        "FROM position p ORDER BY p.level_no"
    )
    with get_conn() as conn:
        return _rows(conn.execute(sql))


def list_position_codes() -> list[str]:
    with get_conn() as conn:
        return [r[0] for r in conn.execute("SELECT position_code FROM position ORDER BY level_no")]


def get_position(position_code):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM position WHERE position_code = ?", (position_code,)).fetchone()
        return dict(row) if row else None


# --------------------------------------------------------------------------- #
# 사원 (employee)
# --------------------------------------------------------------------------- #

_EMP_SELECT = (
    "SELECT e.*, d.dept_name, p.position_name, p.level_no, m.name AS manager_name "
    "FROM employee e "
    "LEFT JOIN department d ON d.dept_code = e.dept_code "
    "LEFT JOIN position p ON p.position_code = e.position_code "
    "LEFT JOIN employee m ON m.emp_id = e.manager_emp_id "
)


def list_employees(dept_code=None, position_code=None, status=None, employment_type=None,
                   q=None, manager_emp_id=None, limit=200):
    sql = _EMP_SELECT + "WHERE 1=1"
    args: list[Any] = []
    if dept_code:
        sql += " AND e.dept_code = ?"; args.append(dept_code)
    if position_code:
        sql += " AND e.position_code = ?"; args.append(position_code)
    if status:
        sql += " AND e.status = ?"; args.append(status)
    if employment_type:
        sql += " AND e.employment_type = ?"; args.append(employment_type)
    if manager_emp_id:
        sql += " AND e.manager_emp_id = ?"; args.append(manager_emp_id)
    if q:
        sql += " AND (e.emp_id LIKE ? OR e.name LIKE ? OR e.email LIKE ?)"
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY e.emp_id LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        return _rows(conn.execute(sql, args))


def get_employee(emp_id):
    with get_conn() as conn:
        emp = conn.execute(_EMP_SELECT + "WHERE e.emp_id = ?", (emp_id,)).fetchone()
        if emp is None:
            return None
        year = _current_year()
        bal = conn.execute(
            "SELECT * FROM leave_balance WHERE emp_id = ? AND year = ?", (emp_id, year)
        ).fetchone()
        attendance = conn.execute(
            "SELECT * FROM attendance WHERE emp_id = ? ORDER BY work_date DESC LIMIT 10", (emp_id,)
        ).fetchall()
        training = conn.execute(
            "SELECT tr.*, c.course_name, c.category FROM training_record tr "
            "LEFT JOIN course c ON c.course_code = tr.course_code "
            "WHERE tr.emp_id = ? ORDER BY tr.id DESC", (emp_id,)
        ).fetchall()
        leaves = conn.execute(
            "SELECT * FROM leave_request WHERE emp_id = ? ORDER BY id DESC LIMIT 10", (emp_id,)
        ).fetchall()
        appts = conn.execute(
            _APT_SELECT + "WHERE ap.emp_id = ? ORDER BY ap.id", (emp_id,)
        ).fetchall()
    out = dict(emp)
    out["leave_balance"] = dict(bal) if bal else None
    out["attendance"] = [dict(a) for a in attendance]
    out["training"] = [dict(t) for t in training]
    out["leave_requests"] = [dict(x) for x in leaves]
    out["appointments"] = [dict(a) for a in appts]
    return out


def list_employee_ids(status=None) -> list[str]:
    sql = "SELECT emp_id FROM employee"
    args: list[Any] = []
    if status:
        sql += " WHERE status = ?"; args.append(status)
    sql += " ORDER BY emp_id"
    with get_conn() as conn:
        return [r[0] for r in conn.execute(sql, args)]


def headcount_by_dept():
    sql = (
        "SELECT d.dept_code, d.dept_name, "
        "  COUNT(e.emp_id) AS headcount "
        "FROM department d "
        "LEFT JOIN employee e ON e.dept_code = d.dept_code AND e.status='재직' "
        "GROUP BY d.dept_code, d.dept_name ORDER BY headcount DESC, d.dept_code"
    )
    with get_conn() as conn:
        return _rows(conn.execute(sql))


def _next_emp_id(conn) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(emp_id, 2) AS INTEGER)) FROM employee "
        "WHERE emp_id GLOB 'E[0-9][0-9][0-9][0-9]*'"
    ).fetchone()
    n = row[0] or 0
    return f"E{n + 1:04d}"


def hire_employee(name, dept_code, position_code, employment_type="정규직", email=None,
                  phone=None, hire_date=None, birth_date=None, manager_emp_id=None,
                  emp_id=None, year=None):
    """Create an employee (status 재직), grant the year's leave, log a 입사 appointment."""
    if not name:
        raise ValueError("name is required")
    hire_date = hire_date or _today()
    year = year or _current_year()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            dept = conn.execute("SELECT * FROM department WHERE dept_code = ?", (dept_code,)).fetchone()
            if dept is None:
                raise ValueError(f"unknown dept_code: {dept_code!r}")
            pos = conn.execute("SELECT * FROM position WHERE position_code = ?", (position_code,)).fetchone()
            if pos is None:
                raise ValueError(f"unknown position_code: {position_code!r}")
            if emp_id is None:
                emp_id = _next_emp_id(conn)
            conn.execute(
                "INSERT INTO employee (emp_id, name, dept_code, position_code, email, phone,"
                " employment_type, status, hire_date, birth_date, manager_emp_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (emp_id, name, dept_code, position_code, email, phone, employment_type,
                 "재직", hire_date, birth_date, manager_emp_id),
            )
            entitled = float(pos["min_leave_days"] or 15)
            _upsert_leave_balance(conn, emp_id, year, entitled_delta=entitled)
            conn.execute(
                "INSERT INTO appointment (emp_id, effective_date, type, from_dept, to_dept,"
                " from_position, to_position, note) VALUES (?,?,?,?,?,?,?,?)",
                (emp_id, hire_date, "입사", None, dept_code, None, position_code, "신규 입사"),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return get_employee(emp_id)


# --------------------------------------------------------------------------- #
# 교육과정 (course) + 교육이수 (training_record)
# --------------------------------------------------------------------------- #

def list_courses(category=None, is_mandatory=None, q=None):
    sql = "SELECT * FROM course WHERE 1=1"
    args: list[Any] = []
    if category:
        sql += " AND category = ?"; args.append(category)
    if is_mandatory is not None:
        sql += " AND is_mandatory = ?"; args.append(1 if is_mandatory else 0)
    if q:
        sql += " AND (course_code LIKE ? OR course_name LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY course_code"
    with get_conn() as conn:
        return _rows(conn.execute(sql, args))


def list_course_codes() -> list[str]:
    with get_conn() as conn:
        return [r[0] for r in conn.execute("SELECT course_code FROM course ORDER BY course_code")]


def get_course(course_code):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM course WHERE course_code = ?", (course_code,)).fetchone()
        return dict(row) if row else None


def list_training_records(emp_id=None, course_code=None, status=None, category=None,
                          date_from=None, date_to=None, limit=100):
    sql = (
        "SELECT tr.*, e.name AS emp_name, e.dept_code, c.course_name, c.category, c.is_mandatory "
        "FROM training_record tr "
        "LEFT JOIN employee e ON e.emp_id = tr.emp_id "
        "LEFT JOIN course c ON c.course_code = tr.course_code WHERE 1=1"
    )
    args: list[Any] = []
    if emp_id:
        sql += " AND tr.emp_id = ?"; args.append(emp_id)
    if course_code:
        sql += " AND tr.course_code = ?"; args.append(course_code)
    if status:
        sql += " AND tr.status = ?"; args.append(status)
    if category:
        sql += " AND c.category = ?"; args.append(category)
    if date_from:
        sql += " AND tr.enroll_date >= ?"; args.append(date_from)
    if date_to:
        sql += " AND tr.enroll_date <= ?"; args.append(date_to)
    sql += " ORDER BY tr.id DESC LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        return _rows(conn.execute(sql, args))


def _training_row(conn, record_id):
    return dict(conn.execute(
        "SELECT tr.*, e.name AS emp_name, c.course_name, c.category "
        "FROM training_record tr "
        "LEFT JOIN employee e ON e.emp_id = tr.emp_id "
        "LEFT JOIN course c ON c.course_code = tr.course_code WHERE tr.id = ?",
        (record_id,),
    ).fetchone())


def enroll_training(emp_id, course_code, enroll_date=None, status="수강중"):
    """Enroll an employee on a course (default status 수강중)."""
    enroll_date = enroll_date or _today()
    with get_conn() as conn:
        emp = conn.execute("SELECT 1 FROM employee WHERE emp_id = ?", (emp_id,)).fetchone()
        if emp is None:
            raise ValueError(f"unknown emp_id: {emp_id!r}")
        course = conn.execute("SELECT 1 FROM course WHERE course_code = ?", (course_code,)).fetchone()
        if course is None:
            raise ValueError(f"unknown course_code: {course_code!r}")
        rid = conn.execute(
            "INSERT INTO training_record (emp_id, course_code, enroll_date, status)"
            " VALUES (?,?,?,?)",
            (emp_id, course_code, enroll_date, status),
        ).lastrowid
        return _training_row(conn, rid)


def complete_training(record_id, status="이수", score=None, complete_date=None):
    """Mark a training record 이수/미이수 (or back to 수강중)."""
    status = str(status)
    if status not in ("이수", "미이수", "수강중"):
        raise ValueError(f"status must be 이수/미이수/수강중, got {status!r}")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM training_record WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown training record id: {record_id!r}")
        cdate = complete_date if complete_date is not None else (
            _today() if status in ("이수", "미이수") else None
        )
        conn.execute(
            "UPDATE training_record SET status = ?, score = ?, complete_date = ? WHERE id = ?",
            (status, score, cdate, record_id),
        )
        return _training_row(conn, record_id)


def training_completion_rate(mandatory_only=True) -> dict[str, Any]:
    where = "WHERE c.is_mandatory = 1" if mandatory_only else ""
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM training_record tr "
            f"JOIN course c ON c.course_code = tr.course_code {where}"
        ).fetchone()[0]
        done = conn.execute(
            f"SELECT COUNT(*) FROM training_record tr "
            f"JOIN course c ON c.course_code = tr.course_code "
            f"{where}{' AND' if where else 'WHERE'} tr.status = '이수'"
        ).fetchone()[0]
    rate = round(done / total * 100, 1) if total else 0.0
    return {"total": total, "completed": done, "rate": rate}


# --------------------------------------------------------------------------- #
# 연차현황 (leave_balance)
# --------------------------------------------------------------------------- #

def _upsert_leave_balance(conn, emp_id, year, entitled_delta=0.0, used_delta=0.0):
    conn.execute(
        "INSERT INTO leave_balance (emp_id, year, entitled_days, used_days, remaining_days)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(emp_id, year) DO UPDATE SET"
        "   entitled_days  = entitled_days  + excluded.entitled_days,"
        "   used_days      = used_days      + excluded.used_days,"
        "   remaining_days = remaining_days + excluded.entitled_days - excluded.used_days",
        (emp_id, year, entitled_delta, used_delta, entitled_delta - used_delta),
    )


def get_leave_balance(emp_id, year=None):
    year = year or _current_year()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT lb.*, e.name AS emp_name FROM leave_balance lb "
            "LEFT JOIN employee e ON e.emp_id = lb.emp_id "
            "WHERE lb.emp_id = ? AND lb.year = ?", (emp_id, year)
        ).fetchone()
        return dict(row) if row else None


def list_leave_balances(year=None, dept_code=None):
    year = year or _current_year()
    sql = (
        "SELECT lb.*, e.name AS emp_name, e.dept_code, d.dept_name FROM leave_balance lb "
        "LEFT JOIN employee e ON e.emp_id = lb.emp_id "
        "LEFT JOIN department d ON d.dept_code = e.dept_code "
        "WHERE lb.year = ?"
    )
    args: list[Any] = [year]
    if dept_code:
        sql += " AND e.dept_code = ?"; args.append(dept_code)
    sql += " ORDER BY lb.emp_id"
    with get_conn() as conn:
        return _rows(conn.execute(sql, args))


def grant_annual_leave(emp_id, year=None, days=15.0):
    """Grant (accrue) annual-leave entitlement — mirrors a stock receipt."""
    days = float(days)
    if days < 0:
        raise ValueError(f"days must be >= 0, got {days}")
    year = year or _current_year()
    with get_conn() as conn:
        emp = conn.execute("SELECT 1 FROM employee WHERE emp_id = ?", (emp_id,)).fetchone()
        if emp is None:
            raise ValueError(f"unknown emp_id: {emp_id!r}")
        _upsert_leave_balance(conn, emp_id, year, entitled_delta=days)
    return get_leave_balance(emp_id, year)


# --------------------------------------------------------------------------- #
# 휴가신청 (leave_request)
# --------------------------------------------------------------------------- #

_LR_SELECT = (
    "SELECT lr.*, e.name AS emp_name, e.dept_code, d.dept_name, a.name AS approver_name "
    "FROM leave_request lr "
    "LEFT JOIN employee e ON e.emp_id = lr.emp_id "
    "LEFT JOIN department d ON d.dept_code = e.dept_code "
    "LEFT JOIN employee a ON a.emp_id = lr.approver_emp_id "
)


def _lr_row(conn, request_id):
    return dict(conn.execute(_LR_SELECT + "WHERE lr.id = ?", (request_id,)).fetchone())


def list_leave_requests(emp_id=None, status=None, leave_type=None,
                        date_from=None, date_to=None, limit=100):
    sql = _LR_SELECT + "WHERE 1=1"
    args: list[Any] = []
    if emp_id:
        sql += " AND lr.emp_id = ?"; args.append(emp_id)
    if status:
        sql += " AND lr.status = ?"; args.append(status)
    if leave_type:
        sql += " AND lr.leave_type = ?"; args.append(leave_type)
    if date_from:
        sql += " AND lr.start_date >= ?"; args.append(date_from)
    if date_to:
        sql += " AND lr.start_date <= ?"; args.append(date_to)
    sql += " ORDER BY lr.id DESC LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        return _rows(conn.execute(sql, args))


def submit_leave_request(emp_id, leave_type, start_date, end_date=None, reason=None,
                         days=None, applied_date=None):
    """File a leave request (status 신청). Records a non-blocking balance warning."""
    end_date = end_date or start_date
    if days is None:
        days = _leave_days(leave_type, start_date, end_date)
    days = float(days)
    if days <= 0:
        raise ValueError(f"days must be > 0, got {days}")
    applied_date = applied_date or _today()
    year = int(start_date[:4])
    with get_conn() as conn:
        emp = conn.execute("SELECT 1 FROM employee WHERE emp_id = ?", (emp_id,)).fetchone()
        if emp is None:
            raise ValueError(f"unknown emp_id: {emp_id!r}")
        rid = conn.execute(
            "INSERT INTO leave_request (emp_id, leave_type, start_date, end_date, days,"
            " reason, status, applied_date) VALUES (?,?,?,?,?,?,?,?)",
            (emp_id, leave_type, start_date, end_date, days, reason, "신청", applied_date),
        ).lastrowid
        bal = conn.execute(
            "SELECT remaining_days FROM leave_balance WHERE emp_id = ? AND year = ?",
            (emp_id, year),
        ).fetchone()
        remaining = bal["remaining_days"] if bal else 0
        out = _lr_row(conn, rid)
    out["balance_warning"] = None if remaining >= days else (
        f"insufficient balance: request {days}d, remaining {remaining}d"
    )
    return out


def decide_leave_request(request_id, decision, approver_emp_id=None, decided_date=None):
    """Approve/reject/cancel a pending request. 승인 decrements the balance (blocks if short)."""
    decision = str(decision)
    if decision not in ("승인", "반려", "취소"):
        raise ValueError(f"decision must be 승인/반려/취소, got {decision!r}")
    decided_date = decided_date or _today()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            lr = conn.execute("SELECT * FROM leave_request WHERE id = ?", (request_id,)).fetchone()
            if lr is None:
                raise ValueError(f"unknown leave request id: {request_id!r}")
            if lr["status"] != "신청":
                raise ValueError(f"request {request_id} already {lr['status']}, cannot decide")
            if decision == "승인":
                year = int(str(lr["start_date"])[:4])
                bal = conn.execute(
                    "SELECT remaining_days FROM leave_balance WHERE emp_id = ? AND year = ?",
                    (lr["emp_id"], year),
                ).fetchone()
                remaining = bal["remaining_days"] if bal else 0
                days = lr["days"] or 0
                if remaining < days:
                    raise ValueError(
                        f"insufficient leave balance for {lr['emp_id']}: "
                        f"need {days}, have {remaining}"
                    )
                _upsert_leave_balance(conn, lr["emp_id"], year, used_delta=days)
            conn.execute(
                "UPDATE leave_request SET status = ?, approver_emp_id = ?, decided_date = ?"
                " WHERE id = ?",
                (decision, approver_emp_id, decided_date, request_id),
            )
            out = _lr_row(conn, request_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    out["leave_balance"] = get_leave_balance(out["emp_id"], int(str(out["start_date"])[:4]))
    return out


# --------------------------------------------------------------------------- #
# 근태 (attendance)
# --------------------------------------------------------------------------- #

_ATT_SELECT = (
    "SELECT at.*, e.name AS emp_name, e.dept_code, d.dept_name FROM attendance at "
    "LEFT JOIN employee e ON e.emp_id = at.emp_id "
    "LEFT JOIN department d ON d.dept_code = e.dept_code "
)


def log_attendance(emp_id, work_date, check_in=None, check_out=None, status=None):
    """Record (upsert) a day's attendance; derives work/overtime hours and status."""
    work_hours, overtime = _calc_work_hours(check_in, check_out)
    st = _derive_attendance_status(check_in, check_out, status)
    with get_conn() as conn:
        emp = conn.execute("SELECT 1 FROM employee WHERE emp_id = ?", (emp_id,)).fetchone()
        if emp is None:
            raise ValueError(f"unknown emp_id: {emp_id!r}")
        conn.execute(
            "INSERT INTO attendance (emp_id, work_date, check_in, check_out, work_hours,"
            " overtime_hours, status) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(emp_id, work_date) DO UPDATE SET"
            "   check_in = excluded.check_in, check_out = excluded.check_out,"
            "   work_hours = excluded.work_hours, overtime_hours = excluded.overtime_hours,"
            "   status = excluded.status",
            (emp_id, work_date, check_in, check_out, work_hours, overtime, st),
        )
        row = conn.execute(
            _ATT_SELECT + "WHERE at.emp_id = ? AND at.work_date = ?", (emp_id, work_date)
        ).fetchone()
        return dict(row)


def list_attendance(emp_id=None, work_date=None, status=None,
                    date_from=None, date_to=None, limit=100):
    sql = _ATT_SELECT + "WHERE 1=1"
    args: list[Any] = []
    if emp_id:
        sql += " AND at.emp_id = ?"; args.append(emp_id)
    if work_date:
        sql += " AND at.work_date = ?"; args.append(work_date)
    if status:
        sql += " AND at.status = ?"; args.append(status)
    if date_from:
        sql += " AND at.work_date >= ?"; args.append(date_from)
    if date_to:
        sql += " AND at.work_date <= ?"; args.append(date_to)
    sql += " ORDER BY at.work_date DESC, at.emp_id LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        return _rows(conn.execute(sql, args))


def get_attendance_summary(month=None, dept_code=None, work_date=None):
    """Roll up attendance by status (records, employees, hours, overtime)."""
    sql = (
        "SELECT at.status, COUNT(*) AS records, COUNT(DISTINCT at.emp_id) AS employees, "
        "  COALESCE(SUM(at.work_hours),0) AS total_hours, "
        "  COALESCE(SUM(at.overtime_hours),0) AS total_overtime "
        "FROM attendance at "
        "LEFT JOIN employee e ON e.emp_id = at.emp_id WHERE 1=1"
    )
    args: list[Any] = []
    if work_date:
        sql += " AND at.work_date = ?"; args.append(work_date)
    if month:
        sql += " AND substr(at.work_date, 1, 7) = ?"; args.append(month)
    if dept_code:
        sql += " AND e.dept_code = ?"; args.append(dept_code)
    sql += " GROUP BY at.status ORDER BY records DESC"
    with get_conn() as conn:
        return _rows(conn.execute(sql, args))


def latest_work_date():
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(work_date) FROM attendance").fetchone()
        return row[0] if row else None


# --------------------------------------------------------------------------- #
# 인사발령 (appointment)
# --------------------------------------------------------------------------- #

_APT_SELECT = (
    "SELECT ap.*, e.name AS emp_name, "
    "fd.dept_name AS from_dept_name, td.dept_name AS to_dept_name, "
    "fp.position_name AS from_position_name, tp.position_name AS to_position_name "
    "FROM appointment ap "
    "LEFT JOIN employee e ON e.emp_id = ap.emp_id "
    "LEFT JOIN department fd ON fd.dept_code = ap.from_dept "
    "LEFT JOIN department td ON td.dept_code = ap.to_dept "
    "LEFT JOIN position fp ON fp.position_code = ap.from_position "
    "LEFT JOIN position tp ON tp.position_code = ap.to_position "
)

_APT_TYPES = ("입사", "승진", "부서이동", "휴직", "복직", "퇴직")


def list_appointments(emp_id=None, type=None, date_from=None, date_to=None, limit=100):
    sql = _APT_SELECT + "WHERE 1=1"
    args: list[Any] = []
    if emp_id:
        sql += " AND ap.emp_id = ?"; args.append(emp_id)
    if type:
        sql += " AND ap.type = ?"; args.append(type)
    if date_from:
        sql += " AND ap.effective_date >= ?"; args.append(date_from)
    if date_to:
        sql += " AND ap.effective_date <= ?"; args.append(date_to)
    sql += " ORDER BY ap.id DESC LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        return _rows(conn.execute(sql, args))


def create_appointment(emp_id, type, effective_date=None, to_dept=None, to_position=None, note=None):
    """Record a personnel action and apply its effect to the employee row."""
    type = str(type)
    if type not in _APT_TYPES:
        raise ValueError(f"type must be one of {_APT_TYPES}, got {type!r}")
    effective_date = effective_date or _today()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            emp = conn.execute("SELECT * FROM employee WHERE emp_id = ?", (emp_id,)).fetchone()
            if emp is None:
                raise ValueError(f"unknown emp_id: {emp_id!r}")
            from_dept = emp["dept_code"]
            from_position = emp["position_code"]

            if type == "승진":
                if not to_position:
                    raise ValueError("승진 requires to_position")
                conn.execute("UPDATE employee SET position_code = ? WHERE emp_id = ?", (to_position, emp_id))
            elif type == "부서이동":
                if not to_dept:
                    raise ValueError("부서이동 requires to_dept")
                conn.execute("UPDATE employee SET dept_code = ? WHERE emp_id = ?", (to_dept, emp_id))
            elif type == "휴직":
                conn.execute("UPDATE employee SET status = '휴직' WHERE emp_id = ?", (emp_id,))
            elif type == "복직":
                conn.execute("UPDATE employee SET status = '재직' WHERE emp_id = ?", (emp_id,))
            elif type == "퇴직":
                conn.execute("UPDATE employee SET status = '퇴직' WHERE emp_id = ?", (emp_id,))

            new_id = conn.execute(
                "INSERT INTO appointment (emp_id, effective_date, type, from_dept, to_dept,"
                " from_position, to_position, note) VALUES (?,?,?,?,?,?,?,?)",
                (emp_id, effective_date, type, from_dept, to_dept, from_position, to_position, note),
            ).lastrowid
            row = conn.execute(_APT_SELECT + "WHERE ap.id = ?", (new_id,)).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return dict(row)


# --------------------------------------------------------------------------- #
# Dashboard roll-up
# --------------------------------------------------------------------------- #

def get_dashboard_summary() -> dict[str, Any]:
    with get_conn() as conn:
        yr = conn.execute("SELECT MAX(year) FROM leave_balance").fetchone()[0]
        year = yr or _current_year()
        emp_total = conn.execute("SELECT COUNT(*) FROM employee").fetchone()[0]
        employees_by_status = _rows(conn.execute(
            "SELECT status, COUNT(*) AS n FROM employee GROUP BY status ORDER BY n DESC"))
        active_total = conn.execute(
            "SELECT COUNT(*) FROM employee WHERE status='재직'").fetchone()[0]
        dept_total = conn.execute("SELECT COUNT(*) FROM department").fetchone()[0]
        employees_by_position = _rows(conn.execute(
            "SELECT p.position_name, p.level_no, COUNT(e.emp_id) AS n "
            "FROM position p LEFT JOIN employee e ON e.position_code = p.position_code "
            "  AND e.status='재직' "
            "GROUP BY p.position_code, p.position_name, p.level_no ORDER BY p.level_no DESC"))
        employees_by_type = _rows(conn.execute(
            "SELECT employment_type, COUNT(*) AS n FROM employee WHERE status='재직' "
            "GROUP BY employment_type ORDER BY n DESC"))
        pending_leave = conn.execute(
            "SELECT COUNT(*) FROM leave_request WHERE status='신청'").fetchone()[0]
        leave_totals = conn.execute(
            "SELECT COALESCE(SUM(entitled_days),0) AS entitled, COALESCE(SUM(used_days),0) AS used "
            "FROM leave_balance WHERE year = ?", (year,)).fetchone()
        recent_appointments = _rows(conn.execute(
            _APT_SELECT + "ORDER BY ap.id DESC LIMIT 8"))
        recent_leaves = _rows(conn.execute(
            _LR_SELECT + "ORDER BY lr.id DESC LIMIT 8"))
    last_day = latest_work_date()
    attendance_today = get_attendance_summary(work_date=last_day) if last_day else []
    headcounts = headcount_by_dept()
    entitled = leave_totals["entitled"] or 0
    used = leave_totals["used"] or 0
    usage_pct = round(used / entitled * 100, 1) if entitled else 0.0
    training = training_completion_rate(mandatory_only=True)
    return {
        "emp_total": emp_total,
        "active_total": active_total,
        "employees_by_status": employees_by_status,
        "dept_total": dept_total,
        "headcount_by_dept": headcounts,
        "employees_by_position": employees_by_position,
        "employees_by_type": employees_by_type,
        "attendance_date": last_day,
        "attendance_today": attendance_today,
        "pending_leave": pending_leave,
        "leave_entitled": entitled,
        "leave_used": used,
        "leave_usage_pct": usage_pct,
        "training_mandatory": training,
        "recent_appointments": recent_appointments,
        "recent_leaves": recent_leaves,
    }
