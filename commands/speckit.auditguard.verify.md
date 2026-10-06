---
description: "auditGuard: verify every chain, seal and evidence hash, and the trail against git and the other golden sources"
scripts:
  sh: bash scripts/bash/auditguard.sh verify --golden
  ps: scripts/powershell/auditguard.ps1 verify --golden
  py: scripts/python/auditguard.py verify --golden
---

## User Input

```text
$ARGUMENTS
```

## Goal

Prove the trail: internally (no event edited, removed or added out of order; evidence and seals intact) and against
the golden sources (G1 commits reachable, G2 artefact hashes match git, G3 every commit explained, G4 evidence
authentic, G5 decisions agree with the handover pins, the standards lock, the ledger and the workflow runs, G6 sealed
sprints anchored as tags, G7 chain heads anchored as git notes, G8 scopeGuard reports recompute with `--recompute`).

## Steps

1. Run `{SCRIPT}` from the repository root. Append `--sprint <id>` or `--recompute` if the user input asks for it.
2. Show the output as is. Exit code 0 = everything verified; 1 = findings.
3. For every finding, quote the `reproduce:` command so a person can check it by hand. `ephemeral` events are
   informational (a recorded working state that changed again before a commit). Never edit the trail to make a
   finding go away: a finding is a fact about the repository for a person to decide.
