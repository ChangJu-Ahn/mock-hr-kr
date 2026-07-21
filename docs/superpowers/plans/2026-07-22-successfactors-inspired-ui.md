# SuccessFactors-Inspired UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Mock HR web console presentation with the approved SuccessFactors-inspired hybrid home/admin workspace, including atomic identifier/name columns, without changing HR processes.

**Architecture:** Enrich existing SQLite read models with display names, then build a shared Jinja/CSS UI system around the existing FastAPI routes and forms. Migrate pages incrementally so every task leaves the app runnable and tested; finish by rebuilding images and rolling a fresh Azure Container Apps revision.

**Tech Stack:** Python 3.12+, SQLite, FastAPI, Jinja2, plain HTML/CSS, native `<details>`, unittest/TestClient, Docker, GHCR, Azure Container Apps

## Global Constraints

- Preserve every route, form method/action, REST endpoint, MCP tool, SQLite table, transaction, seed count, and API-key rule.
- Use SAP Fiori Morning Horizon / SuccessFactors interaction conventions only as inspiration; do not copy SAP logos, proprietary assets, screenshots, or product branding.
- Keep the product name `Mock HR`.
- Add no frontend framework, SAP component library, icon package, or JavaScript dependency.
- Every read-only table separates identifier/code fields from human-readable names.
- Select controls keep combined labels using `CODE · Name`.
- Existing REST fields remain compatible; new display-name fields are additive.
- Use test-first changes and run the smallest relevant unittest module after each task.
- Commit each task with the required `Co-authored-by` and `Copilot-Session` trailers.
- Never stage `.superpowers/`; remove the local visual-companion directory before final delivery.

---

## File Map

| File | Responsibility |
| --- | --- |
| `hr_core/db.py` | Add parent-department and appointment display-name joins; reuse enriched appointment rows in employee detail/dashboard/API. |
| `hr_core/test_db.py` | Pin department and appointment display-name contracts. |
| `api/rest.py` | Add optional REST response fields without changing existing fields. |
| `api/templates/_ui.html` | Shared page header, status badge, message strip, and empty-row Jinja macros. |
| `api/templates/base.html` | Morning Horizon-inspired shell bar, global employee search, grouped work navigation, accessibility landmarks. |
| `api/static/styles.css` | Design tokens and all shell/card/form/table/badge/responsive styling. |
| `api/templates/dashboard.html` | Latest Home-style quick actions, KPIs, attention items, summaries, and atomic recent-record tables. |
| `api/templates/employees.html` | Employee administrator workspace and atomic employee/manager columns. |
| `api/templates/departments.html` | Organization workspace with parent and manager code/name columns. |
| `api/templates/positions.html` | Position workspace using shared page/table patterns. |
| `api/templates/courses.html` | Training workspace with atomic employee/course columns. |
| `api/templates/leave.html` | Leave workspace with atomic employee/department/approver columns. |
| `api/templates/attendance.html` | Attendance workspace with atomic employee/department columns. |
| `api/templates/appointments.html` | Grouped before/after appointment table with display-name fallbacks. |
| `api/templates/employee_detail.html` | SuccessFactors-style profile header and atomic detail/history fields. |
| `api/templates/guide.html` | Redesign the guide into cards/steps without changing its content contract. |
| `api/tests/test_app.py` | Assert shell, atomic columns, badges, data joins, and existing form behavior. |
| `README.md` | Mention the SuccessFactors-inspired hybrid web console. |

---

### Task 1: Enrich Read Models for Atomic Display Columns

**Files:**
- Modify: `hr_core/test_db.py:67-93,262-293`
- Modify: `hr_core/db.py:274-279,392-420,865-929`
- Modify: `api/rest.py:29-40,94-104`
- Modify: `api/tests/test_app.py:52-58,70-73,105-113`

**Interfaces:**
- Consumes: existing `list_departments()`, `get_org_tree()`, `list_appointments()`, `create_appointment()`, and `get_employee()`.
- Produces: department rows with `parent_dept_name`; appointment rows with `from_dept_name`, `to_dept_name`, `from_position_name`, `to_position_name`.
- Compatibility: raw `from_dept`, `to_dept`, `from_position`, and `to_position` values remain unchanged, including `None` for unchanged targets.

- [ ] **Step 1: Write failing department and appointment data-layer tests**

Add these assertions:

```python
def test_org_tree_includes_parent_department_name(self):
    by_code = {r["dept_code"]: r for r in self.db.get_org_tree()}
    self.assertIsNone(by_code["D0"]["parent_dept_name"])
    self.assertEqual(by_code["D1"]["parent_dept_name"], "본사")

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
```

- [ ] **Step 2: Run the focused core tests and confirm the missing-field failure**

Run:

```bash
cd /Users/changjuahn/Repo/mock-hr-kr
.venv/bin/python -m unittest \
  hr_core.test_db.DepartmentPositionTests \
  hr_core.test_db.AppointmentTests
```

Expected: failures containing `KeyError: 'parent_dept_name'`,
`KeyError: 'from_dept_name'`, or missing appointment display fields.

- [ ] **Step 3: Add the exact department and appointment joins**

Replace `_DEPT_SELECT` with:

```python
_DEPT_SELECT = (
    "SELECT d.*, parent.dept_name AS parent_dept_name, m.name AS manager_name, "
    "  (SELECT COUNT(*) FROM employee e WHERE e.dept_code = d.dept_code AND e.status='재직') "
    "    AS headcount "
    "FROM department d "
    "LEFT JOIN department parent ON parent.dept_code = d.parent_dept_code "
    "LEFT JOIN employee m ON m.emp_id = d.manager_emp_id "
)
```

Replace `_APT_SELECT` with:

```python
_APT_SELECT = (
    "SELECT ap.*, e.name AS emp_name, "
    "  fd.dept_name AS from_dept_name, td.dept_name AS to_dept_name, "
    "  fp.position_name AS from_position_name, tp.position_name AS to_position_name "
    "FROM appointment ap "
    "LEFT JOIN employee e ON e.emp_id = ap.emp_id "
    "LEFT JOIN department fd ON fd.dept_code = ap.from_dept "
    "LEFT JOIN department td ON td.dept_code = ap.to_dept "
    "LEFT JOIN position fp ON fp.position_code = ap.from_position "
    "LEFT JOIN position tp ON tp.position_code = ap.to_position "
)
```

Change the employee-detail appointment query to use the same contract:

```python
appts = conn.execute(
    _APT_SELECT + "WHERE ap.emp_id = ? ORDER BY ap.id", (emp_id,)
).fetchall()
```

