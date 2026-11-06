"""Token accounting from Claude Code transcripts (INSTRUCTIONS.md §7).

tokens = input + cache_creation + output; cache_read is reported separately.
Assistant messages can be streamed over several transcript lines that share a
message id; only the last usage seen for an id is counted.
"""

import datetime as dt
import json
from pathlib import Path


def _parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def summarize(path, start_line=0):
    """Summarize a transcript from start_line. Returns a dict including 'lines'."""
    path = Path(path)
    out = {"tokens": 0, "input": 0, "output": 0, "cache_creation": 0, "cache_read": 0,
           "tool_uses": 0, "tool_errors": 0, "model": None, "duration_s": 0,
           "first_prompt": None, "lines": start_line}
    if not path.exists():
        return out
    per_msg, first, last = {}, None, None
    lines = path.read_text(errors="replace").splitlines()
    for line in lines[start_line:]:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(rec.get("timestamp", ""))
        if ts:
            first = first or ts
            last = ts
        msg = rec.get("message") or {}
        role = msg.get("role") or rec.get("type")
        content = msg.get("content")
        if role == "user":
            if out["first_prompt"] is None:
                out["first_prompt"] = _text(content)
            if isinstance(content, list):
                out["tool_errors"] += sum(1 for c in content
                                          if isinstance(c, dict) and c.get("type") == "tool_result"
                                          and c.get("is_error"))
        elif role == "assistant":
            u = msg.get("usage")
            mid = msg.get("id") or rec.get("uuid")
            if u:
                per_msg[mid] = u
            if msg.get("model"):
                out["model"] = msg["model"]
            if isinstance(content, list):
                out["tool_uses"] += sum(1 for c in content
                                        if isinstance(c, dict) and c.get("type") == "tool_use")
    for u in per_msg.values():
        out["input"] += u.get("input_tokens", 0) or 0
        out["output"] += u.get("output_tokens", 0) or 0
        out["cache_creation"] += u.get("cache_creation_input_tokens", 0) or 0
        out["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
    out["tokens"] = out["input"] + out["output"] + out["cache_creation"]
    if first and last:
        out["duration_s"] = int((last - first).total_seconds())
    out["lines"] = len(lines)
    return out


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def from_headless_json(obj):
    """Usage from `claude -p --output-format json` output. `modelUsage` covers every model
    the run used, including forked subagents (top-level `usage` does not)."""
    mu = obj.get("modelUsage") or {}
    if mu:
        inp = sum(m.get("inputTokens", 0) or 0 for m in mu.values())
        outp = sum(m.get("outputTokens", 0) or 0 for m in mu.values())
        cc = sum(m.get("cacheCreationInputTokens", 0) or 0 for m in mu.values())
        cr = sum(m.get("cacheReadInputTokens", 0) or 0 for m in mu.values())
        model = max(mu, key=lambda k: mu[k].get("outputTokens", 0) or 0)
    else:
        u = obj.get("usage") or {}
        inp, outp = u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0
        cc, cr, model = u.get("cache_creation_input_tokens", 0) or 0, u.get("cache_read_input_tokens", 0) or 0, None
    return {"tokens": inp + outp + cc, "input": inp, "output": outp, "cache_creation": cc,
            "cache_read": cr, "model": model, "duration_s": int((obj.get("duration_ms") or 0) / 1000),
            "cost_usd": obj.get("total_cost_usd")}


def subagent_transcript(transcript_path, session_id, agent_id):
    """Where Claude Code stores a subagent transcript, next to the main one."""
    p = Path(transcript_path)
    return p.parent / session_id / "subagents" / f"agent-{agent_id}.jsonl"
