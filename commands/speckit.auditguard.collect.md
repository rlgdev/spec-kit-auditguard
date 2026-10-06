---
description: "auditGuard: collect new evidence (gate reports, waivers, sign-offs, workflow gate verdicts) into the trail now"
scripts:
  sh: bash scripts/bash/auditguard.sh collect
  ps: scripts/powershell/auditguard.ps1 collect
  py: scripts/python/auditguard.py collect
---

## User Input

```text
$ARGUMENTS
```

## Goal

Bring the audit trail up to date without running a Spec Kit command: the collectors read what scopeGuard, archiGuard,
the decision ledger and Spec Kit workflow runs wrote since the last record, and the reconciliation records every
artefact or code change no recorded command explains. Running it twice records nothing new the second time.

## Steps

1. Run `{SCRIPT}` from the repository root (append `--all` when the user asks for every feature, or
   `--feature-dir <dir>`).
2. Show the one-line result. Do not edit anything under `audit/`.
