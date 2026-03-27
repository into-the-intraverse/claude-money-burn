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
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# Opus 4.6/4.5: $5/$25 — Sonnet 4.6/4.5/4: $3/$15 — Haiku 4.5: $1/$5
# Fast mode (Opus 4.6): 6x standard = $30/$150
PRICING = {
    "opus": {"input": 5.0, "output": 25.0},
    "opus_fast": {"input": 30.0, "output": 150.0},
    "sonnet": {"input": 3.0, "output": 15.0},
    "haiku": {"input": 1.0, "output": 5.0},
}

# Model string prefix → family mapping (matches Claude Code's model grouping)
MODEL_FAMILIES = {
    "opus": "opus",
    "sonnet": "sonnet",
    "haiku": "haiku",
}


def model_family(model_str):
    """Map a full model string like 'claude-opus-4-6' to a pricing family."""
    m = (model_str or "").lower()
    for key, family in MODEL_FAMILIES.items():
        if key in m:
            return family
    return "sonnet"


def load_stats_cache(claude_dir):
    """Load stats-cache.json — the same data source /stats uses.

    Returns None if the file doesn't exist or can't be parsed.
    """
    cache_path = claude_dir / "stats-cache.json"
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != 2:
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


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
    """Find all JSONL conversation files under projects/.

    Only looks in the projects/ directory (same as /stats).
    Excludes subagent files — their tokens are already counted
    in the parent conversation's API usage and are marked as
    sidechain messages which we filter in analyze_conversation().
    """
    projects_dir = claude_dir / "projects"
    if not projects_dir.exists():
        return []
    pattern = str(projects_dir / "**" / "*.jsonl")
    found = set()
    for f in globmod.glob(pattern, recursive=True):
        norm = os.path.normpath(f)
        if os.sep + "subagents" + os.sep in norm or "/subagents/" in f:
            continue
        found.add(os.path.abspath(norm))
    return sorted(found)




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
        "model_tokens": defaultdict(lambda: {
            "input": 0, "output": 0, "cache_create": 0, "cache_read": 0,
        }),
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

        # Skip sidechain messages (alternate branches) — /stats skips these
        if msg.get("isSidechain"):
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

        if role in ("user", "human"):
            stats["user_messages"] += 1

        elif role in ("assistant", "model"):
            # Skip synthetic messages (not real API calls) — /stats skips these
            msg_model = inner.get("model", "")
            if msg_model == "<synthetic>":
                continue

            stats["assistant_messages"] += 1
            m = model_family(msg_model)
            # Fast mode uses 6x pricing — track separately
            speed = inner.get("speed", "")
            if speed == "fast" and m in PRICING and f"{m}_fast" in PRICING:
                m = f"{m}_fast"
            stats["model"] = m if "_fast" not in m else m.split("_")[0]
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
                # Per-model token tracking for accurate cost estimation
                mt = stats["model_tokens"][m]
                mt["input"] += api_input
                mt["output"] += api_output
                mt["cache_create"] += cache_create
                mt["cache_read"] += cache_read

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

    if stats["first_timestamp"] and stats["last_timestamp"]:
        delta = stats["last_timestamp"] - stats["first_timestamp"]
        stats["duration_minutes"] = round(delta.total_seconds() / 60, 1)

    return stats


def estimate_cost(stats):
    """Compute cost using per-model token tracking for accurate pricing."""
    model_tokens = stats.get("model_tokens", {})
    input_cost = 0.0
    output_cost = 0.0
    if model_tokens:
        for m, mt in model_tokens.items():
            p = PRICING.get(m, PRICING["sonnet"])
            input_cost += (mt["input"] / 1_000_000) * p["input"]
            input_cost += (mt["cache_create"] / 1_000_000) * p["input"] * 1.25
            input_cost += (mt["cache_read"] / 1_000_000) * p["input"] * 0.10
            output_cost += (mt["output"] / 1_000_000) * p["output"]
    else:
        # Fallback for stats without per-model tracking
        model = stats.get("model", "sonnet")
        p = PRICING.get(model, PRICING["sonnet"])
        input_cost = (stats["input_tokens"] / 1_000_000) * p["input"]
        input_cost += (stats["cache_creation_input_tokens"] / 1_000_000) * p["input"] * 1.25
        input_cost += (stats["cache_read_input_tokens"] / 1_000_000) * p["input"] * 0.10
        output_cost = (stats["output_tokens"] / 1_000_000) * p["output"]
    model = stats.get("model", "sonnet")
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


