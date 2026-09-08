"""Master-data parity across REST, MCP and the rendered console."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app
from hr_core import db, seed
from mcp_server import server


MASTERS = (
    ("departments", "department", "dept_code", "DTST", "dept_name",
     {"dept_name": "Test department", "parent_dept_code": "D000"}),
    ("positions", "position", "position_code", "PTST", "position_name",
     {"position_name": "Test position", "level_no": 8, "min_leave_days": 12.5}),
    ("courses", "course", "course_code", "CTST", "course_name",
     {"course_name": "Test course", "hours": 3, "capacity": 10, "is_mandatory": False}),
)


class MasterDataWriteSurfaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {
            "HR_DB_PATH": str(Path(temporary.name) / "crud.db"),
            "HR_HISTORY_START": "2026-07-06",
            "HR_API_KEY": "changjuahn",
        })
        environment.start()
        self.addCleanup(environment.stop)
        seed.seed()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"X-API-Key": "changjuahn"}

    def test_rest_master_create_update_delete_roundtrips(self):
        for collection, _, key, code, field, values in MASTERS:
            with self.subTest(collection=collection):
                payload = {key: code, **values}
                created = self.client.post(f"/api/{collection}", json=payload, headers=self.headers)
                self.assertIn(created.status_code, (200, 201), created.text)
                self.assertEqual(created.json()[key], code)
                url = f"/api/{collection}/{code}"
                changed = self.client.patch(url, json={field: "Changed"}, headers=self.headers)
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(self.client.get(url, headers=self.headers).json()[field], "Changed")
                removed = self.client.delete(url, headers=self.headers)
                self.assertEqual(removed.status_code, 200, removed.text)
                self.assertTrue(removed.json()["deleted"])
                self.assertEqual(self.client.get(url, headers=self.headers).status_code, 404)

    def test_employee_hire_update_and_guarded_delete(self):
        created = self.client.post("/api/employees", headers=self.headers, json={
            "name": "Test employee", "dept_code": "D200", "position_code": "P3",
            "hire_date": "2026-07-06",
        })
        self.assertEqual(created.status_code, 200, created.text)
        emp_id = created.json()["emp_id"]
        changed = self.client.patch(f"/api/employees/{emp_id}", headers=self.headers, json={
            "name": "Updated employee", "hire_date": "2026-07-13", "email": None,
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        employee = self.client.get(f"/api/employees/{emp_id}", headers=self.headers).json()
        self.assertEqual(employee["name"], "Updated employee")
        self.assertEqual(employee["appointments"][0]["effective_date"], "2026-07-13")
        blocked = self.client.delete(f"/api/employees/{emp_id}", headers=self.headers)
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("leave balances", blocked.json()["detail"])
        self.assertIn("appointments", blocked.json()["detail"])

    def test_mcp_writes_are_visible_in_rest_and_html_for_all_masters(self):
        for collection, singular, key, code, field, values in MASTERS:
            with self.subTest(collection=collection):
                created = getattr(server, f"create_{singular}")(**{key: code, **values})
                self.assertEqual(created[key], code)
                changed = getattr(server, f"update_{singular}")(
                    **{key: code, "changes": {field: f"Visible {code}"}})
                self.assertNotIn("error", changed)
                row = self.client.get(f"/api/{collection}/{code}", headers=self.headers).json()
                self.assertEqual(row[field], f"Visible {code}")
                self.assertIn(f"Visible {code}", self.client.get(f"/{collection}").text)
                self.assertTrue(getattr(server, f"delete_{singular}")(**{key: code})["deleted"])
        employee = server.create_employee("MCP hire", "D200", "P3")
        self.assertNotIn("error", employee)
        changed = server.update_employee(employee["emp_id"], {"name": "MCP visible employee"})
        self.assertNotIn("error", changed)
        self.assertEqual(self.client.get(f"/api/employees/{employee['emp_id']}",
                                        headers=self.headers).json()["name"], "MCP visible employee")
        self.assertIn("MCP visible employee", self.client.get(f"/employees/{employee['emp_id']}").text)
        self.assertIn("error", server.delete_employee(employee["emp_id"]))

    def test_reference_deletes_return_rest_409_and_mcp_error_objects(self):
        for collection, singular, key, code in (
            ("departments", "department", "dept_code", "D200"),
            ("positions", "position", "position_code", "P3"),
            ("courses", "course", "course_code", "C001"),
            ("employees", "employee", "emp_id", "E0001"),
        ):
            with self.subTest(entity=singular):
                response = self.client.delete(f"/api/{collection}/{code}", headers=self.headers)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn(f"cannot delete {singular} {code}", response.json()["detail"])
                self.assertIn("still referenced by", response.json()["detail"])
                error = getattr(server, f"delete_{singular}")(**{key: code})
                self.assertEqual(error["error"], response.json()["detail"])

    def test_mutations_require_auth_and_reject_immutable_or_unknown_fields(self):
        for collection, _, key, code in (
            ("departments", "department", "dept_code", "D200"),
            ("positions", "position", "position_code", "P3"),
            ("courses", "course", "course_code", "C001"),
            ("employees", "employee", "emp_id", "E0001"),
        ):
            url = f"/api/{collection}/{code}"
            with self.subTest(collection=collection):
                self.assertEqual(self.client.patch(url, json={}).status_code, 401)
                self.assertEqual(self.client.delete(url).status_code, 401)
                for changes in ({key: "RENAMED"}, {"unknown": "field"}):
                    response = self.client.patch(url, json=changes, headers=self.headers)
                    self.assertIn(response.status_code, (400, 422))
                self.assertEqual(self.client.get(url, headers=self.headers).status_code, 200)

    def test_duplicates_missing_records_and_invalid_references_are_domain_errors(self):
        for collection, _, key, code, field, values in MASTERS:
            with self.subTest(collection=collection):
                payload = {key: code, **values}
                self.client.post(f"/api/{collection}", json=payload, headers=self.headers)
                self.assertEqual(self.client.post(f"/api/{collection}", json=payload,
                                                  headers=self.headers).status_code, 409)
                self.assertEqual(self.client.patch(f"/api/{collection}/MISSING",
                                                  json={field: "Change"},
                                                  headers=self.headers).status_code, 404)
                self.assertEqual(self.client.delete(f"/api/{collection}/MISSING",
                                                   headers=self.headers).status_code, 404)
        response = self.client.patch("/api/employees/E0001", headers=self.headers,
                                     json={"manager_emp_id": "MISSING"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/employees", headers=self.headers, json={
            "emp_id": "E0001", "name": "Duplicate", "dept_code": "D200", "position_code": "P3",
        })
        self.assertEqual(response.status_code, 409)

    def test_patch_distinguishes_omitted_and_null_fields(self):
        self.client.patch("/api/employees/E0001", headers=self.headers,
                          json={"email": "example@mock-hr.example", "phone": "010-0000-0000"})
        response = self.client.patch("/api/employees/E0001", headers=self.headers, json={"email": None})
        self.assertEqual(response.status_code, 200, response.text)
        employee = self.client.get("/api/employees/E0001", headers=self.headers).json()
        self.assertIsNone(employee["email"])
        self.assertEqual(employee["phone"], "010-0000-0000")
        self.assertIsNone(server.update_employee("E0001", {"phone": None})["phone"])

    def test_actual_mcp_tool_envelope_does_not_raise_for_guarded_deletes(self):
        async def call():
            return await server.mcp.call_tool("delete_department", {"dept_code": "D200"})
        result = asyncio.run(call())
        content, structured = result
        self.assertIn("still referenced by", structured["error"])
        self.assertEqual(json.loads(content[0].text)["error"], structured["error"])

    def test_mcp_update_unknown_fields_cannot_shadow_wrapper_arguments(self):
        for update, code in (
            (server.update_employee, "E0001"), (server.update_department, "D200"),
            (server.update_position, "P3"), (server.update_course, "C001"),
        ):
            for field in ("action", "identifier", "changes", "args"):
                with self.subTest(action=update.__name__, field=field):
                    self.assertIn("error", update(code, {field: "invalid"}))

    def test_web_master_roundtrips_and_employee_edit(self):
        for collection, _, key, code, field, values in MASTERS:
            with self.subTest(collection=collection):
                form = {key: code, **values}
                if collection == "courses":
                    form["is_mandatory"] = "0"
                created = self.client.post(f"/{collection}/create", data=form, follow_redirects=False)
                self.assertEqual(created.status_code, 303, created.text)
                self.assertNotIn("error=", created.headers["location"])
                changed = self.client.post(f"/{collection}/{code}/update",
                                           data={name: ("Web changed" if name == field else value)
                                                 for name, value in form.items() if name != key},
                                           follow_redirects=False)
                self.assertEqual(changed.status_code, 303)
                self.assertNotIn("error=", changed.headers["location"])
                self.assertIn("Web changed", self.client.get(f"/{collection}").text)
                deleted = self.client.post(f"/{collection}/{code}/delete", follow_redirects=False)
                self.assertEqual(deleted.status_code, 303)
                self.assertNotIn("error=", deleted.headers["location"])
        response = self.client.post("/employees/E0001/update", follow_redirects=False, data={
            "name": "Web employee", "dept_code": "D000", "position_code": "P7",
            "employment_type": "정규직", "status": "재직", "hire_date": "2015-03-02",
        })
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("error=", response.headers["location"])
        self.assertIn("Web employee", self.client.get("/employees/E0001").text)

    def test_web_shows_guard_reason_and_encodes_query_messages(self):
        response = self.client.post("/departments/D200/delete")
        self.assertEqual(response.status_code, 200)
        self.assertIn("still referenced by", response.text)
        response = self.client.post("/employees/E0001/delete")
        self.assertIn("cannot delete employee E0001", response.text)
        response = self.client.post("/departments/create", follow_redirects=False, data={
            "dept_code": "DX", "dept_name": "한글 & + 조직",
        })
        self.assertEqual(response.status_code, 303)
        self.assertIn("한글 &amp; + 조직", self.client.get(response.headers["location"]).text)

    def test_forms_expose_create_edit_and_delete_for_every_master(self):
        for path in ("/departments", "/positions", "/courses"):
            text = self.client.get(path).text
            self.assertIn(f'action="{path}/create"', text)
            self.assertIn("/update", text)
            self.assertIn("/delete", text)
        text = self.client.get("/employees/E0001").text
        self.assertIn('action="/employees/E0001/update"', text)
        self.assertIn('action="/employees/E0001/delete"', text)

    def test_docs_describe_current_history_and_all_registered_master_tools(self):
        guide = self.client.get("/guide").text
        for phrase in ("HR_HISTORY_START", "minReplicas=1", "409", "update_employee"):
            self.assertIn(phrase, guide)
        spec = self.client.get("/mcp-docs/spec.json").json()
        tools = {tool["name"]: tool for tool in spec["tools"]}
        for singular, field in (("employee", "name"), ("department", "dept_name"),
                                ("position", "position_name"), ("course", "course_name")):
            for action in ("create", "update", "delete"):
                name = f"{action}_{singular}"
                self.assertIn(name, tools)
                self.assertFalse(tools[name]["read_only"])
                self.assertTrue(tools[name]["console_page"])
            self.assertIn(field, tools[f"update_{singular}"]["example_arguments"]["changes"])
        self.assertEqual(len(tools), 27)
