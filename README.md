# OpenCode Go Usage (Omarchy plugin)

An [Omarchy](https://omarchy.org) bar widget that shows your AI coding
subscription usage at a glance — with a dedicated **OpenCode Go** tab.

It is a user-space extension of Omarchy's built-in `omarchy.agents` panel:
same bar icon, same popup, same meters — plus an `opencode-go` provider that
reads the official OpenCode Go usage endpoint and your local `opencode.db`.

## What it shows

- **Limits** — percentage used per allowance window with a meter and a live
  reset countdown. OpenCode Go reports Session (5-hour), Weekly (7-day), and
  Monthly (30-day).
- **Tokens by day** — the last week, today bolded, hover for detail.
- **Tokens by model** — per-model totals scaled to the heaviest model,
  hover for the input / output / cache split.
- **Subscription switch** — one chip per enabled provider (Claude, Codex,
  Fireworks, OpenCode Go), shown only when more than one is present.

The widget hides itself from the bar when it has nothing to show, and appears
the first time a scan finds usage.

## Usage

- **Click the bar icon** to open the panel; **Esc** closes it.
- **Refresh** by pressing **R** (or **Enter**) in the panel, or clicking the
  small **Refresh** button. The header shows **Updated HH:mm** — the last time
  a collector run finished.
- **Hover the bar icon** for the current limit percentages at a glance.
- Switching subscriptions: **h**/**l**, the provider chips, or middle-click the
  bar icon. Right-click the bar icon launches the agent.

## Requirements

- Omarchy (Quattro) with the built-in `omarchy.agents` collectors. Claude,
  Codex, and Fireworks tabs come from those stock collectors unchanged.
- For the OpenCode Go tab: the `opencode` CLI signed in, or an OpenCode API
  key. No extra packages — the collector is stdlib-only Python 3.

## Install

```sh
omarchy plugin add https://github.com/mrc04d/omarchy-opencode-go.git --enable
```

The widget lands in the right section of the bar. If it doesn't appear, move
it explicitly:

```sh
omarchy bar move io.github.mrc04d.opencode-go --section right
```

## OpenCode Go key

The collector resolves the key in this order (first non-empty wins):

1. `OPENCODE_GO_API_KEY`
2. `OPENCODE_API_KEY`
3. `~/.local/share/opencode/auth.json` → `opencode-go.key`

If you use the OpenCode CLI, the key is already there — nothing to configure.
The key is sent only to `https://opencode.ai/zen/go/v1/usage` as a Bearer
token; it is never written to the usage record, the cache, or any log.

## Configure

Settings live in the widget's entry in `~/.config/omarchy/shell.json`; set
them with `omarchy bar set io.github.mrc04d.opencode-go <key> <value>`.

| Key | Default | What it does |
|---|---|---|
| `refreshIntervalSec` | `900` | How often the usage records regenerate |
| `syncMode` | `"Off"` | `"On"` writes this machine's snapshot and merges others |
| `syncDir` | `""` | A folder synced by Syncthing, Dropbox, rsync, … |
| `syncFileName` | `<hostname>.json` | This machine's snapshot file |
| `syncDeviceId` | hostname | Stable device name inside the snapshot |

Numbers need `--json`. Per-provider enablement is nested, so pass the whole
`providers` object:

```sh
omarchy bar set io.github.mrc04d.opencode-go refreshIntervalSec 300 --json

omarchy bar set io.github.mrc04d.opencode-go providers '{
  "claude": { "enabled": true },
  "codex": { "enabled": true },
  "fireworks": { "enabled": true },
  "opencode-go": { "enabled": false }
}' --json
```

## How it works

Omarchy's `omarchy-agent-usage-update` writes one JSON record per provider to
`~/.local/state/omarchy/agents/usage/`; the panel just draws whatever it
finds. This plugin bundles a collector and a wrapper:

- `helpers/omarchy-agent-usage-opencode-go` — stdlib-only Python collector.
  Fetches the Go limit windows and scans `opencode.db` for local tokens.
- `helpers/agents-update.sh` — runs the stock updater for the other providers
  and this collector for OpenCode Go, writing `opencode-go.json` atomically.

Caches live under `~/.cache/omarchy/agent-usage/`. Nothing outside the plugin
directory, that cache, and the usage-record directory is written.

## Remove

```sh
omarchy plugin remove io.github.mrc04d.opencode-go
```

## Credits

Panel and collectors are derived from Omarchy's built-in `omarchy.agents`
plugin (MIT). Licensed under the MIT License — see [LICENSE](LICENSE).
