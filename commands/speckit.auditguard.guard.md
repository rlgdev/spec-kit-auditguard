---
description: "(agent event, not for direct use) auditGuard guard for PreToolUse"
scripts:
  py: scripts/python/auditguard.py event pre_tool_use
---

## Goal

This command is wired to the agent's `pre_tool_use` event by Spec Kit (`events:` in `extension.yml`). The tool
payload arrives on stdin. The script exits 2 with the reason on stderr when an agent edits the audit trail or the
auditGuard extension, or runs an auditGuard command reserved for people (`decide`, `note`, `sprint open`,
`sprint close`, `anchor`). It is a convenience: CODEOWNERS on `audit/` and `auditguard verify` in CI are the
guarantee.

Do not run this command by hand.