- [ ] **Step 4: Add optional REST response fields**

Add `parent_dept_name` to `DepartmentRow` and these fields to
`AppointmentRow`:

```python
parent_dept_name: str | None = None
```

```python
from_dept_name: str | None = None
to_dept_name: str | None = None
from_position_name: str | None = None
to_position_name: str | None = None
```

- [ ] **Step 5: Add REST compatibility tests**

Extend the REST tests:

```python
def test_departments_include_parent_name(self):
    rows = self.client.get("/api/departments/tree").json()
    child = next(row for row in rows if row["parent_dept_code"])
    self.assertIsNotNone(child["parent_dept_name"])

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
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/python -m unittest hr_core.test_db api.tests.test_app.RestApiTests
```

Expected: all focused tests pass.

- [ ] **Step 7: Commit**

```bash
git add hr_core/db.py hr_core/test_db.py api/rest.py api/tests/test_app.py
git commit -m "Enrich HR display read models

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>
Copilot-Session: ba94d4ac-6efc-4486-b024-d0cf36bad1de"
```

---

### Task 2: Build the Shared Morning Horizon-Inspired UI System

**Files:**
- Create: `api/templates/_ui.html`
- Modify: `api/templates/base.html:1-33`
- Replace: `api/static/styles.css:1-65`
- Modify: `api/tests/test_app.py:142-150`

**Interfaces:**
- Produces Jinja macros:
  - `page_header(title: str, subtitle: str, eyebrow: str = "인사 운영")`
  - `status_badge(value: str | None)`
  - `message_strip(kind: str, text: str)`
  - `empty_row(colspan: int, message: str)`
- Produces CSS component classes used by all later templates:
  `shellbar`, `shell-search`, `work-nav`, `page-shell`, `page-header`,
  `action-panel`, `filter-bar`, `content-card`, `table-shell`, `data-table`,
  `status-badge`, `kpi-grid`, `attention-grid`, `detail-grid`.

- [ ] **Step 1: Write failing shell/design-system tests**

Add:

```python
def test_successfactors_inspired_shell_is_present(self):
    html = self.client.get("/").text
    self.assertIn('class="shellbar"', html)
    self.assertIn('class="shell-search"', html)
    self.assertIn('action="/employees"', html)
    self.assertIn('name="q"', html)
    self.assertIn('aria-label="업무 메뉴"', html)
    self.assertIn('id="main-content"', html)

def test_horizon_design_tokens_are_served(self):
    css = self.client.get("/static/styles.css")
    self.assertEqual(css.status_code, 200)
    self.assertIn("--sap-blue: #0a6ed1", css.text)
    self.assertIn(".status-badge", css.text)
    self.assertIn(".table-shell", css.text)
```

- [ ] **Step 2: Run the shell tests and confirm they fail**

Run:

```bash
.venv/bin/python -m unittest \
  api.tests.test_app.WebConsoleTests.test_successfactors_inspired_shell_is_present \
  api.tests.test_app.WebConsoleTests.test_horizon_design_tokens_are_served
```

Expected: failures because the new shell classes and CSS tokens do not exist.

- [ ] **Step 3: Create the shared Jinja macros**

Create `api/templates/_ui.html` with:

```jinja2
{% macro page_header(title, subtitle, eyebrow='인사 운영') -%}
<header class="page-header">
  <div>
    <p class="page-eyebrow">{{ eyebrow }}</p>
    <h1>{{ title }}</h1>
    {% if subtitle %}<p class="page-subtitle">{{ subtitle }}</p>{% endif %}
  </div>
</header>
{%- endmacro %}

{% macro status_badge(value) -%}
  {% set positive = ['재직', '승인', '이수', '정상', '필수'] %}
  {% set info = ['신청', '수강중', '재택'] %}
  {% set warning = ['휴직', '지각', '조퇴'] %}
  {% set negative = ['반려', '퇴직', '미이수', '결근', '취소'] %}
  {% if value in positive %}{% set tone = 'positive' %}
  {% elif value in info %}{% set tone = 'info' %}
  {% elif value in warning %}{% set tone = 'warning' %}
  {% elif value in negative %}{% set tone = 'negative' %}
  {% else %}{% set tone = 'neutral' %}{% endif %}
<span class="status-badge status-badge--{{ tone }}">{{ value or '—' }}</span>
{%- endmacro %}

{% macro message_strip(kind, text) -%}
<div class="message-strip message-strip--{{ kind }}" role="status">
  <span class="message-strip__icon" aria-hidden="true">{% if kind == 'success' %}✓{% else %}!{% endif %}</span>
  <span>{{ text }}</span>
</div>
{%- endmacro %}

{% macro empty_row(colspan, message) -%}
<tr><td class="empty-state" colspan="{{ colspan }}">{{ message }}</td></tr>
{%- endmacro %}
```

- [ ] **Step 4: Replace the base template with the accessible shell**

Use this structure while preserving the existing stylesheet and HTMX includes:

