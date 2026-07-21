# mock-hr-kr — Design Spec

**Date:** 2026-07-21
**Status:** Approved
**Author:** ChangJu-Ahn (with Copilot)

## 1. Purpose

A **throwaway, on-demand mock HR system (HRIS)** that exists purely as a
**connection point for agent demos**. It mirrors the `mock-mes-kr`
architecture **1:1** — same API + MCP + container-based SQLite + web console +
Azure Container Apps deployment — swapping only the business domain from
semiconductor-fab **MES** to general-enterprise **HR**.

The system is **MVP / demo only**: the database is ephemeral and re-seeded
identically on every cold start, and a single shared demo key gates the agent
surfaces. There is no real security, no persistence guarantee, and no
multi-tenant concern.

## 2. Scope

**In scope** — a general HRIS covering:
사원(employee) · 부서(department) · 직급(position) · 교육(training/course) ·
연차·휴가(leave) · 근태(attendance) · 인사발령(appointment).

**Out of scope** — payroll, recruiting/ATS, performance reviews, benefits
administration, real authentication/authorization, and any data persistence
beyond a single container lifetime.

## 3. Architecture

Identical topology to `mock-mes-kr`: a **single** Azure Container App
(Consumption, one replica, **scale-to-zero**) whose containers share one
**ephemeral `EmptyDir` volume** at `/data` holding `hr.db`.

- **seed** (init container) — `python -m hr_core.seed`; builds the deterministic
  dataset once, then exits.
- **api** — FastAPI/Uvicorn `:8000`; serves the web console (`/`) and REST (`/api`).
- **mcp** — MCP streamable-HTTP server `:8001` at `/mcp`.
- **proxy** — Caddy `:8080`; single external ingress, routes `/mcp*` → mcp,
  `/*` → api.

Two public GHCR images (`mock-hr-app`, `mock-hr-proxy`) are built by GitHub
Actions; Azure needs no registry credentials. Each container is sized at the
ACA minimum (0.25 vCPU / 0.5 GiB); `minReplicas=0` (~$0 idle), `maxReplicas=1`
(the shared `EmptyDir` is per-replica).

### Naming swaps from MES

`mes_core`→`hr_core` · `MES_DB_PATH`→`HR_DB_PATH` (`/data/hr.db`) ·
`MES_API_KEY`→`HR_API_KEY` · `mock-mes`→`mock-hr` · `rg-mock-mes-kr`→`rg-mock-hr-kr`.

## 4. Data model (9 tables)

`department`, `position`, `employee`, `course`, `training_record`,
`leave_balance`, `leave_request`, `attendance`, `appointment`.

The org backbone is `department` (self-referencing tree via `parent_dept_code`,
each with a `manager_emp_id`), `position` (rank + `min_leave_days`), and
`employee` (status `재직`/`휴직`/`퇴직`). Learning is `course` +
`training_record`. Operational records are `leave_balance` (per emp/year),
`leave_request`, `attendance` (one row per emp/day), and `appointment`
(personnel actions). Full column detail lives in `README.md`.

## 5. Surface split

The three surfaces deliberately divide responsibility so a demo can show an
agent using **REST for the system of record** and **MCP for day-to-day
operations**, while a human watches the **web console**.

- **Web console** (`/`, open) — every HR function (view + input) plus a dashboard.
- **REST API** (`/api`, `X-API-Key`) — **system of record / master data**:
  Employee · Department · Position · Course · Training · Appointment.
- **MCP server** (`/mcp`, `X-API-Key`) — **operational**: Attendance · Leave,
  plus employee lookup for context. Nine tools.

## 6. Key transactions & business rules

- **hire_employee** — atomically creates the employee (`재직`), auto-assigns the
  next `emp_id` (`E0001`…), grants an annual-leave balance
  (`entitled = position.min_leave_days`), and writes an `입사` appointment.
- **log_attendance** — upserts one row per (emp_id, work_date); derives
  `work_hours` (minus 1h lunch) and `overtime`, and auto-classifies status
  (`지각`/`조퇴`/`결근`/`정상`) unless an explicit status is given.
- **submit_leave_request** — computes `days` from the inclusive date span
  (반차 = 0.5); returns a **non-blocking** balance warning.
- **decide_leave_request** — `승인` decrements remaining balance and **blocks**
  (error, no change) if insufficient; `반려`/`취소` leave the balance untouched;
  only `신청` requests can be decided.
- **create_appointment** — applies the action's effect: `승진`→position,
  `부서이동`→department, `휴직`→status 휴직, `복직`→재직, `퇴직`→퇴직.

## 7. Determinism

`hr_core/seed.py` uses a fixed `random.Random(42)` and a fixed
`REFERENCE_DATE (2026-07-17)`, so every cold start yields an identical snapshot:
10 departments, 7 positions, 34 employees (재직 31 / 휴직 1 / 퇴직 2), 10 courses,
212 training records, 23 leave requests, 310 attendance rows, 43 appointments.
The unit tests pin these counts.

## 8. Testing

Three unittest modules mirror the MES layout:
- `hr_core/test_db.py` — schema, data-access, transactions, and pinned seed counts.
- `api/tests/test_app.py` — REST + web console via FastAPI `TestClient`.
- `mcp_server/test_server.py` — MCP tool functions + the API-key guard.

Run: `python -m unittest hr_core.test_db api.tests.test_app mcp_server.test_server`.

## 9. Deliverable

A standalone repository `ChangJu-Ahn/mock-hr-kr`, structured identically to
`mock-mes-kr`, with its own GHCR images and its own ACA app (`mock-hr` in
`rg-mock-hr-kr`). Deployment is **documented but not auto-run**.
