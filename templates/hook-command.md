---
description: "auditGuard: record the {WHAT} of /speckit.{CMD} in the audit trail (hook {EVENT})"
scripts:
  sh: bash scripts/bash/auditguard.sh hook {EVENT} --via hooks
  ps: scripts/powershell/auditguard.ps1 hook {EVENT} --via hooks
  py: scripts/python/auditguard.py hook {EVENT} --via hooks
---

## User Input

```text
$ARGUMENTS
```

## Goal

Append the `{EVENT}` record of this feature to the audit trail. auditGuard is a recorder: it decides
nothing, repairs nothing and never changes the result of `/speckit.{CMD}`.

## Steps

1. From the repository root run `{SCRIPT}` with the environment variable `AUDITGUARD_CONTEXT=agent` set
   (bash: `AUDITGUARD_CONTEXT=agent {SCRIPT}`; PowerShell: `$env:AUDITGUARD_CONTEXT='agent'; {SCRIPT}`). If the
   user input above or the Setup step of `/speckit.{CMD}` names a feature directory, append `--feature-dir <dir>`.
2. Show its one-line output as is. If it prints `skipped`, auditGuard records through workflow steps in this
   project; say nothing more.
3. Continue with `/speckit.{CMD}`. The script always exits 0 in record mode; if it prints `could not record`,
   show that line and continue - the trail is reconciled at the next hook.
4. Never edit anything under `audit/` or `.specify/auditguard/`, and never run `auditguard decide`, `note`,
   `sprint` or `anchor`: those record decisions and actions of people.
