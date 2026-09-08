"""Reset the shared database from a literal fixture at every replica boot.

Only calendar dates move. The offset is a whole number of weeks, anchored to
the latest attendance day, so weekdays, all gaps and clock times survive.
HR_HISTORY_START pins the first attendance date; it must keep its weekday.
Birth dates are facts, not history. Annual leave is rebuilt after moving dates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import db

DATASET_PATH = Path(__file__).resolve().parent / "dataset.json"
HISTORY_START_ENV = "HR_HISTORY_START"
TIME_COLUMNS = {
    "attendance": ("work_date",),
    "leave_request": ("start_date", "end_date", "applied_date", "decided_date"),
    "training_record": ("enroll_date", "complete_date"),
    "appointment": ("effective_date",),
    "employee": ("hire_date",),
}
Tables = dict[str, list[list[Any]]]


def load_dataset(path: Path | None = None) -> Tables:
    tables = json.loads((path or DATASET_PATH).read_text(encoding="utf-8"))
    if not isinstance(tables, dict) or set(tables) != set(db.TABLES):
        raise ValueError("dataset must contain exactly the nine HR tables")
    for table, rows in tables.items():
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise ValueError(f"dataset {table}: expected an array of rows")
    return tables


def _iso_date(value: str, label: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO date (YYYY-MM-DD)") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be an ISO date (YYYY-MM-DD)")
    return parsed


def _rebuild_balances(tables: Tables, columns: dict[str, list[str]], year: int) -> None:
    """Preserve original grants, not the leave allowance of a later promotion."""
    balance_columns = columns["leave_balance"]
    entitlements = {}
    for row in sorted(tables["leave_balance"], key=lambda row: row[balance_columns.index("year")]):
        balance = dict(zip(balance_columns, row))
        entitlements[balance["emp_id"]] = balance["entitled_days"]
    employees = {row[columns["employee"].index("emp_id")] for row in tables["employee"]}
    if set(entitlements) != employees:
        raise ValueError("dataset must provide a leave entitlement for every employee")

    used = {(emp_id, year): 0.0 for emp_id in employees}
    for row in tables["leave_request"]:
        request = dict(zip(columns["leave_request"], row))
        if request["emp_id"] not in employees:
            raise ValueError(f"leave request {request['id']} references an unknown employee")
        key = (request["emp_id"], _iso_date(request["start_date"], "start_date").year)
        used.setdefault(key, 0.0)
        if request["status"] == "승인":
            used[key] += request["days"]

    balances = []
    for (emp_id, balance_year), days in sorted(used.items()):
        entitled = entitlements[emp_id]
        balance = {
            "emp_id": emp_id, "year": balance_year, "entitled_days": entitled,
            "used_days": days, "remaining_days": entitled - days,
        }
        balances.append([balance[column] for column in balance_columns])
    tables["leave_balance"] = balances


def retime(tables: Tables, columns: dict[str, list[str]], *,
           today: date, start: date | None = None) -> Tables:
    """Return a copy shifted by one weekly offset, with fresh yearly balances.

    The pin describes the first attendance date, not the oldest hire record.
    Leave requests belong to their start year, matching leave approval in db.py.
    Pinned runs use the pinned latest-attendance year, not the wall-clock year.
    """
    for table, rows in tables.items():
        if any(len(row) != len(columns[table]) for row in rows):
            raise ValueError(f"dataset {table}: row does not match the schema")
    work_date = columns["attendance"].index("work_date")
    attendance_dates = [_iso_date(row[work_date], "attendance.work_date")
                        for row in tables["attendance"]]
    if not attendance_dates:
        raise ValueError("dataset must contain attendance to anchor the history")
    first, last = min(attendance_dates), max(attendance_dates)
    days = (start - first).days if start is not None else 7 * ((today - last).days // 7)
    if days % 7:
        raise ValueError(
            f"{HISTORY_START_ENV} must keep the weekday of {first.isoformat()} "
            "(a whole-week offset)"
        )
    shift = timedelta(days=days)
    moved = {}
    for table, rows in tables.items():
        indices = [columns[table].index(name) for name in TIME_COLUMNS.get(table, ())]
        moved_rows = []
        for original in rows:
            row = list(original)
            for index in indices:
                if row[index] is not None:
                    row[index] = (_iso_date(row[index], f"{table}.{columns[table][index]}")
                                  + shift).isoformat()
            moved_rows.append(row)
        moved[table] = moved_rows
    _rebuild_balances(moved, columns, (last + shift).year if start is not None else today.year)
    return moved


def seed(force: bool = False, *, path: Path | None = None,
         today: date | None = None) -> bool:
    """Always reset, atomically. ``force`` is accepted only for old callers."""
    tables = load_dataset(path)
    raw = os.environ.get(HISTORY_START_ENV, "").strip()
    start = _iso_date(raw, HISTORY_START_ENV) if raw else None
    db.init_db()
    with db.get_conn() as conn:
        columns = {table: [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
                   for table in db.TABLES}
        tables = retime(tables, columns, today=today or date.today(), start=start)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in db.TABLES:
                conn.execute(f"DELETE FROM {table}")
            marks = ", ".join("?" for _ in db.TABLES)
            conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({marks})", db.TABLES)
            for table in reversed(db.TABLES):
                names = ", ".join(f'"{column}"' for column in columns[table])
                marks = ", ".join("?" for _ in columns[table])
                conn.executemany(
                    f"INSERT INTO {table} ({names}) VALUES ({marks})", tables[table])
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="deprecated: seeding always resets the database")
    args = parser.parse_args([] if argv is None else argv)
    seed(force=args.force)
    print(f"[seed] reset mock HR database from {DATASET_PATH.name} at {db.get_db_path()}")
    print("[seed] rows: " + ", ".join(f"{key}={value}" for key, value in db.counts().items()))
    with db.get_conn() as conn:
        first, last = conn.execute(
            "SELECT MIN(work_date), MAX(work_date) FROM attendance").fetchone()
    print(f"[seed] attendance: {first} .. {last} (whole-week history shift)")


if __name__ == "__main__":
    main(sys.argv[1:])
