"""auditguard-config.yml: defaults, validation (unknown keys are errors), local and environment overrides."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import yamlio
from .common import CONFIG_REL, LOCAL_CONFIG_RELS, AuditGuardError, is_ci

DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "profile": "light",
    "integration": "hooks",
    "mode": "record",
    "audit": {
        "root": "audit",
        "register": "audit/sprints.yml",
        "unassigned": "_unassigned",
        "project": "_project",
        "evidence": {"snapshot": True, "max_file_kb": 512},
    },
    "render": {"on_hook": True, "html_on_hook": False, "expiring_days": 30},
    "viewer": {"inline_kb": 64, "theme": "auto"},
    "sessions": {"record": True},
    "events": {"stop": True},
    "stages": {
        "design": {"commands": ["constitution", "specify", "clarify", "plan", "tasks", "analyze", "checklist",
                                "taskstoissues"],
                   "completed_by": ["design.signed"]},
        "implement": {"commands": ["implement", "converge"], "completed_by": ["handover.4-5", "implement.approved"]},
        "test": {"commands": [], "completed_by": ["pr.approved"]},
    },
    "collectors": {
        "git": {
            "enabled": True,
            "artefacts": ["spec.md", "plan.md", "research.md", "data-model.md", "quickstart.md", "tasks.md",
                          "handover.yml", "contracts/**", "checklists/**"],
            "project_artefacts": [".specify/memory/constitution.md"],
            "code": ["src/**", "app/**", "lib/**", "tests/**", "test/**"],
        },
        "scopeguard": {"enabled": "auto", "version": ">=0.4,<0.6", "report": True, "timeout": 120},
        "archiguard": {"enabled": "auto", "version": ">=0.1,<0.3", "ledger": ".specify/archiguard/ledger.jsonl"},
        "workflow": {"enabled": True, "runs": ".specify/workflows/runs"},
    },
    "golden": {
        "git": {
            "enabled": True,
            "remote": "origin",
            "base": "main",
            "explain_paths": ["specs/**", "src/**", "app/**", "lib/**", "tests/**", "test/**", ".specify/memory/**"],
            "exclude_paths": ["specs/*/gates/**", "specs/*/.scopeguard/**", "specs/*/scopeguard-*.md", "audit/**"],
            "notes_ref": "refs/notes/auditguard",
            "sign": False,
        },
        "handover": "handover.yml",
        "lock": ".specify/archiguard/standards.lock.yml",
        "ledger": ".specify/archiguard/ledger.jsonl",
        "tracker": None,
    },
    "actors": {
        "human": "auto",
        "agent": "auto",
        "ci_env": ["GITHUB_ACTOR", "BITBUCKET_STEP_TRIGGERER_UUID", "GITLAB_USER_LOGIN", "BUILD_REQUESTEDFOR"],
    },
    "rules": {
        "implement_requires_design_signed": True,
        "waiver_requires_approver": True,
        "escalations_decided_before_close": True,
        "no_changes_after_signoff": True,
        "commands_closed_before_close": True,
        "no_unassigned_events": True,
        "anchored_before_export": False,
    },
    "guard": {
        "enabled": True,
        "readonly": ["audit/**", ".specify/extensions/auditguard/**"],
        "human_only": ["decide", "note", "sprint open", "sprint close", "anchor"],
    },
}

# The profile sets the switches that decide how much runs around the agent's work. A key set explicitly in
# the config file wins over the profile. `light` (the default) records the commands and what they changed
# and nothing else runs in the agent's loop: no agent events, no report subprocess, no re-render per hook.
# `full` is the complete recorder: sessions, the stop event, the pre_tool_use guard, scopeGuard's coverage
# report at every hook, and the Markdown views rebuilt after every hook.
PROFILES: Dict[str, Dict[str, Any]] = {
    "light": {
        "render": {"on_hook": False},
        "sessions": {"record": False},
        "events": {"stop": False},
        "guard": {"enabled": False},
        "collectors": {"scopeguard": {"report": False}},
    },
    "full": {
        "render": {"on_hook": True},
        "sessions": {"record": True},
        "events": {"stop": True},
        "guard": {"enabled": True},
        "collectors": {"scopeguard": {"report": True}},
    },
}
PROFILE_KEYS = (("render", "on_hook"), ("sessions", "record"), ("events", "stop"), ("guard", "enabled"),
                ("collectors", "scopeguard", "report"))

# the agent runtime events (extension.yml `events:`) and the switch each one follows
EVENT_SWITCHES = {
    "session_start": ("sessions", "record"),
    "stop": ("events", "stop"),
    "session_end": ("sessions", "record"),
    "pre_tool_use": ("guard", "enabled"),
}

KNOWN_COLLECTORS = ("git", "scopeguard", "archiguard", "workflow")
PLUGIN_COLLECTOR_KEYS = {"enabled", "command", "timeout", "version"}
STAGE_KEYS = {"commands", "completed_by"}
LOCAL_OVERRIDABLE = {("integration",), ("render", "on_hook"), ("render", "html_on_hook"), ("viewer",)}
CHOICES = {
    ("profile",): tuple(PROFILES),
    ("integration",): ("hooks", "workflow"),
    ("mode",): ("record", "enforce"),
    ("viewer", "theme"): ("auto", "light", "dark"),
}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key not in ("stages",):
            out[key] = _merge(out[key], value)
        elif value is None and isinstance(out.get(key), dict):
            continue                    # `sessions:` with every key commented out: the defaults stay
        else:
            out[key] = copy.deepcopy(value)
    return out


def _validate(data: Dict[str, Any], where: str) -> List[str]:
    errors: List[str] = []

    def walk(value: Any, default: Any, path: List[str]) -> None:
        if not isinstance(value, dict):
            return
        dotted = ".".join(path)
        if path == ["stages"]:
            for name, stage in value.items():
                if not isinstance(stage, dict):
                    errors.append(f"{where}: stages.{name} must be a mapping with commands / completed_by")
                    continue
                for k in stage:
                    if k not in STAGE_KEYS:
                        errors.append(f"{where}: unknown setting stages.{name}.{k}")
            return
        if path == ["collectors"]:
            for name, conf in value.items():
                if name in KNOWN_COLLECTORS:
                    walk(conf, DEFAULTS["collectors"][name], path + [name])
                elif not isinstance(conf, dict) or not conf.get("command"):
                    errors.append(f"{where}: collectors.{name} is not a built-in collector and has no 'command' (plug-in)")
                else:
                    for k in conf:
                        if k not in PLUGIN_COLLECTOR_KEYS:
                            errors.append(f"{where}: unknown setting collectors.{name}.{k}")
            return
        if path == ["golden", "tracker"]:
            return
        if not isinstance(default, dict):
            return
        for key, sub in value.items():
            if key not in default:
                errors.append(f"{where}: unknown setting {dotted + '.' if dotted else ''}{key}")
                continue
            walk(sub, default[key], path + [key])

    walk(data, DEFAULTS, [])
    for path, choices in CHOICES.items():
        cur: Any = data
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        if cur is not None and cur not in choices:
            errors.append(f"{where}: {'.'.join(path)} must be one of {', '.join(choices)} (got {cur!r})")
    return errors


class Config:
    def __init__(self, root: Path, data: Dict[str, Any], sources: List[str], notes: List[str],
                 pinned: Optional[set] = None):
        self.root = root
        self.data = data
        self.sources = sources
        self.notes = notes
        self.pinned_keys = pinned or set()   # profile switches the config file sets explicitly

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    @property
    def mode(self) -> str:
        return self.data["mode"]

    @property
    def enforce(self) -> bool:
        return self.data["mode"] == "enforce"

    @property
    def profile(self) -> str:
        return self.data.get("profile") or "light"

    def pinned(self, *keys: str) -> bool:
        """True when the config file sets this profile switch explicitly (the profile did not decide it)."""
        return tuple(keys) in self.pinned_keys

    def switches(self) -> Dict[str, Any]:
        """The profile switches as in force: dotted key -> value."""
        return {".".join(keys): self.get(*keys) for keys in PROFILE_KEYS}

    def events_wanted(self) -> Dict[str, bool]:
        """Which agent runtime events should be wired, per EVENT_SWITCHES."""
        return {event: bool(self.get(*keys, default=True)) for event, keys in EVENT_SWITCHES.items()}

    def path(self, value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() else self.root / p

    @property
    def audit_root(self) -> Path:
        return self.path(self.get("audit", "root")) or self.root / "audit"

    @property
    def register_path(self) -> Path:
        return self.path(self.get("audit", "register")) or self.audit_root / "sprints.yml"

    def stage_names(self) -> List[str]:
        return list((self.data.get("stages") or {}).keys())

    def collector(self, name: str) -> Dict[str, Any]:
        return dict((self.data.get("collectors") or {}).get(name) or {})

    def collector_enabled(self, name: str) -> bool:
        conf = self.collector(name)
        enabled = conf.get("enabled", True)
        if enabled == "auto":
            return (self.root / ".specify" / "extensions" / name).is_dir()
        return bool(enabled)

    def plugin_collectors(self) -> Dict[str, Dict[str, Any]]:
        return {k: v for k, v in (self.data.get("collectors") or {}).items()
                if k not in KNOWN_COLLECTORS and isinstance(v, dict) and v.get("enabled", True)}


def load_config(root: Path, explicit: Optional[str] = None) -> Config:
    sources: List[str] = []
    notes: List[str] = []
    data = copy.deepcopy(DEFAULTS)
    cfg_path = Path(explicit) if explicit else root / CONFIG_REL
    if explicit and not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    if explicit and not cfg_path.is_file():
        raise AuditGuardError(f"config file not found: {cfg_path}")
    errors: List[str] = []
    user: Dict[str, Any] = {}
    if cfg_path.is_file():
        user = yamlio.load_file(cfg_path) or {}
        errors += _validate(user, str(cfg_path))
        sources.append(str(cfg_path))
    # the profile decides the switches of PROFILE_KEYS; a key the file sets explicitly wins
    profile = user.get("profile") if isinstance(user, dict) else None
    if profile in PROFILES:
        data = _merge(data, PROFILES[profile])
    elif profile is None:
        data = _merge(data, PROFILES[DEFAULTS["profile"]])
    pinned = {keys for keys in PROFILE_KEYS if _has(user, keys)}
    if user:
        data = _merge(data, user)
    if not is_ci():
        for rel in LOCAL_CONFIG_RELS:
            local = root / rel
            if not local.is_file():
                continue
            over = yamlio.load_file(local)
            errors += _validate(over, str(local))
            allowed: Dict[str, Any] = {}
            for key, value in over.items():
                if (key,) in LOCAL_OVERRIDABLE:
                    allowed[key] = value
                elif isinstance(value, dict):
                    sub = {k: v for k, v in value.items() if (key, k) in LOCAL_OVERRIDABLE}
                    if sub:
                        allowed[key] = sub
                    if len(sub) != len(value):
                        notes.append(f"{local}: only integration, render.on_hook, render.html_on_hook and viewer.* "
                                     "may be overridden locally - other keys ignored")
                else:
                    notes.append(f"{local}: '{key}' cannot be overridden locally - ignored")
            data = _merge(data, allowed)
            sources.append(str(local))
        env_int = os.environ.get("AUDITGUARD_INTEGRATION", "").strip()
        if env_int:
            data["integration"] = env_int
            sources.append("env AUDITGUARD_INTEGRATION")
    env_mode = os.environ.get("AUDITGUARD_MODE", "").strip()
    if env_mode:
        data["mode"] = env_mode
        sources.append("env AUDITGUARD_MODE")
    errors += _validate({k: data[k] for k in ("integration", "mode")}, "effective config")
    if errors:
        raise AuditGuardError("invalid configuration:\n  " + "\n  ".join(errors))
    return Config(root, data, sources, notes, pinned)


def _has(data: Any, keys: tuple) -> bool:
    cur = data
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return False
        cur = cur[k]
    return True
