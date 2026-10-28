from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from collections.abc import Mapping
from urllib.parse import urlsplit

from public_output import redact_public

_NAME = re.compile(r"^FF_[A-Z0-9_]+$")
_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_REF = _NAME
_MAX_MANIFEST = 200 * 1024
_BUILTINS = {
    "provider": ("FF_COORD_MODEL_NAME", "FF_COORD_MODEL", "anthropic"),
    "planner": ("FF_MODEL_NAME", "FF_MODEL", "openai"),
    "model_worker": ("FF_WORKER_MODEL_NAME", "FF_WORKER_MODEL", "openai"),
    "auxiliary_a": ("FF_AUX_MODEL_NAME", "FF_AUX_MODEL", "openai"),
    "auxiliary_b": ("FF_AUX_MODEL_NAME", "FF_AUX_MODEL", "openai"),
}


def _bad(code="invalid manifest"):
    raise ValueError(code)


def _safe_file(path: Path):
    path = Path(path)
    current = Path(path.anchor) if path.is_absolute() else Path(".")
    parts = path.parts[1:] if path.is_absolute() else path.parts
    if not parts:
        parts = (".",)
    for part in parts:
        if part == ".":
            continue
        if part == "..":
            _bad("invalid file")
        current /= part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            return False
        except OSError:
            _bad("invalid file")
        attrs = getattr(st, "st_file_attributes", 0)
        if stat.S_ISLNK(st.st_mode) or (attrs & 0x400):
            _bad("invalid file")
    if not stat.S_ISREG(st.st_mode):
        _bad("invalid file")
    return True


def load_env(path: Path) -> dict:
    path = Path(path)
    if not _safe_file(path):
        return {}
    try:
        if os.lstat(path).st_size > _MAX_MANIFEST:
            _bad("invalid env")
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        _bad("invalid env")
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not _NAME.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name] = value
    return out


def _ref(value, suffix):
    if not isinstance(value, str) or not _REF.fullmatch(value) or not value.endswith(suffix):
        _bad("invalid manifest")
    return value


def _base(value, required=False):
    if not isinstance(value, str) or (required and not value):
        _bad("invalid manifest")
    if not value:
        return ""
    try:
        p = urlsplit(value)
    except ValueError:
        _bad("invalid base url")
    if p.scheme != "https" or not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment:
        _bad("invalid base url")
    try:
        p.port
    except ValueError:
        _bad("invalid base url")
    return value


def _parse_models(value, roles=None):
    if isinstance(value, Mapping):
        obj = value
    else:
        if isinstance(value, bytes):
            if len(value) > _MAX_MANIFEST:
                _bad("invalid manifest")
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                _bad("invalid manifest")
        if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_MANIFEST:
            _bad("invalid manifest")
        try:
            obj = json.loads(value, parse_constant=lambda _: _bad("invalid manifest"))
        except (TypeError, ValueError, json.JSONDecodeError):
            _bad("invalid manifest")
    if not isinstance(obj, dict) or set(obj) != {"schema", "models"} or type(obj["schema"]) is not int or obj["schema"] != 1 or not isinstance(obj["models"], list) or len(obj["models"]) > 24:
        _bad("invalid manifest")
    result, seen = [], set()
    for item in obj["models"]:
        if not isinstance(item, dict):
            _bad("invalid manifest")
        allowed = {"id", "key_env", "base_env", "model_env", "model", "protocol", "capabilities"}
        if set(item) - allowed or {"id", "key_env", "base_env", "protocol", "capabilities"} - set(item):
            _bad("invalid manifest")
        if ("model_env" in item) == ("model" in item):
            _bad("invalid manifest")
        ident = item["id"]
        if not isinstance(ident, str) or not _ALIAS.fullmatch(ident) or ident in seen or ident in _BUILTINS:
            _bad("invalid manifest")
        seen.add(ident)
        key = _ref(item["key_env"], "_API_KEY")
        base = _ref(item["base_env"], "_BASE_URL")
        menv = item.get("model_env")
        if menv is not None and (not isinstance(menv, str) or not _REF.fullmatch(menv)):
            _bad("invalid manifest")
        model = item.get("model")
        if model is not None and (not isinstance(model, str) or not model):
            _bad("invalid manifest")
        protocol = item["protocol"]
        if protocol not in ("openai", "anthropic"):
            _bad("invalid manifest")
        caps = item["capabilities"]
        if not isinstance(caps, list) or any(not isinstance(x, str) for x in caps):
            _bad("invalid manifest")
        result.append({"id": ident, "key_env": key, "base_env": base, "model_env": menv, "model": model, "protocol": protocol, "capabilities": list(caps), "builtin": False})
    return result


def _redact(value, secrets=()):
    if isinstance(value, dict):
        return {_redact(k, secrets): _redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, secrets) for v in value]
    redacted = redact_public(value)
    if isinstance(redacted, str):
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


class Registry:
    def __init__(self, entries):
        self._entries = entries

    def ids(self):
        return frozenset(self._entries)

    def describe(self):
        out = []
        for ident, e in self._entries.items():
            c = e["config"]
            host = ""
            if c.get("base"):
                try:
                    host = urlsplit(c["base"]).hostname or ""
                except ValueError:
                    host = ""
            out.append({"id": ident, "protocol": c["protocol"], "model": _redact(c["model"]), "host": _redact(host), "capabilities": list(e["capabilities"]), "builtin": e["builtin"], "configured": bool(c["key"] and c["base"] and c["model"])})
        secrets = [e["config"]["key"] for e in self._entries.values() if e["config"]["key"]]
        return _redact(out, secrets)

    def transport_config(self):
        return {k: {"base": v["config"]["base"], "model": v["config"]["model"], "key": v["config"]["key"], "protocol": v["config"]["protocol"]} for k, v in self._entries.items() if v["config"]["key"] and v["config"]["base"] and v["config"]["model"]}


def load_registry(env: Mapping[str, str], manifest_path: Path | None = None) -> Registry:
    env = {k: v for k, v in env.items() if isinstance(k, str) and isinstance(v, str)}
    entries = {}
    for ident, (model_env, prefix, protocol) in _BUILTINS.items():
        model = "auxiliary_b-k3" if ident == "auxiliary_b" else env.get(model_env, "") or env.get(prefix + "_NAME", "")
        base = env.get(prefix + "_BASE_URL", "")
        if base:
            try:
                _base(base)
            except ValueError:
                base = ""
        entries[ident] = {"config": {"base": base, "model": model, "key": env.get(prefix + "_API_KEY", ""), "protocol": protocol}, "capabilities": [], "builtin": True}
    if manifest_path is None:
        return Registry(entries)
    path = Path(manifest_path)
    if not _safe_file(path):
        return Registry(entries)
    try:
        raw = path.read_bytes()
    except OSError:
        _bad("invalid manifest")
    if len(raw) > _MAX_MANIFEST:
        _bad("invalid manifest")
    for name, secret in env.items():
        if name.endswith("_API_KEY") and secret and secret.encode() in raw:
            _bad("invalid manifest")
    specs = _parse_models(raw)
    configured_keys = [value for name, value in env.items() if name.endswith("_API_KEY") and value]
    if _redact(specs, configured_keys) != specs:
        _bad("invalid manifest")
    for s in specs:
        base = env.get(s["base_env"], "")
        if base:
            _base(base)
        model = s["model"] if s["model"] is not None else env.get(s["model_env"], "")
        entries[s["id"]] = {"config": {"base": base, "model": model, "key": env.get(s["key_env"], ""), "protocol": s["protocol"]}, "capabilities": s["capabilities"], "builtin": False}
    return Registry(entries)
