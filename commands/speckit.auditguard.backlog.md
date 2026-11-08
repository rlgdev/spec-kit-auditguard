---
description: "auditGuard: the loop-feedback items archiGuard fed back to the domain master design, as backlog candidates with their triage status"
scripts:
  sh: bash scripts/bash/auditguard.sh backlog
  ps: scripts/powershell/auditguard.ps1 backlog
  py: scripts/python/auditguard.py backlog
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Goal

Show the loop feedback recorded in the audit trail: the items archiGuard reported after coding (code no context
maps, a new build unit, an undeclared topology, a dependency the domain map does not allow, ...), merged across
features, with the triage status a person gave them (`new`, `deferred`, `accepted`, `in-backlog`, `rejected`,
`resolved`). Read-only: nothing is recorded.

## Steps

1. Run `{SCRIPT}` from the repository root. Append `--sprint <id>` if the user input names a sprint,
   `--status <list>` (`new,deferred,accepted,in-backlog,rejected,resolved` or `all`; default `new,accepted`) if it
   asks for a status, and `--format csv --out <file>` if the user wants a file to import into the tracker.
2. Show the output as is, then summarise in at most three lines: how many items wait for a triage and which ones have
   high impact.
3. **Never triage an item yourself.** Accepting, rejecting or deferring is a decision of a person (the lead architect
   or a domain owner): tell the user the command to run, `auditguard decide feedback:<id> accept|reject|defer --by
   <name> --reason <text>` (and `--ref <tracker key>` once the backlog item exists). `decide` is refused in an agent
   context.
