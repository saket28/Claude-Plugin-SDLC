"""Scripted work that costs no orchestrator tokens: verify commands, `run`
reviewers, and headless built-in reviews (INSTRUCTIONS.md §4, §5)."""

import glob
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import reviewers as reviewers_mod, usage, yamlish

TAIL = 60   # lines of command output kept in artifacts (keeps briefs small)

FINDINGS_FORMAT = """
When you have finished the review, end your reply with exactly one fenced block:
```json
{"findings": [{"severity": "important" | "nit" | "pre-existing", "file": "path",
  "line": 0, "summary": "one sentence", "detail": "why it matters and how to fix it"}]}
```
Use "important" only for issues that should block merging. Use [] when there are no findings.
"""


def tail(text, n=TAIL):
    lines = (text or "").rstrip().splitlines()
    return "\n".join(lines[-n:])


def write_artifact(path, front, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yamlish.dumps(front) + "\n---\n\n" + body.rstrip() + "\n")


def read_artifact(path):
    path = Path(path)
    if not path.exists():
        return None, None
    try:
        return yamlish.parse_frontmatter(path.read_text())
    except yamlish.YamlError:
        return {"_invalid": True}, path.read_text()


def sh(cmd, cwd, timeout=1800, env=None):
    t = time.time()
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or ""), int(time.time() - t)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out + f"\n[timed out after {timeout}s]", int(time.time() - t)


# ------------------------------------------------------------------ verify --

def verify(cfg, worktree, out_path):
    results, ok = [], True
    for name in cfg.v("test.verify.order"):
        cmd = cfg.command(name)
        if not cmd:
            continue
        code, out, secs = sh(cmd, worktree, timeout=cfg.v("global.command.timeout"))
        results.append((name, cmd, code, secs, out))
        if code != 0:
            ok = False
            if cfg.v("test.verify.stop_on_failure"):
                break           # later commands rarely help once one fails
    body = ["# Verify", ""]
    if not results:
        body.append("No verify commands are configured (global.command.lint/build/test, listed in test.verify.order).")
    for name, cmd, code, secs, out in results:
        body += [f"## {name}: {'PASS' if code == 0 else 'FAIL'} (exit {code}, {secs}s)",
                 f"`{cmd}`", ""]
        if code != 0:
            body += ["Last lines of output:", "```", tail(out), "```", ""]
    write_artifact(out_path, {"verdict": "PASS" if ok else "FAIL",
                              "commands": [r[0] for r in results]}, "\n".join(body))
    return ok


# ------------------------------------------------------------ run reviewer --

def run_reviewer(name, spec, worktree, out_path):
    """`run` reviewer: exit 0 = PASS, 126/127 = error (not runnable), else CHANGES."""
    code, out, secs = sh(spec["run"], worktree, timeout=spec.get("timeout", 900))
    if code in (126, 127):
        return {"error": f"`{spec['run']}` is not runnable (exit {code}): {tail(out, 5)}"}
    verdict = "PASS" if code == 0 else "CHANGES"
    important = 0 if code == 0 else 1
    body = [f"# Review: {name}", "", f"`{spec['run']}` exited {code} after {secs}s.", ""]
    if code != 0:
        body += ["## Important", f"1. `{spec['run']}` failed:", "```", tail(out), "```"]
    write_artifact(out_path, {"verdict": verdict, "important": important, "nit": 0,
                              "engine": "run", "blocking": spec.get("blocking", True)}, "\n".join(body))
    return {"verdict": verdict, "usage": {"tokens": 0}}


# ------------------------------------------------------- built-in reviews --

def claude_bin():
    for var in ("SDLC_CLAUDE_BIN", "EVAL_SDLC_CLAUDE_BIN", "CLAUDE_CODE_EXECPATH"):
        if os.environ.get(var) and Path(os.environ[var]).exists():
            return os.environ[var]          # CLAUDE_CODE_EXECPATH: the Claude Code running us
    found = shutil.which("claude")
    if found:
        return found
    for cand in [Path.home() / ".claude/local/claude", Path.home() / ".local/bin/claude"]:
        if cand.exists():
            return str(cand)
    bundled = sorted(glob.glob(str(Path.home() / ".vscode/extensions/anthropic.claude-code-*/"
                                   "resources/native-binary/claude")))
    if bundled:
        return bundled[-1]
    raise FileNotFoundError("cannot find the `claude` CLI; set SDLC_CLAUDE_BIN")


