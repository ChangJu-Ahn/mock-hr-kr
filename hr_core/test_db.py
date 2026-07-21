"""Unit tests for the mock HR data layer (hr_core.db)."""

import importlib
import os
import unittest
from pathlib import Path

TEST_DB = Path(__file__).resolve().parents[1] / "data" / "hr_core_unit_test.db"


class DbTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HR_DB_PATH"] = str(TEST_DB)
        TEST_DB.parent.mkdir(exist_ok=True)
        from hr_core import db
        importlib.reload(db)
        cls.db = db

    @classmethod
    def tearDownClass(cls):
        for f in TEST_DB.parent.glob(TEST_DB.name + "*"):
            f.unlink(missing_ok=True)

    def setUp(self):
        self.db.reset_db()

    def _seed_master(self):
        """Minimal master data: 3 depts (nested), 2 positions, 2 courses."""
        db = self.db
        with db.get_conn() as conn:
            conn.executemany(
                "INSERT INTO department (dept_code, dept_name, parent_dept_code) VALUES (?,?,?)",
                [("D0", "본사", None), ("D1", "인사팀", "D0"), ("D2", "개발팀", "D0")],
            )
            conn.executemany(
                "INSERT INTO position (position_code, position_name, level_no, min_leave_days)"
                " VALUES (?,?,?,?)",
                [("P1", "사원", 1, 15.0), ("P4", "과장", 4, 16.0)],
            )
            conn.executemany(
                "INSERT INTO course (course_code, course_name, category, delivery, hours,"
                " capacity, is_mandatory) VALUES (?,?,?,?,?,?,?)",
                [
                    ("C1", "정보보안", "법정필수", "온라인", 2.0, 999, 1),
                    ("C2", "Python 실무", "직무", "온라인", 16.0, 40, 0),
                ],
            )


class SchemaTests(DbTestBase):
    def test_all_tables_exist_after_reset(self):
        with self.db.get_conn() as conn:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in ("department", "position", "employee", "course", "training_record",
                  "leave_balance", "leave_request", "attendance", "appointment"):
            self.assertIn(t, names)

    def test_counts_zero_after_reset(self):
        c = self.db.counts()
        self.assertEqual(c["employee"], 0)
        self.assertEqual(c["department"], 0)
        self.assertIn("attendance", c)


class DepartmentPositionTests(DbTestBase):
    def setUp(self):
        super().setUp()
        self._seed_master()

    def test_list_codes(self):
        self.assertEqual(self.db.list_department_codes(), ["D0", "D1", "D2"])
        self.assertEqual(self.db.list_position_codes(), ["P1", "P4"])

    def test_org_tree_depth(self):
        tree = self.db.get_org_tree()
        by_code = {r["dept_code"]: r for r in tree}
        self.assertEqual(by_code["D0"]["depth"], 0)
        self.assertEqual(by_code["D1"]["depth"], 1)
        self.assertEqual(by_code["D2"]["depth"], 1)
        # root comes before its children
        codes = [r["dept_code"] for r in tree]
        self.assertLess(codes.index("D0"), codes.index("D1"))

    def test_org_tree_includes_parent_department_name(self):
        by_code = {r["dept_code"]: r for r in self.db.get_org_tree()}
        self.assertIsNone(by_code["D0"]["parent_dept_name"])
        self.assertEqual(by_code["D1"]["parent_dept_name"], "본사")

    def test_headcount_reflects_active_only(self):
        db = self.db
        db.hire_employee("갑", "D1", "P1", year=2026)
        emp = db.hire_employee("을", "D1", "P1", year=2026)
        db.create_appointment(emp["emp_id"], "퇴직", effective_date="2026-06-01")
        dept = db.get_department("D1")
        self.assertEqual(dept["headcount"], 1)  # 을 retired, only 갑 active


