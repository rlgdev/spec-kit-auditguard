"""Recording events with stage bookkeeping: stage.entered / reentered / completed and the milestones."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .common import PROJECT_KEY, rel_path
from .context import RunContext, key_of

MILESTONES = ("design.signed", "design.reopened", "handover.4-5", "implement.approved", "test.exit", "pr.approved")
REOPENS = {"design.reopened": "design"}


def command_stage(cfg: Any, command: str) -> Optional[str]:
    short = command.replace("speckit.", "")
    for name, stage in (cfg.get("stages") or {}).items():
        if short in (stage.get("commands") or []):
            return name
    return None


class StageStatus:
    def __init__(self, names: List[str]):
        self.names = names
        self.state: Dict[str, str] = {n: "not" for n in names}           # not | entered | completed | reentered
        self.entered: Dict[str, Optional[str]] = {n: None for n in names}
        self.completed: Dict[str, Optional[Dict[str, Any]]] = {n: None for n in names}
        self.reentered: Dict[str, List[str]] = {n: [] for n in names}
        self.current: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {n: {"state": self.state[n], "entered": self.entered[n], "completed": self.completed[n],
                    "reentered": self.reentered[n]} for n in self.names}


def stage_status(cfg: Any, chain: Iterable[Dict[str, Any]]) -> StageStatus:
    names = list((cfg.get("stages") or {}).keys())
    st = StageStatus(names)
    for ev in chain:
        kind = ev.get("kind")
        stage = (ev.get("data") or {}).get("stage")
        if stage not in st.state:
            continue
        if kind == "stage.entered":
            st.state[stage] = "entered"
            st.entered[stage] = st.entered[stage] or ev.get("at")
            st.current = stage
        elif kind == "stage.reentered":
            st.state[stage] = "reentered"
            st.reentered[stage].append(ev.get("at"))
            st.current = stage
        elif kind == "stage.completed":
            st.state[stage] = "completed"
            st.completed[stage] = {"at": ev.get("at"), "by_event": (ev.get("data") or {}).get("by_event"),
                                   "by_kind": (ev.get("data") or {}).get("by_kind"),
                                   "by": (ev.get("data") or {}).get("by")}
            st.current = stage
    return st


class Recorder:
    """Appends events for one invocation; keeps the stage status of every chain it touches current."""

    def __init__(self, rc: RunContext):
        self.rc = rc
        self.cfg = rc.cfg
        self.store = rc.store
        self.appended: List[Dict[str, Any]] = []

    def feature_path(self, feature_dir: Optional[Path]) -> Optional[str]:
        return rel_path(feature_dir, self.rc.root) if feature_dir is not None else None

    def chain(self, key: str) -> List[Dict[str, Any]]:
        return self.store.load_chain(key)

    def status(self, key: str) -> StageStatus:
        return stage_status(self.cfg, self.chain(key))

    # ---------------------------------------------------------------- append
    def record(self, feature: Optional[str], spec: Dict[str, Any],
               evidence: Iterable[Tuple[str, bytes, Optional[str]]] = (), *, sprint: Optional[str] = None,
               git_fields: bool = True) -> Dict[str, Any]:
        key = key_of(feature)
        full = dict(spec)
        full["feature"] = feature
        if "stage" not in full:
            full["stage"] = self.status(key).current if key != PROJECT_KEY else None
        if git_fields:
            for k, v in self.rc.git_fields().items():
                full.setdefault(k, v)
        ev = self.store.append(key, full, evidence=evidence, sprint=sprint)
        self.appended.append(ev)
        if ev["kind"] in MILESTONES and key != PROJECT_KEY:
            self._after_milestone(feature, ev, sprint)
        return ev

    def enter_for_command(self, feature: Optional[str], command: str, at: Optional[str] = None,
                          sprint: Optional[str] = None) -> Optional[str]:
        """Emit stage.entered / stage.reentered before a command of a stage; returns the command's stage."""
        stage = command_stage(self.cfg, command)
        key = key_of(feature)
        if stage is None or key == PROJECT_KEY:
            return stage
        st = self.status(key)
        actor = self.rc.actor_script()
        if st.state.get(stage) == "not":
            self.record(feature, {"kind": "stage.entered", "stage": stage, "at": at, "actor": actor,
                                  "source": "derived", "data": {"stage": stage, "by_command": command}}, sprint=sprint)
        elif st.state.get(stage) == "completed":
            done = st.completed.get(stage) or {}
            self.record(feature, {"kind": "stage.reentered", "stage": stage, "at": at, "actor": actor,
                                  "source": "derived",
                                  "data": {"stage": stage, "by_command": command, "completed_at": done.get("at"),
                                           "completed_by_event": done.get("by_event")}}, sprint=sprint)
        return stage

    def _after_milestone(self, feature: Optional[str], ev: Dict[str, Any], sprint: Optional[str]) -> None:
        key = key_of(feature)
        names = list((self.cfg.get("stages") or {}).keys())
        actor = self.rc.actor_script()
        kind = ev["kind"]
        if kind in REOPENS:
            stage = REOPENS[kind]
            st = self.status(key)
            if st.state.get(stage) == "completed":
                done = st.completed.get(stage) or {}
                self.record(feature, {"kind": "stage.reentered", "stage": stage, "at": ev.get("at"), "actor": actor,
                                      "source": "derived",
                                      "data": {"stage": stage, "by_event": ev["hash"], "by_kind": kind,
                                               "completed_at": done.get("at"),
                                               "completed_by_event": done.get("by_event")}}, sprint=sprint)
            return
        for idx, name in enumerate(names):
            completed_by = (self.cfg.get("stages") or {}).get(name, {}).get("completed_by") or []
            if kind not in completed_by:
                continue
            st = self.status(key)
            if st.state.get(name) == "completed":
                continue
            if st.state.get(name) == "not":
                self.record(feature, {"kind": "stage.entered", "stage": name, "at": ev.get("at"), "actor": actor,
                                      "source": "derived", "data": {"stage": name, "by_event": ev["hash"]}},
                            sprint=sprint)
            by = (ev.get("data") or {}).get("by") or (ev.get("actor") or {}).get("id")
            self.record(feature, {"kind": "stage.completed", "stage": name, "at": ev.get("at"), "actor": actor,
                                  "source": "derived",
                                  "data": {"stage": name, "by_event": ev["hash"], "by_kind": kind, "by": by}},
                        sprint=sprint)
            # a following stage without commands (test) is entered when its predecessor completes
            if idx + 1 < len(names):
                nxt = names[idx + 1]
                nxt_cmds = (self.cfg.get("stages") or {}).get(nxt, {}).get("commands") or []
                if not nxt_cmds and self.status(key).state.get(nxt) == "not":
                    self.record(feature, {"kind": "stage.entered", "stage": nxt, "at": ev.get("at"), "actor": actor,
                                          "source": "derived",
                                          "data": {"stage": nxt, "after_stage": name, "by_event": ev["hash"]}},
                                sprint=sprint)
