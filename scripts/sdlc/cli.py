"""`sdlc.py` command line. Every command prints compact output (JSON for actions),
because whatever it prints lands in the orchestrator's context."""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import adapters, config as config_mod, hooks, learn, pipeline, runners, yamlish
from .store import Store

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent


def out(obj):
    print(json.dumps(obj, separators=(",", ":")) if not isinstance(obj, str) else obj)


def load():
    return config_mod.load()


# ------------------------------------------------------------------ init ---

def detect(root):
    root = Path(root)
    d = {"language": None, "commands": {}, "test_paths": None, "host": config_mod.detect_host(root),
         "gh": bool(shutil.which("gh")), "has_sdlc_md": (root / "SDLC.md").exists(),
         "has_review_md": (root / "REVIEW.md").exists(), "has_claude_md": (root / "CLAUDE.md").exists()}
    pj = root / "package.json"
    if pj.exists():
        d["language"] = "javascript"
        scripts = json.loads(pj.read_text()).get("scripts", {})
        pm = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
        d["commands"]["setup"] = f"{pm} install" if pm != "npm" else "npm ci" if (root / "package-lock.json").exists() else "npm install"
        for k in ("lint", "build", "test"):
            if k in scripts:
                d["commands"][k] = f"{pm} run {k}" if k != "test" else f"{pm} test"
        d["test_paths"] = ["**/*.test.*", "**/*.spec.*", "test/**", "tests/**", "__tests__/**"]
    elif (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        d["language"] = "python"
        d["commands"]["test"] = "pytest -q" if shutil.which("pytest") else "python -m pytest -q"
        if "ruff" in ((root / "pyproject.toml").read_text() if (root / "pyproject.toml").exists() else ""):
            d["commands"]["lint"] = "ruff check ."
        d["test_paths"] = ["tests/**", "test/**", "**/test_*.py", "**/*_test.py"]
    elif (root / "go.mod").exists():
        d["language"] = "go"
        d["commands"].update(build="go build ./...", test="go test ./...", lint="go vet ./...")
        d["test_paths"] = ["**/*_test.go"]
    elif (root / "Cargo.toml").exists():
        d["language"] = "rust"
        d["commands"].update(build="cargo build", test="cargo test", lint="cargo clippy -- -D warnings")
        d["test_paths"] = ["tests/**"]
    if (root / "Makefile").exists():
        mk = (root / "Makefile").read_text(errors="replace")
        for k in ("lint", "build", "test"):
            if f"\n{k}:" in "\n" + mk and k not in d["commands"]:
                d["commands"][k] = f"make {k}"
    return d


def cmd_init(a):
    root = config_mod.main_checkout()
    if a.detect:
        return out(detect(root))
    settings = json.loads(Path(a.write).read_text()) if a.write else {}
    sdlc_md = root / "SDLC.md"
    if not sdlc_md.exists():
        tmpl = (PLUGIN_ROOT / "templates" / "SDLC.md").read_text()
        _, body = yamlish.parse_frontmatter(tmpl)
        sdlc_md.write_text("---\n" + yamlish.dumps(settings) + "\n---\n" + body if settings else tmpl)
        created = ["SDLC.md"]
    else:
        created = []
    if not (root / "REVIEW.md").exists():
        shutil.copy(PLUGIN_ROOT / "templates" / "REVIEW.md", root / "REVIEW.md")
        created.append("REVIEW.md")
    cfg = config_mod.load(root)
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    gi = root / ".gitignore"
    entry = cfg["work_dir"].rstrip("/") + "/"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if entry not in lines and entry.rstrip("/") not in lines:
        gi.write_text("\n".join(lines + [entry]) + "\n")
        created.append(".gitignore entry")
    out({"created": created, "work_dir": str(cfg.work_dir), "host": cfg["host"]["adapter"],
         "worktree_dir": str(cfg.worktree_dir)})


# ------------------------------------------------------------ jobs etc. ----

def cmd_intake(a):
    cfg = load()
    Store(cfg).add_backlog(" ".join(a.text))
    out(f"added to backlog: {' '.join(a.text)}")


def cmd_start(a):
    cfg = load()
    st = Store(cfg)
    arg = " ".join(a.what).strip()
    if arg and (st.job_dir(arg.removeprefix("job-")) / "job.json").exists():
        job_id = arg.removeprefix("job-")
        if pipeline.retry(cfg, job_id):
            out({"job": job_id, "retrying": True})
            return
        out({"job": job_id, "continuing": True})
        return
    try:
        learn.sync_pending(cfg)
    except Exception:  # noqa: BLE001 - never block starting a job on learnings bookkeeping
        pass
    item = st.pop_backlog_item(arg or None)
    text = arg or (item or {}).get("title")
    if not text:
        return out({"error": "the backlog is empty; give a feature description or /sdlc:intake one"})
    desc = (item or {}).get("description")
    meta = st.create_job(text, source=f"{text}\n\n{desc}" if desc else None)
    out({"job": meta["id"], "created": True, "branch": meta["branch"]})


def cmd_next(a):
    cfg = load()
    p = pipeline.Pipeline(cfg, a.job, session_id=a.session or os.environ.get("CLAUDE_CODE_SESSION_ID"))
    try:
        act = p.next_action()
    except Exception as e:  # noqa: BLE001
        p.ev("error", stage="next", detail=str(e))
        act = {"action": "failed", "job": a.job, "reason": f"internal error: {e}"}
    if act["action"] in ("done", "failed") and not act.get("cancelled"):
        try:
            learn.retro(cfg, a.job)
        except Exception as e:  # noqa: BLE001
            act["retro_error"] = str(e)
    out(act)


def cmd_report(a):
    cfg = load()
    if a.answers:
        out(pipeline.report_answers(cfg, a.job, Path(a.answers).read_text()))
    else:
        out({"error": "nothing to report (use --answers FILE)"})


def cmd_approve(a):
    cfg = load()
    if a.job == "learnings":
        return out(approve_learnings(cfg, [a.gate] + (a.value or []) if a.gate else []))
    try:
        out(pipeline.approve(cfg, a.job, a.gate, (a.value or [None])[0]))
    except ValueError as e:
        out({"error": str(e)})


def approve_learnings(cfg, ids):
    from . import briefs
    p = cfg.work_dir / "learnings-pending.md"
    secs = briefs.pending_sections(cfg)
    props = briefs.parse_entries(secs.get("Proposals", ""))
    chosen = [e for e in props if not ids or e["id"] in ids]
    if not chosen:
        return {"approved": 0}
    entries = [{"kind": "learning", "roles": e["roles"], "text": e["text"]} for e in chosen]
    text = p.read_text()
    for e in chosen:
        text = text.replace(e["line"] + "\n", "")
    p.write_text(text)
    learn.add_pending(cfg, entries, "approved")
    return {"approved": len(chosen), "note": "settings/REVIEW.md/CLAUDE.md proposals must be "
                                             "applied by hand in v1; learnings were moved to Learnings"}


def cmd_rework(a):
    out(pipeline.rework(load(), a.job, " ".join(a.feedback)))


def cmd_cancel(a):
    cfg = load()
    pipeline.cancel(cfg, a.job)
    learn.retro(cfg, a.job)
    out(f"job {a.job} cancelled; its folder and branch are kept")


def cmd_status(a):
    cfg = load()
    st = Store(cfg)
    if a.check:
        return out(health(cfg))
    jobs = st.jobs() if not a.job else [st.load_job(a.job)]
    rows = []
    for m in jobs:
        p = pipeline.Pipeline(cfg, m["id"])
        s = p.state()
        total, by_stage = p.job_usage()
        status = ("cancelled" if s.cancelled else "failed" if s.failed else
                  "needs-human" if s.needs_human else s.stage)
        row = {"job": m["id"], "title": m["title"][:60], "status": status, "round": s.round,
               "tokens": total}
        if s.needs_human:
            row["why"] = s.needs_human.get("reason") + ": " + s.needs_human.get("detail", "")[:120]
        if a.job:
            row.update(by_stage=by_stage, branch=m["branch"], pr=m.get("pr"),
                       gates=s.gates, overrides=len(s.overrides))
        rows.append(row)
    out({"jobs": rows, "backlog": st.backlog()[:10], "month_tokens": st.month_tokens(),
         "month_budget": cfg["budgets"]["per_month"]})


def health(cfg):
    issues, ok, notes = [], [], []
    st = Store(cfg)
    ok.append("SDLC.md valid")
    if not (cfg["commands"] or {}).get("test"):
        issues.append("commands.test is not set: verify can't check anything")
    for name in ("lint", "build"):
        if not (cfg["commands"] or {}).get(name):
            notes.append(f"commands.{name} is not set (optional)")
    if cfg["host"]["adapter"] == "github" and not shutil.which("gh"):
        issues.append("host is github but `gh` is not installed")
    known = {m["branch"] for m in st.jobs()}
    branches = config_mod.git("branch", "--list", cfg["branch_prefix"] + "*", "--format=%(refname:short)",
                              cwd=cfg.root, check=False).split()
    orphans = [b for b in branches if b not in known and not b.startswith(cfg["branch_prefix"] + "learnings")]
    if orphans:
        issues.append("branches with no job folder: " + ", ".join(orphans))
    wts = [p for p in cfg.worktree_dir.glob("*") if p.is_dir()] if cfg.worktree_dir.exists() else []
    known_wt = {str(Path(m["worktree"])) for m in st.jobs()}
    lost = [str(p) for p in wts if str(p) not in known_wt and p.name != "learnings"]
    if lost:
        issues.append("worktrees with no job folder: " + ", ".join(lost))
    try:
        runners.claude_bin()
        ok.append("claude CLI found")
    except FileNotFoundError as e:
        issues.append(str(e))
    try:
        adapter_test(cfg, dry_run=True)
        ok.append(f"adapters configured (board: {cfg['board']['adapter']}, host: {cfg['host']['adapter']})")
    except adapters.AdapterError as e:
        issues.append(str(e))
    return {"ok": ok, "issues": issues, "notes": notes}


def adapter_test(cfg, kind=None, dry_run=False):
    """Check the configured adapters. --dry-run checks configuration only; a real run
    calls every operation twice (idempotency) against a test branch and cleans up."""
    results = []
    for k in ([kind] if kind else ["board", "host"]):
        a = adapters.board(cfg) if k == "board" else adapters.host(cfg)
        for op, tmpl in a.overrides.items():
            if op not in a.ops:
                raise adapters.AdapterError(f"{k}.ops.{op} is not a {k} operation ({', '.join(a.ops)})")
            if tmpl != "instructions":
                exe = tmpl.split()[0]
                if not (shutil.which(exe) or (cfg.root / exe).exists()):
                    raise adapters.AdapterError(f"{k}.ops.{op}: `{exe}` not found")
        if k == "host" and cfg["host"]["adapter"] == "github" and not shutil.which("gh"):
            raise adapters.AdapterError("host is github but `gh` is not installed")
        results.append({"adapter": k, "name": a.settings.get("adapter"), "configured": True})
        if dry_run:
            continue
        if k == "host" and isinstance(a, adapters.LocalHost):
            for _ in range(2):
                r = a.call("pr_status", branch="sdlc/adapter-test")
            results[-1]["pr_status"] = r
    return results


def cmd_wait(a):
    """Background waiter: exits (and so notifies the session) when something changes."""
    cfg = load()
    host = adapters.host(cfg)
    meta = Store(cfg).load_job(a.job)
    deadline = time.time() + a.max_hours * 3600
    while time.time() < deadline:
        s = host.call("pr_status", branch=meta["branch"])
        if a.for_ == "ci" and s["ci"] != "pending":
            return out({"job": a.job, "ci": s["ci"], "next": f"/sdlc:start {a.job}"})
        if a.for_ == "approval" and (s["approved"] or s.get("merged")):
            return out({"job": a.job, "approved": True, "next": f"/sdlc:start {a.job}"})
        time.sleep(a.interval)
    out({"job": a.job, "timeout": True})


def cmd_serve(a):
    from . import serve
    serve.serve(load(), port=a.port, open_browser=a.open)


def cmd_adapter(a):
    cfg = load()
    try:
        out({"results": adapter_test(cfg, a.kind, dry_run=a.dry_run)})
    except adapters.AdapterError as e:
        out({"error": str(e)})
        sys.exit(1)


def cmd_hook(a):
    sys.exit(hooks.main(a.event))


def cmd_config(a):
    cfg = load()
    if a.action == "validate":
        return out("SDLC.md is valid")
    out(cfg.data if not a.key else cfg.data.get(a.key))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sdlc.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--detect", action="store_true"); p.add_argument("--write")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("intake"); p.add_argument("text", nargs="+"); p.set_defaults(fn=cmd_intake)
    p = sub.add_parser("start"); p.add_argument("what", nargs="*"); p.set_defaults(fn=cmd_start)
    p = sub.add_parser("next"); p.add_argument("job"); p.add_argument("--session")
    p.set_defaults(fn=cmd_next)
    p = sub.add_parser("report"); p.add_argument("job"); p.add_argument("--answers")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("approve"); p.add_argument("job"); p.add_argument("gate", nargs="?")
    p.add_argument("value", nargs="*"); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("rework"); p.add_argument("job"); p.add_argument("feedback", nargs="+")
    p.set_defaults(fn=cmd_rework)
    p = sub.add_parser("cancel"); p.add_argument("job"); p.set_defaults(fn=cmd_cancel)
    p = sub.add_parser("status"); p.add_argument("job", nargs="?"); p.add_argument("--check", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("wait"); p.add_argument("job"); p.add_argument("--for", dest="for_", choices=["ci", "approval"])
    p.add_argument("--interval", type=int, default=60); p.add_argument("--max-hours", type=float, default=12)
    p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("hook"); p.add_argument("event"); p.set_defaults(fn=cmd_hook)
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=0)
    p.add_argument("--open", action="store_true"); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("adapter"); p.add_argument("action", choices=["test"]); p.add_argument("kind", nargs="?",
                                                                                            choices=["board", "host"])
    p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_adapter)
    p = sub.add_parser("config"); p.add_argument("action", nargs="?", default="get"); p.add_argument("key", nargs="?")
    p.set_defaults(fn=cmd_config)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except config_mod.ConfigError as e:
        out({"error": str(e)})
        sys.exit(2)
    except KeyError as e:
        out({"error": str(e).strip("'\"")})
        sys.exit(2)
