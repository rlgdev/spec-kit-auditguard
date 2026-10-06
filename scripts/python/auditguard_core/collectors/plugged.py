"""Plug-in collectors: `<command> --feature-dir <dir> --since <iso> --json` prints a JSON list of events."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common import read_bytes, rel_path
from .base import Emit, last

CORE_KINDS = {"decision", "note", "gate.verdict", "escalation.raised", "escalation.decided", "pr.approved",
              "implement.approved", "commit.merged", "report.saved"}


def _argv(command: str, root: Path) -> List[str]:
    parts = shlex.split(command, posix=True)
    if not parts:
        return []
    head = Path(parts[0])
    for base in (root, root / ".specify" / "extensions"):
        cand = head if head.is_absolute() else base / head
        if cand.is_file():
            head = cand
            break
    if head.suffix == ".py":
        return [sys.executable, str(head), *parts[1:]]
    return [str(head), *parts[1:]]


def collect(rc: Any, name: str, conf: Dict[str, Any], feature_dir: Optional[Path], feature: Optional[str],
            chain: List[Dict[str, Any]]) -> List[Emit]:
    root = rc.root
    prev = last(chain, ["collector.ran"], name=name)
    since = (prev or {}).get("at") or "1970-01-01T00:00:00+00:00"
    argv = _argv(str(conf.get("command")), root)
    if feature_dir is not None:
        argv += ["--feature-dir", rel_path(feature_dir, root)]
    argv += ["--since", str(since), "--json"]
    error: Optional[str] = None
    items: List[Any] = []
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, timeout=int(conf.get("timeout") or 60),
                              stdin=subprocess.DEVNULL)
        if proc.returncode == 0:
            items = json.loads(proc.stdout.decode("utf-8", errors="replace") or "[]")
            if not isinstance(items, list):
                error = "the collector did not print a JSON list"
                items = []
        else:
            error = (proc.stderr or proc.stdout).decode("utf-8", errors="replace").strip()[-500:] or f"exit {proc.returncode}"
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        error = str(exc)
    out: List[Emit] = []
    if error:
        out.append(Emit(feature, {"kind": "collector.error", "actor": rc.actor_script(name), "source": f"collector:{name}",
                                  "data": {"name": name, "message": error}}))
        return out
    for item in items:
        if not isinstance(item, dict) or not item.get("kind"):
            continue
        kind = str(item["kind"])
        if kind not in CORE_KINDS and not kind.startswith(name + "."):
            kind = f"{name}.{kind}"
        evidence = []
        for ref in item.get("evidence") or []:
            path = Path(str(ref.get("path") or ""))
            if not path.is_absolute():
                path = root / path
            if path.is_file():
                evidence.append((str(ref.get("name") or path.name), read_bytes(path), rel_path(path, root)))
        actor = item.get("actor") if isinstance(item.get("actor"), dict) else rc.actor_script(name)
        out.append(Emit(feature, {"kind": kind, "at": item.get("at"), "actor": actor, "source": f"collector:{name}",
                                  "data": item.get("data") or {}}, evidence))
    if items:
        out.append(Emit(feature, {"kind": "collector.ran", "actor": rc.actor_script(name), "source": f"collector:{name}",
                                  "data": {"name": name, "events": len(items)}}))
    return out
