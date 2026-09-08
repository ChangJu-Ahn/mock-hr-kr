"""MCP specification introspection powering the ``/mcp-docs`` reference tab.

The REST surface documents itself through OpenAPI (``/api/openapi.json`` →
``/api/docs``). MCP has no equivalent static document, so this module derives an
OpenAPI-shaped reference straight from the **live** ``FastMCP`` server defined in
``mcp_server.server``: the same object that answers ``initialize`` and
``tools/list`` on ``/mcp``.

Because every field below is read from that object (never hand-copied), the MCP
docs tab cannot drift from the tools the endpoint actually advertises.
"""

from __future__ import annotations

import json
import re
from typing import Any

from mcp_server import server as mcp_server
from hr_core import db

# The MCP revision the bundled SDK negotiates, and the transport/session shape
# implied by how `mcp_server.server.mcp` is constructed.
from mcp.types import LATEST_PROTOCOL_VERSION

API_KEY_HEADER = "X-API-Key"
MCP_PATH = "/mcp"

#: Tool name -> the console page covering the same business function.
_TOOL_CONSOLE_PAGE = {
    "log_attendance": ("/attendance", "근태"),
    "list_attendance": ("/attendance", "근태"),
    "get_attendance_summary": ("/attendance", "근태"),
    "submit_leave_request": ("/leave", "연차·휴가"),
    "decide_leave_request": ("/leave", "연차·휴가"),
    "get_leave_balance": ("/leave", "연차·휴가"),
    "list_leave_requests": ("/leave", "연차·휴가"),
    "list_employees": ("/employees", "사원"),
    "get_employee": ("/employees", "사원"),
}

#: Ordered tool groups. Any tool missing here lands in the trailing "기타" group.
_TOOL_GROUPS: list[tuple[str, str, str, tuple[str, ...]]] = [
    (
        "attendance",
        "근태 Attendance",
        "출퇴근 기록과 근무·연장 시간 집계. 상태(정상/지각/조퇴/결근/휴가/재택)는 자동 분류됩니다.",
        ("log_attendance", "list_attendance", "get_attendance_summary"),
    ),
    (
        "leave",
        "연차·휴가 Leave",
        "휴가 신청·승인·반려와 잔여 연차 조회. 승인 시 잔여 연차가 차감되며 부족하면 차단됩니다.",
        ("submit_leave_request", "decide_leave_request", "get_leave_balance", "list_leave_requests"),
    ),
    (
        "employee",
        "사원 Employee",
        "사원 조회·입사·수정·참조 없는 사원 삭제. 입사 시 생성된 연차와 발령도 삭제 방어 대상입니다.",
        ("list_employees", "get_employee", "create_employee", "update_employee", "delete_employee"),
    ),
    ("department", "부서 Department", "조직 기준정보 CRUD. 참조 중인 부서 삭제와 조직 순환은 차단합니다.",
     ("list_departments", "get_department", "create_department", "update_department", "delete_department")),
    ("position", "직급 Position", "직급·기본 연차 CRUD. 사원·발령이 참조 중이면 삭제할 수 없습니다.",
     ("list_positions", "get_position", "create_position", "update_position", "delete_position")),
    ("course", "과정 Course", "교육과정 CRUD. 교육이력이 있는 과정은 삭제할 수 없습니다.",
     ("list_courses", "get_course", "create_course", "update_course", "delete_course")),
]

for _singular, _plural, _label in (
    ("employee", "employees", "사원"), ("department", "departments", "부서"),
    ("position", "positions", "직급"), ("course", "courses", "교육"),
):
    for _name in (f"list_{_plural}", *(f"{action}_{_singular}"
                                      for action in ("get", "create", "update", "delete"))):
        _TOOL_CONSOLE_PAGE[_name] = (f"/{_plural}", _label)

