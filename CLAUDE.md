# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Claude Code plugin providing the `/burn` skill — shows what your token usage would cost at Anthropic API rates. Reads the same API-reported `usage` fields from conversation JSONL files as the built-in `/stats`, then applies cache-aware pricing to produce cost breakdowns. Stdlib-only Python, no dependencies.

## Structure

```
.claude-plugin/plugin.json    # Plugin metadata
skills/burn/SKILL.md           # Skill manifest (triggers, usage docs)
skills/burn/scripts/estimate_cost.py  # The estimator script
```

## Running the script directly

```bash
python3 skills/burn/scripts/estimate_cost.py              # Current session only
python3 skills/burn/scripts/estimate_cost.py --all         # All conversations
python3 skills/burn/scripts/estimate_cost.py --all --days 7
python3 skills/burn/scripts/estimate_cost.py --all --top 10
python3 skills/burn/scripts/estimate_cost.py --all --export csv
```

## Architecture

Two data sources, used together for accuracy:

1. **`~/.claude/stats-cache.json`** — headline token totals and per-model breakdown in `--all` mode (same source `/stats` uses, ensures matching numbers).
2. **JSONL conversation files** — per-conversation analysis, per-project breakdown, time-filtered views, cost estimation. Filters applied to match `/stats`: skips `isSidechain` messages and `<synthetic>` model entries.

Pipeline: discover JSONL files under `~/.claude/projects/` (excluding subagent files) -> parse each line as JSON -> skip sidechain/synthetic messages -> extract API-reported `usage` fields from assistant messages (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) -> track tokens per model per message -> compute costs with cache-aware per-model pricing -> aggregate and display.

Pricing: `PRICING` dict (USD per million tokens for opus/sonnet/haiku). Cache writes at 1.25x input rate, cache reads at 0.10x input rate.
