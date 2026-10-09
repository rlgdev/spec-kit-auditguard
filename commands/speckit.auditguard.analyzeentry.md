---
description: "auditGuard: record the start of /speckit.analyze in the audit trail (hook before_analyze)"
scripts:
  sh: bash scripts/bash/auditguard.sh hook before_analyze --via hooks
  ps: scripts/powershell/auditguard.ps1 hook before_analyze --via hooks
  py: scripts/python/auditguard.py hook before_analyze --via hooks
---

## User Input

```text
$ARGUMENTS
```

## Goal

Append the `before_analyze` record of this feature to the audit trail. auditGuard is a recorder: it decides
nothing, repairs nothing and never changes the result of `/speckit.analyze`.

## Steps

1. From the repository root run `{SCRIPT}` as it is. If the user input above or the Setup step of
   `/speckit.analyze` names a feature directory, append `--feature-dir <dir>`. Nothing else: no environment
   variables, no other flags.
2. Show its one-line output as is. If it prints `skipped`, auditGuard records through workflow steps in this
   project; say nothing more.
3. Continue with `/speckit.analyze`. The script always exits 0 in record mode; if it prints `could not record`,
   show that line and continue - the trail is reconciled at the next hook.
4. Never edit anything under `audit/` or `.specify/auditguard/`, and never run `auditguard decide`, `note`,
   `sprint` or `anchor`: those record decisions and actions of people.