#: Parameter name -> realistic example value, so generated snippets are runnable
#: against the seeded demo dataset.
_EXAMPLE_BY_NAME = {
    "emp_id": "E0001",
    "approver_emp_id": "E0002",
    "manager_emp_id": "E0002",
    "request_id": 1,
    "work_date": "2026-07-20",
    "start_date": "2026-08-03",
    "end_date": "2026-08-04",
    "date_from": "2026-07-01",
    "date_to": "2026-07-31",
    "check_in": "09:05",
    "check_out": "18:30",
    "month": "2026-07",
    "year": 2026,
    "dept_code": "D200",
    "position_code": "P3",
    "leave_type": "연차",
    "decision": "승인",
    "employment_type": "정규직",
    "reason": "가족 여행",
    "days": 1.0,
    "q": "김",
    "name": "연결 테스트 사원",
    "dept_name": "연결 테스트 부서",
    "position_name": "연결 테스트 직급",
    "course_code": "C001",
    "course_name": "연결 테스트 과정",
}

#: Enumerations the underlying HR domain accepts, surfaced per parameter because
#: the generated JSON Schema types them as plain strings.
_ALLOWED_VALUES = {
    ("log_attendance", "status"): ["정상", "지각", "조퇴", "결근", "휴가", "재택"],
    ("list_attendance", "status"): ["정상", "지각", "조퇴", "결근", "휴가", "재택"],
    ("submit_leave_request", "leave_type"): ["연차", "반차", "병가", "경조사", "공가"],
    ("list_leave_requests", "leave_type"): ["연차", "반차", "병가", "경조사", "공가"],
    ("list_leave_requests", "status"): ["신청", "승인", "반려", "취소"],
    ("decide_leave_request", "decision"): ["승인", "반려", "취소"],
    ("list_employees", "status"): ["재직", "휴직", "퇴직"],
}

_SUMMARY_RE = re.compile(r"^\s*(?P<summary>[^(]+)\((?P<name>[A-Za-z_][A-Za-z0-9_]*)\)\s*:\s*(?P<detail>.*)$", re.S)


def _live_defaults() -> dict[str, Any]:
    """Sample values taken from the seeded dataset so the examples really run.

    The docs are useless if copy-pasting an example returns "not found", so the
    identifiers that must exist (employee, department, a *pending* leave
    request, a work date with records) are read from the live database. Any
    failure falls back to the static samples — the page must never break.
    """
    values: dict[str, Any] = {}
    try:
        employees = db.list_employees(status="재직", limit=1)
        if employees:
            values["emp_id"] = employees[0]["emp_id"]
        headcounts = [d for d in db.headcount_by_dept() if d.get("headcount")]
        if headcounts:
            values["dept_code"] = max(headcounts, key=lambda d: d["headcount"])["dept_code"]
        pending = db.list_leave_requests(status="신청", limit=1)
        if pending:
            values["request_id"] = pending[0]["id"]
        work_date = db.latest_work_date()
        if work_date:
            values["work_date"] = work_date
            values["month"] = work_date[:7]
            values["start_date"] = work_date
            values["end_date"] = work_date
            values["date_from"] = work_date[:7] + "-01"
            values["date_to"] = work_date
        positions = db.list_positions()
        courses = db.list_courses()
        if positions:
            values["position_code"] = positions[0]["position_code"]
        if courses:
            values["course_code"] = courses[0]["course_code"]
        for field, codes, prefix in (
            ("dept_code", db.list_department_codes(), "D_DEMO"),
            ("position_code", db.list_position_codes(), "P_DEMO"),
            ("course_code", db.list_course_codes(), "C_DEMO"),
        ):
            existing = set(codes)
            suffix = 1
            while f"{prefix}{suffix}" in existing:
                suffix += 1
            values[f"new_{field}"] = f"{prefix}{suffix}"
    except Exception:  # pragma: no cover - docs must render even without data
        pass
    return values