class EmployeeTests(DbTestBase):
    def setUp(self):
        super().setUp()
        self._seed_master()

    def test_hire_creates_employee_balance_and_appointment(self):
        db = self.db
        emp = db.hire_employee("홍길동", "D1", "P4", employment_type="정규직", year=2026)
        self.assertEqual(emp["emp_id"], "E0001")
        self.assertEqual(emp["status"], "재직")
        self.assertEqual(emp["dept_name"], "인사팀")
        self.assertEqual(emp["position_name"], "과장")
        bal = db.get_leave_balance("E0001", 2026)
        self.assertEqual(bal["entitled_days"], 16.0)   # P4 min_leave_days
        self.assertEqual(bal["remaining_days"], 16.0)
        appts = db.list_appointments(emp_id="E0001")
        self.assertEqual(len(appts), 1)
        self.assertEqual(appts[0]["type"], "입사")

    def test_hire_auto_increments_id(self):
        db = self.db
        a = db.hire_employee("일", "D1", "P1", year=2026)
        b = db.hire_employee("이", "D2", "P1", year=2026)
        self.assertEqual(a["emp_id"], "E0001")
        self.assertEqual(b["emp_id"], "E0002")

    def test_hire_unknown_dept_or_position_rejected(self):
        with self.assertRaises(ValueError):
            self.db.hire_employee("x", "NOPE", "P1", year=2026)
        with self.assertRaises(ValueError):
            self.db.hire_employee("x", "D1", "NOPE", year=2026)

    def test_list_employees_filters(self):
        db = self.db
        db.hire_employee("김하나", "D1", "P1", year=2026)
        db.hire_employee("이두울", "D2", "P4", year=2026)
        self.assertEqual(len(db.list_employees(dept_code="D1")), 1)
        self.assertEqual(len(db.list_employees(position_code="P4")), 1)
        self.assertEqual(len(db.list_employees(q="하나")), 1)
        self.assertEqual(len(db.list_employees()), 2)


class TrainingTests(DbTestBase):
    def setUp(self):
        super().setUp()
        self._seed_master()
        self.emp = self.db.hire_employee("교육생", "D2", "P1", year=2026)["emp_id"]

    def test_enroll_and_complete(self):
        db = self.db
        rec = db.enroll_training(self.emp, "C1")
        self.assertEqual(rec["status"], "수강중")
        done = db.complete_training(rec["id"], "이수", score=95)
        self.assertEqual(done["status"], "이수")
        self.assertEqual(done["score"], 95)
        self.assertIsNotNone(done["complete_date"])

    def test_enroll_unknown_emp_or_course_rejected(self):
        with self.assertRaises(ValueError):
            self.db.enroll_training("NOPE", "C1")
        with self.assertRaises(ValueError):
            self.db.enroll_training(self.emp, "NOPE")

    def test_complete_bad_status_rejected(self):
        rec = self.db.enroll_training(self.emp, "C1")
        with self.assertRaises(ValueError):
            self.db.complete_training(rec["id"], "WHATEVER")

    def test_completion_rate(self):
        db = self.db
        r1 = db.enroll_training(self.emp, "C1")  # mandatory
        db.complete_training(r1["id"], "이수")
        db.enroll_training(self.emp, "C1")       # mandatory, still 수강중
        rate = db.training_completion_rate(mandatory_only=True)
        self.assertEqual(rate["total"], 2)
        self.assertEqual(rate["completed"], 1)
        self.assertEqual(rate["rate"], 50.0)


class LeaveTests(DbTestBase):
    def setUp(self):
        super().setUp()
        self._seed_master()
        self.emp = self.db.hire_employee("휴가자", "D1", "P1", year=2026)["emp_id"]  # 15 days

    def test_submit_computes_days(self):
        db = self.db
        r = db.submit_leave_request(self.emp, "연차", "2026-03-02", "2026-03-04")
        self.assertEqual(r["days"], 3.0)           # inclusive span
        self.assertEqual(r["status"], "신청")
        half = db.submit_leave_request(self.emp, "반차", "2026-03-10")
        self.assertEqual(half["days"], 0.5)

    def test_approve_decrements_balance(self):
        db = self.db
        r = db.submit_leave_request(self.emp, "연차", "2026-03-02", "2026-03-03")  # 2d
        out = db.decide_leave_request(r["id"], "승인", approver_emp_id=self.emp)
        self.assertEqual(out["status"], "승인")
        self.assertEqual(out["leave_balance"]["used_days"], 2.0)
        self.assertEqual(out["leave_balance"]["remaining_days"], 13.0)

    def test_reject_does_not_touch_balance(self):
        db = self.db
        r = db.submit_leave_request(self.emp, "연차", "2026-03-02", "2026-03-03")
        db.decide_leave_request(r["id"], "반려")
        self.assertEqual(db.get_leave_balance(self.emp, 2026)["used_days"], 0.0)

    def test_insufficient_balance_blocks_approval(self):
        db = self.db
        r = db.submit_leave_request(self.emp, "연차", "2026-03-02", "2026-03-31")  # 30d
        self.assertIsNotNone(r["balance_warning"])
        with self.assertRaises(ValueError):
            db.decide_leave_request(r["id"], "승인")
        # balance untouched after the blocked approval
        self.assertEqual(db.get_leave_balance(self.emp, 2026)["remaining_days"], 15.0)
        self.assertEqual(db.list_leave_requests(status="신청")[0]["id"], r["id"])

    def test_cannot_decide_twice(self):
        db = self.db
        r = db.submit_leave_request(self.emp, "연차", "2026-03-02", "2026-03-03")
        db.decide_leave_request(r["id"], "승인", approver_emp_id=self.emp)
        with self.assertRaises(ValueError):
            db.decide_leave_request(r["id"], "취소")

    def test_grant_annual_leave_accrues(self):
        db = self.db
        db.grant_annual_leave(self.emp, 2026, days=5)
        self.assertEqual(db.get_leave_balance(self.emp, 2026)["entitled_days"], 20.0)


