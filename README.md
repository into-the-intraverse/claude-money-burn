# burn

Claude Code plugin that estimates token usage and API costs from local `~/.claude/` conversation JSONL files. Stdlib-only Python, no dependencies.

## Installation

```bash
/plugin install into-the-intraverse/claude-money-burn
```

Or install manually by cloning into `~/.claude/plugins/`:

```bash
git clone https://github.com/into-the-intraverse/claude-money-burn ~/.claude/plugins/claude-money-burn
```

## Usage

```
/burn                    # Current session cost
/burn --all              # All conversations
/burn --all --days 7     # Last 7 days
/burn --all --top 10     # Top 10 by cost
/burn --all --export csv # Export to CSV
```

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

## How it works

Parses assistant messages from JSONL files and reads API-reported `usage` fields (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`). Applies cache-aware pricing: cache writes at 1.25x input rate, cache reads at 0.10x input rate. Subagent JSONL files are excluded (see below).

## Accuracy vs `/stats`

Token counts closely match the built-in `/stats` command:

| Metric | `/stats` | `/burn` | Match |
|--------|----------|---------|-------|
| Input tokens | 1.1M | 944.5K | ~86% |
| Output tokens | 4.2M | 4.17M | ~99% |
| Total tokens | 5.3M | 5.1M | ~96% |

Output tokens are near-exact. The ~15% input gap comes from API calls that `/stats` tracks internally but don't get recorded as assistant messages in the JSONL (failed/retried calls, system prompt overhead).

### Why subagent files are excluded

Subagent conversations are stored in `{project}/{uuid}/subagents/agent-*.jsonl`. Including them inflates output tokens by ~50% vs `/stats`, confirming that `/stats` does not count subagent tokens. Excluding them keeps `/burn` consistent with the built-in reporting.

### Cost estimates are hypothetical on subscriptions

The dollar amounts use API pricing (per-million-token rates). On a Claude Max/Pro subscription you're not billed per-token, so the costs show "what it would cost at API rates" for comparison purposes.
