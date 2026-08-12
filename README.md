# tokenstats

Aggregate token usage and spend across every agent CLI on your machine — in
one table, or a GitHub-style contribution calendar.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![tests](https://img.shields.io/badge/tests-33%20passed-brightgreen)

Supported agents: **pi, prime-agent, Claude Code, Codex, opencode, Cursor,
agy (Gemini CLI), aider, goose, qwen, amp**.

```bash
tokenstats                      # per-agent table
tokenstats model                # per-model table
tokenstats day --since 7        # last week by day
tokenstats cal                  # GitHub-style activity calendar
tokenstats cal --since 30 --metric cost   # last 30 days of spend
tokenstats agents               # which agents are detected
```

## Features

- **One command, every agent** — scans the local data directories of 11 agent
  CLIs and merges them into a single view.
- **GitHub-style calendar** — daily activity quantized into 5 GitHub-green
  levels (their official dark/light palettes), so a bright cell means "one of
  your busiest days", not a raw count. Wide terminals (>120 cols) get 2-char
  cells.
- **Configurable pricing** — built-in USD/M-token price table with prefix
  matching; override per-model via `--prices`. Unknown models fall back to the
  cost recorded by the agent itself; free-tier models cost $0.
- **Machine-readable output** — `--json` for scripting.
- **Defensive parsing** — a malformed line or missing data source never
  crashes the run; `tokenstats agents` shows what was detected.

## Supported agents

| Agent | Local data source | Tokens | Cost | Verified |
|---|---|---|---|---|
| pi | `~/.pi/agent/sessions/*/*.jsonl` | ✅ recorded | ✅ recorded | ✅ |
| prime-agent | `~/.prime/agent/sessions/*.jsonl` | ✅ recorded | ✅ recorded | ✅ |
| Claude Code | `~/.claude/projects/*/*.jsonl` | ✅ recorded | price table | ✅ |
| opencode | `~/.local/share/opencode/opencode.db` | ✅ recorded | ✅ recorded | ✅ |
| Codex | `~/.codex/sessions/**/*.jsonl` | — not recorded | — | ✅ |
| Cursor | `~/.cursor/chats/*/*/meta.json` | — not recorded | — | ✅ |
| agy / Gemini CLI | `~/.gemini/antigravity-cli/conversation_summaries.db` | — not recorded | — | ✅ |
| aider | `.aider.tokens.json` in `--dirs` | ⚠️ best-effort | — | ⚠️ |
| goose | `~/.local/share/goose/sessions/*.jsonl` | ⚠️ best-effort | ⚠️ | ⚠️ |
| qwen | `~/.qwen/sessions/*.jsonl` | ⚠️ best-effort | ⚠️ | ⚠️ |
| amp | `~/.local/share/amp` | ⚠️ best-effort | ⚠️ | ⚠️ |

Agents that don't record usage locally (Codex, Cursor, agy) still show their
session counts, so you get a complete activity timeline across all tools.

## Install

Requires Python 3.11+.

```bash
# with uv (recommended)
uv sync
uv run tokenstats

# or pip
pip install .
tokenstats
```

## Usage

```
Usage: tokenstats [OPTIONS] [VERB]

  verb: agent (default) | model | day | cal | agents

Options:
  --since N       Only the last N days
  --prices FILE   Price table (TOML or JSON), see prices.example.toml
  --dirs PATH     Extra project dirs to scan for aider data
  --json          Emit JSON instead of a table
  --metric M      Calendar metric: tokens (default) | cost
  --weeks N       Calendar span in weeks (default 52)
  --theme T       Calendar theme: dark (default) | light
  -v              Warn about models without a configured price
```

`--since` implies a matching calendar window: `cal --since 30` automatically
draws a 5-week calendar.

### Calendar

```text
  Sessions 467   tokens 6.75B   Active days 39   Peak 2026-08-05 (850.85M)

      Aug   Sep     Oct     Nov       Dec     Jan     Feb     Mar       Apr     May       Jun     Jul     Aug
Sun  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
     ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░██████
Wed  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░██████
Fri  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░█████
     ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
  Less ░░░░░░ More  cutoffs 13.92M / 99.33M / 182.73M+
```

## Pricing

Costs are estimated from built-in USD-per-1M-token prices (early 2026 list
prices). Override with `--prices prices.toml`:

```toml
[models."claude-opus"]
input = 5.0
output = 25.0

[models."grok-4"]
input = 2.0
output = 8.0
```

- Keys match model names by prefix, so `"claude-opus"` also covers
  `claude-opus-5-20260230`; provider prefixes like `deepseek/` are stripped.
- `cache_read` / `cache_write` default to `0.1x` / `1.25x` of the input price
  (Anthropic's rule) unless configured.
- Models without a configured price fall back to the cost the agent itself
  recorded; models with no price and no recorded cost show `—`.
- Model names containing `free` are billed $0.

## Tests

```bash
uv run pytest tests/
```

33 tests cover every parser (fixture data for all 11 agents), pricing
matching/fallback, aggregation, and calendar quantiles.

## Limitations

- Codex, Cursor and agy store no token usage locally — session counts only.
- aider/goose/qwen/amp parsers are best-effort and unverified.
- Scanning ~2GB of pi history takes ~4s; there is no cache yet.
- Prices are approximations — calibrate with `--prices` for exact spend.

## License

[MIT](LICENSE)