class AttendanceTests(DbTestBase):
    def setUp(self):
        super().setUp()
        self._seed_master()
        self.emp = self.db.hire_employee("근태자", "D2", "P1", year=2026)["emp_id"]

    def test_log_derives_hours_and_status(self):
        db = self.db
        a = db.log_attendance(self.emp, "2026-07-01", check_in="08:50", check_out="19:00")
        self.assertEqual(a["work_hours"], 9.17)     # 10h10m - 1h lunch
        self.assertEqual(a["overtime_hours"], 1.17)
        self.assertEqual(a["status"], "정상")

    def test_late_and_absent_status(self):
        db = self.db
        late = db.log_attendance(self.emp, "2026-07-02", check_in="09:30", check_out="18:00")
        self.assertEqual(late["status"], "지각")
        absent = db.log_attendance(self.emp, "2026-07-03")
        self.assertEqual(absent["status"], "결근")

    def test_upsert_same_day(self):
        db = self.db
        db.log_attendance(self.emp, "2026-07-04", check_in="09:30", check_out="18:00")
        db.log_attendance(self.emp, "2026-07-04", check_in="08:50", check_out="18:00")
        rows = db.list_attendance(emp_id=self.emp, work_date="2026-07-04")
        self.assertEqual(len(rows), 1)              # UNIQUE(emp_id, work_date)
        self.assertEqual(rows[0]["status"], "정상")

    def test_summary_groups_by_status(self):
        db = self.db
        db.log_attendance(self.emp, "2026-07-06", check_in="08:50", check_out="18:00")
        db.log_attendance(self.emp, "2026-07-07", check_in="09:30", check_out="18:00")
        summary = {r["status"]: r["records"] for r in db.get_attendance_summary(month="2026-07")}
        self.assertEqual(summary.get("정상"), 1)
        self.assertEqual(summary.get("지각"), 1)


class AppointmentTests(DbTestBase):
    def setUp(self):
        super().setUp()
        self._seed_master()
        self.emp = self.db.hire_employee("발령자", "D1", "P1", year=2026)["emp_id"]

    def test_promotion_updates_position(self):
        db = self.db
        db.create_appointment(self.emp, "승진", to_position="P4", effective_date="2026-01-01")
        self.assertEqual(db.get_employee(self.emp)["position_code"], "P4")

    def test_transfer_updates_dept_and_captures_from(self):
        db = self.db
        row = db.create_appointment(self.emp, "부서이동", to_dept="D2", effective_date="2026-02-01")
        self.assertEqual(row["from_dept"], "D1")
        self.assertEqual(row["to_dept"], "D2")
        self.assertEqual(db.get_employee(self.emp)["dept_code"], "D2")

    def test_appointment_rows_include_department_and_position_names(self):
        db = self.db
        transfer = db.create_appointment(
            self.emp, "부서이동", to_dept="D2", effective_date="2026-02-01"
        )
        self.assertEqual(transfer["from_dept_name"], "인사팀")
        self.assertEqual(transfer["to_dept_name"], "개발팀")
        self.assertEqual(transfer["from_position_name"], "사원")
        self.assertIsNone(transfer["to_position_name"])

        promotion = db.create_appointment(
            self.emp, "승진", to_position="P4", effective_date="2026-03-01"
        )
        self.assertEqual(promotion["from_position_name"], "사원")
        self.assertEqual(promotion["to_position_name"], "과장")

        detail_rows = self.db.get_employee(self.emp)["appointments"]
        self.assertTrue(all("from_dept_name" in row for row in detail_rows))
        self.assertTrue(all("to_position_name" in row for row in detail_rows))

    def test_leave_and_return_status(self):
        db = self.db
        db.create_appointment(self.emp, "휴직", effective_date="2026-03-01")
        self.assertEqual(db.get_employee(self.emp)["status"], "휴직")
        db.create_appointment(self.emp, "복직", effective_date="2026-06-01")
        self.assertEqual(db.get_employee(self.emp)["status"], "재직")

    def test_bad_type_and_missing_target_rejected(self):
        with self.assertRaises(ValueError):
            self.db.create_appointment(self.emp, "이상한발령")
        with self.assertRaises(ValueError):
            self.db.create_appointment(self.emp, "승진")           # no to_position
        with self.assertRaises(ValueError):
            self.db.create_appointment(self.emp, "부서이동")       # no to_dept


