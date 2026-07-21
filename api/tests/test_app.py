import os
import unittest

from fastapi.testclient import TestClient

KEY = {"X-API-Key": "changjuahn"}


class RestApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HR_DB_PATH"] = os.path.join(os.getcwd(), "data", "hr_api_unittest.db")
        from hr_core import db, seed
        db.reset_db()
        seed.seed()
        from api.main import app
        cls.db = db
        cls.client = TestClient(app, headers=KEY)
        cls.open_client = TestClient(app)  # no key

    @classmethod
    def tearDownClass(cls):
        for suffix in ("", "-shm", "-wal"):
            path = os.environ["HR_DB_PATH"] + suffix
            if os.path.exists(path):
                os.remove(path)

    # --- auth gate --------------------------------------------------------- #
    def test_api_requires_key(self):
        self.assertEqual(self.open_client.get("/api/employees").status_code, 401)
        self.assertEqual(self.client.get("/api/employees").status_code, 200)

    def test_docs_and_web_open(self):
        self.assertEqual(self.open_client.get("/api/openapi.json").status_code, 200)
        self.assertEqual(self.open_client.get("/api/docs").status_code, 200)
        self.assertEqual(self.open_client.get("/").status_code, 200)

    def test_openapi_lists_paths(self):
        paths = self.open_client.get("/api/openapi.json").json()["paths"]
        for p in ("/api/employees", "/api/departments", "/api/departments/tree",
                  "/api/positions", "/api/courses", "/api/training-records",
                  "/api/appointments"):
            self.assertIn(p, paths)
        self.assertNotIn("/", paths)

    # --- queries ----------------------------------------------------------- #
    def test_employees_filter(self):
        rows = self.client.get("/api/employees", params={"status": "재직"}).json()
        self.assertTrue(all(r["status"] == "재직" for r in rows))
        self.assertGreaterEqual(len(rows), 31)

    def test_departments_tree_and_detail(self):
        depts = self.client.get("/api/departments").json()
        self.assertEqual(len(depts), 10)
        tree = self.client.get("/api/departments/tree").json()
        self.assertTrue(all("depth" in r for r in tree))
        self.assertEqual(self.client.get("/api/departments/D110").status_code, 200)
        self.assertEqual(self.client.get("/api/departments/NOPE").status_code, 404)

    def test_departments_include_parent_name(self):
        rows = self.client.get("/api/departments/tree").json()
        child = next(row for row in rows if row["parent_dept_code"])
        self.assertIsNotNone(child["parent_dept_name"])

    def test_positions_and_courses(self):
        self.assertEqual(len(self.client.get("/api/positions").json()), 7)
        mand = self.client.get("/api/courses", params={"is_mandatory": True}).json()
        self.assertTrue(all(r["is_mandatory"] == 1 for r in mand))
        self.assertEqual(len(mand), 5)

    def test_training_records_filter(self):
        rows = self.client.get("/api/training-records", params={"status": "이수"}).json()
        self.assertTrue(all(r["status"] == "이수" for r in rows))

    def test_appointments_filter(self):
        rows = self.client.get("/api/appointments", params={"type": "입사"}).json()
        self.assertTrue(all(r["type"] == "입사" for r in rows))
        self.assertGreaterEqual(len(rows), 34)

    # --- transactions ------------------------------------------------------ #
    def test_hire_employee_and_detail(self):
        r = self.client.post("/api/employees",
                             json={"name": "신입사원", "dept_code": "D110", "position_code": "P1"})
        self.assertEqual(r.status_code, 200)
        emp = r.json()
        self.assertEqual(emp["status"], "재직")
        self.assertTrue(emp["emp_id"].startswith("E"))
        detail = self.client.get(f"/api/employees/{emp['emp_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("leave_balance", detail.json())

    def test_hire_bad_dept_is_400(self):
        r = self.client.post("/api/employees",
                             json={"name": "x", "dept_code": "NOPE", "position_code": "P1"})
        self.assertEqual(r.status_code, 400)

    def test_enroll_then_complete_training(self):
        emp = self.client.post(
            "/api/employees",
            json={"name": "교육대상", "dept_code": "D210", "position_code": "P1"}).json()
        rec = self.client.post("/api/training-records",
                              json={"emp_id": emp["emp_id"], "course_code": "C006"})
        self.assertEqual(rec.status_code, 200)
        rid = rec.json()["id"]
        done = self.client.post(f"/api/training-records/{rid}/complete",
                               json={"status": "이수", "score": 88})
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.json()["status"], "이수")

    def test_appointment_promotion_updates_employee(self):
        emp = self.client.post(
            "/api/employees",
            json={"name": "승진대상", "dept_code": "D110", "position_code": "P1"}).json()
        r = self.client.post("/api/appointments",
                            json={"emp_id": emp["emp_id"], "type": "승진", "to_position": "P4"})
        self.assertEqual(r.status_code, 200)
        detail = self.client.get(f"/api/employees/{emp['emp_id']}").json()
        self.assertEqual(detail["position_code"], "P4")

    def test_appointment_response_includes_display_names(self):
        emp = self.client.post(
            "/api/employees",
            json={"name": "표시명검증", "dept_code": "D110", "position_code": "P1"},
        ).json()
        row = self.client.post(
            "/api/appointments",
            json={"emp_id": emp["emp_id"], "type": "승진", "to_position": "P4"},
        ).json()
        self.assertEqual(row["from_dept_name"], "인사팀")
        self.assertEqual(row["from_position_name"], "사원")
        self.assertEqual(row["to_position_name"], "과장")

    def test_appointment_missing_target_is_400(self):
        emp = self.client.post(
            "/api/employees",
            json={"name": "발령오류", "dept_code": "D110", "position_code": "P1"}).json()
        r = self.client.post("/api/appointments",
                            json={"emp_id": emp["emp_id"], "type": "승진"})  # no to_position
        self.assertEqual(r.status_code, 400)


class WebConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HR_DB_PATH"] = os.path.join(os.getcwd(), "data", "hr_web_unittest.db")
        from hr_core import db, seed
        db.reset_db()
        seed.seed()
        from api.main import app
        cls.db = db
        cls.client = TestClient(app)  # web is open (no key)

    @classmethod
    def tearDownClass(cls):
        for suffix in ("", "-shm", "-wal"):
            path = os.environ["HR_DB_PATH"] + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_all_pages_render(self):
        for path in ("/", "/employees", "/departments", "/positions", "/courses",
                     "/leave", "/attendance", "/appointments", "/guide"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_employee_detail_renders(self):
        emp_id = self.db.list_employee_ids()[0]
        self.assertEqual(self.client.get(f"/employees/{emp_id}").status_code, 200)

    def test_hire_form(self):
        before = len(self.db.list_employee_ids())
        r = self.client.post("/employees/hire",
                             data={"name": "웹입사", "dept_code": "D120", "position_code": "P1"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(len(self.db.list_employee_ids()), before + 1)

    def test_enroll_and_complete_form(self):
        emp_id = self.db.hire_employee("웹교육", "D210", "P1")["emp_id"]
        r = self.client.post("/courses/enroll",
                             data={"emp_id": emp_id, "course_code": "C007"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        rid = self.db.list_training_records(emp_id=emp_id)[0]["id"]
        r2 = self.client.post(f"/courses/{rid}/complete",
                              data={"status_value": "이수", "score": "95"},
                              follow_redirects=False)
        self.assertEqual(r2.status_code, 303)
        self.assertEqual(self.db.list_training_records(emp_id=emp_id)[0]["status"], "이수")

    def test_leave_submit_and_decide_form(self):
        emp_id = self.db.hire_employee("웹휴가", "D310", "P1")["emp_id"]
        r = self.client.post("/leave/submit",
                             data={"emp_id": emp_id, "leave_type": "연차",
                                   "start_date": "2026-08-03", "end_date": "2026-08-04"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        req_id = self.db.list_leave_requests(emp_id=emp_id, status="신청")[0]["id"]
        r2 = self.client.post(f"/leave/{req_id}/decide",
                              data={"decision": "승인", "approver_emp_id": emp_id},
                              follow_redirects=False)
        self.assertEqual(r2.status_code, 303)
        self.assertEqual(self.db.get_leave_balance(emp_id, 2026)["used_days"], 2.0)

    def test_attendance_log_form(self):
        emp_id = self.db.list_employee_ids()[0]
        r = self.client.post("/attendance/log",
                             data={"emp_id": emp_id, "work_date": "2026-07-20",
                                   "check_in": "08:55", "check_out": "18:10"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        rows = self.db.list_attendance(emp_id=emp_id, work_date="2026-07-20")
        self.assertEqual(rows[0]["status"], "정상")

    def test_appointment_create_form(self):
        emp_id = self.db.hire_employee("웹발령", "D110", "P1")["emp_id"]
        r = self.client.post("/appointments/create",
                             data={"emp_id": emp_id, "type": "부서이동", "to_dept": "D120"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.db.get_employee(emp_id)["dept_code"], "D120")

    def test_guide_page(self):
        r = self.client.get("/guide")
        self.assertEqual(r.status_code, 200)
        self.assertIn("HR", r.text)


if __name__ == "__main__":
    unittest.main()