```jinja2
{% from "_ui.html" import message_strip %}
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Mock HR{% endblock %}</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
</head>
<body>
  <a class="skip-link" href="#main-content">본문으로 건너뛰기</a>
  <header class="shellbar">
    <a class="shellbar__brand" href="/" aria-label="Mock HR 홈">
      <span class="shellbar__mark" aria-hidden="true">HR</span>
      <span>Mock HR</span>
    </a>
    <form class="shell-search" method="get" action="/employees" role="search">
      <label class="sr-only" for="global-search">사원 검색</label>
      <span aria-hidden="true">⌕</span>
      <input id="global-search" name="q" placeholder="사번, 이름, 이메일 검색">
    </form>
    <div class="shellbar__actions">
      <a href="/guide">도움말</a>
      <span class="user-chip"><span aria-hidden="true">운</span> HR 운영자</span>
    </div>
  </header>
  <nav class="work-nav" aria-label="업무 메뉴">
    <a href="/" {% if request.url.path == '/' %}aria-current="page"{% endif %}>홈</a>
    <a href="/employees" {% if request.url.path.startswith('/employees') %}aria-current="page"{% endif %}>사원</a>
    <details>
      <summary>조직</summary>
      <div class="nav-popover"><a href="/departments">부서</a><a href="/positions">직급</a></div>
    </details>
    <a href="/courses" {% if request.url.path == '/courses' %}aria-current="page"{% endif %}>교육</a>
    <details>
      <summary>휴가·근태</summary>
      <div class="nav-popover"><a href="/leave">연차·휴가</a><a href="/attendance">근태</a></div>
    </details>
    <a href="/appointments" {% if request.url.path == '/appointments' %}aria-current="page"{% endif %}>인사관리</a>
    <a href="/api/docs">API</a>
  </nav>
  <main id="main-content" class="page-shell">
    {% if message %}{{ message_strip('success', message) }}{% endif %}
    {% if error %}{{ message_strip('error', error) }}{% endif %}
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 5: Replace CSS with the shared component system**

Define these exact tokens first:

```css
:root {
  color-scheme: light;
  --sap-blue: #0a6ed1;
  --sap-blue-hover: #0854a0;
  --sap-blue-soft: #e5f2ff;
  --sap-shell: #ffffff;
  --sap-page-bg: #f5f6f7;
  --sap-surface: #ffffff;
  --sap-text: #1d2d3e;
  --sap-muted: #556b82;
  --sap-border: #d9d9d9;
  --sap-border-strong: #89919a;
  --sap-positive: #256f3a;
  --sap-positive-bg: #eaf4e8;
  --sap-warning: #8d2a00;
  --sap-warning-bg: #fff3e8;
  --sap-negative: #aa0808;
  --sap-negative-bg: #ffeaf4;
  --sap-info: #0a6ed1;
  --sap-info-bg: #e5f2ff;
  --shadow-card: 0 1px 3px rgba(34, 53, 72, .12);
}
```

Implement complete styles for:

```css
* { box-sizing: border-box; }
html { font-size: 16px; }
body { margin: 0; font-family: "72", "Segoe UI", Arial, sans-serif; background: var(--sap-page-bg); color: var(--sap-text); }
a { color: var(--sap-blue); text-decoration: none; }
a:hover { text-decoration: underline; }
.skip-link { position: fixed; left: 1rem; top: -4rem; z-index: 100; background: var(--sap-blue); color: #fff; padding: .5rem .75rem; }
.skip-link:focus { top: .5rem; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.shellbar { min-height: 3rem; display: grid; grid-template-columns: auto minmax(16rem, 36rem) auto; align-items: center; gap: 1rem; padding: .5rem 1.25rem; background: var(--sap-shell); border-bottom: 1px solid var(--sap-border); position: sticky; top: 0; z-index: 30; }
.shellbar__brand { display: inline-flex; align-items: center; gap: .6rem; color: var(--sap-text); font-weight: 700; }
.shellbar__mark { display: inline-grid; place-items: center; width: 2rem; height: 2rem; border-radius: .4rem; background: var(--sap-blue); color: #fff; font-size: .75rem; }
.shell-search { display: flex; align-items: center; gap: .5rem; min-height: 2.25rem; border: 1px solid var(--sap-border-strong); border-radius: .375rem; padding: 0 .7rem; background: #fff; }
.shell-search:focus-within { outline: 2px solid var(--sap-blue); outline-offset: 1px; }
.shell-search input { width: 100%; border: 0; outline: 0; background: transparent; min-height: 2rem; }
.shellbar__actions { display: flex; justify-content: flex-end; align-items: center; gap: .9rem; white-space: nowrap; }
.user-chip { display: inline-flex; align-items: center; gap: .4rem; font-size: .875rem; }
.user-chip > span { display: inline-grid; place-items: center; width: 1.75rem; height: 1.75rem; border-radius: 50%; background: var(--sap-blue-soft); color: var(--sap-blue); font-weight: 700; }
.work-nav { min-height: 2.75rem; display: flex; align-items: stretch; gap: .25rem; padding: 0 1.25rem; background: var(--sap-shell); border-bottom: 1px solid var(--sap-border); position: sticky; top: 3rem; z-index: 20; }
.work-nav > a, .work-nav > details > summary { min-height: 2.75rem; display: inline-flex; align-items: center; padding: 0 .8rem; color: var(--sap-text); cursor: pointer; list-style: none; border-bottom: 3px solid transparent; }
.work-nav > a[aria-current="page"] { color: var(--sap-blue); border-bottom-color: var(--sap-blue); font-weight: 700; }
.work-nav details { position: relative; }
.work-nav summary::-webkit-details-marker { display: none; }
.nav-popover { position: absolute; left: 0; top: calc(100% - .15rem); min-width: 10rem; padding: .35rem; background: #fff; border: 1px solid var(--sap-border); border-radius: .5rem; box-shadow: 0 8px 24px rgba(34,53,72,.16); }
.nav-popover a { display: block; padding: .55rem .7rem; color: var(--sap-text); border-radius: .3rem; }
.nav-popover a:hover { background: var(--sap-blue-soft); text-decoration: none; }
.page-shell { max-width: 1440px; margin: 0 auto; padding: 1.5rem; }
.page-header { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; margin-bottom: 1rem; }
.page-header h1 { margin: 0; font-size: clamp(1.55rem, 2vw, 2rem); font-weight: 500; }
.page-eyebrow { margin: 0 0 .25rem; color: var(--sap-blue); font-size: .75rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; }
.page-subtitle { margin: .35rem 0 0; color: var(--sap-muted); }
.content-card, .action-panel, .panel, section { background: var(--sap-surface); border: 1px solid var(--sap-border); border-radius: .5rem; box-shadow: var(--shadow-card); }
.content-card, section { margin-bottom: 1rem; padding: 1rem; }
.action-panel { margin-bottom: 1rem; }
.action-panel > summary { cursor: pointer; padding: .9rem 1rem; color: var(--sap-blue); font-weight: 700; }
.action-panel[open] > summary { border-bottom: 1px solid var(--sap-border); }
.action-panel__body { padding: 1rem; }
.filter-bar, .row { display: flex; flex-wrap: wrap; gap: .75rem; align-items: end; }
.filter-bar { margin-bottom: 1rem; padding: .8rem; background: #fff; border: 1px solid var(--sap-border); border-radius: .5rem; }
label { display: flex; flex-direction: column; gap: .3rem; color: var(--sap-muted); font-size: .78rem; font-weight: 600; }
input, select { min-height: 2.25rem; border: 1px solid var(--sap-border-strong); border-radius: .375rem; padding: .4rem .6rem; font: inherit; background: #fff; color: var(--sap-text); }
input:focus, select:focus, button:focus, summary:focus, a:focus { outline: 2px solid var(--sap-blue); outline-offset: 2px; }
button, .button { display: inline-flex; align-items: center; justify-content: center; min-height: 2.25rem; border: 1px solid var(--sap-blue); border-radius: .375rem; padding: .45rem .8rem; background: var(--sap-blue); color: #fff; font-weight: 700; cursor: pointer; }
button:hover, .button:hover { background: var(--sap-blue-hover); text-decoration: none; }
.button--secondary { background: #fff; color: var(--sap-blue); }
.button--negative, button[value="반려"] { border-color: var(--sap-negative); background: #fff; color: var(--sap-negative); }
.table-shell { width: 100%; overflow-x: auto; border: 1px solid var(--sap-border); border-radius: .375rem; background: #fff; }
.data-table, table { width: 100%; min-width: 720px; border-collapse: separate; border-spacing: 0; }
.data-table th, .data-table td, th, td { padding: .65rem .75rem; border-bottom: 1px solid var(--sap-border); text-align: left; white-space: nowrap; }
.data-table th, th { background: #f7f7f7; color: var(--sap-muted); font-size: .75rem; font-weight: 700; }
.data-table tbody tr:hover td, tbody tr:hover td { background: #f5faff; }
.data-table thead .group-heading { text-align: center; color: var(--sap-text); background: #edf4fb; }
.business-id { color: var(--sap-blue); font-weight: 700; }
.status-badge { display: inline-flex; align-items: center; min-height: 1.5rem; border-radius: 999px; padding: .15rem .55rem; font-size: .75rem; font-weight: 700; }
.status-badge--positive { color: var(--sap-positive); background: var(--sap-positive-bg); }
.status-badge--info { color: var(--sap-info); background: var(--sap-info-bg); }
.status-badge--warning { color: var(--sap-warning); background: var(--sap-warning-bg); }
.status-badge--negative { color: var(--sap-negative); background: var(--sap-negative-bg); }
.status-badge--neutral { color: var(--sap-muted); background: #eef0f2; }
.message-strip { display: flex; align-items: center; gap: .55rem; margin-bottom: 1rem; padding: .75rem .9rem; border-radius: .375rem; border: 1px solid; background: #fff; }
.message-strip--success { color: var(--sap-positive); border-color: #5dc122; background: var(--sap-positive-bg); }
.message-strip--error { color: var(--sap-negative); border-color: #ff8888; background: var(--sap-negative-bg); }
.message-strip__icon { display: inline-grid; place-items: center; width: 1.25rem; height: 1.25rem; border-radius: 50%; border: 1px solid currentColor; font-weight: 700; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: .75rem; margin-bottom: 1rem; }
.kpi-card { background: #fff; border: 1px solid var(--sap-border); border-radius: .5rem; padding: 1rem; box-shadow: var(--shadow-card); }
.kpi-card__value { font-size: 1.75rem; color: var(--sap-blue); font-weight: 600; }
.kpi-card__label { color: var(--sap-muted); font-size: .8rem; }
.attention-grid, .grid2, .grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
.attention-item { display: flex; justify-content: space-between; gap: 1rem; padding: .7rem 0; border-bottom: 1px solid var(--sap-border); }
.attention-item:last-child { border-bottom: 0; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); gap: .75rem; }
.detail-item { padding: .75rem; border: 1px solid var(--sap-border); border-radius: .375rem; background: #fff; }
.detail-item__label { display: block; margin-bottom: .25rem; color: var(--sap-muted); font-size: .75rem; }
.empty-state { padding: 2rem !important; text-align: center !important; color: var(--sap-muted); }
.muted { color: var(--sap-muted); }
.mono, code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre { overflow-x: auto; padding: 1rem; border-radius: .375rem; background: #1d2d3e; color: #f7f7f7; }
@media (min-width: 900px) {
  .sticky-id { position: sticky; left: 0; z-index: 2; background: #fff; }
  .sticky-name { position: sticky; left: 6.5rem; z-index: 2; background: #fff; box-shadow: 2px 0 0 var(--sap-border); }
  thead .sticky-id, thead .sticky-name { z-index: 3; background: #f7f7f7; }
}
@media (max-width: 800px) {
  .shellbar { grid-template-columns: 1fr auto; }
  .shell-search { grid-column: 1 / -1; grid-row: 2; }
  .work-nav { overflow-x: auto; position: static; }
  .page-shell { padding: 1rem; }
  .attention-grid, .grid2, .grid-2 { grid-template-columns: 1fr; }
  .shellbar__actions a { display: none; }
}
```

- [ ] **Step 6: Run all web console render tests**

Run:

```bash
.venv/bin/python -m unittest api.tests.test_app.WebConsoleTests
```

Expected: all web console tests pass while old page templates continue to render
inside the new shell.

- [ ] **Step 7: Commit**

```bash
git add api/templates/_ui.html api/templates/base.html api/static/styles.css api/tests/test_app.py
git commit -m "Add SuccessFactors-inspired UI system

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>
Copilot-Session: ba94d4ac-6efc-4486-b024-d0cf36bad1de"
```

---

### Task 3: Redesign the Dashboard and Fix Its Merged Columns

**Files:**
- Modify: `api/tests/test_app.py:142-150`
- Replace: `api/templates/dashboard.html:1-73`

**Interfaces:**
- Consumes: `summary` from `db.get_dashboard_summary()` and Task 1 appointment display-name fields.
- Produces: quick-action links, KPI cards, attention items, atomic department/employee columns, grouped appointment before/after columns.

- [ ] **Step 1: Write failing dashboard structure tests**

Add:

```python
def test_dashboard_uses_hybrid_home_layout(self):
    html = self.client.get("/").text
    self.assertIn('class="quick-actions"', html)
    self.assertIn('class="kpi-grid"', html)
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
    self.assertNotIn("E0034 방통상", html)
```

- [ ] **Step 2: Run dashboard tests and confirm failure**

Run:

```bash
.venv/bin/python -m unittest \
  api.tests.test_app.WebConsoleTests.test_dashboard_uses_hybrid_home_layout \
  api.tests.test_app.WebConsoleTests.test_dashboard_uses_atomic_identity_and_org_columns
```

Expected: missing quick-action/KPI classes and atomic/grouped headers.

- [ ] **Step 3: Replace dashboard markup**

Import shared macros:

```jinja2
{% from "_ui.html" import page_header, status_badge, empty_row %}
```

Use the approved order:

```jinja2
{{ page_header('인사 운영 현황', '조직·휴가·교육·근태의 주요 지표와 처리 항목을 한눈에 확인합니다.', '홈') }}

<section class="content-card quick-actions" aria-labelledby="quick-actions-title">
  <h2 id="quick-actions-title">빠른 작업</h2>
  <div class="row">
    <a class="button" href="/employees#employee-action">사원 등록</a>
    <a class="button button--secondary" href="/leave#leave-action">휴가 신청</a>
    <a class="button button--secondary" href="/attendance#attendance-action">근태 기록</a>
    <a class="button button--secondary" href="/appointments#appointment-action">인사발령</a>
  </div>
</section>

<div class="kpi-grid">
  <article class="kpi-card"><div class="kpi-card__value">{{ summary.active_total }}</div><div class="kpi-card__label">재직 사원</div></article>
  <article class="kpi-card"><div class="kpi-card__value">{{ summary.dept_total }}</div><div class="kpi-card__label">운영 부서</div></article>
  <article class="kpi-card"><div class="kpi-card__value">{{ summary.pending_leave }}</div><div class="kpi-card__label">휴가 승인 대기</div></article>
  <article class="kpi-card"><div class="kpi-card__value">{{ summary.leave_usage_pct }}%</div><div class="kpi-card__label">연차 사용률</div></article>
  <article class="kpi-card"><div class="kpi-card__value">{{ summary.training_mandatory.rate }}%</div><div class="kpi-card__label">필수교육 이수율</div></article>
</div>
```

Create the attention card with deterministic values already present in
`summary`:

```jinja2
{% set ns = namespace(attendance_exceptions=0) %}
{% for item in summary.attendance_today %}
  {% if item.status != '정상' %}
    {% set ns.attendance_exceptions = ns.attendance_exceptions + item.records %}
  {% endif %}
{% endfor %}
<section class="content-card">
  <h2>확인이 필요합니다</h2>
  <div class="attention-grid">
    <a class="attention-item" href="/leave?status_filter=신청">
      <span>휴가 승인 대기</span><strong>{{ summary.pending_leave }}건</strong>
    </a>
    <a class="attention-item" href="/courses?status_filter=미이수">
      <span>필수교육 미이수</span>
      <strong>{{ summary.training_mandatory.total - summary.training_mandatory.completed }}건</strong>
    </a>
    <a class="attention-item" href="/attendance?work_date={{ summary.attendance_date or '' }}">
      <span>최근 근무일 근태 예외</span><strong>{{ ns.attendance_exceptions }}건</strong>
    </a>
  </div>
</section>
```

Retain these summary sections with the exact headings `부서별 인원`,
`직급별 인원`, `재직 상태`, `고용형태(재직)`, and `최근 근무일 근태`.
Split department headcount into:

```jinja2
<th>부서코드</th><th>부서명</th><th>재직인원</th>
```

For every appointment row, define after-value fallbacks before rendering:

```jinja2
{% set after_dept_code = r.to_dept or r.from_dept %}
{% set after_dept_name = r.to_dept_name or r.from_dept_name %}
{% set after_position_code = r.to_position or r.from_position %}
{% set after_position_name = r.to_position_name or r.from_position_name %}
```

Render grouped headings:

```jinja2
<thead>
  <tr>
    <th rowspan="2" class="sticky-id">사번</th>
    <th rowspan="2" class="sticky-name">이름</th>
    <th rowspan="2">발령일</th>
    <th rowspan="2">유형</th>
    <th colspan="4" class="group-heading">변경 전</th>
    <th colspan="4" class="group-heading">변경 후</th>
    <th rowspan="2">비고</th>
  </tr>
  <tr>
    <th>부서코드</th><th>부서명</th><th>직급코드</th><th>직급명</th>
    <th>부서코드</th><th>부서명</th><th>직급코드</th><th>직급명</th>
  </tr>
</thead>
```

Render employee cells separately and use `—` for missing before values:

```jinja2
<td class="sticky-id business-id">{{ r.emp_id }}</td>
<td class="sticky-name">{{ r.emp_name or '—' }}</td>
<td>{{ r.from_dept or '—' }}</td>
<td>{{ r.from_dept_name or '—' }}</td>
<td>{{ r.from_position or '—' }}</td>
<td>{{ r.from_position_name or '—' }}</td>
<td>{{ after_dept_code or '—' }}</td>
<td>{{ after_dept_name or '—' }}</td>
<td>{{ after_position_code or '—' }}</td>
<td>{{ after_position_name or '—' }}</td>
```

Split recent leave rows into `사번`, `이름`, `부서코드`, `부서명`, `유형`,
`시작일`, `종료일`, `일수`, `상태`, `사유`, `ID`, and render status through
`status_badge(r.status)`.

- [ ] **Step 4: Run dashboard and all web tests**

Run:

```bash
.venv/bin/python -m unittest api.tests.test_app.WebConsoleTests
```

Expected: all web tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/templates/dashboard.html api/tests/test_app.py
git commit -m "Redesign HR dashboard workspace

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>
Copilot-Session: ba94d4ac-6efc-4486-b024-d0cf36bad1de"
```

---

### Task 4: Migrate Employee, Department, and Position Workspaces

**Files:**
- Modify: `api/tests/test_app.py`
- Replace: `api/templates/employees.html`
- Replace: `api/templates/departments.html`
- Replace: `api/templates/positions.html`

**Interfaces:**
- Consumes: shared macros/classes from Task 2 and `parent_dept_name` from Task 1.
- Produces: administrator workspaces with collapsed forms, filter bars, and atomic master-data tables.

- [ ] **Step 1: Write failing master-workspace tests**

Add:

```python
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
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python -m unittest \
  api.tests.test_app.WebConsoleTests.test_employee_table_uses_atomic_code_name_columns \
  api.tests.test_app.WebConsoleTests.test_department_table_splits_parent_and_manager_identity \
  api.tests.test_app.WebConsoleTests.test_position_page_uses_shared_workspace_components
```

Expected: missing atomic headers and workspace classes.

- [ ] **Step 3: Migrate the employee page**

Use:

```jinja2
{% from "_ui.html" import page_header, status_badge, empty_row %}
{{ page_header('사원 관리', '사원 마스터와 재직 상태를 조회하고 신규 입사를 처리합니다.', '내 직원') }}
<details class="action-panel" id="employee-action">
  <summary>＋ 사원 등록</summary>
  <div class="action-panel__body">
    <form method="post" action="/employees/hire" class="row">
      <label>이름 <input name="name" required></label>
      <label>부서
        <select name="dept_code" required>
          {% for d in departments %}<option value="{{ d.dept_code }}">{{ d.dept_code }} · {{ d.dept_name }}</option>{% endfor %}
        </select>
      </label>
      <label>직급
        <select name="position_code" required>
          {% for p in positions %}<option value="{{ p.position_code }}">{{ p.position_code }} · {{ p.position_name }}</option>{% endfor %}
        </select>
      </label>
      <label>고용형태
        <select name="employment_type"><option>정규직</option><option>계약직</option><option>인턴</option></select>
      </label>
      <label>입사일 <input type="date" name="hire_date"></label>
      <label>이메일 <input name="email"></label>
      <label>연락처 <input name="phone"></label>
      <label>관리자
        <select name="manager_emp_id"><option value="">없음</option>
          {% for m in managers %}<option value="{{ m.emp_id }}">{{ m.emp_id }} · {{ m.name }}</option>{% endfor %}
        </select>
      </label>
      <button type="submit">입사 처리</button>
    </form>
  </div>
</details>
```

Use this exact GET filter structure:

```jinja2
<form method="get" action="/employees" class="filter-bar">
  <label>부서
    <select name="dept_code"><option value="">전체</option>
      {% for d in departments %}<option value="{{ d.dept_code }}" {% if filters.dept_code == d.dept_code %}selected{% endif %}>{{ d.dept_code }} · {{ d.dept_name }}</option>{% endfor %}
    </select>
  </label>
  <label>직급
    <select name="position_code"><option value="">전체</option>
      {% for p in positions %}<option value="{{ p.position_code }}" {% if filters.position_code == p.position_code %}selected{% endif %}>{{ p.position_code }} · {{ p.position_name }}</option>{% endfor %}
    </select>
  </label>
  <label>상태
    <select name="status_filter"><option value="">전체</option>
      {% for value in ['재직', '휴직', '퇴직'] %}<option {% if filters.status_filter == value %}selected{% endif %}>{{ value }}</option>{% endfor %}
    </select>
  </label>
  <label>고용형태
    <select name="employment_type"><option value="">전체</option>
      {% for value in ['정규직', '계약직', '인턴'] %}<option {% if filters.employment_type == value %}selected{% endif %}>{{ value }}</option>{% endfor %}
    </select>
  </label>
  <label>검색 <input name="q" value="{{ filters.q }}" placeholder="이름, 사번, 이메일"></label>
  <button type="submit">조회</button>
</form>
```

Use combined select labels:

```jinja2
{{ d.dept_code }} · {{ d.dept_name }}
{{ p.position_code }} · {{ p.position_name }}
{{ m.emp_id }} · {{ m.name }}
```

Render exact columns:

```text
사번 | 이름 | 부서코드 | 부서명 | 직급코드 | 직급명 | 고용형태 |
상태 | 입사일 | 관리자 사번 | 관리자 이름
```

Use `status_badge(r.status)`, link the business ID to employee detail, and use
`empty_row(11, '조건에 맞는 사원이 없습니다.')`.

- [ ] **Step 4: Migrate department and position pages**

Department columns:

```text
부서코드 | 부서명 | 상위부서코드 | 상위부서명 |
부서장 사번 | 부서장 이름 | 코스트센터 | 재직인원
```

Use the existing `depth` to indent only the department-name cell. Render
`parent_dept_name` from Task 1. Keep the existing headcount summary as a second
card with `부서코드 | 부서명 | 재직인원`.

Position columns remain:

```text
직급코드 | 직급명 | 레벨 | 기본 연차(일) | 재직인원
```

Wrap each table in `table-shell`, add `data-table`, and use `empty_row`.

- [ ] **Step 5: Run web tests**

Run:

```bash
.venv/bin/python -m unittest api.tests.test_app.WebConsoleTests
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add api/templates/employees.html api/templates/departments.html \
  api/templates/positions.html api/tests/test_app.py
git commit -m "Migrate HR master data workspaces

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>
Copilot-Session: ba94d4ac-6efc-4486-b024-d0cf36bad1de"
```

---

### Task 5: Migrate Training, Leave, Attendance, and Appointment Workspaces

**Files:**
- Modify: `api/tests/test_app.py`
- Replace: `api/templates/courses.html`
- Replace: `api/templates/leave.html`
- Replace: `api/templates/attendance.html`
- Replace: `api/templates/appointments.html`

**Interfaces:**
- Consumes: existing form actions and data rows; shared macros; appointment display names.
- Produces: collapsed operational forms, compact filters, atomic tables, semantic badges, and grouped appointment history.

- [ ] **Step 1: Write failing operational-workspace tests**

Add:

```python
def test_training_table_splits_employee_and_course_columns(self):
    html = self.client.get("/courses").text
    for heading in ("사번", "이름", "과정코드", "과정명"):
        self.assertIn(heading, html)
    self.assertIn('id="training-action"', html)

def test_leave_tables_split_employee_department_and_approver_columns(self):
    html = self.client.get("/leave").text
    for heading in (
        "사번", "이름", "부서코드", "부서명",
        "승인자 사번", "승인자 이름",
    ):
        self.assertIn(heading, html)
    self.assertIn('id="leave-action"', html)

def test_attendance_table_splits_employee_and_department_columns(self):
    html = self.client.get("/attendance").text
    for heading in ("사번", "이름", "부서코드", "부서명"):
        self.assertIn(heading, html)
    self.assertIn('id="attendance-action"', html)

def test_appointment_table_has_grouped_before_after_columns(self):
    html = self.client.get("/appointments").text
    self.assertIn("변경 전", html)
    self.assertIn("변경 후", html)
    self.assertGreaterEqual(html.count("부서코드"), 2)
    self.assertGreaterEqual(html.count("직급명"), 2)
    self.assertIn('id="appointment-action"', html)
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python -m unittest \
  api.tests.test_app.WebConsoleTests.test_training_table_splits_employee_and_course_columns \
  api.tests.test_app.WebConsoleTests.test_leave_tables_split_employee_department_and_approver_columns \
  api.tests.test_app.WebConsoleTests.test_attendance_table_splits_employee_and_department_columns \
  api.tests.test_app.WebConsoleTests.test_appointment_table_has_grouped_before_after_columns
```

Expected: missing atomic/grouped headers and action-panel anchors.

- [ ] **Step 3: Migrate training**

Use `id="training-action"` on a collapsed action panel and keep
`POST /courses/enroll`. Keep the course catalog columns atomic as they already
are. Render training records with:

```text
사번 | 이름 | 과정코드 | 과정명 | 구분 | 신청일 | 이수일 |
상태 | 점수 | 처리 | ID
```

Use `status_badge(t.status)`. Keep the inline `POST /courses/{id}/complete`
controls and label employee/course selects as `CODE · Name`.

- [ ] **Step 4: Migrate leave**

Use `id="leave-action"` on a collapsed action panel and keep
`POST /leave/submit`.

Request columns:

```text
사번 | 이름 | 부서코드 | 부서명 | 유형 | 시작일 | 종료일 | 일수 |
상태 | 사유 | 승인자 사번 | 승인자 이름 | 처리 | ID
```

Balance columns:

```text
사번 | 이름 | 부서코드 | 부서명 | 연도 | 부여 | 사용 | 잔여
```

Use `status_badge(r.status)`. Keep approval/rejection actions unchanged, but
label approver options `{{ e.emp_id }} · {{ e.name }}`.

- [ ] **Step 5: Migrate attendance**

Use `id="attendance-action"` on a collapsed action panel and keep
`POST /attendance/log`. Render summary status with `status_badge(s.status)`.

Record columns:

```text
사번 | 이름 | 부서코드 | 부서명 | 근무일 | 출근 | 퇴근 |
근무시간 | 연장시간 | 상태
```

Use combined employee select labels and semantic status badges.

- [ ] **Step 6: Migrate appointments**

Use `id="appointment-action"` on a collapsed action panel and keep
`POST /appointments/create`. Label employee/department/position options with
`CODE · Name`.

Define the appointment fallback values in this template:

```jinja2
{% set after_dept_code = r.to_dept or r.from_dept %}
{% set after_dept_name = r.to_dept_name or r.from_dept_name %}
{% set after_position_code = r.to_position or r.from_position %}
{% set after_position_name = r.to_position_name or r.from_position_name %}
```

Render this grouped header:

```jinja2
<thead>
  <tr>
    <th rowspan="2" class="sticky-id">사번</th>
    <th rowspan="2" class="sticky-name">이름</th>
    <th rowspan="2">발령일</th>
    <th rowspan="2">유형</th>
    <th colspan="4" class="group-heading">변경 전</th>
    <th colspan="4" class="group-heading">변경 후</th>
    <th rowspan="2">비고</th>
    <th rowspan="2">ID</th>
  </tr>
  <tr>
    <th>부서코드</th><th>부서명</th><th>직급코드</th><th>직급명</th>
    <th>부서코드</th><th>부서명</th><th>직급코드</th><th>직급명</th>
  </tr>
</thead>
```

Render:

```text
사번 | 이름 | 발령일 | 유형 |
변경 전(부서코드 | 부서명 | 직급코드 | 직급명) |
변경 후(부서코드 | 부서명 | 직급코드 | 직급명) |
비고 | ID
```

- [ ] **Step 7: Run web tests**

Run:

```bash
.venv/bin/python -m unittest api.tests.test_app.WebConsoleTests
```

Expected: all tests pass, including existing form-transaction tests.

- [ ] **Step 8: Commit**

```bash
git add api/templates/courses.html api/templates/leave.html \
  api/templates/attendance.html api/templates/appointments.html \
  api/tests/test_app.py
git commit -m "Migrate HR operational workspaces

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>
Copilot-Session: ba94d4ac-6efc-4486-b024-d0cf36bad1de"
```

---

### Task 6: Migrate Employee Detail and Guide

**Files:**
- Modify: `api/tests/test_app.py`
- Replace: `api/templates/employee_detail.html`
- Replace: `api/templates/guide.html`

**Interfaces:**
- Consumes: enriched `emp.appointments`, existing `emp` detail object, and shared macros/styles.
- Produces: profile-oriented employee detail and a card/step guide.

- [ ] **Step 1: Write failing detail/guide tests**

Add:

```python
def test_employee_detail_uses_atomic_profile_fields(self):
    emp_id = self.db.list_employee_ids()[0]
    html = self.client.get(f"/employees/{emp_id}").text
    for label in (
        "부서코드", "부서명", "직급코드", "직급명",
        "관리자 사번", "관리자 이름",
    ):
        self.assertIn(label, html)
    self.assertIn('class="profile-header"', html)
    self.assertIn("변경 전", html)
    self.assertIn("변경 후", html)

def test_guide_uses_step_cards(self):
    html = self.client.get("/guide").text
    self.assertIn('class="guide-steps"', html)
    self.assertIn("1", html)
    self.assertIn("입사", html)
    self.assertIn("인사발령", html)
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python -m unittest \
  api.tests.test_app.WebConsoleTests.test_employee_detail_uses_atomic_profile_fields \
  api.tests.test_app.WebConsoleTests.test_guide_uses_step_cards
```

Expected: missing profile/guide classes and atomic labels.

- [ ] **Step 3: Redesign employee detail**

Build a profile header:

```jinja2
<header class="profile-header">
  <div class="profile-avatar" aria-hidden="true">{{ emp.name[:1] }}</div>
  <div>
    <p class="page-eyebrow">사원 프로필</p>
    <h1>{{ emp.name }}</h1>
    <p class="page-subtitle">{{ emp.emp_id }} · {{ emp.dept_name or '부서 미지정' }} · {{ emp.position_name or '직급 미지정' }}</p>
  </div>
  {{ status_badge(emp.status) }}
</header>
```

Add the exact profile styles:

```css
.profile-header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
  padding: 1.25rem;
  background: var(--sap-surface);
  border: 1px solid var(--sap-border);
  border-radius: .5rem;
  box-shadow: var(--shadow-card);
}
.profile-header h1 { margin: 0; font-size: 1.75rem; font-weight: 500; }
.profile-header .status-badge { margin-left: auto; }
.profile-avatar {
  display: inline-grid;
  place-items: center;
  width: 4rem;
  height: 4rem;
  flex: 0 0 4rem;
  border-radius: 50%;
  background: var(--sap-blue-soft);
  color: var(--sap-blue);
  font-size: 1.5rem;
  font-weight: 700;
}
```
Render profile fields as separate detail items:

```text
사번 | 이름 | 부서코드 | 부서명 | 직급코드 | 직급명 |
고용형태 | 상태 | 입사일 | 생년월일 | 이메일 | 연락처 |
관리자 사번 | 관리자 이름
```

Split training course code/name. Use badges in attendance, training, and leave
history. Render appointment history with the Task 3 grouped before/after table
and fallback rules.

- [ ] **Step 4: Redesign guide**

Keep every existing explanation and endpoint. Render the five workflow steps as
ordered cards inside:

```jinja2
<ol class="guide-steps">
  <li><span class="guide-step__number">1</span><div><strong>입사</strong><p>부서·직급 지정, 연차 자동 부여, 입사 발령 기록</p></div></li>
  <li><span class="guide-step__number">2</span><div><strong>교육</strong><p>수강신청과 이수·미이수 처리</p></div></li>
  <li><span class="guide-step__number">3</span><div><strong>연차·휴가</strong><p>신청·승인·반려와 잔여 연차 차감</p></div></li>
  <li><span class="guide-step__number">4</span><div><strong>근태</strong><p>출퇴근 기록과 근무시간·상태 자동 계산</p></div></li>
  <li><span class="guide-step__number">5</span><div><strong>인사발령</strong><p>승진·이동·휴직·복직·퇴직 반영</p></div></li>
</ol>
```

Add `.guide-steps`, `.guide-step__number`, `.profile-header`, and
`.profile-avatar` styles to `styles.css`.

- [ ] **Step 5: Run all web tests**

Run:

```bash
.venv/bin/python -m unittest api.tests.test_app.WebConsoleTests
```

Expected: all web tests pass.

- [ ] **Step 6: Commit**

```bash
git add api/templates/employee_detail.html api/templates/guide.html \
  api/static/styles.css api/tests/test_app.py
git commit -m "Polish employee profile and HR guide

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>
Copilot-Session: ba94d4ac-6efc-4486-b024-d0cf36bad1de"
```

---

### Task 7: Full Regression, Documentation, Image Build, and Azure Rollout

**Files:**
- Modify: `README.md` (web-console description only)
- Verify: all changed files

**Interfaces:**
- Consumes: completed UI and unchanged API/MCP/deployment contracts.
- Produces: published GHCR images and a verified live ACA revision.

- [ ] **Step 1: Update README web-console description**

Add this paragraph under the web console surface:

```markdown
The web console uses a SuccessFactors-inspired hybrid experience: a
Morning Horizon-style card dashboard for quick actions and attention items,
with compact administrator workspaces for master and operational records.
All read-only tables keep identifiers/codes and names in separate columns.
```

- [ ] **Step 2: Run the complete test suite**

Run:

```bash
.venv/bin/python -m unittest hr_core.test_db api.tests.test_app mcp_server.test_server
```

Expected: all tests pass; the count is greater than the original 61 because of
new display-contract and HTML structure tests.

- [ ] **Step 3: Seed and run the app locally**

Run:

```bash
rm -f data/hr_ui_e2e.db*
HR_DB_PATH="$(pwd)/data/hr_ui_e2e.db" .venv/bin/python -m hr_core.seed
HR_DB_PATH="$(pwd)/data/hr_ui_e2e.db" HR_API_KEY=changjuahn \
  .venv/bin/python -m uvicorn api.main:app --port 8000
```

Expected: Uvicorn listens on `http://127.0.0.1:8000`.

- [ ] **Step 4: Verify all web pages and unchanged REST behavior**

Check in the browser:

```text
/
/employees
/employees/E0001
/departments
/positions
/courses
/leave
/attendance
/appointments
/guide
```

Confirm:

- shell/navigation and global employee search work;
- dashboard cards and grouped appointment columns render;
- every table splits code/ID and name columns;
- action panels open and submit;
- horizontal table scrolling works at narrow widths;
- no merged `E0001 김대표`, `D110 인사팀`, or `P4 과장` cells remain in read-only tables.

Run:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/employees
curl -s -H 'X-API-Key: changjuahn' http://localhost:8000/api/health
```

Expected: `200`, `401`, then JSON with `"status":"ok"` and the seeded counts.

- [ ] **Step 5: Build both container images locally**

Run:

```bash
docker build -t mock-hr-app:ui .
docker build -t mock-hr-proxy:ui proxy
```

Expected: both builds complete successfully.

- [ ] **Step 6: Clean visual-companion and E2E artifacts**

Stop the visual companion and remove only local artifacts:

```bash
/Users/changjuahn/.copilot/installed-plugins/superpowers-marketplace/superpowers/skills/brainstorming/scripts/stop-server.sh \
  /Users/changjuahn/Repo/mock-hr-kr/.superpowers/brainstorm/52295-1784674843
rm -rf .superpowers
rm -f data/hr_ui_e2e.db*
```

Expected: `git status --short` does not show `.superpowers/` or database files.

- [ ] **Step 7: Commit documentation and push**

```bash
git add README.md
git commit -m "Document SuccessFactors-inspired web console

Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>
Copilot-Session: ba94d4ac-6efc-4486-b024-d0cf36bad1de"
git push origin main
```

- [ ] **Step 8: Wait for GHCR image workflow**

Run:

```bash
RUN_ID="$(gh run list --repo ChangJu-Ahn/mock-hr-kr --workflow build-images --limit 1 --json databaseId -q '.[0].databaseId')"
gh run watch "$RUN_ID" --repo ChangJu-Ahn/mock-hr-kr --exit-status
```

Expected: both `mock-hr-app` and `mock-hr-proxy` jobs complete successfully.

- [ ] **Step 9: Roll a fresh Azure Container Apps revision**

Run:

```bash
az containerapp update \
  -g rg-mock-hr-kr \
  -n mock-hr \
  --revision-suffix "ui$(date +%y%m%d%H%M%S)"
```

Expected: update succeeds and the app remains at:

```text
https://mock-hr.whitehill-65406bed.koreacentral.azurecontainerapps.io/
```

- [ ] **Step 10: Verify live web, REST, and MCP**

Run:

```bash
FQDN="mock-hr.whitehill-65406bed.koreacentral.azurecontainerapps.io"
curl -s -o /dev/null -w '%{http_code}\n' "https://$FQDN/"
curl -s -o /dev/null -w '%{http_code}\n' "https://$FQDN/api/employees"
curl -s -H 'X-API-Key: changjuahn' "https://$FQDN/api/health"
```

Expected: web `200`, unauthenticated REST `401`, authenticated health JSON with
the deterministic seed counts.

Run this MCP smoke client:

```bash
FQDN="mock-hr.whitehill-65406bed.koreacentral.azurecontainerapps.io" \
.venv/bin/python - <<'PY'
import asyncio
import os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    url = f"https://{os.environ['FQDN']}/mcp"
    async with streamablehttp_client(
        url, headers={"X-API-Key": "changjuahn"}
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 9, [tool.name for tool in tools.tools]
            result = await session.call_tool(
                "get_attendance_summary", {"month": "2026-07"}
            )
            assert result.structuredContent
            print("MCP tools:", len(tools.tools))

asyncio.run(main())
PY
```

Expected: `MCP tools: 9`.

- [ ] **Step 11: Final repository check**

Run:

```bash
git status -sb
git --no-pager log --oneline -8
```

Expected: `main...origin/main` with no uncommitted files.