def headless(prompt, cwd, system_append="", model=None, disallow_edits=True, timeout=3600):
    cmd = [claude_bin(), "-p", prompt, "--output-format", "json"]
    if system_append:
        cmd += ["--append-system-prompt", system_append]
    if disallow_edits:
        cmd += ["--disallowedTools", "Edit", "Write", "NotebookEdit"]
    if model:
        cmd += ["--model", model]
    env = {**os.environ, "SDLC_NESTED": "1"}
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout)
    try:
        obj = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude -p returned non-JSON (exit {r.returncode}): "
                           f"{(r.stderr or r.stdout)[:500]}")
    if obj.get("is_error"):
        raise RuntimeError(f"claude -p reported an error: {str(obj.get('result'))[:500]}")
    return obj.get("result") or "", usage.from_headless_json(obj)


def parse_findings(text):
    """Accepts our {"findings": [...]} block or /code-review's own bare JSON list."""
    blocks = re.findall(r"```json\s*([\[{].*?[\]}])\s*```", text or "", flags=re.S)
    stripped = (text or "").strip()
    if stripped.startswith(("[", "{")):
        blocks.append(stripped)
    for b in reversed(blocks):
        try:
            data = json.loads(b)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("findings"), list):
            return data["findings"]
        if isinstance(data, list) and all(isinstance(x, dict) for x in data):
            return data
    if re.search(r"no (issues|findings|bugs) (were )?found|found no (issues|bugs)", text or "", re.I):
        return []
    return None


def classify(findings, review_rules, cwd, model="haiku"):
    """Give each finding without a severity one (important | nit), using REVIEW.md's
    definitions. One cheap headless call; on failure, unclassified findings count as important."""
    todo = [i for i, f in enumerate(findings) if not f.get("severity")]
    if not todo:
        return findings, {"tokens": 0}
    prompt = ("Classify each code review finding as \"important\" (would break behaviour, lose or leak "
              "data, block a rollback, or violates an explicit rule) or \"nit\" (style, naming, minor "
              "robustness). Reply with only a JSON list of strings, one per finding, in order.\n\n"
              + ("Repository review rules:\n" + review_rules[:6000] + "\n\n" if review_rules else "")
              + "Findings:\n" + json.dumps([findings[i] for i in todo], indent=1)[:20000])
    try:
        text, use = headless(prompt, cwd, model=model)
        m = re.search(r"\[.*\]", text, flags=re.S)
        labels = json.loads(m.group(0)) if m else []
    except Exception:  # noqa: BLE001
        labels, use = [], {"tokens": 0}
    for n, i in enumerate(todo):
        lab = str(labels[n]).lower() if n < len(labels) else "important"
        findings[i]["severity"] = "nit" if lab.startswith("nit") else "important"
    return findings, use


def findings_artifact(name, engine, findings, out_path, blocking=True, raw=""):
    imp = [f for f in findings if str(f.get("severity", "")).lower() == "important"]
    nit = [f for f in findings if str(f.get("severity", "")).lower() == "nit"]
    body = [f"# Review: {name}", ""]
    for title, group in (("Important", imp), ("Nit", nit)):
        if group:
            body.append(f"## {title}")
            for i, f in enumerate(group, 1):
                loc = f"`{f.get('file')}:{f.get('line')}` " if f.get("file") else ""
                body.append(f"{i}. {loc}{f.get('summary', '')}")
                for k in ("detail", "failure_scenario"):
                    if f.get(k):
                        body.append(f"   {f[k]}")
            body.append("")
    if not imp and not nit:
        body.append("No findings.")
    write_artifact(out_path, {"verdict": "CHANGES" if imp else "PASS", "important": len(imp),
                              "nit": len(nit), "engine": engine, "blocking": blocking},
                   "\n".join(body))


def builtin_review(cfg, name, spec, worktree, base, out_path):
    review_md = cfg.root / "REVIEW.md"
    rules = review_md.read_text() if review_md.exists() else ""
    extra = reviewers_mod.extra_instructions(spec)      # the reviewer file's own text, if any
    system = (("Repository review rules (REVIEW.md):\n" + rules + "\n" if rules else "") +
              ("Extra instructions for this review:\n" + extra + "\n" if extra else "") + FINDINGS_FORMAT)
    if spec["builtin"] == "code-review":
        prompt = f"/code-review {spec.get('effort', 'high')} {base}...HEAD"
    else:
        prompt = "/security-review"
    text, use = headless(prompt, worktree, system_append=system, model=spec.get("model"))
    findings = parse_findings(text)
    if findings is not None:
        findings, cuse = classify(findings, rules, worktree, model=cfg.model("judge"))
        use = {**use, "tokens": use.get("tokens", 0) + cuse.get("tokens", 0)}
    if findings is None:
        # hand the raw text to the orchestrator to convert (§5)
        raw = Path(out_path).with_suffix(".raw.md")
        raw.write_text(text)
        return {"convert": str(raw), "usage": use}
    findings_artifact(name, spec["builtin"], findings, out_path, spec.get("blocking", True))
    return {"usage": use}
