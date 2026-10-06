#!/usr/bin/env bash
# End-to-end check against a real Spec Kit install (used by CI; runnable locally):
#   1. builds the archive and serves dist/ on localhost
#   2. specify init, then installs auditGuard from the archive
#   3. the 20 hooks are registered and the 4 agent events are wired into the agent's settings
#   4. configure: hooks on (integration: hooks) and off (integration: workflow)
#   5. a command lifecycle through the rendered hook commands, the agent events through Spec Kit's dispatcher
#      (session start, guard, stop) and verify
#
# Usage: tools/e2e-speckit.sh       (needs `specify` on PATH, git, python3)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${E2E_PORT:-8766}"
WORK="$(mktemp -d)"
trap 'kill "${SERVER:-}" 2>/dev/null || true; rm -rf "$WORK"' EXIT
PY="$(command -v python3 || command -v python)"

fail() { echo "E2E FAIL: $*" >&2; exit 1; }
expect() {  # expect <exit code> <command...>
    local want="$1"; shift
    set +e; "$@" > "$WORK/out.txt" 2>&1; local got=$?; set -e
    if [[ "$got" != "$want" ]]; then cat "$WORK/out.txt"; fail "expected exit $want, got $got: $*"; fi
}
contains() { grep -qF -- "$1" "$WORK/out.txt" || { cat "$WORK/out.txt"; fail "output lacks: $1"; }; }

"$PY" "$REPO/tools/build.py" >/dev/null
mkdir -p "$WORK/www"
cp "$REPO/dist/auditguard.zip" "$WORK/www/"
(cd "$WORK/www" && exec "$PY" -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
SERVER=$!
sleep 2

echo "== specify $(specify version 2>/dev/null | grep -o 'CLI Version *[0-9.]*' | grep -o '[0-9.]*$' || echo '?')"
cd "$WORK"
specify init lab --integration claude --script sh --ignore-agent-tools --non-interactive >/dev/null
cd lab
git init -q -b main && git config user.email e2e@example.com && git config user.name e2e && git config commit.gpgsign false
printf 'y\ny\n' | specify extension add auditguard --from "http://127.0.0.1:$PORT/auditguard.zip" >/dev/null
A=(bash .specify/extensions/auditguard/scripts/bash/auditguard.sh)

echo "== registration"
"$PY" - <<'PY'
import re
text = open(".specify/extensions.yml").read()
blocks = [b for b in text.split("- extension: ")[1:] if b.startswith("auditguard")]
assert len(blocks) == 20, f"expected 20 auditguard hooks, found {len(blocks)}"
PY
for ev in sessionstart stop sessionend guard; do
    grep -q "speckit.auditguard.$ev" .claude/settings.json || fail "event command speckit.auditguard.$ev not wired"
done
[[ -f .specify/extensions/auditguard/auditguard-config.yml ]] || fail "config not created from the template"

echo "== rendered hook commands"
SKILL=$(grep -rl "hook before_plan --via hooks" .claude | head -1)
[[ -n "$SKILL" ]] || fail "no rendered command for the before_plan hook"
while read -r p; do
    [[ -e "$p" ]] || fail "rendered path does not exist: $p"
done < <(grep -rhoE "\.specify/extensions/auditguard/[A-Za-z0-9_./-]+" .claude | sort -u)

echo "== configure"
expect 0 "${A[@]}" configure
contains "integration   : hooks"
contains "hooks         : 20 of 20 on"
git add -A && git commit -qm base
expect 0 "${A[@]}" sprint open S-1 --start 2026-01-01 --end 2099-12-31 --by e2e

echo "== a command through the hooks"
mkdir -p specs/001-demo && printf '# Spec\n' > specs/001-demo/spec.md
printf '{"feature_directory": "specs/001-demo"}' > .specify/feature.json
export AUDITGUARD_CONTEXT=agent
expect 0 "${A[@]}" hook before_plan --via hooks
contains "#2 command.started"
printf '# Plan\n' > specs/001-demo/plan.md
expect 0 "${A[@]}" hook after_plan --via hooks
contains "command.finished (pass)"
unset AUDITGUARD_CONTEXT

echo "== agent events through Spec Kit's dispatcher"
export CLAUDE_PROJECT_DIR="$PWD"
printf '{"hook_event_name":"SessionStart","session_id":"e2e-1","source":"startup"}' | "$PY" .specify/events.py speckit.auditguard.sessionstart session_start 15 \
    || fail "session_start handler failed"
grep -q '"kind": "session.started"' audit/sprints/S-1/_project/journal.jsonl || fail "session not recorded"
set +e
printf '{"hook_event_name":"PreToolUse","tool_name":"Edit","cwd":"%s","tool_input":{"file_path":"%s/audit/sprints.yml"}}' "$PWD" "$PWD" \
    | "$PY" .specify/events.py speckit.auditguard.guard pre_tool_use 10 2> "$WORK/out.txt"; code=$?
set -e
[[ $code == 2 ]] || fail "guard did not block an edit of the trail (exit $code)"
contains "read-only"
set +e
printf '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"bash .specify/extensions/auditguard/scripts/bash/auditguard.sh decide design approve --by agent"}}' \
    | "$PY" .specify/events.py speckit.auditguard.guard pre_tool_use 10 2> "$WORK/out.txt"; code=$?
set -e
[[ $code == 2 ]] || fail "guard did not block 'auditguard decide' for the agent (exit $code)"
expect 0 "${A[@]}" hook before_tasks --via hooks
printf '{"hook_event_name":"Stop","session_id":"e2e-1"}' | "$PY" .specify/events.py speckit.auditguard.stop stop 30 \
    || fail "stop handler must exit 0"

echo "== integration: workflow"
sed -i.bak 's/^integration: hooks/integration: workflow/' .specify/extensions/auditguard/auditguard-config.yml && rm -f .specify/extensions/auditguard/auditguard-config.yml.bak
expect 0 "${A[@]}" configure
contains "hooks         : 0 of 20 on"
expect 0 "${A[@]}" hook after_tasks --via hooks
contains "skipped"

echo "== verify"
git add -A && git commit -qm "trail"
expect 0 "${A[@]}" verify --golden --offline
contains "internal : PASS"
expect 0 "${A[@]}" render --html
[[ -f audit/viewer/index.html && -f audit/viewer/data.js ]] || fail "viewer not rendered"

echo "E2E PASS"
