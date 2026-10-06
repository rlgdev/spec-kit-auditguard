---
description: "(agent event, not for direct use) auditGuard: session_end"
scripts:
  py: scripts/python/auditguard.py event session_end
---

## Goal

This command is wired to the agent's `session_end` event by Spec Kit (`events:` in `extension.yml`). The event payload
arrives on stdin. auditGuard records the session boundary or closes a command that ended without its `after_` hook
(for example an escalation). It always exits 0 and prints nothing, so it never interrupts the agent.

Do not run this command by hand.
