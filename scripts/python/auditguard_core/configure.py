"""auditguard configure: apply the config to Spec Kit's hook registry, prepare the project and report what is in force."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, yamlio
from .common import (EXTENSIONS_YML, HOOK_EVENTS, WORK_REL, AuditGuardError, git, is_git_repo, read_text, rel_path,
                     version_satisfies, write_text)
from .sprints import Register

GITATTRIBUTES = ["{audit}/**/*.jsonl -text", "{audit}/**/evidence/** -text", "{audit}/**/seal.json -text"]


def _scalar(value: str) -> str:
    return value.strip().strip("'\"")


def set_hook_flags(text: str, decide: Any) -> Tuple[str, List[Dict[str, Any]]]:
    """Set `enabled:` on hook entries of .specify/extensions.yml, preserving the rest of the file (as archiGuard)."""
    lines = text.split("\n")
    out: List[str] = []
    found: List[Dict[str, Any]] = []
    in_hooks = False
    event: Optional[str] = None
    i = 0
    while i < len(lines):
        line = lines[i]
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if stripped and indent == 0 and not stripped.startswith("#"):
            in_hooks = stripped == "hooks:"
            event = None
            out.append(line)
            i += 1
            continue
        item = re.match(r"^(\s*)-\s+(.*)$", line)
        if in_hooks and item is None:
            ev = re.match(r"^\s+([A-Za-z0-9_]+):\s*$", line)
            if ev:
                event = ev.group(1)
            out.append(line)
            i += 1
            continue
        if not (in_hooks and item and event):
            out.append(line)
            i += 1
            continue
        item_indent = len(item.group(1))
        block = [line]
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip(" ")) > item_indent):
            block.append(lines[j])
            j += 1
        fields: Dict[str, Tuple[int, str]] = {}
        for k, bline in enumerate(block):
            body = bline.strip()[2:] if k == 0 else bline.strip()
            m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", body)
            if m and (k == 0 or len(bline) - len(bline.lstrip(" ")) == item_indent + 2):
                fields[m.group(1)] = (k, m.group(2))
        ext = _scalar(fields.get("extension", (0, ""))[1])
        cmd = _scalar(fields.get("command", (0, ""))[1])
        want = decide(ext, event, cmd)
        if want is not None:
            current = _scalar(fields["enabled"][1]).lower() not in ("false", "no", "off") if "enabled" in fields else True
            value = "true" if want else "false"
            if "enabled" in fields:
                k = fields["enabled"][0]
                if k == 0:
                    block[0] = re.sub(r"enabled:\s*\S+", f"enabled: {value}", block[0])
                else:
                    prefix = block[k][: len(block[k]) - len(block[k].lstrip(" "))]
                    block[k] = f"{prefix}enabled: {value}"
            else:
                block.insert(1, " " * (item_indent + 2) + f"enabled: {value}")
            found.append({"extension": ext, "event": event, "command": cmd, "was": current, "now": want})
        out.extend(block)
        i = j
    return "\n".join(out), found


def _ext_version(root: Path, ext: str) -> Optional[str]:
    yml = root / ".specify" / "extensions" / ext / "extension.yml"
    if not yml.is_file():
        return None
    try:
        return str((yamlio.load_file(yml).get("extension") or {}).get("version"))
    except AuditGuardError:
        return "?"


def run_configure(root: Path, cfg: Any, dry_run: bool) -> Tuple[str, Dict[str, Any]]:
    integration = cfg["integration"]
    lines = [f"auditGuard {__version__} | configure{' (dry run)' if dry_run else ''}",
             f"config: {', '.join(rel_path(Path(s), root) if not s.startswith('env ') else s for s in cfg.sources) or 'built-in defaults'}",
             ""]
    changes: List[str] = []

    # hooks
    ext_yml = root / EXTENSIONS_YML
    found: List[Dict[str, Any]] = []
    if ext_yml.is_file():
        original = read_text(ext_yml)
        updated, found = set_hook_flags(original, lambda ext, event, cmd: (integration == "hooks")
                                        if ext == "auditguard" and event in HOOK_EVENTS else None)
        if any(f["was"] != f["now"] for f in found) and not dry_run:
            try:
                yamlio.loads(updated, str(ext_yml))
            except AuditGuardError as exc:
                raise AuditGuardError(f"refusing to write {EXTENSIONS_YML}: the result would not parse ({exc})")
            write_text(ext_yml, updated)
            changes.append(f"{EXTENSIONS_YML.as_posix()}: {sum(1 for f in found if f['was'] != f['now'])} hook(s) switched")

    # the audit folder, the register, .gitattributes, the workstation state
    audit_rel = rel_path(cfg.audit_root, root)
    reg_path = cfg.register_path
    if not reg_path.is_file():
        if not dry_run:
            Register(reg_path, [], False).save()
        changes.append(f"{rel_path(reg_path, root)}: created (empty register)")
    ga = root / ".gitattributes"
    want = [l.format(audit=audit_rel) for l in GITATTRIBUTES]
    have = read_text(ga).split("\n") if ga.is_file() else []
    missing = [l for l in want if l not in have]
    if missing:
        if not dry_run:
            text = "\n".join(have).rstrip("\n")
            block = "\n# auditGuard: the audit trail is hashed byte for byte - no line-ending conversion\n" + "\n".join(missing) + "\n"
            write_text(ga, (text + "\n" if text else "") + block)
        changes.append(f".gitattributes: {len(missing)} line(s) added")
    gi = root / WORK_REL / ".gitignore"
    if not gi.is_file():
        if not dry_run:
            write_text(gi, "# auditGuard workstation state (open commands, session, locks) - not part of the audit trail\nstate/\n")
        changes.append(f"{rel_path(gi, root)}: created")

    hooks_on = sum(1 for f in found if f["now"])
    lines += [
        f"  integration   : {integration}",
        f"  mode          : {cfg.mode}" + ("  (check and CI fail on a broken rule)" if cfg.enforce else "  (never blocks)"),
        f"  audit folder  : {audit_rel}/  (register {rel_path(reg_path, root)})",
        f"  hooks         : {hooks_on} of {len(found)} on" + ("" if found else "  (none registered - is the auditguard extension installed?)"),
    ]
    settings = root / ".claude" / "settings.json"
    wired = settings.is_file() and "speckit.auditguard" in read_text(settings)
    lines.append(f"  events        : {'wired into .claude/settings.json' if wired else 'not wired here (agent events are wired by specify extension add for agents that support them)'}"
                 f" · guard {'on' if cfg.get('guard', 'enabled') else 'off'} · sessions {'recorded' if cfg.get('sessions', 'record') else 'not recorded'}")
    # collectors
    lines.append("")
    lines.append("  Collectors:")
    for name in ("scopeguard", "archiguard"):
        conf = cfg.collector(name)
        version = _ext_version(root, name)
        if version is None:
            state = "not installed" + (" (enabled: auto - off)" if conf.get("enabled") == "auto" else "")
        else:
            ok = version_satisfies(version, conf.get("version") or "")
            state = f"{version} " + ("ok" if ok else f"UNSUPPORTED (wanted {conf.get('version')})") + \
                    ("" if cfg.collector_enabled(name) else " - disabled")
        lines.append(f"    {name:<11} : {state}")
    runs = cfg.path(cfg.get("collectors", "workflow", "runs"))
    lines.append(f"    workflow    : {'on' if cfg.collector_enabled('workflow') else 'off'}"
                 f" ({rel_path(runs, root) if runs else '-'}{'' if runs and runs.is_dir() else ', no runs yet'})")
    lines.append(f"    git         : {'on' if is_git_repo(root) else 'NOT A GIT REPOSITORY - commits, code changes and golden checks are skipped'}")
    for name in sorted(cfg.plugin_collectors()):
        lines.append(f"    {name:<11} : plug-in ({cfg.plugin_collectors()[name].get('command')})")
    # golden sources
    lines.append("")
    lines.append("  Golden sources:")
    if is_git_repo(root):
        remote = cfg.get("golden", "git", "remote")
        code, url = git(root, "remote", "get-url", remote)
        lines.append(f"    git remote  : {remote} -> {url.strip() if code == 0 else 'NOT CONFIGURED (G1 / G7 report unanchored)'}")
        base = cfg.get("golden", "git", "base")
        code, _ = git(root, "rev-parse", "--verify", "--quiet", base)
        code2, _ = git(root, "rev-parse", "--verify", "--quiet", f"{remote}/{base}")
        lines.append(f"    base branch : {base} {'ok' if code == 0 or code2 == 0 else '(not found yet)'}")
    for label, key in (("handover", "handover"), ("lock", "lock"), ("ledger", "ledger")):
        value = cfg.get("golden", key)
        if key == "handover":
            lines.append(f"    {label:<11} : specs/*/{value}")
        else:
            p = cfg.path(value)
            lines.append(f"    {label:<11} : {value} {'ok' if p and p.is_file() else '(not present)'}")
    # sprint
    reg = Register.load(reg_path)
    cur = reg.current()
    lines.append("")
    lines.append(f"  Current sprint: {cur['id'] + ' (' + cur.get('status', '') + ')' if cur else 'none - events go to _unassigned until you open one: auditguard sprint open <id> --by <name>'}")
    for p in reg.problems():
        lines.append(f"  REGISTER PROBLEM: {p}")
    if (root / ".specify" / "extensions" / "archiguard").is_dir():
        lines.append("")
        lines.append(f"  archiGuard is installed: add \"{audit_rel}/**\" to edit_guard.always_readonly in "
                     ".specify/extensions/archiguard/archiguard-config.yml, so its edit guard protects the trail too.")
    if changes:
        lines.append("")
        lines += [f"  {'would change' if dry_run else 'changed'}: {c}" for c in changes]
    for n in cfg.notes:
        lines.append(f"  NOTE: {n}")
    return "\n".join(lines), {"integration": integration, "mode": cfg.mode, "hooks": found, "changes": changes,
                              "dry_run": dry_run}
