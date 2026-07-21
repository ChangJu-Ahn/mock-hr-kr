# SuccessFactors-Inspired UI Redesign

**Date:** 2026-07-22  
**Status:** Approved  
**Scope:** Web console presentation only; existing HR processes and interfaces remain compatible

## 1. Goal

Redesign the Mock HR web console so it feels familiar to users of SAP
SuccessFactors while preserving every existing HR process, URL, form action,
REST endpoint, MCP tool, and database transaction.

The target is a **hybrid SuccessFactors experience**:

- a Latest Home Page-style dashboard with quick actions, KPI cards, and
  attention items;
- dense administrator workspaces for employee, organization, training, leave,
  attendance, and appointment operations.

The implementation uses public SAP Fiori / Morning Horizon interaction and
visual conventions as inspiration, but must not copy SAP logos, proprietary
assets, screenshots, or product-specific branding. The product remains
**Mock HR**.

## 2. Approved Direction

Three directions were considered:

1. **Latest Home Page only** — closest to employee self-service, but adds
   navigation depth to administrator tasks.
2. **Administrator workspace only** — best data density, but misses the familiar
   card-based SuccessFactors home experience.
3. **Hybrid** — approved. The home page is card- and attention-oriented, while
   operational pages retain high-density filters, forms, and tables.

## 3. Global Shell and Visual Language

Use a light Morning Horizon/Fiori-inspired system:

- white shell bar and work navigation;
- blue primary/accent color;
- neutral gray page background;
- low-elevation cards, thin borders, and moderate corner radius;
- content-first typography with strong headings and compact table text;
- clear focus rings and accessible contrast;
- status colors accompanied by text, never color alone.

The shell contains:

- `Mock HR` brand;
- a global search form that submits to the existing `/employees?q=` route;
- help and user affordances;
- grouped work navigation:
  `홈 / 사원 / 조직 / 교육 / 휴가·근태 / 인사관리 / API`.

All existing destinations remain reachable. Navigation labels can be grouped,
but routes do not change.

## 4. Dashboard Information Architecture

The dashboard is reorganized in this order:

1. page greeting and date/context;
2. quick actions for common operations;
3. KPI cards (active employees, pending approvals, leave use, mandatory
   training);
4. needs-attention items (leave approvals, training incompletion, attendance
   exceptions);
5. organizational and attendance summaries;
6. recent appointments and leave requests.

Cards provide summary and navigation; administrator actions still use the
existing forms and routes.

## 5. Operational Page Pattern

Every administrator page follows one predictable pattern:

1. page title, purpose text, and primary action;
2. native `<details>` action panel for create/update forms, collapsed by default;
3. compact filter bar;
4. data table;
5. empty and error states.

Filters and the list are visible immediately without opening the action panel.
No new frontend framework or SAP component library is introduced; shared Jinja
markup, native HTML behavior, and CSS classes provide the design system.

## 6. Data Display Rules

### 6.1 Atomic columns

All **read-only tables** separate identifiers and human-readable names:

- `사번 | 이름`
- `부서코드 | 부서명`
- `직급코드 | 직급명`

This applies to dashboard tables and the employee, department, position,
training, leave, attendance, appointment, and employee-detail views wherever
the fields are relevant.

Input and filter controls deliberately retain combined labels for
identification:

- `E0001 · 김대표`
- `D110 · 인사팀`
- `P4 · 과장`

### 6.2 Appointment history

Appointment tables use grouped headers:

- employee: `사번`, `이름`;
- change metadata: `발령일`, `유형`;
- before: department code/name and position code/name;
- after: department code/name and position code/name;
- note.

Display values follow explicit event rules:

- hire: before values are `—`; after values show the hired department and
  position;
- promotion: department before/after is unchanged; position shows from/to;
- transfer: position before/after is unchanged; department shows from/to;
- leave, return, and termination: department and position before/after are
  unchanged; the appointment type conveys the employment-status transition.

This makes every record understandable without decoding raw codes or mistaking
an unchanged value for missing data.

Wide tables use horizontal scrolling. On viewports at least 900px wide, CSS
keeps `사번` and `이름` sticky. Internal database IDs are de-emphasized or moved
after business columns.

### 6.3 Status badges

Use semantic text badges consistently:

- positive: `재직`, `승인`, `이수`, `정상`;
- information/in progress: `신청`, `수강중`, `재택`;
- warning: `휴직`, `지각`, `조퇴`;
- negative: `반려`, `퇴직`, `미이수`, `결근`.

## 7. Data and Compatibility

The SQLite schema and all business rules remain unchanged.

Appointment reads are enriched with joins to department and position tables so
templates can show names alongside codes. Existing REST response fields remain
unchanged; the response adds `from_dept_name`, `to_dept_name`,
`from_position_name`, and `to_position_name`. MCP contracts and tools remain
unchanged.

Form methods, action URLs, redirect behavior, and transaction semantics remain
unchanged:

- hire still creates the employee, annual leave, and hire appointment;
- leave approval still blocks on insufficient balance;
- attendance still derives hours and status;
- appointments still apply promotion, transfer, leave, return, and termination
  effects.

## 8. Error, Empty, and Responsive States

- Success and failure messages appear in a shell-level message strip with icon,
  text, and semantic color.
- Field labels remain visible and keyboard focus is obvious.
- Empty tables show a plain-language empty state.
- Tables scroll horizontally on narrow screens rather than crushing columns.
- Navigation and page actions wrap or collapse on small screens.
- Dates, hours, quantities, and statuses keep their existing values and meaning.

## 9. Implementation Boundaries

Primary files:

- `api/templates/base.html`
- `api/static/styles.css`
- all templates in `api/templates/`
- `hr_core/db.py` for additive appointment display joins
- REST response models only where additive display-name fields are required
- existing tests plus focused display/join tests

No API, MCP, route, schema, seed-count, or deployment architecture redesign is
in scope.

## 10. Verification and Delivery

1. Run all existing database, app, and MCP tests.
2. Add tests for appointment department/position names and separated table
   headers.
3. Verify dashboard, employees, departments, positions, training, leave,
   attendance, appointments, employee detail, and guide locally.
4. Verify form submissions and redirect/error behavior.
5. Build and publish both existing container images.
6. Deploy a fresh Azure Container Apps revision.
7. Verify the live web console, REST authentication/health, and MCP tool list.

## 11. Public Design References

- SAP Help Portal, *SAP Fiori Visual Themes in SAP SuccessFactors*:  
  https://help.sap.com/docs/successfactors-platform/managing-sap-successfactors-user-experience/sap-fiori-visual-themes-in-sap-successfactors
- SAP Help Portal, *Latest Home Page Experience*:  
  https://help.sap.com/docs/successfactors-platform/managing-sap-successfactors-user-experience/latest-home-page-experience
