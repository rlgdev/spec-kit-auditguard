"""The hook state machine: before_<cmd> / after_<cmd> of the ten Spec Kit commands and the agent's runtime
events (session_start, stop, session_end).

    IDLE --before_X--> OPEN(X) --after_X--> IDLE
    OPEN(X) --stop--> OPEN(X, stopped)        (a command may span several agent turns: /speckit.clarify asks)
    OPEN(X, stopped) --before_Y / session_end / collect--> command.abandoned (at the time of the stop)
    OPEN(X) --stop, an escalation note appeared--> command.abandoned (escalated) at once
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .collectors import run_collectors
from .common import (PROJECT_KEY, RUNTIME_EVENTS, SPECKIT_COMMANDS, AuditGuardError, content_hash, now_iso,
                     read_bytes, read_json, rel_path, resolve_feature_dir, time_key)
from .context import RunContext, key_of
from .reconcile import diff_artefacts, reconcile, save_baseline
from .recorder import Recorder, command_stage

OPEN_STATE = "open.json"


def parse_event(name: str) -> Tuple[str, str]:
    for when in ("before", "after"):
        prefix = when + "_"
        if name.startswith(prefix) and name[len(prefix):] in SPECKIT_COMMANDS:
            return when, name[len(prefix):]
    raise AuditGuardError(f"unknown hook event '{name}' (expected before_<command> / after_<command> for one of "
                          f"{', '.join(SPECKIT_COMMANDS)}, or {', '.join(RUNTIME_EVENTS)})")


def watch_files(feature_dir: Optional[Path]) -> Dict[str, str]:
    """Hashes of the files whose change during a command decides its outcome (escalation notes, step verdicts)."""
    out: Dict[str, str] = {}
    if feature_dir is None or not feature_dir.is_dir():
        return out
    paths = list(feature_dir.glob("scopeguard-escalation-*.md"))
    gates = feature_dir / "gates"
    if gates.is_dir():
        paths += list(gates.glob("escalation-*.md"))
        paths += [p for p in gates.glob("*-a.json")] + [p for p in gates.glob("*-b.json")]
    for p in paths:
        try:
            out[p.name if p.parent == feature_dir else f"gates/{p.name}"] = content_hash(read_bytes(p))
        except AuditGuardError:
            pass
    return out


def outcome_of(feature_dir: Optional[Path], before: Dict[str, str], finished: bool,
               command: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """escalated (a new escalation note) > error (a step verdict that could not evaluate) > fail (the command's own
    archiGuard step-B verdict ended with blocking findings) > pass (finished) / unknown (abandoned)."""
    now = watch_files(feature_dir)
    for name, h in sorted(now.items()):
        if name.endswith(".md") and before.get(name) != h:
            return "escalated", name
    for name, h in sorted(now.items()):
        if name.endswith(".json") and before.get(name) != h and feature_dir is not None:
            data = read_json(feature_dir / name, {}) or {}
            if data.get("status") == "error":
                return "error", name
    short_cmd = (command or "").replace("speckit.", "")
    own = f"gates/{short_cmd}-b.json"
    if short_cmd and own in now and before.get(own) != now[own] and feature_dir is not None:
        if (read_json(feature_dir / own, {}) or {}).get("status") == "violation":
            return "fail", own
    return ("pass" if finished else "unknown"), None


class Hooks:
    def __init__(self, root: Path, cfg: Any):
        self.root = root
        self.cfg = cfg
        self.rc = RunContext(root, cfg)
        self.rec = Recorder(self.rc)

    # ---------------------------------------------------------------- state
    def open_entries(self) -> List[Dict[str, Any]]:
        data = self.rc.state(OPEN_STATE, {"open": []}) or {}
        return list(data.get("open") or [])

    def save_open(self, entries: List[Dict[str, Any]]) -> None:
        self.rc.save_state(OPEN_STATE, {"open": entries})

    def feature_from(self, value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        p = self.root / value
        return p if p.is_dir() else None

    def resolve(self, explicit: Optional[str]) -> Optional[Path]:
        return resolve_feature_dir(self.root, explicit, required=False)

    # ----------------------------------------------------------------- hooks
    def run(self, event: str, feature_arg: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> str:
        if event == "session_start":
            return self.session_start(payload or {})
        if event == "session_end":
            return self.session_end(payload or {})
        if event == "stop":
            return self.stop(payload or {})
        when, cmd = parse_event(event)
        return self.before(cmd, feature_arg) if when == "before" else self.after(cmd, feature_arg)

    def before(self, cmd: str, feature_arg: Optional[str]) -> str:
        self.close_open(reason=f"/speckit.{cmd} started")
        command = f"speckit.{cmd}"
        entry: Dict[str, Any] = {"command": command, "started_at": now_iso(), "actor": self.rc.actor_agent(),
                                 "head": self.rc.head, "worktree": self.rc.worktree_code()}
        if cmd == "specify" and not feature_arg:
            # the feature does not exist yet: /speckit.specify creates it - recorded at after_specify
            entry.update({"feature": None, "pending": True, "known_features": [p.name for p in _features(self.root)]})
            self.save_open(self.open_entries() + [entry])
            save_baseline(self.rc)
            return "/speckit.specify started (the new feature is recorded when it exists)"
        feature_dir = None if cmd == "constitution" else self.resolve(feature_arg)
        feature = self.rec.feature_path(feature_dir)
        reconcile(self.rec, feature_dir)
        run_collectors(self.rec, feature_dir)
        stage = self.rec.enter_for_command(feature, command)
        artefacts = self.rc.artefact_hashes(feature_dir)
        ev = self.rec.record(feature, {"kind": "command.started", "command": command, "stage": stage,
                                       "actor": entry["actor"], "source": f"hook:before_{cmd}", "at": entry["started_at"],
                                       "artefacts": artefacts, "worktree": entry["worktree"] or None, "data": {}})
        entry.update({"feature": feature, "started_seq": ev["seq"], "started_hash": ev["hash"], "artefacts": artefacts,
                      "watch": watch_files(feature_dir), "sprint": ev["sprint"]})
        self.save_open(self.open_entries() + [entry])
        save_baseline(self.rc)
        return self.line(ev)

    def after(self, cmd: str, feature_arg: Optional[str]) -> str:
        command = f"speckit.{cmd}"
        entries = self.open_entries()
        entry = next((e for e in reversed(entries) if e.get("command") == command), None)
        if entry is not None:
            entries.remove(entry)
        # other commands still open never got their after_ hook
        self.save_open(entries)
        self.close_open(reason=f"/speckit.{cmd} finished")
        if entry is None:
            feature_dir = None if cmd == "constitution" else self.resolve(feature_arg)
        elif entry.get("pending"):
            feature_dir = self.resolve(feature_arg)
        else:
            feature_dir = self.feature_from(entry.get("feature")) if entry.get("feature") else (
                None if cmd == "constitution" else self.resolve(feature_arg))
        feature = self.rec.feature_path(feature_dir)
        stage = command_stage(self.cfg, command)
        started_missing = entry is None
        if entry is not None and entry.get("pending"):
            stage = self.rec.enter_for_command(feature, command, at=entry["started_at"])
            ev0 = self.rec.record(feature, {"kind": "command.started", "command": command, "stage": stage,
                                            "actor": entry["actor"], "source": f"hook:before_{cmd}",
                                            "at": entry["started_at"], "artefacts": {},
                                            "data": {"recorded_at": "after_specify (the feature did not exist yet)"}})
            entry.update({"started_seq": ev0["seq"], "started_hash": ev0["hash"], "artefacts": {}, "watch": {}})
        elif entry is None:
            stage = self.rec.enter_for_command(feature, command)
        first_seq = (entry or {}).get("started_seq")
        if first_seq is None:
            first_seq = self.rc.store.head(key_of(feature))[0]
        run_collectors(self.rec, feature_dir)
        ev = self.finish(entry, command, feature_dir, stage, finished=True, started_missing=started_missing,
                         first_seq=first_seq, source=f"hook:after_{cmd}")
        save_baseline(self.rc)
        return self.line(ev)

    def finish(self, entry: Optional[Dict[str, Any]], command: str, feature_dir: Optional[Path], stage: Optional[str],
               *, finished: bool, started_missing: bool = False, first_seq: Optional[int] = None,
               source: str = "", at: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        feature = self.rec.feature_path(feature_dir)
        chain = self.rec.chain(key_of(feature))
        now_artefacts = self.rc.artefact_hashes(feature_dir)
        if entry is not None and entry.get("artefacts") is not None:
            base_artefacts = entry.get("artefacts") or {}
        else:
            from .reconcile import last_artefacts
            base_artefacts = last_artefacts(chain) or {}
        changed = diff_artefacts(base_artefacts, now_artefacts)
        if entry is not None and entry.get("head"):
            changed += self.rc.code_changes(entry.get("head"), entry.get("worktree") or {})
        outcome, outcome_file = outcome_of(feature_dir, (entry or {}).get("watch") or {}, finished, command)
        window = [e for e in chain if first_seq is not None and isinstance(e.get("seq"), int) and e["seq"] > first_seq]
        gates = []
        refs = []
        for e in window:
            if e.get("kind") == "gate.verdict":
                d = e.get("data") or {}
                gates.append({k: d.get(k) for k in ("tool", "gate", "step", "status", "coverage", "blocking", "advisory",
                                                     "waived", "violations", "iterations") if d.get(k) is not None})
                if e.get("_sprint_dir") == self.rc.store.route()[0]:
                    refs.extend(e.get("evidence") or [])
        data: Dict[str, Any] = {"gates": gates, "waiver_changes": sum(1 for e in window if str(e.get("kind")).startswith("waiver."))}
        started_at = (entry or {}).get("started_at")
        end_at = at or now_iso()
        if started_at:
            data["duration_s"] = max(0, int(time_key(end_at) - time_key(started_at)))
            data["started_at"] = started_at
        if started_missing:
            data["started_missing"] = True
        if outcome_file:
            data["outcome_file"] = outcome_file
        if reason:
            data["reason"] = reason
        if entry is not None and entry.get("started_hash"):
            data["started_event"] = entry["started_hash"]
        spec = {"kind": "command.finished" if finished else "command.abandoned", "command": command, "stage": stage,
                "outcome": outcome, "actor": (entry or {}).get("actor") or self.rc.actor_agent(),
                "source": source, "artefacts": now_artefacts, "changed": changed,
                "worktree": self.rc.worktree_code() or None, "data": data, "evidence_refs": _dedupe(refs)}
        if at:
            spec["at"] = at
        return self.rec.record(feature, spec)

    def close_open(self, reason: str, *, only_stopped: bool = False) -> List[Dict[str, Any]]:
        """Close open commands as command.abandoned (their after_ hook never ran)."""
        entries = self.open_entries()
        keep, closed = [], []
        for entry in entries:
            if only_stopped and not entry.get("stopped_at"):
                keep.append(entry)
                continue
            closed.append(self.abandon(entry, reason))
        self.save_open(keep)
        return closed

    def abandon(self, entry: Dict[str, Any], reason: str) -> Dict[str, Any]:
        command = entry["command"]
        if entry.get("pending"):
            known = set(entry.get("known_features") or [])
            new = [p for p in _features(self.root) if p.name not in known]
            feature_dir = new[0] if len(new) == 1 else None
            feature = self.rec.feature_path(feature_dir)
            stage = self.rec.enter_for_command(feature, command, at=entry["started_at"]) if feature else None
            ev0 = self.rec.record(feature, {"kind": "command.started", "command": command, "stage": stage,
                                            "actor": entry["actor"], "source": "hook:before_specify",
                                            "at": entry["started_at"], "artefacts": {}, "data": {}})
            entry = dict(entry, started_seq=ev0["seq"], artefacts={}, watch={}, started_hash=ev0["hash"])
        else:
            feature_dir = self.feature_from(entry.get("feature"))
        stage = command_stage(self.cfg, command)
        run_collectors(self.rec, feature_dir)
        return self.finish(entry, command, feature_dir, stage, finished=False, first_seq=entry.get("started_seq"),
                           source="event:stop" if entry.get("stopped_at") else "hook:next-command",
                           at=entry.get("stopped_at"), reason=reason)

    # -------------------------------------------------------- runtime events
    def session_start(self, payload: Dict[str, Any]) -> str:
        if not self.cfg.get("sessions", "record", default=True):
            return ""
        sid = str(payload.get("session_id") or payload.get("sessionId") or f"local-{int(time.time())}")
        cur = self.rc.state("session.json", {}) or {}
        if cur.get("open") and cur.get("session") == sid:
            return ""
        if cur.get("open") and cur.get("session") != sid:
            self._session_end_event(cur.get("session"), "replaced by a new session")
        self.rc.save_state("session.json", {"open": True, "session": sid, "started_at": now_iso()})
        self.rec.record(None, {"kind": "session.started", "actor": self.rc.actor_agent(), "source": "event:session_start",
                               "data": {"session": sid, "agent": self.rc.agent_id(),
                                        "trigger": payload.get("source") or payload.get("trigger")}})
        return ""

    def _session_end_event(self, sid: Optional[str], reason: Optional[str]) -> None:
        self.rec.record(None, {"kind": "session.ended", "actor": self.rc.actor_agent(), "source": "event:session_end",
                               "data": {"session": sid, "agent": self.rc.agent_id(), "reason": reason}})

    def session_end(self, payload: Dict[str, Any]) -> str:
        self.close_open(reason="the agent session ended")
        if not self.cfg.get("sessions", "record", default=True):
            return ""
        cur = self.rc.state("session.json", {}) or {}
        sid = str(payload.get("session_id") or payload.get("sessionId") or cur.get("session") or "")
        self._session_end_event(sid or None, payload.get("reason"))
        self.rc.save_state("session.json", {"open": False, "session": sid, "ended_at": now_iso()})
        return ""

    def stop(self, payload: Dict[str, Any]) -> str:
        """End of an agent turn: an open command is closed at once when it escalated, otherwise marked stopped."""
        if not self.cfg.get("events", "stop", default=True):
            return ""                                    # profile light: the next hook closes an abandoned command
        entries = self.open_entries()
        keep = []
        for entry in entries:
            feature_dir = self.feature_from(entry.get("feature"))
            outcome, _ = outcome_of(feature_dir, entry.get("watch") or {}, False, entry.get("command"))
            if outcome in ("escalated", "error") and not entry.get("pending"):
                entry["stopped_at"] = now_iso()
                self.abandon(entry, reason="the command ended without its after_ hook (escalated)")
                continue
            entry.setdefault("stopped_at", now_iso())
            keep.append(entry)
        self.save_open(keep)
        return ""

    # ------------------------------------------------------------------ out
    def line(self, ev: Dict[str, Any]) -> str:
        from . import __version__
        gates = len((ev.get("data") or {}).get("gates") or [])
        waivers = (ev.get("data") or {}).get("waiver_changes") or 0
        feature = Path(ev["feature"]).name if ev.get("feature") else PROJECT_KEY
        trail = rel_path(self.rc.store.sprints_dir / ev["sprint"] / feature / "trail.md", self.root)
        outcome = f" ({ev['outcome']})" if ev.get("outcome") else ""
        return (f"auditGuard {__version__} | {ev.get('source', '').split(':', 1)[-1]} | {ev['sprint']} / {feature} | "
                f"#{ev['seq']} {ev['kind']}{outcome} | {gates} gate report(s), {waivers} waiver change(s) | {trail}")


def _features(root: Path) -> List[Path]:
    from .common import feature_dirs
    return feature_dirs(root)


def _dedupe(refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for r in refs:
        key = (r.get("sha256"), r.get("name"))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def parse_payload(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    if isinstance(data, dict) and isinstance(data.get("input"), dict) and "hook_event_name" not in data:
        return data["input"]   # opencode envelope {input, output}
    return data if isinstance(data, dict) else {}
