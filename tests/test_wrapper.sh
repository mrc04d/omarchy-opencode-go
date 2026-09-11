#!/usr/bin/bash
# Integration tests for agents-update.sh.
#
# The hardened wrapper uses fixed absolute paths, so this test runs a COPY of it
# with the trusted root and the stock-updater path rewritten to temp stubs. The
# shipped script is never edited; only the copy is.
set -uo pipefail

REAL="$HOME/.config/omarchy/plugins/io.github.mrc04d.opencode-go"
TMP="$(mktemp -d)"
WORK="$TMP/work"; FAKEBIN="$TMP/fakebin"; STOCK_DIR="$TMP/stock"
mkdir -p "$WORK" "$FAKEBIN" "$STOCK_DIR/bin"
export XDG_STATE_HOME="$TMP/state"
USAGE="$XDG_STATE_HOME/omarchy/agents/usage"
STOCK_LOG="$TMP/stock.log"; GO_LOG="$TMP/go.log"
export STOCK_LOG GO_LOG

fail=0
check() { if [[ "$1" != "$2" ]]; then echo "FAIL: $3 (got: '$1' want: '$2')"; fail=1; else echo "ok: $3"; fi; }

# Fake stock updater: records argv.
cat > "$FAKEBIN/omarchy-agent-usage-update" <<'EOF'
#!/usr/bin/bash
printf 'RUN:%s\n' "$*" >> "$STOCK_LOG"
exit 0
EOF
chmod +x "$FAKEBIN/omarchy-agent-usage-update"

# Fake stock collectors, discovered from the (rewritten) trusted root.
for a in claude codex fireworks; do
  printf '#!/usr/bin/bash\nexit 0\n' > "$STOCK_DIR/bin/omarchy-agent-usage-$a"
  chmod +x "$STOCK_DIR/bin/omarchy-agent-usage-$a"
done

# Working copy of the wrapper, with trusted paths rewritten to the stubs.
sed -e "s#/usr/share/omarchy#$STOCK_DIR#g" \
    -e "s#/usr/bin/omarchy-agent-usage-update#$FAKEBIN/omarchy-agent-usage-update#g" \
    "$REAL/helpers/agents-update.sh" > "$WORK/agents-update.sh"
chmod +x "$WORK/agents-update.sh"

# Stub the bundled collector (a copy lives in SCRIPT_DIR of the working copy).
write_collector_stub() {
  cat > "$WORK/omarchy-agent-usage-opencode-go" <<'EOF'
#!/usr/bin/bash
printf 'RUN:%s\n' "$*" >> "$GO_LOG"
printf '{"schemaVersion":1,"id":"opencode-go","name":"OpenCode Go","ready":false,"limits":[]}\n'
EOF
  chmod +x "$WORK/omarchy-agent-usage-opencode-go"
}
write_collector_stub

run_wrapper() { bash "$WORK/agents-update.sh" "$@"; }
reset_logs() { : > "$STOCK_LOG"; : > "$GO_LOG"; }

reset_logs
run_wrapper --force
check "$(cat "$STOCK_LOG")" "RUN:--force" "plain run runs all stock with --force"
check "$(cat "$GO_LOG")" "RUN:--force" "go collector gets only --force"
[[ -f "$USAGE/opencode-go.json" ]] && echo "ok: record written" || { echo "FAIL: record missing"; fail=1; }

reset_logs
run_wrapper opencode-go
check "$(cat "$STOCK_LOG")" "" "only opencode-go runs no stock"
check "$(cat "$GO_LOG")" "RUN:" "go collector invoked with no flags"

reset_logs
run_wrapper claude opencode-go
check "$(cat "$STOCK_LOG")" "RUN:claude" "mixed runs only stock claude"
check "$(cat "$GO_LOG")" "RUN:" "go collector invoked for mixed"

reset_logs
run_wrapper claude bogus-agent
check "$(cat "$STOCK_LOG")" "RUN:claude" "unknown id filtered from positionals"
check "$(cat "$GO_LOG")" "" "go not wanted for stock-only request"

reset_logs
run_wrapper bogus-agent
check "$(cat "$STOCK_LOG")" "" "unknown-only is a silent no-op: no stock"
check "$(cat "$GO_LOG")" "" "unknown-only is a silent no-op: no go"

reset_logs
run_wrapper --except opencode-go
check "$(cat "$STOCK_LOG")" "RUN:" "except go: stock runs with no args"
check "$(cat "$GO_LOG")" "" "except go: go skipped"

reset_logs
run_wrapper --except claude claude
check "$(cat "$STOCK_LOG")" "" "requested-then-excluded: no stock run"
check "$(cat "$GO_LOG")" "" "requested-then-excluded: no go"

reset_logs
run_wrapper --except bogus 2>/dev/null
check "$(cat "$STOCK_LOG")" "RUN:" "unknown --except dropped, all stock run"
check "$(cat "$GO_LOG")" "RUN:" "unknown --except does not exclude go"

# write-safety: invalid collector output must preserve the prior record
printf '{"prior":true}\n' > "$USAGE/opencode-go.json"
cat > "$WORK/omarchy-agent-usage-opencode-go" <<'EOF'
#!/usr/bin/bash
printf 'not json\n'
EOF
chmod +x "$WORK/omarchy-agent-usage-opencode-go"
run_wrapper opencode-go
check "$(cat "$USAGE/opencode-go.json")" '{"prior":true}' "invalid output preserves prior record"

# bundled-collector identity: a symlink must be refused
write_collector_stub
mv "$WORK/omarchy-agent-usage-opencode-go" "$WORK/real-collector"
ln -s "$WORK/real-collector" "$WORK/omarchy-agent-usage-opencode-go"
run_wrapper opencode-go >/dev/null 2>&1; rc=$?
check "$rc" "1" "symlinked collector is refused"
rm -f "$WORK/omarchy-agent-usage-opencode-go"
write_collector_stub

# missing --except value is an argument error (non-zero, nothing run)
reset_logs
run_wrapper --except >/dev/null 2>&1; rc=$?
check "$rc" "1" "dangling --except exits non-zero"
check "$(cat "$STOCK_LOG")" "" "dangling --except runs nothing"

rm -rf "$TMP"
exit $fail
