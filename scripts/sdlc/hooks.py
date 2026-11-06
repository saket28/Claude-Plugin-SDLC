"""Hook handlers (INSTRUCTIONS.md §8). Called as `sdlc.py hook <Event>` with the
hook input JSON on stdin. They do nothing unless a job is active in this session
and SDLC_NESTED isn't set, so normal Claude Code use is unaffected."""

import json
import os
import re
import sys
from pathlib import Path

from . import config as config_mod, rules, usage, yamlish
from .store import Store


def _deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0


def _context(inp):
    """Return (cfg, store, job) or None when no SDLC job is active in this session."""
    if os.environ.get("SDLC_NESTED"):
        return None
    cwd = inp.get("cwd") or os.getcwd()
    try:
        root = config_mod.main_checkout(cwd)
    except Exception:  # noqa: BLE001 - not a git repo
        return None
    if not (root / "SDLC.md").exists():
        return None
    cfg = config_mod.load(root)
    store = Store(cfg)
    if not store.dir.exists():
        return None
    job = store.find_job_by_session(inp.get("session_id"))
    if not job:
        return None
    return cfg, store, job


def _brief_output(first_prompt):
    m = re.search(r"Read (\S+\.md) first", first_prompt or "")
    if not m:
        return None, None
    try:
        front, _ = yamlish.parse_frontmatter(Path(m.group(1)).read_text())
        return front.get("output"), front
    except (OSError, yamlish.YamlError):
        return None, None


def _agent_transcript(inp):
    p = inp.get("agent_transcript_path")
    if p:
        return Path(p)
    if inp.get("agent_id") and inp.get("transcript_path"):
        return usage.subagent_transcript(inp["transcript_path"], inp["session_id"], inp["agent_id"])
    return None


def pre_tool_use(inp):
    ctx = _context(inp)
    agent_type = inp.get("agent_type") or ""
    if not ctx or not agent_type:
        return 0                     # the orchestrator itself, or no active job
    cfg, store, job = ctx
    tool = inp.get("tool_name")
    ti = inp.get("tool_input") or {}
    session = job.get("session") or {}
    if tool == "Bash":
        cmd = " ".join(str(ti.get("command", "")).split())
        for pat in cfg["blocked_commands"]:
            pat = pat.replace("{base}", cfg["base_branch"])
            if pat in cmd:
                store.event(job["id"], "guardrail_block", agent=agent_type, tool="Bash", detail=pat)
                return _deny(f"SDLC guardrail: `{pat}` is not allowed for pipeline agents.")
        return 0
    target = ti.get("file_path") or ti.get("notebook_path")
    if not target:
        return 0
    target = Path(target).resolve()
    outputs, _ = _allowed_outputs(inp, session, agent_type)
    if any(target == Path(o).resolve() for o in outputs):
        return 0
    wt = Path(session.get("worktree") or job["worktree"]).resolve()
    rel = _relative(target, wt)
    role = agent_type.split(":", 1)[-1] if agent_type.startswith("sdlc:") else "reviewer"
    reason = None
    if role == "builder":
        if rel is None:
            reason = f"the builder may only write inside its worktree {wt}"
        elif any(rules.glob_match(g, rel) for g in cfg["protected_paths"]):
            reason = f"{rel} is a protected path"
    elif role == "tester":
        if rel is None or not any(rules.glob_match(g, rel) for g in cfg["test_paths"]):
            reason = f"the tester may only write test files ({', '.join(cfg['test_paths'])})"
    else:
        reason = "this agent may only write its own output file: " + ", ".join(outputs or ["(none)"])
    if reason:
        store.event(job["id"], "guardrail_block", agent=agent_type, tool=tool, path=str(target))
        return _deny(f"SDLC guardrail: {reason}.")
    return 0


def _allowed_outputs(inp, session, agent_type):
    tr = _agent_transcript(inp)
    if tr and tr.exists():
        out, front = _brief_output(usage.summarize(tr)["first_prompt"])
        if out:
            return [out], front
    return [s["output"] for s in session.get("spawned", []) if s.get("agent") == agent_type], None


def _relative(target, root):
    try:
        return str(target.relative_to(root))
    except ValueError:
        return None


def subagent_stop(inp):
    ctx = _context(inp)
    if not ctx:
        return 0
    cfg, store, job = ctx
    session = job.get("session") or {}
    tr = _agent_transcript(inp)
    summary = usage.summarize(tr) if tr else {"tokens": 0}
    output, front = _brief_output(summary.get("first_prompt"))
    stage = (front or {}).get("stage") or session.get("stage") or "other"
    data = {k: summary.get(k) for k in ("tokens", "input", "output", "cache_creation", "cache_read",
                                         "duration_s", "tool_uses", "tool_errors", "model")}
    key = inp.get("agent_id") or "agent"
    store.event(job["id"], "agent", key=key, agent_id=inp.get("agent_id"),
                agent=inp.get("agent_type") or "", stage=stage,
                reviewer=(front or {}).get("reviewer"), data=data)
    store.record_usage(job["id"], key, data.get("tokens") or 0)
    if output and not Path(output).exists() and not inp.get("stop_hook_active"):
        print(json.dumps({"decision": "block",
                          "reason": f"You haven't written {output} yet. Write it now, in the format "
                                    f"your brief describes, then finish."}))
    return 0


def stop(inp):
    ctx = _context(inp)
    if not ctx:
        return 0
    cfg, store, job = ctx
    offset = None
    for e in store.events(job["id"]):
        if e.get("agent") == "orchestrator":
            offset = (e.get("data") or {}).get("lines", offset)
    tpath = Path(inp.get("transcript_path", ""))
    if offset is None:
        # first record for this job: start where the session began working on it,
        # so earlier unrelated conversation isn't charged to the job
        offset = 0
        if tpath.exists():
            marker = f"next {job['id']}"
            for i, line in enumerate(tpath.read_text(errors="replace").splitlines()):
                if marker in line:
                    offset = max(i - 2, 0)
                    break
    s = usage.summarize(tpath, start_line=offset)
    if s["tokens"]:
        key = f"orchestrator-{offset}"
        store.event(job["id"], "agent", key=key, agent="orchestrator", stage="orchestrate",
                    data={"tokens": s["tokens"], "cache_read": s["cache_read"], "lines": s["lines"],
                          "model": s["model"]})
        store.record_usage(job["id"], key, s["tokens"])
    if (job.get("session") or {}).get("closing"):
        store.update_job(job["id"], session=None)
    return 0


HANDLERS = {"PreToolUse": pre_tool_use, "SubagentStop": subagent_stop, "Stop": stop}


def main(event):
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    handler = HANDLERS.get(event)
    if not handler:
        return 0
    try:
        return handler(inp)
    except Exception as e:  # noqa: BLE001
        # Fail closed only for writes during an active job; never break other hooks.
        if event == "PreToolUse" and inp.get("agent_type"):
            return _deny(f"SDLC guardrail check failed ({e}); write refused to be safe.")
        print(f"sdlc hook {event} error: {e}", file=sys.stderr)
        return 0