class DashboardTests(DbTestBase):
    def setUp(self):
        super().setUp()
        self._seed_master()

    def test_dashboard_shape(self):
        db = self.db
        emp = db.hire_employee("대시", "D1", "P1", year=2026)["emp_id"]
        r = db.submit_leave_request(emp, "연차", "2026-03-02", "2026-03-03")
        db.decide_leave_request(r["id"], "승인", approver_emp_id=emp)
        db.log_attendance(emp, "2026-07-06", check_in="08:50", check_out="18:00")
        s = db.get_dashboard_summary()
        for key in ("emp_total", "active_total", "employees_by_status", "dept_total",
                    "headcount_by_dept", "employees_by_position", "employees_by_type",
                    "attendance_date", "attendance_today", "pending_leave",
                    "leave_entitled", "leave_used", "leave_usage_pct",
                    "training_mandatory", "recent_appointments", "recent_leaves"):
            self.assertIn(key, s)
        self.assertEqual(s["emp_total"], 1)
        self.assertEqual(s["active_total"], 1)
        self.assertEqual(s["leave_used"], 2.0)
        self.assertEqual(s["attendance_date"], "2026-07-06")

    def test_dashboard_position_summary_exposes_position_code(self):
        db = self.db
        db.hire_employee("대시", "D1", "P1", year=2026)

        rows = {r["position_code"]: r for r in db.get_dashboard_summary()["employees_by_position"]}

        self.assertEqual(rows["P1"]["position_name"], "사원")
        self.assertEqual(rows["P1"]["n"], 1)
        self.assertEqual(rows["P4"]["position_name"], "과장")
        self.assertEqual(rows["P4"]["n"], 0)


class SeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HR_DB_PATH"] = str(TEST_DB)
        TEST_DB.parent.mkdir(exist_ok=True)
        from hr_core import db, seed
        importlib.reload(db)
        importlib.reload(seed)
        cls.db, cls.seed = db, seed
        seed.seed()

    @classmethod
    def tearDownClass(cls):
        for f in TEST_DB.parent.glob(TEST_DB.name + "*"):
            f.unlink(missing_ok=True)

    def test_master_and_row_counts(self):
        c = self.db.counts()
        self.assertEqual(c["department"], 10)
        self.assertEqual(c["position"], 7)
        self.assertEqual(c["course"], 10)
        self.assertEqual(c["employee"], 34)
        self.assertEqual(c["leave_balance"], 34)
        self.assertEqual(c["appointment"], 43)       # 34 입사 + 9 extra actions
        self.assertEqual(c["training_record"], 212)
        self.assertEqual(c["leave_request"], 23)
        self.assertEqual(c["attendance"], 310)        # 31 active x 10 weekdays

    def test_employee_status_breakdown(self):
        by_status = {r["status"]: r["n"] for r in self.db.get_dashboard_summary()["employees_by_status"]}
        self.assertEqual(by_status["재직"], 31)
        self.assertEqual(by_status["휴직"], 1)
        self.assertEqual(by_status["퇴직"], 2)

    def test_appointment_type_breakdown(self):
        types = {r["type"] for r in self.db.list_appointments(limit=100)}
        self.assertEqual(types, {"입사", "승진", "부서이동", "휴직", "복직", "퇴직"})
        self.assertEqual(len(self.db.list_appointments(type="입사", limit=100)), 34)

    def test_leave_requests_and_balances_consistent(self):
        approved = self.db.list_leave_requests(status="승인", limit=100)
        pending = self.db.list_leave_requests(status="신청", limit=100)
        self.assertEqual(len(approved), 17)
        self.assertEqual(len(pending), 4)
        # sum of used days across balances equals sum of approved-request days
        with self.db.get_conn() as c:
            used = c.execute("SELECT SUM(used_days) FROM leave_balance").fetchone()[0]
            appr_days = c.execute(
                "SELECT SUM(days) FROM leave_request WHERE status='승인'").fetchone()[0]
        self.assertAlmostEqual(used, appr_days)
        self.assertAlmostEqual(used, 23.5)

    def test_all_departments_have_manager(self):
        with self.db.get_conn() as c:
            missing = c.execute(
                "SELECT COUNT(*) FROM department WHERE manager_emp_id IS NULL").fetchone()[0]
        self.assertEqual(missing, 0)

    def test_mandatory_training_rate(self):
        rate = self.db.training_completion_rate(mandatory_only=True)
        self.assertEqual(rate["total"], 155)
        self.assertEqual(rate["completed"], 124)


if __name__ == "__main__":
    unittest.main()
