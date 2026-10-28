"""Model runtime DTOs, manifest validation and output checks. Pure: no env, no I/O."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field, fields
from typing import Any, NoReturn, Protocol

ID_RE = re.compile(r"[a-z][a-z0-9-]{0,63}")
RESERVED_TASK_IDS = frozenset({"provider-advisor"})
ROLES = frozenset({"provider", "planner", "model_worker", "auxiliary_b", "auxiliary_a"})
EFFORTS = frozenset({"low", "medium", "high"})
KINDS = frozenset({"draft"})
FIELD_TYPES = frozenset({"string", "array", "object", "boolean", "number", "integer"})
MANIFEST_KEYS = frozenset({"schema", "id", "tasks", "max_calls", "max_parallel", "advisor"})
MAX_TASKS = 12
MAX_INSTRUCTION = 12000
MAX_OUTPUT_CHARS = 200_000


@dataclass
class Task:
    id: str
    role: str
    instruction: str
    inputs: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    required: dict[str, str] = field(default_factory=dict)
    effort: str = "medium"
    max_tokens: int = 2000
    timeout: int = 60
    max_attempts: int = 1
    kind: str = "draft"


@dataclass
class Reply:
    kind: str
    data: dict | None = None
    code: str = ""
    meta: dict = field(default_factory=dict)


class Transport(Protocol):
    def group_key(self, role: str) -> str: ...

    async def send(self, task: Task, inputs: dict) -> Reply: ...


TASK_KEYS = frozenset(f.name for f in fields(Task))
# name: (default, min, max); values must be type int exactly (bool rejected)
INT_FIELDS = {"max_tokens": (2000, 128, 6000), "timeout": (60, 10, 300), "max_attempts": (1, 1, 2)}


class ManifestError(ValueError):
    pass


def _fail(msg: str) -> NoReturn:
    raise ManifestError(msg)


def _strict_int(value: Any, name: str, lo: int, hi: int) -> int:
    if type(value) is not int or not lo <= value <= hi:
        _fail(f"{name} must be an int in {lo}..{hi}")
    return value


def _ident(value: Any, name: str) -> str:
    if type(value) is not str or not ID_RE.fullmatch(value):
        _fail(f"{name} must match [a-z][a-z0-9-]{{0,63}}")
    return value


def _choice(value: Any, name: str, allowed: frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        _fail(f"{name} must be one of {sorted(allowed)}")
    return value


def _reject_unknown(raw: dict, allowed: frozenset[str], where: str) -> None:
    extra = [k for k in raw if k not in allowed]
    if extra:
        _fail(f"{where}: unknown fields {sorted(map(repr, extra))}")


def _parse_task(raw: Any, where: str, roles=ROLES) -> Task:
    if type(raw) is not dict:
        _fail(f"{where} must be an object")
    _reject_unknown(raw, TASK_KEYS, where)
    for key in ("id", "role", "instruction", "required"):
        if key not in raw:
            _fail(f"{where}.{key} is required")
    tid = _ident(raw["id"], f"{where}.id")
    if tid in RESERVED_TASK_IDS:
        _fail(f"{where}.id {tid!r} is reserved")
    where = f"task {tid}"
    role = _choice(raw["role"], f"{where}.role", roles)
    instruction = raw["instruction"]
    if type(instruction) is not str or not 1 <= len(instruction) <= MAX_INSTRUCTION:
        _fail(f"{where}.instruction must be a string of 1..{MAX_INSTRUCTION} chars")
    inputs = raw.get("inputs", {})
    if type(inputs) is not dict or any(type(k) is not str for k in inputs):
        _fail(f"{where}.inputs must be an object with string keys")
    deps = raw.get("depends_on", [])
    if type(deps) is not list:
        _fail(f"{where}.depends_on must be a list")
    for dep in deps:
        _ident(dep, f"{where}.depends_on item")
    if len(set(deps)) != len(deps):
        _fail(f"{where}.depends_on has duplicates")
    if tid in deps:
        _fail(f"{where} depends on itself")
    required = raw["required"]
    if type(required) is not dict or not required:
        _fail(f"{where}.required must be a non-empty object")
    for name, kind in required.items():
        if type(name) is not str or not name:
            _fail(f"{where}.required field names must be non-empty strings")
        _choice(kind, f"{where}.required[{name!r}]", FIELD_TYPES)
    ints = {k: _strict_int(raw.get(k, d), f"{where}.{k}", lo, hi) for k, (d, lo, hi) in INT_FIELDS.items()}
    return Task(
        id=tid,
        role=role,
        instruction=instruction,
        inputs=copy.deepcopy(inputs),
        depends_on=list(deps),
        required=dict(required),
        effort=_choice(raw.get("effort", "medium"), f"{where}.effort", EFFORTS),
        kind=_choice(raw.get("kind", "draft"), f"{where}.kind", KINDS),
        **ints,
    )


def _check_acyclic(by_id: dict[str, Task]) -> None:
    state: dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(tid: str, path: list[str]) -> None:
        mark = state.get(tid)
        if mark == 2:
            return
        if mark == 1:
            _fail(f"dependency cycle: {' -> '.join(path + [tid])}")
        state[tid] = 1
        for dep in by_id[tid].depends_on:
            visit(dep, path + [tid])
        state[tid] = 2

    for tid in by_id:
        visit(tid, [])


def parse_manifest(value: Any, roles=None) -> dict:
    if type(value) is not dict:
        _fail("manifest must be an object")
    _reject_unknown(value, MANIFEST_KEYS, "manifest")
    schema = value.get("schema")
    if type(schema) is not int or schema != 1:
        _fail("schema must be the int 1")
    mid = _ident(value.get("id"), "id")
    advisor = value.get("advisor", True)
    if type(advisor) is not bool:
        _fail("advisor must be a bool")
    max_calls = _strict_int(value.get("max_calls", 6), "max_calls", 2 if advisor else 1, 20)
    max_parallel = value.get("max_parallel", 2)
    if type(max_parallel) is not int or max_parallel not in (1, 2):
        _fail("max_parallel must be the int 1 or 2")
    raw_tasks = value.get("tasks")
    if type(raw_tasks) is not list or not 1 <= len(raw_tasks) <= MAX_TASKS:
        _fail(f"tasks must be a list of 1..{MAX_TASKS} tasks")
    by_id: dict[str, Task] = {}
    for i, raw in enumerate(raw_tasks):
        task = _parse_task(raw, f"tasks[{i}]", ROLES if roles is None else frozenset(roles))
        if task.id in by_id:
            _fail(f"duplicate task id {task.id!r}")
        by_id[task.id] = task
    for task in by_id.values():
        for dep in task.depends_on:
            if dep not in by_id:
                _fail(f"task {task.id} depends on unknown task {dep!r}")
    _check_acyclic(by_id)
    return {
        "id": mid,
        "max_calls": max_calls,
        "max_parallel": max_parallel,
        "advisor": advisor,
        "tasks": list(by_id.values()),
    }


_TYPE_CHECKS = {
    "string": lambda v: type(v) is str,
    "array": lambda v: type(v) is list,
    "object": lambda v: type(v) is dict,
    "boolean": lambda v: type(v) is bool,
    "number": lambda v: type(v) in (int, float),
    "integer": lambda v: type(v) is int,
}


def valid_output(value: Any, required: Any) -> bool:
    try:
        if type(value) is not dict or type(required) is not dict:
            return False
        for name, kind in required.items():
            check = _TYPE_CHECKS.get(kind) if type(kind) is str else None
            if check is None or name not in value or not check(value[name]):
                return False
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return len(encoded) <= MAX_OUTPUT_CHARS
    except Exception:
        return False
