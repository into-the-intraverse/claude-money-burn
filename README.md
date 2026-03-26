# burn

Claude Code plugin that shows what your token usage would cost at Anthropic API rates. Reads the same data as the built-in `/stats` and adds cost estimates, breakdowns by project/model/time, and what-if comparisons.

> **Cheap to run.** `/burn` just calls a Python script — Claude reads the output and prints it. A single invocation costs ~2-3K tokens (~$0.01 on Opus API rates). To skip Claude entirely, run the script directly:
> ```bash
> python3 ~/.claude/plugins/claude-money-burn/skills/burn/scripts/estimate_cost.py        # current session
> python3 ~/.claude/plugins/claude-money-burn/skills/burn/scripts/estimate_cost.py --all   # all conversations
> ```

## What it does

Scans your local `~/.claude/` data and calculates what your usage would cost on the Anthropic API (pay-per-token). The dollar amounts are **not your actual bill** — they show the equivalent API cost with cache-aware pricing (cache reads at 10%, cache writes at 125% of input rate).

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

## How it works

Uses two data sources for accuracy:

1. **`~/.claude/stats-cache.json`** — for headline token totals in `--all` mode. This is the same data source `/stats` reads, so numbers match exactly.
2. **JSONL conversation files** — for per-conversation analysis, per-project breakdown, time-filtered views, and cost estimation. Applies the same filters as `/stats`: skips `isSidechain` messages (alternate branches) and `<synthetic>` model entries.

Tokens are tracked per-model per-message and priced with cache-aware rates (opus/sonnet/haiku). Subagent files are excluded — their tokens are already reflected in the parent conversation's API usage.

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
