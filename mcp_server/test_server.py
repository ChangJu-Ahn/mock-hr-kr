import asyncio
import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

TEST_DB = Path(__file__).resolve().parents[1] / "data" / "hr_mcp_unit_test.db"


class MCPToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HR_DB_PATH"] = str(TEST_DB)
        TEST_DB.parent.mkdir(exist_ok=True)
        subprocess.run([sys.executable, "-m", "hr_core.seed"], check=True,
                       cwd=Path(__file__).resolve().parents[1])
        from hr_core import db
        from mcp_server import server
        importlib.reload(db)
        importlib.reload(server)
        cls.db, cls.server = db, server
        cls.emp_id = db.list_employee_ids(status="재직")[0]

    @classmethod
    def tearDownClass(cls):
        for f in TEST_DB.parent.glob(TEST_DB.name + "*"):
            f.unlink(missing_ok=True)

    def test_log_and_query_attendance(self):
        s = self.server
        row = s.log_attendance(self.emp_id, "2026-07-27", check_in="09:20", check_out="18:00")
        self.assertNotIn("error", row)
        self.assertEqual(row["status"], "지각")
        listed = s.list_attendance(emp_id=self.emp_id, work_date="2026-07-27")
        self.assertEqual(len(listed), 1)
        summary = {r["status"]: r for r in s.get_attendance_summary(month="2026-07")}
        self.assertIn("지각", summary)

    def test_leave_submit_and_decide(self):
        s = self.server
        req = s.submit_leave_request(self.emp_id, "연차", "2026-09-01", "2026-09-02")
        self.assertNotIn("error", req)
        self.assertEqual(req["days"], 2.0)
        before = s.get_leave_balance(self.emp_id, 2026)["remaining_days"]
        out = s.decide_leave_request(req["id"], "승인", approver_emp_id=self.emp_id)
        self.assertEqual(out["status"], "승인")
        self.assertEqual(out["leave_balance"]["remaining_days"], before - 2.0)

    def test_insufficient_leave_returns_error(self):
        s = self.server
        req = s.submit_leave_request(self.emp_id, "연차", "2026-10-01", "2026-12-31")
        self.assertIsNotNone(req["balance_warning"])
        result = s.decide_leave_request(req["id"], "승인")
        self.assertIn("error", result)
        # still pending after the blocked approval
        pending = [r for r in s.list_leave_requests(emp_id=self.emp_id, status="신청")
                   if r["id"] == req["id"]]
        self.assertEqual(len(pending), 1)

    def test_bad_inputs_return_error_or_not_found(self):
        s = self.server
        self.assertIn("error", s.log_attendance("NOPE", "2026-07-27", check_in="09:00"))
        self.assertIn("error", s.submit_leave_request("NOPE", "연차", "2026-07-01"))
        self.assertIn("not_found", s.get_employee("NOPE"))
        self.assertIn("not_found", s.get_leave_balance("NOPE"))

    def test_employee_queries(self):
        s = self.server
        active = s.list_employees(status="재직")
        self.assertTrue(all(e["status"] == "재직" for e in active))
        emp = s.get_employee(self.emp_id)
        self.assertEqual(emp["emp_id"], self.emp_id)
        self.assertIn("leave_balance", emp)

    def test_api_key_guard(self):
        async def dummy(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        guard = self.server._ApiKeyGuard(dummy)
        os.environ["HR_API_KEY"] = "changjuahn"

        async def run(headers):
            sent = []
            async def send(m): sent.append(m)
            async def receive(): return {"type": "http.request"}
            await guard({"type": "http", "headers": headers}, receive, send)
            return sent[0]["status"]

        self.assertEqual(asyncio.run(run([])), 401)
        self.assertEqual(asyncio.run(run([(b"x-api-key", b"changjuahn")])), 200)


if __name__ == "__main__":
    unittest.main()
