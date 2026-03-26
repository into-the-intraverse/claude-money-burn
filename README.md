# burn

Claude Code plugin that shows how much your token usage would cost at Anthropic API rates. Helps you decide between subscription and API: see if your plan pays for itself, or how much you'd save by switching. Works the same way as the built-in `/stats` — reads API-reported `usage` fields from local conversation JSONL files — but adds cost estimates, breakdowns by project/model/time, and what-if comparisons.

> **Cheap to run.** `/burn` just calls a Python script — Claude reads the output and prints it. A single invocation costs ~2-3K tokens (~$0.01 on Opus API rates). To skip Claude entirely, run the script directly:
> ```bash
> python3 ~/.claude/plugins/claude-money-burn/skills/burn/scripts/estimate_cost.py        # current session
> python3 ~/.claude/plugins/claude-money-burn/skills/burn/scripts/estimate_cost.py --all   # all conversations
> ```

## What it does

Scans your local `~/.claude/` conversation logs and calculates what your usage would cost on the Anthropic API (pay-per-token). The dollar amounts are **not your actual bill** — they show the equivalent API cost with cache-aware pricing (cache reads at 10%, cache writes at 125% of input rate).

- **On a subscription?** See if your plan pays for itself vs API pricing.
- **On the API?** See where your money goes — by project, model, and time period.
- **Considering switching?** Compare what you'd pay on the other plan.

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

Parses assistant messages from JSONL files and reads API-reported `usage` fields (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`). Applies cache-aware pricing per model (opus/sonnet/haiku). Subagent JSONL files are excluded to match the built-in `/stats` reporting.

Stdlib-only Python, no dependencies.

## Accuracy vs `/stats`

Token counts closely match the built-in `/stats` command:

| Metric | `/stats` | `/burn` | Match |
|--------|----------|---------|-------|
| Input tokens | 1.1M | 944.5K | ~86% |
| Output tokens | 4.2M | 4.17M | ~99% |
| Total tokens | 5.3M | 5.1M | ~96% |

Output is near-exact. The ~15% input gap comes from API calls that `/stats` tracks internally but don't get recorded in the JSONL (failed/retried calls, system prompt overhead).

### Why subagent files are excluded

Subagent conversations (`{project}/{uuid}/subagents/agent-*.jsonl`) are excluded because including them inflates output by ~50% vs `/stats`. This confirms `/stats` doesn't count subagent tokens either.

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
