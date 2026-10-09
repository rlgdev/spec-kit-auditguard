---
description: "auditGuard: apply auditguard-config.yml (profile, integration) to Spec Kit's hooks and the agent's events, and show what is in force"
scripts:
  sh: bash scripts/bash/auditguard.sh configure
  ps: scripts/powershell/auditguard.ps1 configure
  py: scripts/python/auditguard.py configure
---

## User Input

```text
$ARGUMENTS
```

## Goal

Switch auditGuard's twenty hooks in `.specify/extensions.yml` on (`integration: hooks`) or off (`integration:
workflow`), unwire the agent events the profile switches off (`profile: light`, the default, unwires all four; `full`
keeps them), create the sprint register and the `.gitattributes` lines the hash chain needs, and report what is in
force: profile, hooks, events, collectors found, golden sources, current sprint.

## Steps

1. If the user asks to change a setting (for example "the full profile", "use workflow", "enforce mode", "record
   sessions"), edit `.specify/extensions/auditguard/auditguard-config.yml` accordingly - it is the only file you may
   change for this command. Unknown keys are errors.
2. Run `{SCRIPT}` from the repository root and show the output as is.
3. If the output says `NOT WIRED`, the profile wants agent events Spec Kit has not registered: tell the user to run
   `specify extension disable auditguard && specify extension enable auditguard` and then this command again. Do not
   edit the agent's settings file yourself.
4. If the output says no sprint is open, tell the user the command to run (`auditguard sprint open <id> --name ...
   --start YYYY-MM-DD --end YYYY-MM-DD --by <name>`); do not run it yourself - opening a sprint is a person's action.