def _split_description(tool_name: str, description: str) -> tuple[str, str]:
    """Split ``"근태 기록(log_attendance): upsert ..."`` into (summary, detail).

    The tool descriptions follow a ``한글 요약(tool_name): English detail``
    convention. When a description does not, the whole string becomes the detail
    and the tool name is used as the summary.
    """
    match = _SUMMARY_RE.match(description or "")
    if match and match.group("name") == tool_name:
        return match.group("summary").strip(), " ".join(match.group("detail").split())
    return tool_name, " ".join((description or "").split())


def _type_label(schema: dict[str, Any]) -> str:
    """Render a JSON Schema fragment as a short, human-readable type label."""
    if not isinstance(schema, dict):
        return "any"
    if "anyOf" in schema:
        parts = [_type_label(s) for s in schema["anyOf"] if s.get("type") != "null"]
        return " | ".join(dict.fromkeys(parts)) or "null"
    kind = schema.get("type")
    if kind == "array":
        return f"array<{_type_label(schema.get('items', {}))}>"
    if kind == "object":
        return "object"
    if isinstance(kind, list):
        return " | ".join(k for k in kind if k != "null") or "null"
    return kind or "any"


def _is_nullable(schema: dict[str, Any]) -> bool:
    return any(s.get("type") == "null" for s in schema.get("anyOf", []) if isinstance(s, dict))


def _parameters(tool_name: str, input_schema: dict[str, Any]) -> list[dict[str, Any]]:
    required = set(input_schema.get("required") or [])
    params: list[dict[str, Any]] = []
    for name, schema in (input_schema.get("properties") or {}).items():
        schema = schema if isinstance(schema, dict) else {}
        default = schema.get("default")
        has_default = "default" in schema and default is not None
        params.append({
            "name": name,
            "type": _type_label(schema),
            "required": name in required,
            "nullable": _is_nullable(schema),
            "default": default,
            "has_default": has_default,
            "default_display": _json(default, indent=None) if has_default else "—",
            "allowed": _ALLOWED_VALUES.get((tool_name, name)),
        })
    # Required parameters first, then declaration order.
    params.sort(key=lambda p: not p["required"])
    return params


def _example_value(param: dict[str, Any], live: dict[str, Any]) -> Any:
    name = param["name"]
    if name in live:
        return live[name]
    if name in _EXAMPLE_BY_NAME:
        return _EXAMPLE_BY_NAME[name]
    allowed = param.get("allowed")
    if allowed:
        return allowed[0]
    return {"string": "…", "integer": 1, "number": 1.0, "boolean": True,
            "object": {}, "array": []}.get(param["type"].split(" | ")[0], "…")


def _example_arguments(tool_name: str, params: list[dict[str, Any]],
                       live: dict[str, Any]) -> dict[str, Any]:
    """Build a runnable argument object: every required parameter, plus the
    optional ones that make the example meaningfully demonstrate the tool."""
    highlight = {
        "log_attendance": {"check_in", "check_out"},
        "list_attendance": {"work_date"},
        "get_attendance_summary": {"month"},
        "submit_leave_request": {"end_date", "reason"},
        "list_leave_requests": {"status"},
        "list_employees": {"dept_code", "status"},
    }.get(tool_name, set())
    example: dict[str, Any] = {}
    for param in params:
        if param["required"] or param["name"] in highlight:
            example[param["name"]] = _example_value(param, live)
    for singular, field, label in (
        ("employee", "name", "사원"), ("department", "dept_name", "부서"),
        ("position", "position_name", "직급"), ("course", "course_name", "과정"),
    ):
        if tool_name == f"update_{singular}":
            example["changes"] = {field: f"외부 수정 {label}"}
    for singular, field, prefix in (
        ("department", "dept_code", "D_DEMO"), ("position", "position_code", "P_DEMO"),
        ("course", "course_code", "C_DEMO"),
    ):
        if tool_name == f"create_{singular}":
            example[field] = live.get(f"new_{field}", f"{prefix}1")
    return example


def _json(value: Any, indent: int | None = 2) -> str:
    """Pretty JSON that keeps Hangul readable in the copy-paste snippets."""
    return json.dumps(value, ensure_ascii=False, indent=indent)