def format_duration(minutes):
    if minutes >= 1440:
        return f"{minutes / 1440:.1f}d"
    if minutes >= 60:
        return f"{minutes / 60:.1f}h"
    return f"{minutes:.0f}m"


MODEL_ICON = {"opus": "\U0001f451", "opus_fast": "\U0001f3ce\ufe0f", "sonnet": "\u2728", "haiku": "\u26a1"}

# ── ANSI colors ──────────────────────────────────────────────────
_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

def _ansi(code):
    return f"\033[{code}m" if _COLOR else ""

BOLD    = _ansi("1")
DIM     = _ansi("2")
GREEN   = _ansi("32")
YELLOW  = _ansi("33")
CYAN    = _ansi("36")
RED     = _ansi("31")
WHITE   = _ansi("97")
RESET   = _ansi("0")
BOLD_GREEN  = _ansi("1;32")
BOLD_YELLOW = _ansi("1;33")
BOLD_CYAN   = _ansi("1;36")
BOLD_WHITE  = _ansi("1;97")


def clean_project_name(raw):
    """Decode encoded project directory names to readable paths.

    D--code-moneyrain -> code/moneyrain
    C--Users-intruder -> C:/Users/intruder
    """
    if not raw:
        return raw
    # Remove worktree suffixes (--claude-worktrees-*)
    parts = raw.split("--claude-worktrees-")
    name = parts[0]
    worktree = parts[1] if len(parts) > 1 else None
    # Decode: first segment before -- is the drive, rest are path segments
    segments = name.split("--")
    if len(segments) >= 2:
        drive = segments[0]
        path = "/".join(segments[1:])
        # Single letter = drive letter (D -> D:)
        if len(drive) == 1 and drive.isalpha():
            result = f"{path}"
        else:
            result = f"{drive}/{path}"
    else:
        result = name.replace("-", "/")
    if worktree:
        # Shorten worktree names: "merry-finding-milner" -> " (worktree)"
        result += f" {DIM}(wt){RESET}"
    return result


def smooth_bar(fraction, width=20):
    """Render a smooth bar using Unicode block characters."""
    blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    fraction = max(0.0, min(1.0, fraction))
    full_units = fraction * width
    full = int(full_units)
    remainder = full_units - full
    bar = "\u2588" * full
    if full < width:
        idx = int(remainder * 8)
        if idx > 0:
            bar += blocks[idx]
            full += 1
        bar += " " * (width - full)
    return bar


# ── Single-session compact report ────────────────────────────────
def print_session_report(stats, cost):
    print()
    icon = MODEL_ICON.get(cost["model"], "")
    dur = stats["duration_minutes"]
    dur_str = format_duration(dur) if dur > 0 else ""
    started = stats["first_timestamp"].strftime("%b %d %H:%M") if stats["first_timestamp"] else ""
    meta = f"{icon} {cost['model']}"
    if dur_str:
        meta += f"  {DIM}\u2502{RESET}  {dur_str}"
    if started:
        meta += f"  {DIM}\u2502{RESET}  {started}"

    print(f"  {BOLD}Session{RESET}  {meta}")
    print(f"  {DIM}{'─' * 50}{RESET}")
    print()

    # Key metrics in a compact line
    msgs = f"{stats['user_messages']}/{stats['assistant_messages']} msgs"
    tools = f"{stats['tool_calls']} tools"
    parts = [msgs, tools]
    if stats["agents_spawned"]:
        parts.append(f"{stats['agents_spawned']} agents")
    print(f"  {DIM}{' \u2502 '.join(parts)}{RESET}")
    print()

    # Token display
    total_tok = stats["input_tokens"] + stats["output_tokens"]
    print(f"  {BOLD_WHITE}{format_tokens(total_tok)}{RESET} tokens  "
          f"{DIM}({format_tokens(stats['input_tokens'])} in \u2502 {format_tokens(stats['output_tokens'])} out){RESET}")
    if stats['cache_creation_input_tokens'] or stats['cache_read_input_tokens']:
        print(f"  {DIM}cache: {format_tokens(stats['cache_read_input_tokens'])} read "
              f"\u2502 {format_tokens(stats['cache_creation_input_tokens'])} write{RESET}")
    print()

    # Cost — the main event
    print(f"  {BOLD_GREEN}${cost['total_cost']:.2f}{RESET}  "
          f"{DIM}(${cost['input_cost']:.2f} input + ${cost['output_cost']:.2f} output){RESET}")
    print()

    # What-if on other models
    other_models = [m for m in PRICING if m != cost["model"] and "_fast" not in m]
    if other_models:
        alts = []
        for m in other_models:
            p = PRICING[m]
            alt_cost = (
                (stats["input_tokens"] / 1e6) * p["input"]
                + (stats["cache_creation_input_tokens"] / 1e6) * p["input"] * 1.25
                + (stats["cache_read_input_tokens"] / 1e6) * p["input"] * 0.10
                + (stats["output_tokens"] / 1e6) * p["output"]
            )
            alts.append(f"{MODEL_ICON.get(m, '')} {m} ${alt_cost:.2f}")
        print(f"  {DIM}on other models: {' \u2502 '.join(alts)}{RESET}")
        print()

    # Rate card
    print_rate_card()


