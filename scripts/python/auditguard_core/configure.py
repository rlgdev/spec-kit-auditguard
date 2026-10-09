"""auditguard configure: apply the config to Spec Kit's hook registry and the agent's event config, prepare the
project and report what is in force."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, yamlio
from .common import (EXTENSIONS_YML, HOOK_EVENTS, WORK_REL, AuditGuardError, git, is_git_repo, read_text, rel_path,
                     version_satisfies, write_text)
from .config import EVENT_SWITCHES
from .sprints import Register

GITATTRIBUTES = ["{audit}/**/*.jsonl -text", "{audit}/**/evidence/** -text", "{audit}/**/seal.json -text"]

# the agent event -> the command Spec Kit wires for it (extension.yml `events:`)
EVENT_COMMANDS = {"session_start": "speckit.auditguard.sessionstart", "stop": "speckit.auditguard.stop",
                  "session_end": "speckit.auditguard.sessionend", "pre_tool_use": "speckit.auditguard.guard"}
# the native config files Spec Kit writes the events into, by agent (init-options `ai`): the nested-JSON format
# (`hooks: {<Event>: [{matcher, hooks: [{type, command, timeout}]}]}`) that `prune_native_events` understands
NATIVE_EVENT_FILES = {"claude": ".claude/settings.json", "gemini": ".gemini/settings.json",
                      "qwen": ".qwen/settings.json", "tabnine": ".tabnine/agent/settings.json"}
CLAUDE_SETTINGS = ".claude/settings.json"


def _event_of(command: str) -> Optional[str]:
    for event, name in EVENT_COMMANDS.items():
        if name in command:
            return event
    return None


def prune_native_events(text: str, wanted: Dict[str, bool]) -> Tuple[str, Dict[str, bool], List[str]]:
    """Remove the auditGuard hook entries of switched-off events from a nested-JSON agent config (Claude Code's
    `.claude/settings.json` and the like). Returns the new text, which auditGuard events are (still) wired, and the
    events removed. Other entries, including the siblings', are untouched; an unparsable file is left alone."""
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as exc:
        raise AuditGuardError(f"not valid JSON ({exc})")
    hooks = data.get("hooks") if isinstance(data, dict) else None
    present: Dict[str, bool] = {e: False for e in EVENT_COMMANDS}
    removed: List[str] = []
    if not isinstance(hooks, dict):
        return text, present, removed
    for native in list(hooks):
        groups = hooks[native]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list):
                kept_groups.append(group)
                continue
            kept = []
            for entry in inner:
                event = _event_of(str(entry.get("command", ""))) if isinstance(entry, dict) else None
                if event is None:
                    kept.append(entry)
                elif wanted.get(event, True):
                    present[event] = True
                    kept.append(entry)
                else:
                    removed.append(event)
            if kept:
                kept_groups.append(dict(group, hooks=kept))
        if kept_groups:
            hooks[native] = kept_groups
        else:
            del hooks[native]
    if not hooks:
        del data["hooks"]
    if not removed:
        return text, present, removed
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n", present, sorted(set(removed))


def native_event_file(root: Path) -> Optional[Path]:
    """The agent's native event config this project uses, when it is one auditGuard knows how to edit."""
    opts = root / ".specify" / "init-options.json"
    agent = None
    if opts.is_file():
        try:
            agent = (json.loads(read_text(opts)) or {}).get("ai")
        except ValueError:
            agent = None
    rel = NATIVE_EVENT_FILES.get(str(agent)) if agent else None
    if rel is None and (root / CLAUDE_SETTINGS).is_file():
        rel = CLAUDE_SETTINGS
    return root / rel if rel else None


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

    # the agent's native event config: entries of switched-off events are removed (Spec Kit re-adds them all on
    # the next extension add / enable; configure prunes again)
    wanted = cfg.events_wanted()
    native = native_event_file(root)
    present: Dict[str, bool] = {e: False for e in EVENT_COMMANDS}
    native_note = ""
    if native is not None and native.is_file():
        try:
            new_text, present, removed = prune_native_events(read_text(native), wanted)
        except AuditGuardError as exc:
            native_note = f"{rel_path(native, root)} left alone: {exc}"
        else:
            if removed:
                if not dry_run:
                    write_text(native, new_text)
                changes.append(f"{rel_path(native, root)}: {len(removed)} agent event(s) unwired ({', '.join(removed)})")
    missing = [e for e, on in wanted.items() if on and not present[e]]

    hooks_on = sum(1 for f in found if f["now"])
    switches = cfg.switches()

    def sw(name: str, on_text: str, off_text: str) -> str:
        value = switches[name]
        return (on_text if value else off_text) + ("" if not cfg.pinned(*name.split(".")) else " (pinned)")

    lines += [
        f"  profile       : {cfg.profile}" + ("  (records the commands; nothing else runs in the agent's loop)" if cfg.profile == "light"
                                             else "  (the complete recorder: events, guard, reports, views per hook)"),
        f"  integration   : {integration}",
        f"  mode          : {cfg.mode}" + ("  (check and CI fail on a broken rule)" if cfg.enforce else "  (never blocks)"),
        f"  audit folder  : {audit_rel}/  (register {rel_path(reg_path, root)})",
        f"  hooks         : {hooks_on} of {len(found)} on" + ("" if found else "  (none registered - is the auditguard extension installed?)"),
        f"  per hook      : scopeGuard report {sw('collectors.scopeguard.report', 'run', 'not run (history files only)')}"
        f" · views {sw('render.on_hook', 'rebuilt', 'on demand (render / collect / verify)')}",
    ]
    events_line = " · ".join(
        f"{e} {'on' if wanted[e] else 'off'}" + (" (pinned)" if cfg.pinned(*EVENT_SWITCHES[e]) else "")
        for e in EVENT_COMMANDS)
    lines.append(f"  agent events  : {events_line}")
    if native is not None and native.is_file():
        wired = [e for e in EVENT_COMMANDS if present[e]]
        lines.append(f"  wired in      : {rel_path(native, root)} -> {', '.join(wired) if wired else 'none'}")
        if missing:
            lines.append(f"  NOT WIRED     : {', '.join(missing)} - the profile wants them; re-register the extension's events: "
                         "specify extension disable auditguard && specify extension enable auditguard (then configure)")
    elif any(wanted.values()):
        lines.append("  wired in      : no agent event config here (Spec Kit wires the events at specify extension add for"
                     " agents that support them)")
    if native_note:
        lines.append(f"  NOTE: {native_note}")
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
    return "\n".join(lines), {"profile": cfg.profile, "integration": integration, "mode": cfg.mode, "hooks": found,
                              "switches": switches, "events": wanted, "events_wired": present, "changes": changes,
                              "dry_run": dry_run}
