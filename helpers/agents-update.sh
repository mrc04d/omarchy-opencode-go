#!/usr/bin/env bash
# Wraps omarchy-agent-usage-update and adds the opencode-go collector.
# Grammar: [--force] [--limits-only] [--except <agent>] [agent...]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECTOR="$SCRIPT_DIR/omarchy-agent-usage-opencode-go"
USAGE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/agents/usage"
omarchy_root="${OMARCHY_PATH:-/usr/share/omarchy}"

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

stock_ids=()
for collector in "$omarchy_root"/bin/omarchy-agent-usage-*; do
  [[ -x $collector ]] || continue
  agent="${collector##*/omarchy-agent-usage-}"
  [[ $agent == "update" ]] && continue
  stock_ids+=("$agent")
done
if (( ${#stock_ids[@]} )); then
  mapfile -t stock_ids < <(printf '%s\n' "${stock_ids[@]}" | sort)
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
    [[ "$agent" == "opencode-go" ]] && continue
    stock_args+=(--except "$agent")
  done
  if (( ${#only[@]} > 0 )); then
    stock_args+=("${stock_only[@]}")
  fi
  if (( ${#stock_args[@]} > 0 )); then
    omarchy-agent-usage-update "${stock_args[@]}" || true
  else
    omarchy-agent-usage-update || true
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

command -v jq >/dev/null 2>&1 || { echo "agents-update: jq is required but not found" >&2; exit 1; }

install -d -m 700 "$USAGE_DIR"
tmp="$(mktemp "$USAGE_DIR/.opencode-go.XXXXXX")" || { echo "agents-update: could not create temp file" >&2; exit 1; }
trap 'rm -f "$tmp"' EXIT

if ! record="$("$COLLECTOR" "${flags[@]}")" || [[ -z $record ]] || ! jq -e . >/dev/null 2>&1 <<<"$record"; then
  echo "agents-update: opencode-go collector failed" >&2
  exit 1
fi

printf '%s\n' "$record" >"$tmp"
if ! mv "$tmp" "$USAGE_DIR/opencode-go.json"; then
  echo "agents-update: could not install opencode-go record" >&2
  exit 1
fi
trap - EXIT
exit 0
