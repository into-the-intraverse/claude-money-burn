---
name: burn
description: >
  Show how much money you're burning on Claude Code. Use this skill whenever the user wants to
  check costs, spending, token usage, or billing for their Claude Code sessions. Trigger on /burn,
  and also when users ask things like "how much did i burn", "what's my burn rate", "what's my usage",
  "how many tokens", "am I spending too much", or any question about Claude Code session expenses.
user_invocable: true
---

# Burn - Cost Estimator

Show what Claude Code token usage would cost at Anthropic API rates. Token counts match `/stats` exactly (reads the same `stats-cache.json`). Applies current per-model pricing with cache-aware rates and fast mode detection (6x) to produce cost estimates, breakdowns, and what-if comparisons.

## How to run

The script is at `scripts/estimate_cost.py` relative to this SKILL.md file. Run it with `python3`.

### No arguments — current session only

```bash
python3 <skill-dir>/scripts/estimate_cost.py
```

Auto-detects the current session's JSONL file from the working directory. Shows a compact cost summary for this session.

### `--all` — all conversations

```bash
python3 <skill-dir>/scripts/estimate_cost.py --all
```

Full report across all conversations: totals, cost by time period, model comparison, top expensive conversations, cost by project, and warnings.

### Additional flags (combine with `--all`)

- `--days N` — filter to last N days
- `--top N` — show top N conversations (default 10)
- `--export csv|json` — export to file
- `--claude-dir <path>` — custom `.claude` directory path

### Examples

| User says | Run |
|-----------|-----|
| `/burn` | `python3 .../estimate_cost.py` |
| `/burn --all` | `python3 .../estimate_cost.py --all` |
| "how much has this session cost?" | `python3 .../estimate_cost.py` |
| "show me all my costs for the past week" | `python3 .../estimate_cost.py --all --days 7` |
| "export my usage to csv" | `python3 .../estimate_cost.py --all --export csv` |

## Output

Print the script's output directly. Do not add extra commentary unless the user asks follow-up questions about the results.