def _output(output_schema: dict[str, Any] | None) -> dict[str, Any]:
    """Describe how the tool's result arrives in ``structuredContent``.

    FastMCP wraps non-object returns: a tool returning ``list[dict]`` is
    advertised as ``{"result": [...]}``. Agents need to know which shape to
    unwrap, so this is called out explicitly.
    """
    schema = output_schema or {}
    props = schema.get("properties") or {}
    if list(props) == ["result"]:
        inner = props["result"]
        return {
            "kind": "wrapped",
            "type": _type_label(inner),
            "note": "리스트 반환은 structuredContent.result 로 감싸여 옵니다.",
        }
    return {
        "kind": "object",
        "type": _type_label(schema) if schema else "object",
        "note": "structuredContent 가 결과 객체 그 자체입니다.",
    }


async def build_spec(base_url: str = "") -> dict[str, Any]:
    """Assemble the full MCP reference document from the live server object."""
    base_url = base_url.rstrip("/")
    tools = await mcp_server.mcp.list_tools()
    live = _live_defaults()

    documented: dict[str, dict[str, Any]] = {}
    for tool in tools:
        input_schema = tool.inputSchema or {}
        output_schema = getattr(tool, "outputSchema", None)
        summary, detail = _split_description(tool.name, tool.description or "")
        params = _parameters(tool.name, input_schema)
        example_args = _example_arguments(tool.name, params, live)
        page, page_label = _TOOL_CONSOLE_PAGE.get(tool.name, ("", ""))
        documented[tool.name] = {
            "name": tool.name,
            "summary": summary,
            "description": detail,
            "read_only": tool.name.startswith(("list_", "get_")),
            "parameters": params,
            "required": [p["name"] for p in params if p["required"]],
            "example_arguments": example_args,
            "example_arguments_json": _json(example_args),
            "example_call_json": _json({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool.name, "arguments": example_args},
            }),
            "example_python": (
                f'result = await session.call_tool(\n'
                f'    "{tool.name}",\n'
                f'    {_json(example_args, indent=None)},\n'
                f')\n'
                f'print(result.structuredContent)'
            ),
            "input_schema": input_schema,
            "input_schema_json": _json(input_schema),
            "output_schema": output_schema,
            "output_schema_json": _json(output_schema) if output_schema else "",
            "output": _output(output_schema),
            "console_page": page,
            "console_label": page_label,
        }

    grouped, seen = [], set()
    for key, title, description, names in _TOOL_GROUPS:
        members = [documented[n] for n in names if n in documented]
        seen.update(n for n in names if n in documented)
        if members:
            grouped.append({"key": key, "title": title, "description": description, "tools": members})
    leftovers = [t for name, t in documented.items() if name not in seen]
    if leftovers:
        grouped.append({"key": "other", "title": "기타 Other", "description": "", "tools": leftovers})

    endpoint = f"{base_url}{MCP_PATH}" if base_url else MCP_PATH
    return {
        "server": {
            "name": mcp_server.mcp.name,
            "version": _sdk_version(),
            "protocol_version": LATEST_PROTOCOL_VERSION,
            "transport": "Streamable HTTP",
            "stateless": True,
            "endpoint": endpoint,
            "base_url": base_url,
            "auth": {
                "header": API_KEY_HEADER,
                "key": mcp_server.DEFAULT_API_KEY,
                "note": "키가 없거나 틀리면 401을 반환합니다. 이 문서 페이지는 키 없이 열람할 수 있습니다.",
            },
        },
        "capabilities": [
            {"name": "tools", "supported": True, "detail": f"{len(tools)}개 도구 · listChanged=false"},
            {"name": "resources", "supported": False, "detail": "노출된 리소스 없음 (resources/list → [])"},
            {"name": "prompts", "supported": False, "detail": "노출된 프롬프트 없음 (prompts/list → [])"},
            {"name": "sampling", "supported": False, "detail": "서버가 클라이언트에 LLM 호출을 요청하지 않음"},
        ],
        "methods": [
            {"method": "initialize", "detail": "프로토콜 버전·capabilities·serverInfo 협상"},
            {"method": "tools/list", "detail": f"도구 {len(tools)}개와 JSON Schema 반환"},
            {"method": "tools/call", "detail": "도구 실행 → content(텍스트) + structuredContent(JSON)"},
            {"method": "resources/list", "detail": "빈 배열 (이 서버는 리소스를 제공하지 않음)"},
            {"method": "prompts/list", "detail": "빈 배열 (이 서버는 프롬프트를 제공하지 않음)"},
        ],
        "tool_count": len(tools),
        "groups": grouped,
        "tools": list(documented.values()),
        "snippets": _snippets(endpoint, mcp_server.DEFAULT_API_KEY),
    }


