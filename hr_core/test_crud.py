"""Master mutations protect every Python-managed reference without cascades."""

import os
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from hr_core import db


class MasterDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {"HR_DB_PATH": str(Path(temporary.name) / "crud.db")})
        environment.start()
        self.addCleanup(environment.stop)
        self._minimal()

    def _minimal(self):
        db.reset_db()
        with db.get_conn() as conn:
            conn.executemany("INSERT INTO department (dept_code,dept_name,parent_dept_code) VALUES (?,?,?)",
                             [("D0", "Root", None), ("D1", "Child", "D0")])
            conn.execute("INSERT INTO position VALUES ('P1','Staff',1,15)")
            conn.execute("INSERT INTO course (course_code,course_name) VALUES ('C1','Course')")
            conn.execute("INSERT INTO employee (emp_id,name,dept_code,position_code,status) "
                         "VALUES ('EBASE','Base','D1','P1','재직')")

    def _target(self, entity):
        if entity == "department":
            return db.create_department("DDEL", "Unused")["dept_code"]
        if entity == "position":
            return db.create_position("PDEL", "Unused")["position_code"]
        if entity == "course":
            return db.create_course("CDEL", "Unused")["course_code"]
        with db.get_conn() as conn:
            conn.execute("INSERT INTO employee (emp_id,name) VALUES ('EDEL','Reference-free')")
        return "EDEL"

    def test_every_incoming_reference_is_named_and_blocks_deletion(self):
        cases = (
            ("department", "employee", {"emp_id": "EREF", "name": "Retired", "status": "퇴직",
                                       "dept_code": "DDEL"}, "employees"),
            ("department", "department", {"dept_code": "DREF", "dept_name": "Child",
                                         "parent_dept_code": "DDEL"}, "child departments"),
            ("department", "appointment", {"emp_id": "EBASE", "from_dept": "DDEL"}, "appointments"),
            ("department", "appointment", {"emp_id": "EBASE", "to_dept": "DDEL"}, "appointments"),
            ("position", "employee", {"emp_id": "EREF", "name": "Retired", "status": "퇴직",
                                     "position_code": "PDEL"}, "employees"),
            ("position", "appointment", {"emp_id": "EBASE", "from_position": "PDEL"}, "appointments"),
            ("position", "appointment", {"emp_id": "EBASE", "to_position": "PDEL"}, "appointments"),
            ("course", "training_record", {"emp_id": "EBASE", "course_code": "CDEL"}, "training records"),
            ("employee", "employee", {"emp_id": "EREF", "name": "Report", "manager_emp_id": "EDEL"},
             "reporting employees"),
            ("employee", "department", {"dept_code": "DREF", "dept_name": "Managed",
                                       "manager_emp_id": "EDEL"}, "managed departments"),
            ("employee", "training_record", {"emp_id": "EDEL", "course_code": "C1"}, "training records"),
            ("employee", "leave_balance", {"emp_id": "EDEL", "year": 2026}, "leave balances"),
            ("employee", "leave_request", {"emp_id": "EDEL"}, "leave requests (employee)"),
            ("employee", "leave_request", {"emp_id": "EBASE", "approver_emp_id": "EDEL"},
             "leave requests (approver)"),
            ("employee", "attendance", {"emp_id": "EDEL", "work_date": "2026-07-06"}, "attendance records"),
            ("employee", "appointment", {"emp_id": "EDEL"}, "appointments"),
        )
        for entity, table, values, label in cases:
            with self.subTest(entity=entity, table=table, columns=tuple(values)):
                self._minimal()
                code = self._target(entity)
                with db.get_conn() as conn:
                    names = ", ".join(values)
                    marks = ", ".join("?" for _ in values)
                    conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})",
                                 tuple(values.values()))
                before = db.counts()
                message = f"cannot delete {entity} {code}: still referenced by 1 {label}"
                with self.assertRaisesRegex(db.ConflictError, re.escape(message)):
                    getattr(db, f"delete_{entity}")(code)
                self.assertEqual(db.counts(), before)

    def test_unreferenced_records_can_be_deleted_for_every_master(self):
        for entity in ("employee", "department", "position", "course"):
            code = self._target(entity)
            self.assertTrue(getattr(db, f"delete_{entity}")(code)["deleted"])
            self.assertIsNone(getattr(db, f"get_{entity}")(code))

    def test_appointment_pointing_both_ways_is_counted_once(self):
        db.create_department("DDEL", "Unused")
        with db.get_conn() as conn:
            conn.execute("INSERT INTO appointment (emp_id,from_dept,to_dept) VALUES ('EBASE','DDEL','DDEL')")
        with self.assertRaisesRegex(db.ConflictError, "1 appointments"):
            db.delete_department("DDEL")

    def test_guard_lists_all_blockers_and_never_cascades(self):
        employee = db.hire_employee("Hired", "D1", "P1", year=2026)
        db.log_attendance(employee["emp_id"], "2026-07-06")
        before = db.counts()
        with self.assertRaises(db.ConflictError) as blocked:
            db.delete_employee(employee["emp_id"])
        for label in ("1 leave balances", "1 attendance records", "1 appointments"):
            self.assertIn(label, str(blocked.exception))
        self.assertEqual(db.counts(), before)

    def test_keys_and_unknown_fields_are_rejected_in_direct_db_updates(self):
        for entity, code, key in (("department", "D1", "dept_code"), ("position", "P1", "position_code"),
                                  ("course", "C1", "course_code"), ("employee", "EBASE", "emp_id")):
            with self.subTest(entity=entity):
                with self.assertRaisesRegex(ValueError, "immutable"):
                    getattr(db, f"update_{entity}")(code, **{key: "OTHER"})
                with self.assertRaisesRegex(ValueError, "unknown"):
                    getattr(db, f"update_{entity}")(code, typo="ignored")
                with self.assertRaisesRegex(ValueError, "at least one"):
                    getattr(db, f"update_{entity}")(code)
                self.assertIsNotNone(getattr(db, f"get_{entity}")(code))

    def test_missing_updates_and_deletes_never_upsert(self):
        for entity, field in (("department", "dept_name"), ("position", "position_name"),
                              ("course", "course_name"), ("employee", "name")):
            with self.subTest(entity=entity):
                with self.assertRaises(db.NotFoundError):
                    getattr(db, f"update_{entity}")("MISSING", **{field: "Name"})
                with self.assertRaises(db.NotFoundError):
                    getattr(db, f"delete_{entity}")("MISSING")

    def test_duplicate_keys_are_conflicts_not_sqlite_errors(self):
        calls = (
            lambda: db.create_department("D1", "Duplicate"),
            lambda: db.create_position("P1", "Duplicate"),
            lambda: db.create_course("C1", "Duplicate"),
            lambda: db.hire_employee("Duplicate", "D1", "P1", emp_id="EBASE"),
        )
        for call in calls:
            with self.assertRaises(db.ConflictError):
                call()

    def test_master_keys_are_url_addressable_and_do_not_shadow_the_tree_route(self):
        for code in ("D/CHILD", "D?x=1", "D#fragment", "D\\CHILD", "..", "has space", "D" * 65):
            for create in (db.create_department, db.create_position, db.create_course):
                with self.subTest(code=code, action=create.__name__), self.assertRaises(ValueError):
                    create(code, "Unreachable")
            with self.assertRaises(ValueError):
                db.hire_employee("Unreachable", "D1", "P1", emp_id=code)
        with self.assertRaisesRegex(ValueError, "reserved"):
            db.create_department("tree", "Shadowed by GET /departments/tree")

    def test_partial_updates_and_explicit_null_clearing(self):
        db.update_employee("EBASE", email="mail@mock-hr.example", phone="123")
        row = db.update_employee("EBASE", email=None)
        self.assertIsNone(row["email"])
        self.assertEqual(row["phone"], "123")
        db.update_department("D1", parent_dept_code=None, manager_emp_id="EBASE", cost_center="CC")
        row = db.update_department("D1", manager_emp_id=None, cost_center=None)
        self.assertIsNone(row["manager_emp_id"])
        self.assertIsNone(row["cost_center"])
        self.assertIsNone(row["parent_dept_code"])
        row = db.update_course("C1", hours=None, capacity=None, category=None, delivery=None)
        self.assertIsNone(row["hours"])

    def test_required_names_references_and_dates_cannot_be_blank(self):
        for entity, code, field in (("department", "D1", "dept_name"), ("position", "P1", "position_name"),
                                    ("course", "C1", "course_name"), ("employee", "EBASE", "name")):
            for value in ("", "   ", None):
                with self.subTest(entity=entity, value=value), self.assertRaises(ValueError):
                    getattr(db, f"update_{entity}")(code, **{field: value})
        for field in ("dept_code", "position_code", "hire_date"):
            with self.assertRaises(ValueError):
                db.update_employee("EBASE", **{field: None})
        for value in ("20260706", "2026-02-30", "2026-07-06T00:00:00"):
            with self.assertRaises(ValueError):
                db.update_employee("EBASE", hire_date=value)

    def test_numeric_inputs_must_be_finite_nonnegative_and_integral_where_needed(self):
        for value in (-1, float("inf"), float("-inf"), float("nan"), True, "many"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    db.update_position("P1", min_leave_days=value)
                with self.assertRaises(ValueError):
                    db.update_course("C1", hours=value)
        for value in (0, 1.5):
            with self.assertRaises(ValueError):
                db.update_position("P1", level_no=value)
        with self.assertRaises(ValueError):
            db.update_course("C1", capacity=1.5)
        for value in (None, 2, "false"):
            with self.assertRaises(ValueError):
                db.update_course("C1", is_mandatory=value)
        for value in (2**64, 10**1000):
            with self.assertRaises(ValueError):
                db.update_course("C1", capacity=value)

    def test_zero_leave_allowance_is_not_replaced_with_fifteen(self):
        db.create_position("PZERO", "No allowance", min_leave_days=0)
        employee = db.hire_employee("Zero", "D1", "PZERO", year=2026)
        self.assertEqual(db.get_leave_balance(employee["emp_id"], 2026)["entitled_days"], 0)

    def test_invalid_references_and_cycles_roll_back_the_whole_update(self):
        for field in ("parent_dept_code", "manager_emp_id"):
            with self.assertRaises(ValueError):
                db.update_department("D1", dept_name="Should not change", **{field: "MISSING"})
            self.assertEqual(db.get_department("D1")["dept_name"], "Child")
        with self.assertRaisesRegex(ValueError, "cycle"):
            db.update_department("D0", parent_dept_code="D1")
        with self.assertRaisesRegex(ValueError, "cycle"):
            db.update_department("D1", parent_dept_code="D1")
        employee = db.hire_employee("Report", "D1", "P1", manager_emp_id="EBASE")
        with self.assertRaisesRegex(ValueError, "cycle"):
            db.update_employee("EBASE", manager_emp_id=employee["emp_id"])
        for field in ("dept_code", "position_code", "manager_emp_id"):
            with self.assertRaises(ValueError):
                db.update_employee("EBASE", name="Should not change", **{field: "MISSING"})
            self.assertEqual(db.get_employee("EBASE")["name"], "Base")

    def test_hire_date_update_moves_only_the_hire_appointment(self):
        employee = db.hire_employee("Hire", "D1", "P1", hire_date="2026-07-06")
        db.create_appointment(employee["emp_id"], "휴직", effective_date="2026-07-20")
        db.update_employee(employee["emp_id"], hire_date="2026-07-13")
        appointments = {row["type"]: row["effective_date"]
                        for row in db.list_appointments(emp_id=employee["emp_id"])}
        self.assertEqual(appointments, {"입사": "2026-07-13", "휴직": "2026-07-20"})

    def test_personnel_actions_cannot_reintroduce_deleted_reference_codes(self):
        db.create_department("DDEL", "Unused")
        db.delete_department("DDEL")
        before = db.get_employee("EBASE")
        with self.assertRaises(ValueError):
            db.create_appointment("EBASE", "부서이동", to_dept="DDEL")
        with self.assertRaises(ValueError):
            db.create_appointment("EBASE", "승진", to_position="MISSING")
        self.assertEqual(db.get_employee("EBASE"), before)

    def test_leave_requests_always_get_a_start_year_balance_and_validate_approver(self):
        request = db.submit_leave_request("EBASE", "연차", "2030-01-07")
        balance = db.get_leave_balance("EBASE", 2030)
        self.assertIsNotNone(balance)
        self.assertEqual(balance["entitled_days"], 0)
        with self.assertRaises(ValueError):
            db.decide_leave_request(request["id"], "반려", approver_emp_id="MISSING")
        self.assertEqual(db.list_leave_requests()[0]["status"], "신청")

    def test_concurrent_enrollment_and_course_delete_never_orphan_training(self):
        def enroll():
            try:
                db.enroll_training("EBASE", "CRACE")
            except ValueError:
                return "deleted first"
            return "enrolled"

        def delete():
            try:
                db.delete_course("CRACE")
            except db.ConflictError:
                return "enrolled first"
            return "deleted"

        for _ in range(5):
            self._minimal()
            db.create_course("CRACE", "Race")
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(enroll), pool.submit(delete)]
                outcomes = [future.result() for future in results]
            self.assertIn(outcomes, (["enrolled", "enrolled first"], ["deleted first", "deleted"]))
            with db.get_conn() as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM training_record tr LEFT JOIN course c "
                    "ON c.course_code=tr.course_code WHERE c.course_code IS NULL"
                ).fetchone()[0], 0)