# ── Full multi-conversation report ───────────────────────────────
def print_full_report(results, totals, args):
    by_model = defaultdict(
        lambda: {"count": 0, "cost": 0, "input": 0, "output": 0,
                 "cache_create": 0, "cache_read": 0,
                 "input_cost": 0, "output_cost": 0}
    )

    for r in results:
        model_tokens = r.get("model_tokens", {})
        if model_tokens:
            for m, mt in model_tokens.items():
                by_model[m]["input"] += mt["input"]
                by_model[m]["output"] += mt["output"]
                by_model[m]["cache_create"] += mt["cache_create"]
                by_model[m]["cache_read"] += mt["cache_read"]
                p = PRICING.get(m, PRICING["sonnet"])
                ic = ((mt["input"] / 1e6) * p["input"]
                      + (mt["cache_create"] / 1e6) * p["input"] * 1.25
                      + (mt["cache_read"] / 1e6) * p["input"] * 0.10)
                oc = (mt["output"] / 1e6) * p["output"]
                by_model[m]["input_cost"] += ic
                by_model[m]["output_cost"] += oc
                by_model[m]["cost"] += ic + oc
        else:
            m = r["model"]
            by_model[m]["input"] += r["input_tokens"]
            by_model[m]["output"] += r["output_tokens"]
            by_model[m]["cost"] += r["total_cost"]
            by_model[m]["input_cost"] += r["input_cost"]
            by_model[m]["output_cost"] += r["output_cost"]
        by_model[r["model"]]["count"] += 1

    # ── Header ─────────────────────────────────────────────────────
    total_tok = totals["input_tokens"] + totals["output_tokens"]
    print()
    print(f"  {BOLD}\U0001f4b0 {BOLD_GREEN}${totals['total_cost']:.2f}{RESET}  "
          f"{DIM}estimated API cost{RESET}  "
          f"{BOLD_WHITE}{format_tokens(total_tok)}{RESET} {DIM}tokens{RESET}  "
          f"{DIM}\u2502{RESET}  {totals['conversations']} sessions")
    print(f"  {DIM}{'─' * 64}{RESET}")
    print()

    # Per-model breakdown (compact)
    for model, data in sorted(by_model.items(), key=lambda x: x[1]["cost"], reverse=True):
        icon = MODEL_ICON.get(model, "")
        pct = (data["cost"] / totals["total_cost"] * 100) if totals["total_cost"] > 0 else 0
        bar = smooth_bar(pct / 100, 15)
        print(f"  {icon} {BOLD}{model:>7}{RESET}  {GREEN}${data['cost']:>8.2f}{RESET}  "
              f"{DIM}{bar}{RESET}  "
              f"{DIM}{format_tokens(data['input'])} in \u2502 {format_tokens(data['output'])} out{RESET}")
    print()

    # ── Time periods ──────────────────────────────────────────────
    now = datetime.now()
    periods = [
        ("7d", now - timedelta(days=7)),
        ("30d", now - timedelta(days=30)),
        ("all", None),
    ]
    print(f"  {BOLD}By period{RESET}")
    for label, since in periods:
        if since is None:
            p_cost = totals["total_cost"]
            p_convos = totals["conversations"]
            p_input = totals["input_tokens"]
            p_output = totals["output_tokens"]
        else:
            p_cost = p_convos = p_input = p_output = 0
            for r in results:
                ts = r.get("last_timestamp") or r.get("first_timestamp")
                if ts and ts >= since:
                    p_cost += r["total_cost"]
                    p_convos += 1
                    p_input += r["input_tokens"]
                    p_output += r["output_tokens"]
        print(f"    {label:>4}  {GREEN}${p_cost:>8.2f}{RESET}  "
              f"{DIM}{p_convos:>4} sess  {format_tokens(p_input):>7} in  {format_tokens(p_output):>7} out{RESET}")
    print()

    # ── What-if ───────────────────────────────────────────────────
    alts = []
    for comp_name in ["opus", "sonnet", "haiku"]:
        p = PRICING[comp_name]
        comp_total = (
            (totals["input_tokens"] / 1e6) * p["input"]
            + (totals["cache_creation_input_tokens"] / 1e6) * p["input"] * 1.25
            + (totals["cache_read_input_tokens"] / 1e6) * p["input"] * 0.10
            + (totals["output_tokens"] / 1e6) * p["output"]
        )
        alts.append(f"{MODEL_ICON.get(comp_name, '')} {comp_name} ${comp_total:.2f}")
    print(f"  {DIM}if all on one model: {' \u2502 '.join(alts)}{RESET}")
    print()

    # ── Top conversations ─────────────────────────────────────────
    top_n = min(args.top, len(results))
    if top_n > 0:
        print(f"  {BOLD}\U0001f3c6 Top {top_n} sessions{RESET}")
        print(f"  {DIM}{'─' * 64}{RESET}")
        max_cost = results[0]["total_cost"] if results else 1
        for i, r in enumerate(results[:top_n], 1):
            project = clean_project_name(Path(r["filepath"]).parent.name)
            date_str = r["first_timestamp"].strftime("%b %d") if r["first_timestamp"] else "???"
            dur = format_duration(r["duration_minutes"]) if r["duration_minutes"] > 0 else ""
            bar = smooth_bar(r["total_cost"] / max(max_cost, 0.01), 10)
            rank_str = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(i, f"{i:>2}.")
            print(f"  {rank_str} {GREEN}${r['total_cost']:>7.2f}{RESET}  "
                  f"{DIM}{bar}{RESET}  "
                  f"{date_str}  {dur:>5}  "
                  f"{DIM}{format_tokens(r['output_tokens'])} out{RESET}  "
                  f"{project}")
        print()

    # ── Cost by project ───────────────────────────────────────────
    by_project = defaultdict(
        lambda: {
            "count": 0, "cost": 0, "input_tokens": 0, "output_tokens": 0,
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
        ts = r.get("first_timestamp")
        if ts:
            if p["first_ts"] is None or ts < p["first_ts"]:
                p["first_ts"] = ts
            if p["last_ts"] is None or ts > p["last_ts"]:
                p["last_ts"] = ts

    sorted_projects = sorted(by_project.items(), key=lambda x: x[1]["cost"], reverse=True)
    # Filter out zero-cost projects, cap at top 15
    sorted_projects = [(k, v) for k, v in sorted_projects if v["cost"] > 0.01]
    show_projects = sorted_projects[:15]
    remaining = len(sorted_projects) - 15

    if show_projects:
        max_proj_cost = show_projects[0][1]["cost"]
        print(f"  {BOLD}\U0001f4c1 By project{RESET}")
        print(f"  {DIM}{'─' * 64}{RESET}")
        for project_raw, data in show_projects:
            project = clean_project_name(project_raw)
            pct = (data["cost"] / totals["total_cost"] * 100) if totals["total_cost"] > 0 else 0
            bar = smooth_bar(data["cost"] / max(max_proj_cost, 0.01), 10)
            date_range = ""
            if data["first_ts"] and data["last_ts"]:
                date_range = f"{data['first_ts'].strftime('%m/%d')}-{data['last_ts'].strftime('%m/%d')}"
            print(f"  {GREEN}${data['cost']:>8.2f}{RESET}  {DIM}{bar}{RESET}  "
                  f"{pct:4.1f}%  {DIM}{data['count']:>3} sess  {date_range:>11}{RESET}  "
                  f"{project}")
        if remaining > 0:
            print(f"  {DIM}... and {remaining} more projects{RESET}")
        print()

    # ── Warnings (aggregated, capped) ─────────────────────────────
    warn_long = sum(1 for r in results if r["duration_minutes"] > 120)
    warn_agents = sum(1 for r in results if r["agents_spawned"] > 5)
    warn_context = sum(1 for r in results if r["input_tokens"] > 500_000)
    opus_convs = by_model.get("opus", {}).get("count", 0)

    warnings = []
    if warn_long:
        warnings.append(f"{YELLOW}{warn_long}{RESET} sessions ran over 2h")
    if warn_agents:
        warnings.append(f"{YELLOW}{warn_agents}{RESET} sessions spawned 5+ agents")
    if warn_context:
        warnings.append(f"{YELLOW}{warn_context}{RESET} sessions used 500K+ input tokens")
    if opus_convs > totals["conversations"] * 0.5 and totals["conversations"] > 10:
        warnings.append(f"Opus used in {YELLOW}{opus_convs}/{totals['conversations']}{RESET} sessions")

    if warnings:
        print(f"  {DIM}\u26a0\ufe0f  {' \u2502 '.join(warnings)}{RESET}")
        print()

    # Rate card
    print_rate_card()


def print_rate_card():
    """Show current per-model pricing used for estimates."""
    parts = []
    for m in ["opus", "sonnet", "haiku"]:
        p = PRICING[m]
        icon = MODEL_ICON.get(m, "")
        parts.append(f"{icon} {m} ${p['input']}/{p['output']}")
    print(f"  {DIM}rates ($/Mtok in/out): {' \u2502 '.join(parts)}{RESET}")
    pf = PRICING["opus_fast"]
    print(f"  {DIM}cache: write 1.25x \u2502 read 0.10x  \u2502  fast mode: ${pf['input']}/{pf['output']} (6x){RESET}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Estimate Claude Code token usage and costs")
    parser.add_argument("--all", action="store_true", help="Analyze all conversations (default: current session only)")
    parser.add_argument("--file", type=str, help="Analyze a specific JSONL file")
    parser.add_argument("--days", type=int, default=0, help="Only analyze last N days (with --all)")
    parser.add_argument("--top", type=int, default=10, help="Show top N conversations by cost (with --all)")
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

    # Use stats-cache.json for headline token numbers when not day-filtered.
    # This is the same data source /stats uses, ensuring consistent numbers.
    stats_cache = load_stats_cache(claude_dir)
    if stats_cache and not cutoff:
        cache_usage = stats_cache.get("modelUsage", {})
        if cache_usage:
            # Merge cache and JSONL: max() per token field, then add
            # the cost of tokens that exist in cache but not in JSONL
            # (deleted conversation files whose tokens persist in cache).
            jsonl_in = totals["input_tokens"]
            jsonl_out = totals["output_tokens"]
            jsonl_cc = totals["cache_creation_input_tokens"]
            jsonl_cr = totals["cache_read_input_tokens"]

            cache_in = sum(v.get("inputTokens", 0) for v in cache_usage.values())
            cache_out = sum(v.get("outputTokens", 0) for v in cache_usage.values())
            cache_cc = sum(v.get("cacheCreationInputTokens", 0) for v in cache_usage.values())
            cache_cr = sum(v.get("cacheReadInputTokens", 0) for v in cache_usage.values())

            totals["input_tokens"] = max(jsonl_in, cache_in)
            totals["output_tokens"] = max(jsonl_out, cache_out)
            totals["cache_creation_input_tokens"] = max(jsonl_cc, cache_cc)
            totals["cache_read_input_tokens"] = max(jsonl_cr, cache_cr)

            # Add cost for tokens in cache but not JSONL (deleted files)
            d_in = max(0, cache_in - jsonl_in)
            d_out = max(0, cache_out - jsonl_out)
            d_cc = max(0, cache_cc - jsonl_cc)
            d_cr = max(0, cache_cr - jsonl_cr)
            if d_in or d_out or d_cc or d_cr:
                # Price at dominant model's rates
                dom = max(cache_usage.items(),
                          key=lambda x: x[1].get("outputTokens", 0))[0]
                p = PRICING.get(model_family(dom), PRICING["sonnet"])
                totals["total_cost"] += (
                    (d_in / 1e6) * p["input"]
                    + (d_out / 1e6) * p["output"]
                    + (d_cc / 1e6) * p["input"] * 1.25
                    + (d_cr / 1e6) * p["input"] * 0.10
                )
            if stats_cache.get("totalSessions"):
                totals["conversations"] = stats_cache["totalSessions"]

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