def _snippets(endpoint: str, api_key: str) -> list[dict[str, str]]:
    """Copy-paste connection recipes for the common MCP clients."""
    return [
        {
            "key": "vscode",
            "title": "VS Code · GitHub Copilot",
            "file": ".vscode/mcp.json",
            "language": "json",
            "note": "streamable HTTP를 기본 지원합니다. 워크스페이스에 저장 후 서버를 시작하세요.",
            "code": _json({
                "servers": {
                    "mock-hr": {
                        "type": "http",
                        "url": endpoint,
                        "headers": {API_KEY_HEADER: api_key},
                    }
                }
            }),
        },
        {
            "key": "claude",
            "title": "Claude Desktop",
            "file": "claude_desktop_config.json",
            "language": "json",
            "note": "원격 HTTP 서버는 mcp-remote 브리지를 통해 연결합니다.",
            "code": _json({
                "mcpServers": {
                    "mock-hr": {
                        "command": "npx",
                        "args": ["-y", "mcp-remote", endpoint,
                                 "--header", f"{API_KEY_HEADER}:{api_key}"],
                    }
                }
            }),
        },
        {
            "key": "python",
            "title": "Python SDK",
            "file": "client.py",
            "language": "python",
            "note": "공식 mcp 패키지의 streamable HTTP 클라이언트입니다.",
            "code": (
                "import asyncio\n"
                "from mcp import ClientSession\n"
                "from mcp.client.streamable_http import streamablehttp_client\n\n"
                "async def main():\n"
                f'    url = "{endpoint}"\n'
                f'    headers = {{"{API_KEY_HEADER}": "{api_key}"}}\n'
                "    async with streamablehttp_client(url, headers=headers) as (r, w, _):\n"
                "        async with ClientSession(r, w) as session:\n"
                "            await session.initialize()\n"
                "            tools = await session.list_tools()\n"
                "            print([t.name for t in tools.tools])\n"
                '            out = await session.call_tool("get_leave_balance", {"emp_id": "E0001"})\n'
                "            print(out.structuredContent)\n\n"
                "asyncio.run(main())"
            ),
        },
        {
            "key": "curl",
            "title": "curl · 원시 JSON-RPC",
            "file": "tools/list",
            "language": "bash",
            "note": ("stateless 서버라 initialize 없이 바로 호출할 수 있고 세션 ID도 필요 없습니다. "
                     "응답은 text/event-stream(SSE) 프레임으로 돌아옵니다."),
            "code": (
                f"curl -N -X POST {endpoint} \\\n"
                f'  -H "{API_KEY_HEADER}: {api_key}" \\\n'
                '  -H "Content-Type: application/json" \\\n'
                '  -H "Accept: application/json, text/event-stream" \\\n'
                "  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'"
            ),
        },
    ]


def _sdk_version() -> str:
    try:
        from importlib.metadata import version

        return version("mcp")
    except Exception:  # pragma: no cover - metadata always present in the image
        return "unknown"


__all__ = ["API_KEY_HEADER", "MCP_PATH", "build_spec"]
