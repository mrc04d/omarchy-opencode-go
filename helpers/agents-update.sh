#!/usr/bin/bash
# Wraps omarchy-agent-usage-update and adds the opencode-go collector.
# Grammar: [--force] [--limits-only] [--except <agent>] [agent...]
#
# Security posture: fixed absolute tool paths (no PATH lookup), a fixed install
# root (no environment redirection of which executables run), an absolute
# stock-updater path, a validated bundled collector, and a hard subprocess
# timeout. No value taken from the environment selects an executable.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
COLLECTOR="$SCRIPT_DIR/omarchy-agent-usage-opencode-go"
USAGE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/agents/usage"
OMARCHY_ROOT=/usr/share/omarchy

SORT=/usr/bin/sort
JQ=/usr/bin/jq
INSTALL=/usr/bin/install
MKTEMP=/usr/bin/mktemp
MV=/usr/bin/mv
RM=/usr/bin/rm
TIMEOUT=/usr/bin/timeout
STOCK=/usr/bin/omarchy-agent-usage-update
SUBPROCESS_TIMEOUT_SECONDS=120

force=0
limits_only=0
excluded=()
only=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) force=1 ;;
    --limits-only) limits_only=1 ;;
    --except)
      if [[ $# -lt 2 ]]; then
        echo "agents-update: --except requires an agent" >&2
        exit 1
      fi
      excluded+=("$2")
      shift
      ;;
    *) only+=("$1") ;;
  esac
  shift
done

flags=()
(( force )) && flags+=(--force)
(( limits_only )) && flags+=(--limits-only)

is_excluded() {
  local needle="$1" item
  for item in "${excluded[@]}"; do [[ "$item" == "$needle" ]] && return 0; done
  return 1
}

# Stock agent ids, discovered only from the trusted installed root.
stock_ids=()
for collector in "$OMARCHY_ROOT"/bin/omarchy-agent-usage-*; do
  [[ -x $collector ]] || continue
  agent="${collector##*/omarchy-agent-usage-}"
  [[ $agent == "update" ]] && continue
  stock_ids+=("$agent")
done
if (( ${#stock_ids[@]} )); then
  mapfile -t stock_ids < <(printf '%s\n' "${stock_ids[@]}" | "$SORT")
fi

in_stock() {
  local needle="$1" item
  for item in "${stock_ids[@]}"; do [[ "$item" == "$needle" ]] && return 0; done
  return 1
}

# --- stock updater ---------------------------------------------------------
stock_only=()
if (( ${#only[@]} > 0 )); then
  for agent in "${only[@]}"; do
    in_stock "$agent" || continue
    is_excluded "$agent" && continue
    stock_only+=("$agent")
  done
fi

if ! { (( ${#only[@]} > 0 )) && (( ${#stock_only[@]} == 0 )); }; then
  stock_args=("${flags[@]}")
  for agent in "${excluded[@]}"; do
    in_stock "$agent" || continue
    stock_args+=(--except "$agent")
  done
  if (( ${#only[@]} > 0 )); then
    stock_args+=("${stock_only[@]}")
  fi
  if [[ -x $STOCK ]]; then
    if (( ${#stock_args[@]} > 0 )); then
      "$TIMEOUT" "$SUBPROCESS_TIMEOUT_SECONDS" "$STOCK" "${stock_args[@]}" || true
    else
      "$TIMEOUT" "$SUBPROCESS_TIMEOUT_SECONDS" "$STOCK" || true
    fi
  fi
fi

# --- opencode-go collector -------------------------------------------------
wanted=0
if (( ${#only[@]} > 0 )); then
  for agent in "${only[@]}"; do [[ "$agent" == "opencode-go" ]] && wanted=1; done
  is_excluded "opencode-go" && wanted=0
else
  is_excluded "opencode-go" || wanted=1
fi

(( wanted )) || exit 0

[[ -x $JQ ]] || { echo "agents-update: jq is required but not found" >&2; exit 1; }

# The bundled collector must be a regular, executable file owned by us; never a
# symlink or a file substituted through the environment.
if [[ ! -f $COLLECTOR || -L $COLLECTOR || ! -x $COLLECTOR || ! -O $COLLECTOR ]]; then
  echo "agents-update: bundled collector is not a trusted executable" >&2
  exit 1
fi

if ! "$INSTALL" -d -m 700 "$USAGE_DIR"; then
  echo "agents-update: could not create $USAGE_DIR" >&2
  exit 1
fi
tmp="$("$MKTEMP" "$USAGE_DIR/.opencode-go.XXXXXX")" || { echo "agents-update: could not create temp file" >&2; exit 1; }
trap '"$RM" -f "$tmp"' EXIT

if ! record="$("$TIMEOUT" "$SUBPROCESS_TIMEOUT_SECONDS" "$COLLECTOR" "${flags[@]}")" || [[ -z $record ]] || ! "$JQ" -e . >/dev/null 2>&1 <<<"$record"; then
  echo "agents-update: opencode-go collector failed" >&2
  exit 1
fi

printf '%s\n' "$record" >"$tmp"
if ! "$MV" "$tmp" "$USAGE_DIR/opencode-go.json"; then
  echo "agents-update: could not install opencode-go record" >&2
  exit 1
fi
trap - EXIT
exit 0
