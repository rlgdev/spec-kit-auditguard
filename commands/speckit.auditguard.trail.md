---
description: "auditGuard: show the current feature's audit trail for this sprint, with the open items"
scripts:
  sh: bash scripts/bash/auditguard.sh show
  ps: scripts/powershell/auditguard.ps1 show
  py: scripts/python/auditguard.py show
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Goal

Show what the audit trail records for the active feature: every Spec Kit command by stage, the gate reports of
scopeGuard and archiGuard, the waiver changes, the human decisions and the changes made outside a recorded command,
followed by the open items (undecided escalations, pending decisions, waivers about to expire). Read-only.

## Steps

1. Run `{SCRIPT}` from the repository root. Append `--feature-dir <dir>` if the user input names a feature,
   `--sprint <id>` if it names a sprint, `--all` for every feature, `--open` if the user only wants the open items,
   `--kind <group>` (command, gate, waiver, decision, escalation, change, session, note) to filter.
2. Show the output as is, then summarise in at most three lines: the stage the feature is in, what is open, and
   anything flagged with `!` (a change outside a command, a re-entered stage, an escalation).
3. Point to the rendered page `audit/sprints/<sprint>/<feature>/trail.md` and, for the full interactive view, to
   `audit/viewer/index.html` (`auditguard render --html` builds it; `auditguard serve` serves it live).
