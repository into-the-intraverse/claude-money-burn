# burn

Claude Code plugin that shows what your token usage would cost at Anthropic API rates. Token counts match the built-in `/stats` exactly. Adds cost estimates, breakdowns by project/model/time, and what-if comparisons.

> **Cheap to run.** `/burn` just calls a Python script — Claude reads the output and prints it. A single invocation costs ~2-3K tokens (~$0.01 on Opus API rates). To skip Claude entirely, run the script directly:
> ```bash
> python3 ~/.claude/plugins/claude-money-burn/skills/burn/scripts/estimate_cost.py        # current session
> python3 ~/.claude/plugins/claude-money-burn/skills/burn/scripts/estimate_cost.py --all   # all conversations
> ```

## What it does

Scans your local `~/.claude/` data and calculates what your usage would cost on the Anthropic API (pay-per-token). The dollar amounts are **not your actual bill** — they show the equivalent API cost so you can compare subscription vs pay-per-token.

- **On a subscription?** See if your plan pays for itself vs API pricing.
- **On the API?** See where your money goes — by project, model, and time period.
- **Considering switching?** Compare what you'd pay on each plan.

## Installation

```bash
/plugin install into-the-intraverse/claude-money-burn
```

Or manually:

```bash
git clone https://github.com/into-the-intraverse/claude-money-burn ~/.claude/plugins/claude-money-burn
```

## Usage

```
/burn                    # Current session
/burn --all              # All conversations
/burn --all --days 7     # Last 7 days
/burn --all --top 10     # Top 10 by cost
/burn --all --export csv # Export to CSV
```

## Pricing

Rates from [platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing) (USD per million tokens):

| Model | Input | Output | Cache write | Cache read |
|-------|------:|-------:|------------:|-----------:|
| Opus 4.6/4.5 | $5 | $25 | $6.25 (1.25x) | $0.50 (0.10x) |
| Sonnet 4.6/4.5/4 | $3 | $15 | $3.75 (1.25x) | $0.30 (0.10x) |
| Haiku 4.5 | $1 | $5 | $1.25 (1.25x) | $0.10 (0.10x) |
| Opus fast mode | $30 | $150 | $37.50 (1.25x) | $3.00 (0.10x) |

### What's included in the estimate

| Pricing factor | Accounted for? | Notes |
|---|---|---|
| Per-model rates | Yes | Tokens tracked per-message, priced by actual model used |
| Prompt caching | Yes | Cache reads at 0.10x, cache writes at 1.25x input rate |
| Fast mode (6x) | Yes | Detected from `speed` field on each message |
| Batch API (50% off) | N/A | Claude Code is interactive, not batch |
| Long context (2x >200k) | N/A | Opus 4.6/Sonnet 4.6 have standard pricing at all lengths |
| Data residency (1.1x) | No | Only applies if `inference_geo` is set to US-only |

## How it works

Uses two data sources for accuracy:

1. **`~/.claude/stats-cache.json`** — for headline token totals in `--all` mode. This is the same data source `/stats` reads, so numbers match exactly.
2. **JSONL conversation files** — for per-conversation analysis, per-project breakdown, time-filtered views, and cost estimation. Applies the same filters as `/stats`: skips `isSidechain` messages (alternate branches) and `<synthetic>` model entries.

Tokens are tracked per-model per-message and priced with cache-aware rates. Fast mode messages (from `/fast` toggle) are detected and priced at 6x. Subagent files are excluded — their tokens are already reflected in the parent conversation's API usage.

Stdlib-only Python, no dependencies.

## Structure

```
claude-money-burn/
├── .claude-plugin/
│   └── plugin.json              # Plugin metadata
├── skills/
│   └── burn/
│       ├── SKILL.md             # Skill definition
│       └── scripts/
│           └── estimate_cost.py # Token estimator
└── README.md
```
