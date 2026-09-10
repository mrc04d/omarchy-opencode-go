#!/usr/bin/env bash
# Integration tests for agents-update.sh using stub stock updater + stub collector.
set -uo pipefail

PLUGIN="$HOME/.config/omarchy/plugins/io.github.mrc04d.opencode-go"
WRAPPER="$PLUGIN/helpers/agents-update.sh"
STOCK_LOG="$(mktemp)"
GO_LOG="$(mktemp)"
export STOCK_LOG GO_LOG
FAKEBIN="$(mktemp -d)"
STOCK_DIR="$(mktemp -d)"
export XDG_STATE_HOME="$(mktemp -d)"
USAGE="$XDG_STATE_HOME/omarchy/agents/usage"

# Fake stock updater: records argv, writes a claude record.
cat > "$FAKEBIN/omarchy-agent-usage-update" <<'EOF'
#!/usr/bin/env bash
printf 'RUN:%s\n' "$*" >> "$STOCK_LOG"
exit 0
EOF
chmod +x "$FAKEBIN/omarchy-agent-usage-update"

# Fake stock collector discovery dir (collectors live under $OMARCHY_PATH/bin/).
mkdir -p "$STOCK_DIR/bin"
for a in claude codex fireworks; do
  printf '#!/usr/bin/env bash\nexit 0\n' > "$STOCK_DIR/bin/omarchy-agent-usage-$a"
  chmod +x "$STOCK_DIR/bin/omarchy-agent-usage-$a"
done

# Stub the collector invoked by the wrapper (keep a pristine copy of the real one).
cp "$PLUGIN/helpers/omarchy-agent-usage-opencode-go" "$GO_LOG.real"
cat > "$PLUGIN/helpers/omarchy-agent-usage-opencode-go" <<'EOF'
#!/usr/bin/env bash
printf 'RUN:%s\n' "$*" >> "$GO_LOG"
printf '{"schemaVersion":1,"id":"opencode-go","name":"OpenCode Go","ready":false,"limits":[]}\n'
EOF
chmod +x "$PLUGIN/helpers/omarchy-agent-usage-opencode-go"
restore() { mv "$GO_LOG.real" "$PLUGIN/helpers/omarchy-agent-usage-opencode-go"; rm -rf "$FAKEBIN" "$STOCK_DIR" "${XDG_STATE_HOME:-}"; }
trap restore EXIT

run_wrapper() {
  PATH="$FAKEBIN:$PATH" OMARCHY_PATH="$STOCK_DIR" bash "$WRAPPER" "$@"
}

fail=0
check() { if [[ "$1" != "$2" ]]; then echo "FAIL: $3 (got: $1 want: $2)"; fail=1; else echo "ok: $3"; fi; }

: > "$STOCK_LOG"; : > "$GO_LOG"
run_wrapper --force
check "$(cat "$STOCK_LOG")" "RUN:--force" "plain run runs all stock with --force"
check "$(cat "$GO_LOG")" "RUN:--force" "go collector gets only --force"
[[ -f "$USAGE/opencode-go.json" ]] && echo "ok: record written" || { echo "FAIL: record missing"; fail=1; }

: > "$STOCK_LOG"; : > "$GO_LOG"
run_wrapper opencode-go
check "$(cat "$STOCK_LOG")" "" "only opencode-go runs no stock"
check "$(cat "$GO_LOG")" "RUN:" "go collector invoked with no flags"

: > "$STOCK_LOG"; : > "$GO_LOG"
run_wrapper claude opencode-go
check "$(cat "$STOCK_LOG")" "RUN:claude" "mixed runs only stock claude"
check "$(cat "$GO_LOG")" "RUN:" "go collector invoked for mixed"

: > "$STOCK_LOG"; : > "$GO_LOG"
run_wrapper bogus-agent
check "$(cat "$STOCK_LOG")" "" "unknown-only no-op: no stock"
check "$(cat "$GO_LOG")" "" "unknown-only no-op: no go"

: > "$STOCK_LOG"; : > "$GO_LOG"
run_wrapper --except opencode-go
check "$(cat "$STOCK_LOG")" "RUN:" "except go: stock runs with no args, go excluded"
check "$(cat "$GO_LOG")" "" "except go: go skipped"

: > "$STOCK_LOG"; : > "$GO_LOG"
run_wrapper --limits-only opencode-go
check "$(cat "$STOCK_LOG")" "" "limits-only go-only: no stock run"
check "$(cat "$GO_LOG")" "RUN:--limits-only" "limits-only forwarded to go collector"

: > "$STOCK_LOG"; : > "$GO_LOG"
run_wrapper --except codex
check "$(cat "$STOCK_LOG")" "RUN:--except codex" "except codex forwarded to stock updater"
check "$(cat "$GO_LOG")" "RUN:" "except codex does not exclude go"

: > "$STOCK_LOG"; : > "$GO_LOG"
run_wrapper --except claude claude
check "$(cat "$STOCK_LOG")" "" "requested-then-excluded: no stock run"
check "$(cat "$GO_LOG")" "" "requested-then-excluded: no go"

: > "$STOCK_LOG"; : > "$GO_LOG"
run_wrapper --except bogus 2>/dev/null
check "$(cat "$STOCK_LOG")" "RUN:" "unknown --except dropped, all stock run"
check "$(cat "$GO_LOG")" "RUN:" "unknown --except does not exclude go"

# write-safety: invalid collector output must preserve the prior record
printf '{"prior":true}\n' > "$USAGE/opencode-go.json"
cat > "$PLUGIN/helpers/omarchy-agent-usage-opencode-go" <<'EOF'
#!/usr/bin/env bash
printf 'not json\n'
EOF
chmod +x "$PLUGIN/helpers/omarchy-agent-usage-opencode-go"
run_wrapper opencode-go 2>/dev/null
check "$(cat "$USAGE/opencode-go.json")" '{"prior":true}' "invalid output preserves prior record"

# missing --except value is an argument error (non-zero, nothing run)
: > "$STOCK_LOG"
run_wrapper --except >/dev/null 2>&1; rc=$?
check "$rc" "1" "dangling --except exits non-zero"
check "$(cat "$STOCK_LOG")" "" "dangling --except runs nothing"

rm -f "$STOCK_LOG" "$GO_LOG"
if (( fail == 0 )); then echo ALL_PASS; else echo FAIL; fi
exit $fail
