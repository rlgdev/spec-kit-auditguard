"""The agent guard (pre_tool_use): agents never edit the audit trail and never record a person's decision.

Edits: blocks paths matching guard.readonly (default audit/** and the auditGuard extension).
Shell: blocks the auditGuard subcommands reserved for people (decide, note, sprint open/close, anchor) and
commands that write into the audit folder (redirects, rm, mv, cp, tee, sed -i, Set-Content, Remove-Item, ...).

Convenience, never the guarantee: on any setup problem it lets the call through (exit 0). CODEOWNERS on
audit/ and `auditguard verify` in CI are the guarantee. Exit 2 + a message on stderr blocks the call.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import glob_match, rel_path

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "edit", "write", "apply_patch", "create_file", "replace")
SHELL_TOOLS = ("Bash", "PowerShell", "bash", "shell", "run_shell_command", "terminal")
INVOCATION = re.compile(r"auditguard(?:\.sh|\.ps1|\.py)?\b(?![-_/\\])")
SEGMENT_END = re.compile(r"[;&|\n]|\)\s*$")
WRITE_VERBS = re.compile(r"(^|[\s;&|(])(rm|rmdir|mv|cp|tee|truncate|shred|unlink|install|dd|Set-Content|Add-Content|"
                         r"Out-File|Remove-Item|Move-Item|Copy-Item|New-Item|Clear-Content)\b", re.I)


def human_only_command(command: str, human_only: List[str]) -> Optional[str]:
    wanted = {tuple(h.split()) for h in human_only}
    for m in INVOCATION.finditer(command):
        rest = SEGMENT_END.split(command[m.end():], maxsplit=1)[0]
        try:
            tokens = shlex.split(rest, posix=True)
        except ValueError:
            tokens = rest.split()
        words = [t for t in tokens if not t.startswith("-")]
        if not words:
            continue
        candidates = [(words[0],)] + ([(words[0], words[1])] if len(words) > 1 else [])
        for cand in candidates:
            if cand in wanted:
                return " ".join(cand)
    return None


def writes_audit(command: str, audit_rel: str) -> bool:
    """A shell command that redirects into, or runs a file-changing verb on, the audit folder."""
    target = re.escape(audit_rel.strip("/"))
    path_rx = rf"(?:^|[\s'\"=(]|\./){target}(?:/|\b)"
    if re.search(rf">>?\s*['\"]?(?:\./)?{target}/", command):
        return True
    if re.search(r"\bsed\b[^;&|]*\s-i", command) and re.search(path_rx, command):
        return True
    for segment in re.split(r"[;&|\n]+", command):
        if WRITE_VERBS.search(segment) and re.search(path_rx, segment):
            return True
    return False


def evaluate(payload: Dict[str, Any], root: Path, cfg: Any) -> Tuple[int, str]:
    guard = cfg.get("guard") or {}
    if not guard.get("enabled", True):
        return 0, ""
    tool = str(payload.get("tool_name") or payload.get("toolName") or payload.get("tool") or "")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or payload.get("args") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    audit_rel = rel_path(cfg.audit_root, root)
    if tool in SHELL_TOOLS or "command" in tool_input and tool not in EDIT_TOOLS:
        command = str(tool_input.get("command") or "")
        if not command:
            return 0, ""
        if "auditguard" in command:
            sub = human_only_command(command, guard.get("human_only") or [])
            if sub:
                return 2, (f"auditGuard guard (blocked): 'auditguard {sub}' records a decision or an action of a person "
                           "(a sign-off, a sprint boundary, an anchor). Ask the user to run it; an agent never runs it.")
        if writes_audit(command, audit_rel):
            return 2, (f"auditGuard guard (blocked): {audit_rel}/ is the audit trail and is append-only through the "
                       "auditGuard CLI. Agents never change it.")
        return 0, ""
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") \
        or payload.get("file_path")
    if not file_path:
        return 0, ""
    path = Path(str(file_path))
    if not path.is_absolute():
        path = Path(str(payload.get("cwd") or root)) / path
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return 0, ""
    if glob_match(rel, guard.get("readonly") or []):
        return 2, (f"auditGuard guard (blocked): {rel} is part of the audit trail or the auditGuard engine and is "
                   "read-only for agents. The trail is written only by auditGuard's hooks and CLI.")
    return 0, ""
