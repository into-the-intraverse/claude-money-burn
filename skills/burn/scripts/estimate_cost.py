#!/usr/bin/env python3
"""
Claude Code Token Usage & Cost Estimator

Estimates token usage and costs from local Claude Code conversation data.

Usage:
    python estimate_cost.py              # Current session only (auto-detect)
    python estimate_cost.py --all        # All conversations
    python estimate_cost.py --all --days 7
    python estimate_cost.py --file path  # Specific JSONL file
    python estimate_cost.py --all --export csv
"""

import json
import os
import sys
import argparse
import glob as globmod
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# ── Pricing (USD per million tokens) ──────────────────────────────
PRICING = {
    "opus": {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0, "output": 15.0},
    "haiku": {"input": 0.80, "output": 4.0},
}

CHARS_PER_TOKEN = 4


def find_claude_dir():
    """Find the .claude directory."""
    home = Path.home()
    claude_dir = home / ".claude"
    if claude_dir.exists():
        return claude_dir
    for env_var in ["USERPROFILE", "HOME", "APPDATA"]:
        base = os.environ.get(env_var)
        if base:
            candidate = Path(base) / ".claude"
            if candidate.exists():
                return candidate
    return None


def find_current_session():
    """Find the JSONL file for the current session based on CWD."""
    claude_dir = find_claude_dir()
    if not claude_dir:
        return None

    cwd = os.getcwd().replace("\\", "/").replace(":", "-").replace("/", "-")
    project_dir = claude_dir / "projects" / cwd

    if not project_dir.exists():
        return None

    jsonl_files = sorted(
        project_dir.glob("*.jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return jsonl_files[0] if jsonl_files else None


def find_conversation_files(claude_dir):
    """Find all JSONL conversation files, excluding subagent files.

    Subagent tokens are not counted by /stats, so we exclude them
    to stay consistent with the built-in token reporting.
    """
    patterns = [
        str(claude_dir / "projects" / "**" / "*.jsonl"),
        str(claude_dir / "conversations" / "**" / "*.jsonl"),
        str(claude_dir / "**" / "*.jsonl"),
    ]
    found = set()
    for pattern in patterns:
        for f in globmod.glob(pattern, recursive=True):
            norm = os.path.normpath(f)
            if os.sep + "subagents" + os.sep in norm or "/subagents/" in f:
                continue
            found.add(os.path.abspath(norm))
    return sorted(found)


def estimate_tokens_from_text(text):
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def extract_text_from_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
                if "input" in item:
                    inp = item["input"]
                    parts.append(json.dumps(inp) if isinstance(inp, dict) else str(inp))
                if "content" in item and isinstance(item["content"], (str, list)):
                    parts.append(extract_text_from_content(item["content"]))
        return "\n".join(parts)
    if isinstance(content, dict):
        return content.get("text", json.dumps(content))
    return str(content)


def detect_model(message):
    model = message.get("model", "")
    if not model:
        model = (
            message.get("metadata", {}).get("model", "")
            if isinstance(message.get("metadata"), dict)
            else ""
        )
    model = model.lower()
    if "opus" in model:
        return "opus"
    elif "haiku" in model:
        return "haiku"
    elif "sonnet" in model:
        return "sonnet"
    return "sonnet"


def analyze_conversation(filepath, cutoff_date=None):
    stats = {
        "filepath": filepath,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "user_messages": 0,
        "assistant_messages": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "files_read": 0,
        "agents_spawned": 0,
        "models_used": defaultdict(int),
        "model": "sonnet",
        "first_timestamp": None,
        "last_timestamp": None,
        "duration_minutes": 0,
    }

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        stats["error"] = str(e)
        return stats

    if not lines:
        return stats

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = msg.get("timestamp") or msg.get("created_at") or msg.get("ts")
        if ts:
            try:
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
                else:
                    dt = datetime.fromisoformat(
                        str(ts).replace("Z", "+00:00")
                    ).replace(tzinfo=None)

                if cutoff_date and dt < cutoff_date:
                    continue

                if stats["first_timestamp"] is None or dt < stats["first_timestamp"]:
                    stats["first_timestamp"] = dt
                if stats["last_timestamp"] is None or dt > stats["last_timestamp"]:
                    stats["last_timestamp"] = dt
            except (ValueError, TypeError, OSError):
                pass

        role = msg.get("role", msg.get("type", ""))
        inner = msg.get("message", msg)
        content = inner.get("content", "")
        text = extract_text_from_content(content)
        chars = len(text)

        if role in ("user", "human"):
            stats["user_messages"] += 1
            # Don't count user text as tokens — it's already included
            # in the next assistant message's usage.input_tokens

        elif role in ("assistant", "model"):
            stats["assistant_messages"] += 1
            m = detect_model(inner)
            stats["model"] = m
            stats["models_used"][m] += 1

            # Use API-reported usage (present on every assistant message)
            usage = inner.get("usage", {})
            if isinstance(usage, dict):
                api_input = usage.get("input_tokens", 0) or 0
                api_output = usage.get("output_tokens", 0) or 0
                cache_create = usage.get("cache_creation_input_tokens", 0) or 0
                cache_read = usage.get("cache_read_input_tokens", 0) or 0
                stats["input_tokens"] += api_input
                stats["output_tokens"] += api_output
                stats["cache_creation_input_tokens"] += cache_create
                stats["cache_read_input_tokens"] += cache_read
            else:
                # Fallback for messages without usage (e.g. synthetic)
                stats["output_tokens"] += estimate_tokens_from_text(text)

            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        stats["tool_calls"] += 1
                        tool_name = item.get("name", "")
                        if tool_name in ("Read", "Glob", "Grep"):
                            stats["files_read"] += 1
                        elif tool_name == "Agent":
                            stats["agents_spawned"] += 1

        elif role == "tool":
            stats["tool_results"] += 1
            # Don't count tool result text as tokens — it's already
            # included in the next assistant message's usage.input_tokens

    if stats["first_timestamp"] and stats["last_timestamp"]:
        delta = stats["last_timestamp"] - stats["first_timestamp"]
        stats["duration_minutes"] = round(delta.total_seconds() / 60, 1)

    return stats


def estimate_cost(stats):
    model = stats.get("model", "sonnet")
    pricing = PRICING.get(model, PRICING["sonnet"])
    base_input_cost = (stats["input_tokens"] / 1_000_000) * pricing["input"]
    cache_write_cost = (stats["cache_creation_input_tokens"] / 1_000_000) * pricing["input"] * 1.25
    cache_read_cost = (stats["cache_read_input_tokens"] / 1_000_000) * pricing["input"] * 0.10
    input_cost = base_input_cost + cache_write_cost + cache_read_cost
    output_cost = (stats["output_tokens"] / 1_000_000) * pricing["output"]
    return {
        "input_cost": round(input_cost, 4),
        "output_cost": round(output_cost, 4),
        "total_cost": round(input_cost + output_cost, 4),
        "model": model,
    }


def format_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


MODEL_ICON = {"opus": "\U0001f451", "sonnet": "\u2728", "haiku": "\u26a1"}


# ── Single-session compact report ────────────────────────────────
def print_session_report(stats, cost):
    print()
    print("=" * 50)
    print("  \U0001f4b0 SESSION COST ESTIMATE")
    print("=" * 50)
    print()

    # Model(s)
    models_used = stats.get("models_used", {})
    if models_used:
        primary = max(models_used, key=models_used.get)
        icon = MODEL_ICON.get(primary, "")
        if len(models_used) == 1:
            print(f"  Model:           {icon} {primary}")
        else:
            parts = [f"{m}({c})" for m, c in sorted(models_used.items(), key=lambda x: -x[1])]
            print(f"  Models:          {', '.join(parts)}")
    else:
        print(f"  Model:           {cost['model']}")

    # Duration
    if stats["duration_minutes"] > 0:
        dur = stats["duration_minutes"]
        if dur >= 60:
            print(f"  Duration:        {dur / 60:.1f}h")
        else:
            print(f"  Duration:        {dur:.0f} min")

    # Timestamps
    if stats["first_timestamp"]:
        print(f"  Started:         {stats['first_timestamp'].strftime('%Y-%m-%d %H:%M')}")

    print()
    print(f"  Messages:        {stats['user_messages']} user / {stats['assistant_messages']} assistant")
    print(f"  Tool calls:      {stats['tool_calls']}")
    if stats["files_read"]:
        print(f"  File reads:      {stats['files_read']}")
    if stats["agents_spawned"]:
        print(f"  Agents spawned:  {stats['agents_spawned']}")

    print()
    print(f"  Input tokens:    {format_tokens(stats['input_tokens'])}")
    print(f"  Output tokens:   {format_tokens(stats['output_tokens'])}")
    if stats['cache_creation_input_tokens'] or stats['cache_read_input_tokens']:
        total_ctx = stats['input_tokens'] + stats['cache_creation_input_tokens'] + stats['cache_read_input_tokens']
        print(f"  Cache context:   {format_tokens(total_ctx)} total per-call input")
        print(f"    Cache write:   {format_tokens(stats['cache_creation_input_tokens'])}")
        print(f"    Cache read:    {format_tokens(stats['cache_read_input_tokens'])}")

    print()
    print(f"  Input cost:      ${cost['input_cost']:.4f}")
    print(f"  Output cost:     ${cost['output_cost']:.4f}")
    print(f"  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    print(f"  \U0001f4b5 TOTAL:          ${cost['total_cost']:.4f}")
    print()

    # What-if on other models
    other_models = [m for m in PRICING if m != cost["model"]]
    if other_models:
        print(f"  On other models:")
        for m in other_models:
            p = PRICING[m]
            alt_cost = (
                (stats["input_tokens"] / 1e6) * p["input"]
                + (stats["cache_creation_input_tokens"] / 1e6) * p["input"] * 1.25
                + (stats["cache_read_input_tokens"] / 1e6) * p["input"] * 0.10
                + (stats["output_tokens"] / 1e6) * p["output"]
            )
            diff = cost["total_cost"] - alt_cost
            if diff > 0:
                diff_s = f"save ${diff:.4f}"
            elif diff < 0:
                diff_s = f"+${-diff:.4f}"
            else:
                diff_s = "same"
            print(f"    {MODEL_ICON.get(m, '')} {m:>7}: ${alt_cost:.4f}  ({diff_s})")
        print()

    print("  Note: based on API-reported usage fields with cache-aware pricing.")
    print()


# ── Full multi-conversation report ───────────────────────────────
def print_full_report(results, totals, args):
    by_model = defaultdict(
        lambda: {"count": 0, "cost": 0, "input": 0, "output": 0, "input_cost": 0, "output_cost": 0}
    )
    for r in results:
        m = r["model"]
        by_model[m]["count"] += 1
        by_model[m]["cost"] += r["total_cost"]
        by_model[m]["input"] += r["input_tokens"]
        by_model[m]["output"] += r["output_tokens"]
        by_model[m]["input_cost"] += r["input_cost"]
        by_model[m]["output_cost"] += r["output_cost"]

    print("=" * 70)
    print("  \U0001f4ca CLAUDE CODE TOKEN USAGE SUMMARY")
    print("=" * 70)
    print()
    print(f"  Conversations analyzed:  {totals['conversations']}")
    print(f"  User messages:           {totals['user_messages']}")
    print(f"  Assistant messages:      {totals['assistant_messages']}")
    print(f"  Tool calls:              {totals['tool_calls']}")
    print(f"  File reads:              {totals['files_read']}")
    print(f"  Agents spawned:          {totals['agents_spawned']}")
    print()
    print(f"  Input tokens:            {format_tokens(totals['input_tokens'])}")
    print(f"  Output tokens:           {format_tokens(totals['output_tokens'])}")
    total_ctx = totals['input_tokens'] + totals['cache_creation_input_tokens'] + totals['cache_read_input_tokens']
    if totals['cache_read_input_tokens'] or totals['cache_creation_input_tokens']:
        print(f"  Cache context:           {format_tokens(total_ctx)} total per-call input")
        print(f"    Cache write:           {format_tokens(totals['cache_creation_input_tokens'])}")
        print(f"    Cache read:            {format_tokens(totals['cache_read_input_tokens'])}")
    print()

    print(f"  Cost breakdown by model:")
    for model, data in sorted(by_model.items(), key=lambda x: x[1]["cost"], reverse=True):
        icon = MODEL_ICON.get(model, f"[{model}]")
        pct = (data["cost"] / totals["total_cost"] * 100) if totals["total_cost"] > 0 else 0
        print(
            f"      {icon:>10} {model:>7}: ${data['cost']:>8.2f}  ({pct:4.1f}%)  "
            f"in={format_tokens(data['input']):>7}  out={format_tokens(data['output']):>7}"
        )
    print()

    print(f"  Estimated input cost:    ${totals['input_cost']:.2f}")
    print(f"  Estimated output cost:   ${totals['output_cost']:.2f}")
    print(f"  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    print(f"  \U0001f4b0 ESTIMATED TOTAL COST:    ${totals['total_cost']:.2f}")
    print()

    # Cost by time period
    now = datetime.now()
    periods = [
        ("Last 7 days", now - timedelta(days=7)),
        ("Last 30 days", now - timedelta(days=30)),
        ("All time", None),
    ]
    print(f"  Cost by time period:")
    for label, since in periods:
        p_cost = p_convos = p_input = p_output = 0
        p_models = defaultdict(float)
        for r in results:
            ts = r.get("last_timestamp") or r.get("first_timestamp")
            if since is None or (ts and ts >= since):
                p_cost += r["total_cost"]
                p_convos += 1
                p_input += r["input_tokens"]
                p_output += r["output_tokens"]
                p_models[r["model"]] += r["total_cost"]
        model_parts = [f"{m}: ${mc:.2f}" for m, mc in sorted(p_models.items(), key=lambda x: -x[1])]
        models_str = " / ".join(model_parts) if model_parts else "\u2014"
        print(
            f"      {label:>18}:  ${p_cost:>8.2f}  |  {p_convos:>4} convos  "
            f"|  in={format_tokens(p_input):>7}  out={format_tokens(p_output):>7}"
        )
        print(f"      {'':>18}   {models_str}")
    print()

    # What-if model comparison (uses same cache-aware pricing)
    print(f"  What if you used a single model for everything?")
    for comp_name in ["opus", "sonnet", "haiku"]:
        p = PRICING[comp_name]
        comp_total = (
            (totals["input_tokens"] / 1e6) * p["input"]
            + (totals["cache_creation_input_tokens"] / 1e6) * p["input"] * 1.25
            + (totals["cache_read_input_tokens"] / 1e6) * p["input"] * 0.10
            + (totals["output_tokens"] / 1e6) * p["output"]
        )
        diff = totals["total_cost"] - comp_total
        diff_str = f"save ${diff:.2f}" if diff > 0 else (f"+${-diff:.2f} more" if diff < 0 else "same")
        print(f"      If ALL on {comp_name:>6}:  ${comp_total:>8.2f}  ({diff_str})")
    print()

    # Top conversations
    top_n = min(args.top, len(results))
    if top_n > 0:
        print("=" * 70)
        print(f"  \U0001f3c6 TOP {top_n} MOST EXPENSIVE CONVERSATIONS")
        print("=" * 70)
        print()
        for i, r in enumerate(results[:top_n], 1):
            project = Path(r["filepath"]).parent.name
            fname = Path(r["filepath"]).stem[:30]
            date_str = r["first_timestamp"].strftime("%Y-%m-%d %H:%M") if r["first_timestamp"] else "unknown"
            rank_str = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(i, f"#{i}")
            print(
                f"  {rank_str:>4}. ${r['total_cost']:>7.2f}  |  {r['model']:>6}  |  "
                f"{date_str}  |  {r['duration_minutes']:>5.0f}min"
            )
            print(
                f"       in={format_tokens(r['input_tokens']):>7}  out={format_tokens(r['output_tokens']):>7}  "
                f"msgs={r['user_messages']}  tools={r['tool_calls']}  agents={r['agents_spawned']}"
            )
            print(f"       {project}/{fname}")
            print()

    # Cost by model
    print("=" * 70)
    print("  COST BY MODEL")
    print("=" * 70)
    for model, data in sorted(by_model.items(), key=lambda x: x[1]["cost"], reverse=True):
        bar_len = int((data["cost"] / max(totals["total_cost"], 0.01)) * 30)
        bar = "\u2588" * bar_len + "\u2591" * (30 - bar_len)
        print(
            f"  {model:>7}: ${data['cost']:>8.2f}  |{bar}|  "
            f"({data['count']} convos, in={format_tokens(data['input'])}, out={format_tokens(data['output'])})"
        )
    print()

    # Cost by project
    by_project = defaultdict(
        lambda: {
            "count": 0, "cost": 0, "input_tokens": 0, "output_tokens": 0,
            "models": defaultdict(lambda: {"count": 0, "cost": 0}),
            "tool_calls": 0, "agents_spawned": 0, "first_ts": None, "last_ts": None,
        }
    )
    for r in results:
        project = Path(r["filepath"]).parent.name
        p = by_project[project]
        p["count"] += 1
        p["cost"] += r["total_cost"]
        p["input_tokens"] += r["input_tokens"]
        p["output_tokens"] += r["output_tokens"]
        p["tool_calls"] += r["tool_calls"]
        p["agents_spawned"] += r["agents_spawned"]
        p["models"][r["model"]]["count"] += 1
        p["models"][r["model"]]["cost"] += r["total_cost"]
        ts = r.get("first_timestamp")
        if ts:
            if p["first_ts"] is None or ts < p["first_ts"]:
                p["first_ts"] = ts
            if p["last_ts"] is None or ts > p["last_ts"]:
                p["last_ts"] = ts

    print("=" * 70)
    print("  \U0001f4c1 COST BY PROJECT")
    print("=" * 70)
    print()
    for rank, (project, data) in enumerate(
        sorted(by_project.items(), key=lambda x: x[1]["cost"], reverse=True), 1
    ):
        pct = (data["cost"] / totals["total_cost"] * 100) if totals["total_cost"] > 0 else 0
        bar_len = int(pct / 100 * 25)
        bar = "\u2588" * bar_len + "\u2591" * (25 - bar_len)
        date_range = ""
        if data["first_ts"] and data["last_ts"]:
            date_range = f"{data['first_ts'].strftime('%m/%d')} -> {data['last_ts'].strftime('%m/%d')}"
        print(f"  {rank:>2}. {project}")
        print(f"      ${data['cost']:>8.2f}  ({pct:4.1f}%)  |{bar}|")
        print(
            f"      {data['count']} convos  in={format_tokens(data['input_tokens'])}  "
            f"out={format_tokens(data['output_tokens'])}  {data['tool_calls']} tools  {data['agents_spawned']} agents"
        )
        model_parts = [
            f"{m}: ${md['cost']:.2f} ({md['count']}x)"
            for m, md in sorted(data["models"].items(), key=lambda x: -x[1]["cost"])
        ]
        print(f"      Models: {' / '.join(model_parts)}")
        if date_range:
            print(f"      {date_range}")
        print()

    # Warnings
    print("=" * 70)
    print("  \u26a0\ufe0f  WARNINGS & RECOMMENDATIONS")
    print("=" * 70)
    warnings = []
    for r in results:
        if r["duration_minutes"] > 60:
            warnings.append(
                f"  * LONG SESSION: {Path(r['filepath']).stem[:40]} ran for "
                f"{r['duration_minutes']:.0f} min -- use /clear more often"
            )
        if r["agents_spawned"] > 5:
            warnings.append(
                f"  * AGENT HEAVY: {Path(r['filepath']).stem[:40]} spawned "
                f"{r['agents_spawned']} agents -- use targeted prompts"
            )
        if r["input_tokens"] > 500_000:
            warnings.append(
                f"  * HUGE CONTEXT: {Path(r['filepath']).stem[:40]} used "
                f"{format_tokens(r['input_tokens'])} input tokens"
            )
    if totals["agents_spawned"] > totals["conversations"] * 3:
        warnings.append(
            f"  * HIGH AGENT USAGE: {totals['agents_spawned']} agents across "
            f"{totals['conversations']} conversations"
        )
    opus_convs = by_model.get("opus", {}).get("count", 0)
    if opus_convs > totals["conversations"] * 0.5:
        warnings.append(
            f"  * OPUS HEAVY: {opus_convs}/{totals['conversations']} conversations "
            f"used Opus -- switch to Sonnet for routine tasks"
        )
    if warnings:
        for w in warnings:
            print(w)
    else:
        print("  \u2705 No major issues detected.")
    print()
    print("  Note: based on API-reported usage fields with cache-aware pricing.")
    print()


def main():
    parser = argparse.ArgumentParser(description="Estimate Claude Code token usage and costs")
    parser.add_argument("--all", action="store_true", help="Analyze all conversations (default: current session only)")
    parser.add_argument("--file", type=str, help="Analyze a specific JSONL file")
    parser.add_argument("--days", type=int, default=0, help="Only analyze last N days (with --all)")
    parser.add_argument("--top", type=int, default=20, help="Show top N conversations by cost (with --all)")
    parser.add_argument("--export", choices=["csv", "json"], help="Export results to file (with --all)")
    parser.add_argument("--claude-dir", type=str, help="Path to .claude directory")
    args = parser.parse_args()

    # ── Single-file mode ─────────────────────────────────────────
    if args.file:
        filepath = args.file
        if not os.path.isfile(filepath):
            print(f"ERROR: File not found: {filepath}")
            sys.exit(1)
        stats = analyze_conversation(filepath)
        if stats.get("error"):
            print(f"ERROR: {stats['error']}")
            sys.exit(1)
        cost = estimate_cost(stats)
        stats.update(cost)
        print_session_report(stats, cost)
        return

    # ── Auto-detect current session (default) ────────────────────
    if not args.all:
        session_file = find_current_session()
        if not session_file:
            print("Could not auto-detect current session.")
            print("Try: --all (all conversations) or --file <path> (specific file)")
            sys.exit(1)
        stats = analyze_conversation(str(session_file))
        if stats.get("error"):
            print(f"ERROR: {stats['error']}")
            sys.exit(1)
        if stats["user_messages"] == 0 and stats["assistant_messages"] == 0:
            print("Current session has no messages yet.")
            sys.exit(0)
        cost = estimate_cost(stats)
        stats.update(cost)
        print_session_report(stats, cost)
        return

    # ── All conversations mode ───────────────────────────────────
    if args.claude_dir:
        claude_dir = Path(args.claude_dir)
    else:
        claude_dir = find_claude_dir()

    if not claude_dir or not claude_dir.exists():
        print("ERROR: Could not find ~/.claude directory.")
        print("       Run with --claude-dir /path/to/.claude")
        sys.exit(1)

    conv_files = find_conversation_files(claude_dir)
    if not conv_files:
        print("No conversation files (.jsonl) found.")
        sys.exit(0)

    print(f"Found {len(conv_files)} conversation file(s)")

    cutoff = None
    if args.days > 0:
        cutoff = datetime.now() - timedelta(days=args.days)
        print(f"Filtering to last {args.days} day(s) (since {cutoff.strftime('%Y-%m-%d')})")
    print()

    results = []
    totals = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        "total_cost": 0, "input_cost": 0, "output_cost": 0,
        "user_messages": 0, "assistant_messages": 0,
        "tool_calls": 0, "files_read": 0, "agents_spawned": 0,
        "conversations": 0,
    }

    for fp in conv_files:
        stats = analyze_conversation(fp, cutoff)
        if stats.get("error"):
            continue
        if stats["user_messages"] == 0 and stats["assistant_messages"] == 0:
            continue
        cost = estimate_cost(stats)
        stats.update(cost)
        results.append(stats)
        totals["input_tokens"] += stats["input_tokens"]
        totals["output_tokens"] += stats["output_tokens"]
        totals["cache_creation_input_tokens"] += stats["cache_creation_input_tokens"]
        totals["cache_read_input_tokens"] += stats["cache_read_input_tokens"]
        totals["total_cost"] += cost["total_cost"]
        totals["input_cost"] += cost["input_cost"]
        totals["output_cost"] += cost["output_cost"]
        totals["user_messages"] += stats["user_messages"]
        totals["assistant_messages"] += stats["assistant_messages"]
        totals["tool_calls"] += stats["tool_calls"]
        totals["files_read"] += stats["files_read"]
        totals["agents_spawned"] += stats["agents_spawned"]
        totals["conversations"] += 1

    results.sort(key=lambda x: x["total_cost"], reverse=True)
    print_full_report(results, totals, args)

    # Export
    if args.export == "csv":
        import csv
        outfile = "claude_usage_report.csv"
        with open(outfile, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "file", "model", "date", "duration_min", "input_tokens",
                "cache_creation_tokens", "cache_read_tokens",
                "output_tokens", "input_cost", "output_cost", "total_cost",
                "user_msgs", "tool_calls", "agents",
            ])
            for r in results:
                writer.writerow([
                    r["filepath"], r["model"],
                    r["first_timestamp"].strftime("%Y-%m-%d %H:%M") if r["first_timestamp"] else "",
                    r["duration_minutes"], r["input_tokens"],
                    r["cache_creation_input_tokens"], r["cache_read_input_tokens"],
                    r["output_tokens"],
                    r["input_cost"], r["output_cost"], r["total_cost"],
                    r["user_messages"], r["tool_calls"], r["agents_spawned"],
                ])
        print(f"Exported to {outfile}")
    elif args.export == "json":
        outfile = "claude_usage_report.json"
        export = []
        for r in results:
            e = {k: v for k, v in r.items() if k not in ("first_timestamp", "last_timestamp", "models_used")}
            e["first_timestamp"] = r["first_timestamp"].isoformat() if r["first_timestamp"] else None
            e["last_timestamp"] = r["last_timestamp"].isoformat() if r["last_timestamp"] else None
            export.append(e)
        with open(outfile, "w") as f:
            json.dump({"totals": totals, "conversations": export}, f, indent=2)
        print(f"Exported to {outfile}")


if __name__ == "__main__":
    main()
