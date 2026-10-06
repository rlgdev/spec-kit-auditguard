# archiGuard escalation - speckit.tasks · step B

- Feature: `specs/001-place-order`
- Reason: no progress: the same findings came back as in iteration 0 (the repairs conflict or cannot satisfy the rules)
- Iteration: 1 of budget 3
- Commit: 65dc9ac648582c0e08a7ba1de4663dbf2af7f6d3 (uncommitted changes)
- Pins: rulebook acme-standards@v2026.10.1 · domain map 1.4.0 · spec 3#803b2ac1ecefaf1b7cba4922a6146300511a013ead43b4ec8c96d574f8a4c71e

The step stopped. The human of this step decides between: fix the artefact, change the specification through the BA (RFI), or raise a waiver in the decision ledger (approved, with owner and expiry).

## Open findings

### 1. [A3.5] ARCH-201

- Finding: no fitness-test task for ARCH-201 Layers depend downwards only (api -> application -> domain)
- Where: tasks.md
- Repairable by the agent: yes
- Suggested fix: add '- [ ] T0xx [FITNESS] <what is verified> - ARCH-201'
- What was attempted: The layering rule ARCH-201 has no fitness task: the profile java-service has no import check for the new module yet. Decision needed: add the task with the generic layering check, or waive ARCH-201.
- Blocker: TODO(agent)
- Decision needed (fix the artefact · RFI to the BA · waiver in the ledger): TODO(agent)

## Iteration history

| Iteration | Status | Blocking | Gates |
|---|---|---|---|
| 0 | violation | 1 | scope tasks: pass, A3 A3.5: violation |
| 1 | escalated | 1 | scope tasks: pass, A3 A3.5: violation |

