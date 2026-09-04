import os
import re
import unittest
from html.parser import HTMLParser

from fastapi.testclient import TestClient

KEY = {"X-API-Key": "changjuahn"}


class TableShellParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._div_stack: list[bool] = []
        self.shell_depth = 0
        self.shell_count = 0
        self.table_count = 0
        self.table_shell_depths: list[int] = []

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        classes = attrs_map.get("class", "").split()
        is_table_shell = tag == "div" and "table-shell" in classes
        if tag == "div":
            self._div_stack.append(is_table_shell)
            if is_table_shell:
                self.shell_depth += 1
                self.shell_count += 1
        elif tag == "table":
            self.table_count += 1
            self.table_shell_depths.append(self.shell_depth)

    def handle_endtag(self, tag):
        if tag == "div" and self._div_stack:
            if self._div_stack.pop():
                self.shell_depth -= 1


class RestApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HR_DB_PATH"] = os.path.join(os.getcwd(), "data", "hr_api_unittest.db")
        from hr_core import db, seed
        db.reset_db()
        seed.seed(force=True)
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
        seed.seed(force=True)
        from api.main import app
        cls.db = db
        cls.client = TestClient(app)  # web is open (no key)

    @classmethod
    def tearDownClass(cls):
        for suffix in ("", "-shm", "-wal"):
            path = os.environ["HR_DB_PATH"] + suffix
            if os.path.exists(path):
                os.remove(path)

    def _assert_single_table_shell_wrapper(self, path: str):
        html = self.client.get(path).text
        parser = TableShellParser()
        parser.feed(html)
        parser.close()
        self.assertGreater(parser.table_count, 0, path)
        self.assertEqual(parser.shell_count, parser.table_count, path)
        self.assertEqual(parser.table_shell_depths, [1] * parser.table_count, path)

    def test_all_pages_render(self):
        for path in ("/", "/employees", "/departments", "/positions", "/courses",
                     "/leave", "/attendance", "/appointments", "/guide", "/mcp-docs"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_employee_detail_renders(self):
        emp_id = self.db.list_employee_ids()[0]
        self.assertEqual(self.client.get(f"/employees/{emp_id}").status_code, 200)

    def test_employee_detail_uses_atomic_profile_fields_and_badges(self):
        emp_id = self.db.hire_employee("상세검증", "D110", "P1")["emp_id"]
        self.db.log_attendance(emp_id, "2026-07-20", check_in="08:55", check_out="18:10")
        self.db.enroll_training(emp_id, "C006", enroll_date="2026-07-01")
        training_id = self.db.list_training_records(emp_id=emp_id)[0]["id"]
        self.db.complete_training(training_id, status="이수", score=96, complete_date="2026-07-03")
        self.db.submit_leave_request(
            emp_id,
            "연차",
            "2026-08-03",
            "2026-08-03",
            reason="상세페이지검증",
        )
        leave_id = self.db.list_leave_requests(emp_id=emp_id, status="신청")[0]["id"]
        self.db.decide_leave_request(leave_id, "승인", approver_emp_id=emp_id, decided_date="2026-08-01")

        html = self.client.get(f"/employees/{emp_id}").text
        for label in (
            "사번", "이름", "부서코드", "부서명", "직급코드", "직급명",
            "고용형태", "상태", "입사일", "생년월일", "이메일", "연락처",
            "관리자 사번", "관리자 이름",
        ):
            self.assertIn(label, html)
        for heading in ("과정코드", "과정명", "변경 전", "변경 후"):
            self.assertIn(heading, html)
        self.assertIn('class="profile-header"', html)
        self.assertIn('<dl class="detail-grid">', html)
        self.assertGreaterEqual(html.count('class="status-badge'), 4)

    def test_employee_detail_appointment_history_uses_grouped_before_after_fallbacks(self):
        emp_id = self.db.hire_employee("발령상세검증", "D110", "P1")["emp_id"]
        self.db.create_appointment(emp_id, "휴직", effective_date="2026-03-01", note="상태변경검증")

        html = self.client.get(f"/employees/{emp_id}").text
        self.assertIn('<th colspan="4" class="group-heading">변경 전</th>', html)
        self.assertIn('<th colspan="4" class="group-heading">변경 후</th>', html)
        self.assertRegex(
            html,
            r"(?s)>휴직</td>\s*<td>D110</td>\s*<td>인사팀</td>\s*<td>P1</td>\s*<td>사원</td>"
            r"\s*<td>D110</td>\s*<td>인사팀</td>\s*<td>P1</td>\s*<td>사원</td>\s*<td>상태변경검증</td>",
        )

    def test_successfactors_inspired_shell_is_present(self):
        html = self.client.get("/").text
        self.assertIn('class="shellbar"', html)
        self.assertIn('class="shell-search"', html)
        self.assertIn('action="/employees"', html)
        self.assertIn('name="q"', html)
        self.assertIn('aria-label="업무 메뉴"', html)
        self.assertIn('id="main-content"', html)

    def test_horizon_design_tokens_and_focus_rules_are_served(self):
        css = self.client.get("/static/styles.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("--sap-blue: #0a6ed1", css.text)
        self.assertIn("--sap-info: #0857a8;", css.text)
        self.assertIn(".grid2 .data-table { min-width: 0; }", css.text)
        self.assertRegex(css.text, r"\.work-nav summary\[aria-current=\"page\"\]\s*\{[^}]*border-bottom-color:\s*var\(--sap-blue\);")
        self.assertNotRegex(css.text, r"\.content-card,\s*section\s*\{[^}]*overflow-x:")
        self.assertRegex(css.text, r"\.table-shell\s*\{[^}]*overflow-x:\s*auto;")
        self.assertRegex(css.text, r"input:focus,\s*select:focus\s*\{[^}]*outline:\s*2px solid var\(--sap-blue\);")
        self.assertRegex(
            css.text,
            r"button:focus-visible,\s*\.button:focus-visible,\s*summary:focus-visible,\s*a:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--sap-blue\);",
        )

    def test_dashboard_uses_hybrid_home_layout(self):
        html = self.client.get("/").text
        self.assertIn('class="content-card quick-actions"', html)
        self.assertIn('class="kpi-grid"', html)
        self.assertEqual(html.count('class="kpi-card"'), 5)
        self.assertIn("확인이 필요합니다", html)
        self.assertIn('href="/leave#leave-action"', html)
        self.assertIn('href="/attendance#attendance-action"', html)

    def test_dashboard_uses_atomic_identity_and_org_columns(self):
        html = self.client.get("/").text
        for heading in (
            "사번", "이름", "부서코드", "부서명",
            "변경 전", "변경 후", "직급코드", "직급명",
        ):
            self.assertIn(heading, html)
        self.assertRegex(
            html,
            r"<tr><th>직급코드</th><th>직급명</th><th>레벨</th><th>재직인원</th></tr>",
        )
        self.assertNotIn("E0034 방통상", html)

    def test_employee_table_uses_atomic_code_name_columns(self):
        html = self.client.get("/employees").text
        for heading in (
            "사번", "이름", "부서코드", "부서명", "직급코드", "직급명",
            "관리자 사번", "관리자 이름",
        ):
            self.assertIn(heading, html)
        self.assertIn('id="employee-action"', html)
        self.assertIn('class="filter-bar"', html)

    def test_department_table_splits_parent_and_manager_identity(self):
        html = self.client.get("/departments").text
        for heading in (
            "부서코드", "부서명", "상위부서코드", "상위부서명",
            "부서장 사번", "부서장 이름",
        ):
            self.assertIn(heading, html)

    def test_position_page_uses_shared_workspace_components(self):
        html = self.client.get("/positions").text
        self.assertIn("직급 관리", html)
        self.assertIn('class="table-shell"', html)

    def test_grouped_org_nav_marks_departments_and_positions_current(self):
        for path in ("/departments", "/positions"):
            html = self.client.get(path).text
            self.assertIn('<summary aria-current="page">조직</summary>', html, path)
            self.assertNotIn('<summary aria-current="page">휴가·근태</summary>', html, path)

    def test_grouped_leave_nav_marks_leave_and_attendance_current(self):
        for path in ("/leave", "/attendance"):
            html = self.client.get(path).text
            self.assertIn('<summary aria-current="page">휴가·근태</summary>', html, path)
            self.assertNotIn('<summary aria-current="page">조직</summary>', html, path)

    def test_status_badge_macro_maps_attendance_leave_to_info_tone(self):
        from api.web import templates

        html = templates.env.from_string(
            '{% from "_ui.html" import status_badge %}{{ status_badge("휴가") }}'
        ).render()
        self.assertIn("status-badge--info", html)

    def test_all_rendered_tables_have_exactly_one_table_shell_wrapper(self):
        emp_id = self.db.list_employee_ids()[0]
        for path in (
            "/",
            "/employees",
            f"/employees/{emp_id}",
            "/departments",
            "/positions",
            "/courses",
            "/leave",
            "/attendance",
            "/appointments",
            "/guide",
            "/mcp-docs",
        ):
            self._assert_single_table_shell_wrapper(path)

    def test_training_workspace_uses_atomic_employee_and_course_columns(self):
        html = self.client.get("/courses").text
        for heading in ("사번", "이름", "과정코드", "과정명"):
            self.assertIn(heading, html)
        self.assertIn('id="training-action"', html)
        self.assertIn(" · ", html)

    def test_leave_workspace_splits_employee_department_and_approver_columns(self):
        html = self.client.get("/leave").text
        for heading in (
            "사번", "이름", "부서코드", "부서명",
            "승인자 사번", "승인자 이름",
        ):
            self.assertIn(heading, html)
        self.assertIn('id="leave-action"', html)
        self.assertIn('class="status-badge', html)

    def test_attendance_workspace_splits_employee_and_department_columns(self):
        html = self.client.get("/attendance").text
        for heading in ("사번", "이름", "부서코드", "부서명"):
            self.assertIn(heading, html)
        self.assertIn('id="attendance-action"', html)
        self.assertIn('class="status-badge', html)

    def test_appointment_workspace_uses_grouped_before_after_columns(self):
        emp_id = self.db.hire_employee("발령화면검증", "D110", "P1")["emp_id"]
        self.db.create_appointment(emp_id, "휴직", note="상태변경검증")
        html = self.client.get("/appointments").text
        self.assertIn("변경 전", html)
        self.assertIn("변경 후", html)
        self.assertGreaterEqual(html.count("부서코드"), 2)
        self.assertGreaterEqual(html.count("직급명"), 2)
        self.assertIn('id="appointment-action"', html)
        self.assertRegex(
            html,
            rf"(?s)>{emp_id}</td>\s*<td[^>]*>발령화면검증</td>.*?<td>D110</td>\s*<td>인사팀</td>\s*<td>P1</td>\s*<td>사원</td>\s*<td>D110</td>\s*<td>인사팀</td>\s*<td>P1</td>\s*<td>사원</td>",
        )

    def test_appointment_filter_preserves_hire_option_and_selected_state(self):
        html = self.client.get("/appointments", params={"type": "입사"}).text
        self.assertIn('<option value="">전체</option>', html)
        self.assertIn('<option selected>입사</option>', html)

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

    def test_guide_uses_step_cards(self):
        html = self.client.get("/guide").text
        self.assertIn('class="guide-steps"', html)
        self.assertIn('<ol class="guide-steps" role="list"', html)
        self.assertEqual(html.count('class="guide-step__number"'), 5)
        for text in (
            "입사", "교육", "연차·휴가", "근태", "인사발령",
            "REST /api/employees", "MCP submit_leave_request/decide_leave_request/get_leave_balance/list_leave_requests",
            'href="/api/docs"',
        ):
            self.assertIn(text, html)

    def test_profile_and_guide_styles_are_served(self):
        css = self.client.get("/static/styles.css").text
        for token in (
            ".profile-header {",
            ".profile-avatar {",
            ".guide-steps {",
            ".guide-step__number {",
        ):
            self.assertIn(token, css)

    # --- MCP 문서 (the /api/docs counterpart) ------------------------------ #
    def test_nav_exposes_both_api_and_mcp_reference_tabs(self):
        html = self.client.get("/").text
        self.assertIn('<a href="/api/docs" >API 문서</a>', html)
        self.assertIn('<a href="/mcp-docs" >MCP 문서</a>', html)

    def test_mcp_docs_tab_is_marked_current_on_the_mcp_docs_page(self):
        html = self.client.get("/mcp-docs").text
        self.assertIn('<a href="/mcp-docs" aria-current="page">MCP 문서</a>', html)

    def test_dashboard_introduces_the_mcp_surface(self):
        html = self.client.get("/").text
        self.assertIn("에이전트 연결 창구", html)
        self.assertIn('class="surface-card" href="/mcp-docs"', html)
        self.assertIn('class="surface-card" href="/api/docs"', html)
        self.assertIn("MCP 서버", html)

    def test_guide_links_to_the_mcp_reference(self):
        self.assertIn('href="/mcp-docs"', self.client.get("/guide").text)

    def test_mcp_docs_page_is_open_and_explains_mcp(self):
        # Mirrors /api/docs: browsable without an API key (cls.client sends none).
        r = self.client.get("/mcp-docs")
        self.assertEqual(r.status_code, 200)
        for text in ("MCP가 뭔가요", "Model Context Protocol", "tools/list", "tools/call",
                     "연결 정보", "서버 기능", "도구 카탈로그", "클라이언트 연결 설정"):
            self.assertIn(text, r.text)

    def test_mcp_docs_documents_every_tool_the_server_advertises(self):
        import asyncio

        from mcp_server.server import mcp

        live = {t.name for t in asyncio.run(mcp.list_tools())}
        self.assertEqual(len(live), 9)

        html = self.client.get("/mcp-docs").text
        spec = self.client.get("/mcp-docs/spec.json").json()
        self.assertEqual({t["name"] for t in spec["tools"]}, live)
        self.assertEqual(spec["tool_count"], len(live))
        for name in live:
            self.assertIn(f'id="tool-{name}"', html, name)

    def test_mcp_docs_renders_parameter_tables_from_the_json_schema(self):
        html = self.client.get("/mcp-docs").text
        for heading in ("이름", "타입", "필수", "기본값", "허용값"):
            self.assertIn(heading, html)
        # log_attendance's schema: two required params, an enum-ish status.
        spec = self.client.get("/mcp-docs/spec.json").json()
        tool = next(t for t in spec["tools"] if t["name"] == "log_attendance")
        self.assertEqual(tool["required"], ["emp_id", "work_date"])
        status = next(p for p in tool["parameters"] if p["name"] == "status")
        self.assertFalse(status["required"])
        self.assertIn("지각", status["allowed"])
        limit = next(p for p in next(
            t for t in spec["tools"] if t["name"] == "list_attendance")["parameters"]
            if p["name"] == "limit")
        self.assertEqual(limit["default_display"], "100")

    def test_mcp_docs_spec_reports_transport_auth_and_capabilities(self):
        spec = self.client.get("/mcp-docs/spec.json").json()
        server = spec["server"]
        self.assertEqual(server["name"], "mock-hr-mcp")
        self.assertEqual(server["transport"], "Streamable HTTP")
        self.assertTrue(server["stateless"])
        self.assertTrue(server["endpoint"].endswith("/mcp"))
        self.assertEqual(server["auth"]["header"], "X-API-Key")
        self.assertEqual(server["auth"]["key"], "changjuahn")
        caps = {c["name"]: c["supported"] for c in spec["capabilities"]}
        self.assertTrue(caps["tools"])
        # The server registers no resources/prompts — say so rather than imply them.
        self.assertFalse(caps["resources"])
        self.assertFalse(caps["prompts"])
        self.assertIn("tools/call", [m["method"] for m in spec["methods"]])

    def test_mcp_docs_documents_the_structured_content_wrapping(self):
        spec = self.client.get("/mcp-docs/spec.json").json()
        by_name = {t["name"]: t for t in spec["tools"]}
        # list_* tools return a list, which FastMCP wraps as {"result": [...]}.
        self.assertEqual(by_name["list_attendance"]["output"]["kind"], "wrapped")
        self.assertEqual(by_name["list_attendance"]["output"]["type"], "array<object>")
        # dict-returning tools expose the object directly.
        self.assertEqual(by_name["get_employee"]["output"]["kind"], "object")

    def test_mcp_docs_examples_use_ids_that_exist_in_the_seeded_data(self):
        spec = self.client.get("/mcp-docs/spec.json").json()
        by_name = {t["name"]: t for t in spec["tools"]}

        emp_id = by_name["get_employee"]["example_arguments"]["emp_id"]
        self.assertIsNotNone(self.db.get_employee(emp_id))

        dept_code = by_name["list_employees"]["example_arguments"]["dept_code"]
        self.assertTrue(self.db.list_employees(dept_code=dept_code))

        # A 승인 example is only runnable against a request still in 신청.
        request_id = by_name["decide_leave_request"]["example_arguments"]["request_id"]
        pending = {r["id"] for r in self.db.list_leave_requests(status="신청", limit=500)}
        self.assertIn(request_id, pending)

    def test_mcp_docs_client_snippets_carry_the_endpoint_and_api_key(self):
        html = self.client.get("/mcp-docs").text
        spec = self.client.get("/mcp-docs/spec.json").json()
        endpoint = spec["server"]["endpoint"]
        keys = {s["key"] for s in spec["snippets"]}
        self.assertEqual(keys, {"vscode", "claude", "python", "curl"})
        for snippet in spec["snippets"]:
            self.assertIn(endpoint, snippet["code"], snippet["key"])
            self.assertIn("changjuahn", snippet["code"], snippet["key"])
        # Streamable HTTP needs both Accept types; say so in the raw example.
        curl = next(s for s in spec["snippets"] if s["key"] == "curl")
        self.assertIn("application/json, text/event-stream", curl["code"])
        self.assertIn("mcp-remote", html)

    def test_mcp_docs_styles_are_served(self):
        css = self.client.get("/static/styles.css").text
        for token in (".tool-card {", ".tool-index {", ".surface-card {", ".tool-chip {"):
            self.assertIn(token, css)

    def test_mcp_docs_advertises_the_forwarded_public_origin(self):
        # Behind the ACA ingress + Caddy the api container only sees the internal
        # hop, so the endpoint must be rebuilt from the forwarded headers.
        r = self.client.get("/mcp-docs/spec.json", headers={
            "X-Forwarded-Proto": "https", "X-Forwarded-Host": "mock-hr.example.net"})
        self.assertEqual(r.json()["server"]["endpoint"], "https://mock-hr.example.net/mcp")

    def test_mcp_docs_origin_survives_chained_and_empty_forwarded_headers(self):
        # Multiple hops append to the header; the client-facing value is first.
        r = self.client.get("/mcp-docs/spec.json", headers={
            "X-Forwarded-Proto": "https, http", "X-Forwarded-Host": "mock-hr.example.net, internal"})
        self.assertEqual(r.json()["server"]["endpoint"], "https://mock-hr.example.net/mcp")

        # A proxy that blanks the header must not yield a "://host" endpoint.
        r = self.client.get("/mcp-docs/spec.json", headers={"X-Forwarded-Proto": "  "})
        endpoint = r.json()["server"]["endpoint"]
        self.assertTrue(endpoint.startswith("http://"), endpoint)
        self.assertTrue(endpoint.endswith("/mcp"), endpoint)



if __name__ == "__main__":
    unittest.main()
