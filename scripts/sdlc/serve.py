"""Local dashboard server (INSTRUCTIONS.md §9, E1). Standard library only.

Listens on 127.0.0.1, random port, with a per-run token. Reads job folders on
every request (no stored board), and writes only SDLC.md and reviewer
files, through configedit / reviewers.
"""

import datetime as dt
import http.server
import json
import re
import secrets
import socketserver
import urllib.parse
from pathlib import Path

from . import briefs, config as config_mod, configedit, pipeline, principles as principles_mod, reviewers as reviewers_mod, runners, thread
from .store import Store

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
COLUMNS = ["queued", "spec", "build", "verify", "review", "merge", "done"]   # "human required" is a flag on a job, never a column


def _ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


EXPLAIN = {
    "exchanges": "A verify, CI or merge failure kept coming back after {x} fix attempts "
                 "(global.pipeline.max_exchanges). Decide whether to merge anyway or send it back.",
    "ruling": "The checks fail after your ruling was applied, so the issue is not settled.",
    "budget": "The job hit its token budget. Raise the budget to continue, or send it back with new direction.",
    "spec-max": "The spec reviewers still object after the maximum number of spec revisions.",
    "rules": "A merge rule (see config) is not satisfied, so the job cannot merge by itself.",
    "stalemate": "Issues were still open after {x} exchange(s) (global.pipeline.max_exchanges), or the "
                 "spec-writer says the spec should change. Neither side will move, so a person has to decide.",
    "adapter": "A git, PR or CI step failed in a way the pipeline could not recover from.",
    "setup": "The setup command failed, so the worktree is not ready.",
}


def _findings(folder):
    """What the reviewers raised in one round: [{reviewer, verdict, severity, title, why}]."""
    out = []
    for f in sorted(folder.glob("review-*.md")):
        front, body = runners.read_artifact(f)
        if front and body:
            out += [{"reviewer": f.stem[len("review-"):], "verdict": front.get("verdict"),
                     "severity": c["severity"] or "blocking", "title": c["text"], "why": c["detail"]}
                    for c in thread.parse_comments(body) if c["id"] == "new"]
    return out


def _rounds(p, events):
    """Per round: why it started, what the builder did, verify/test verdicts, and every review."""
    why = {e["n"]: e.get("reason") for e in events if e["type"] == "round"}
    out = []
    for d in sorted(p.dir.glob("round-*"), key=lambda x: int(x.name.split("-")[1])):
        n = int(d.name.split("-")[1])
        r = {"n": n, "started": why.get(n, ""), "build": "", "verdicts": {}, "reviews": []}
        _, body = runners.read_artifact(d / "build.md")
        r["build"] = (body or "").strip()[:1500]
        for step in ("verify", "test"):
            front, _ = runners.read_artifact(d / f"{step}.md")
            if front:
                r["verdicts"][step] = front.get("verdict", "")
        for f in sorted(d.glob("review-*.md")):
            front, _ = runners.read_artifact(f)
            name = f.stem[len("review-"):]
            r["reviews"].append({"reviewer": name, "verdict": (front or {}).get("verdict", ""),
                                 "findings": [x for x in _findings(d) if x["reviewer"] == name]})
        out.append(r)
    return out


IDLE = {"merge": "The merge gate is approved, but approving only records your decision. Nothing advances "
                 "until the pipeline runs again; it will then rebase, re-verify and merge the branch.",
        "default": "Nothing is running this job right now (no active session). Run it to carry on from "
                   "the {stage} stage."}


def A(cmd, note=""):
    return {"cmd": cmd, "note": note}


def help_for(p, st, status, gate, idle=False):
    """What a person must do for this job, in plain words, plus the exact commands."""
    j = p.job_id
    if st.needs_human and not st.failed:
        reason = (st.needs_human or {}).get("reason", "")
        approve, rework = f"/sdlc:approve {j} ", f'/sdlc:rework {j} "what to change"'
        acts = {"budget": [A(approve + "budget +500k", "raise the budget"), A(rework)],
                "spec-max": [A(approve + "spec", "accept the spec as it is"), A(rework)],
                "exchanges": [A(approve + "override", "merge despite the failure"),
                              A(rework, "send back with your own direction")],
                "ruling": [A(approve + "override"), A(rework)],
                "rules": [A(approve + "override"), A(rework)],
                "stalemate": [A(approve + "override", "side with the builder: accept the work as it is"),
                              A(rework, "side with the checker: send it back with your ruling"),
                              A(rework + " --spec", "the spec is wrong: the spec-writer changes it, then the builder rebuilds")],
                "adapter": [A(approve + "retry")]}.get(reason, [A(f"/sdlc:status {j}")])
        return {"title": f"Human required: {reason}", "explain": EXPLAIN.get(reason, "").format(n=p.cfg.v("global.pipeline.max_rounds"), x=p.cfg.v("global.pipeline.max_exchanges")),
                "history": (st.needs_human or {}).get("detail", ""), "actions": acts}
    if gate:
        cmd = f"/sdlc:approve {j} {gate}"
        return {"title": f"Waiting for your approval: {gate} gate",
                "explain": ("Read the spec and approve it, or send it back." if gate == "spec" else
                            "Review the branch or PR and approve it to merge."),
                "history": "", "actions": [A(cmd), A(f'/sdlc:rework {j} "what to change"')]}
    if idle and not st.failed and status not in ("done", "cancelled"):
        return {"title": f"Paused at {status}: nothing is running it",
                "explain": IDLE.get(status, IDLE["default"]).format(stage=status), "history": "", "idle": True,
                "actions": [A(f"/sdlc:start {j}", "continue the job")]}
    if st.failed:
        return {"title": "Human required: failed", "explain": (st.failed or {}).get("reason", ""), "history": "",
                "actions": [A(f"/sdlc:start {j}", "retry")]}
    return None


def job_summary(cfg, meta, detail=False):
    p = pipeline.Pipeline(cfg, meta["id"])
    st = p.state()
    total, by_stage = p.job_usage()
    events = st.events
    status = "cancelled" if st.cancelled else st.stage     # where the job is; never "needs-human" or "failed"
    human = None                                           # why a person must act, if one must
    if not st.cancelled and st.stage != "done":
        human = "failed" if st.failed else (st.needs_human or {}).get("reason")
    last = max((_ts(e["ts"]) for e in events if _ts(e.get("ts", ""))), default=None)
    session = meta.get("session") or {}
    stalled = bool(session and not session.get("closing") and last and
                   (dt.datetime.now(dt.timezone.utc) - last).total_seconds() > cfg.v("global.dashboard.stall_after") * 60)
    reviewers = {}
    for e in events:
        if e["type"] == "selection" and e.get("round") in (st.round, st.spec_rev):
            reviewers.setdefault(e["reviewer"], "skipped" if not e["selected"] else "pending")
    folder = p.round_dir(st.round)
    for name in list(reviewers):
        fr, _ = runners.read_artifact(folder / f"review-{name}.md")
        if fr:
            reviewers[name] = fr.get("verdict", "pending")
    idle = bool(not session or session.get("closing")) and bool(
        last and (dt.datetime.now(dt.timezone.utc) - last).total_seconds() > 60)
    waiting = None
    for g, s in st.gates.items():
        if s == "waiting":
            waiting = g
    out = {"id": meta["id"], "title": meta["title"], "status": status, "stage": st.stage,
           "round": st.round, "tokens": total, "budget": cfg.v("global.budget")["per_job"] + st.budget_extra,
           "reviewers": reviewers, "gate": waiting, "stalled": stalled,
           "human": human,
           "why": (st.needs_human or {}).get("reason") or (st.failed or {}).get("reason"),
           "detail": (st.needs_human or {}).get("detail", ""), "updated": last.isoformat() if last else None,
           "help": help_for(p, st, status, waiting, idle), "alerts": len(Store(cfg).alerts(meta["id"]))}
    if out["help"] and detail:
        out["help"]["findings"] = _findings(folder)
    if detail:
        out.update(source=meta.get("source"), created=meta.get("created"), owner=meta.get("owner"),
                   branch=meta["branch"], pr=meta.get("pr"), by_stage=by_stage,
                   timeline=_enrich(timeline(events, by_stage), p, _rounds(p, events), out["help"], events, cfg),
                   alerts=Store(cfg).alerts(meta["id"]), overrides=st.overrides, max_rounds=cfg.v("global.pipeline.max_rounds"),
                   blocks=[e for e in events if e["type"] in ("guardrail_block", "error", "retry")][-20:],
                   applied=_applied_entries(cfg, p.dir), threads=thread.threads(p.dir))
    return out


def _applied_entries(cfg, d):
    """The learnings and answers a job cited (`applied: L-xxxx`), with their text from SDLC.md.
    One that has since been deleted from SDLC.md is still listed, marked as removed."""
    body = cfg.body_sections()
    known = {e["id"]: e for sec in ("Learnings", "Answers") for e in briefs.parse_entries(body.get(sec, ""))}
    return [{"id": i, "roles": (known.get(i) or {}).get("roles", []), "text": (known.get(i) or {}).get("text", ""),
             "removed": i not in known} for i in sorted(set(_applied(d)))]


def _applied(d):
    import re
    for f in d.rglob("*.md"):
        if "briefs" in f.parts:
            continue
        yield from re.findall(r"applied:\s*([LA]-[0-9a-f]+)", f.read_text(errors="replace"))


def _note(e):
    """One human sentence for events that explain why a stage is where it is."""
    t, by = e["type"], (" by " + e["by"]) if e.get("by") else ""
    return {"gate": f"{e.get('gate')} gate {e.get('status')}{by}",
            "override": f"override{by}: {e.get('reason', '')}",
            "resume": f"resumed{by}",
            "needs_human": f"needs a human ({e.get('reason')}): {e.get('detail', '')}",
            "failed": f"failed: {e.get('reason', '')}",
            "pr": f"PR opened: {e.get('url')}",
            "retry": f"retry: {e.get('detail', '')}"}.get(t)


def _first_para(text, limit=220):
    for para in re.split(r"\n\s*\n", text or ""):
        para = para.strip()
        if para and not para.startswith(("#", "Commit `")):
            para = " ".join(para.split())
            return para if len(para) <= limit else para[:limit].rstrip() + "…"
    return ""


def _reviewers(stage, n, r, events, cfg, first):
    """Everyone considered for this review step: who ran and what they said, who was skipped and why,
    and (first visit only) configured reviewers the job never evaluated."""
    ran = {x["reviewer"]: x for x in (r or {}).get("reviews", [])}
    out, named = [], set()
    for e in events:
        if e["type"] == "selection" and e.get("step") == stage and e.get("round") == n:
            named.add(e["reviewer"])
            x = ran.get(e["reviewer"])
            spec = (cfg.reviewers.get(e["reviewer"]) or {}) if cfg else {}
            out.append({"name": e["reviewer"], "state": "ran" if x or e["selected"] else "skipped",
                        "verdict": (x or {}).get("verdict") or ("pending" if e["selected"] else ""),
                        "reason": e.get("reason") or ("" if e["selected"] else f"its rule was not met (when: {spec.get('when', 'always')})"),
                        "findings": (x or {}).get("findings", []),
                        "blocking": spec.get("blocking", True)})
    if first and cfg:
        everyone = {e["reviewer"] for e in events if e["type"] == "selection"}
        for name, spec in cfg.reviewers.items():
            if name not in everyone and spec.get("at", "review") == stage:
                out.append({"name": name, "state": "not evaluated", "verdict": "", "findings": [],
                            "reason": "no record for this job: probably added to SDLC.md after this job's review",
                            "blocking": spec.get("blocking", True)})
    return out


def _enrich(steps, p, rounds, help_, events=(), cfg=None):
    """Give every timeline step: an outcome, a one-line summary, the files behind it, its details
    (builder notes, verdicts, review findings) and, on the current step, what a person must do."""
    by_n, seen = {r["n"]: r for r in rounds}, set()
    for s in steps:
        n, stage = s["round"], s["stage"]
        r, d = by_n.get(n), p.round_dir(n)
        rel = lambda *names: [f"{d.name}/{x.name}" for pat in names for x in sorted(d.glob(pat))]
        s["status"], s["summary"], s["files"], s["detail"] = "", "", [], {}
        if stage == "spec":
            s["files"] = ["spec.md"] if (p.dir / "spec.md").exists() else []
            _, body = runners.read_artifact(p.dir / "spec.md")
            s["summary"] = _first_para(re.sub(r"^#.*$", "", body or "", flags=re.M))
        elif stage == "build" and r:
            s["files"] = rel("build.md")
            note = f"Round {n}, started because {r['started']}. " if n > 1 and r["started"] else ""
            s["summary"] = note + _first_para(r["build"])
            s["detail"] = {"build": r["build"]}
        elif stage == "verify" and r:
            s["files"] = rel("verify.md", "test.md")
            v = r["verdicts"]
            s["status"] = ("FAIL" if any(x != "PASS" for x in v.values()) else "PASS") if v else ""
            s["summary"] = " · ".join(f"{k} {x}" for k, x in v.items())
        elif stage in ("review", "merge") and (r or stage == "merge"):
            if stage == "review":
                s["files"] = rel("review-*.md")
            else:
                s["files"] = rel("verify-final*.md", "pr-body.md")
            revs = _reviewers(stage, n, r, events, cfg, first=stage not in seen)
            seen.add(stage)
            ran = [x for x in revs if x["state"] == "ran"]
            vs = [x["verdict"] for x in ran]
            if stage == "review":
                s["status"] = "CHANGES" if "CHANGES" in vs else "PASS" if vs else ""
            parts = [x["name"] + " " + x["verdict"] +
                     (f" ({sum(1 for f in x['findings'] if not f['severity'].lower().startswith('nit'))} important)"
                      if x["verdict"] == "CHANGES" else "") for x in ran]
            parts += [x["name"] + " skipped" for x in revs if x["state"] == "skipped"]
            s["summary"] = " · ".join(parts)
            s["detail"] = {"reviewers": revs}
    if steps and help_:
        steps[-1]["attention"] = {k: help_.get(k) for k in ("title", "explain", "history", "actions")}
    return steps


def timeline(events, by_stage):
    steps, cur, rnd = [], None, 1
    for e in events:
        if e["type"] == "round":
            rnd = e["n"]
        if e["type"] == "stage":
            if cur:
                cur["end"] = e["ts"]
            cur = {"stage": e["stage"], "start": e["ts"], "end": None, "round": rnd, "agents": [], "verdicts": [], "notes": []}
            steps.append(cur)
        elif cur and e["type"] == "agent":
            d = e.get("data") or {}
            cur["agents"].append({"agent": e.get("agent"), "model": d.get("model"),
                                  "tokens": d.get("tokens", 0), "cache_read": d.get("cache_read", 0),
                                  "duration_s": d.get("duration_s"), "reviewer": e.get("reviewer")})
        elif cur and (note := _note(e)):
            cur["notes"].append({"ts": e["ts"], "text": note})
        elif cur and e["type"] == "verdict":
            cur["verdicts"].append({k: e.get(k) for k in ("step", "reviewer", "verdict", "round")})
    for s in steps:
        a, b = _ts(s["start"]), _ts(s["end"]) if s["end"] else None
        s["duration_s"] = int((b - a).total_seconds()) if a and b else None
        s["tokens"] = sum(a.get("tokens") or 0 for a in s["agents"])   # this visit only, not all rounds
    return steps


def board(cfg):
    st = Store(cfg)
    jobs = [job_summary(cfg, m) for m in st.jobs()]
    done = [j for j in jobs if j["status"] == "done"][-30:]
    trends = {"jobs": len(done),
              "avg_tokens": int(sum(j["tokens"] for j in done) / len(done)) if done else 0,
              "first_pass": round(sum(1 for j in done if j["round"] <= 1) / len(done), 2) if done else None}
    return {"columns": COLUMNS, "jobs": jobs, "backlog": st.backlog(), "backlog_items": st.backlog_items(),
            "month": {"tokens": st.month_tokens(), "budget": cfg.v("global.budget")["per_month"]},
            "trends": trends, "repo": cfg.root.name}


def settings(cfg):
    text = (cfg.root / "SDLC.md").read_text() if (cfg.root / "SDLC.md").exists() else ""
    schema = json.loads((PLUGIN_ROOT / "templates" / "sdlc.schema.json").read_text())
    body = cfg.body_sections()
    return {"schema": schema, "effective": cfg.data, "overrides": cfg.raw, "hash": configedit.file_hash(text),
            "defaults": config_mod.DEFAULTS,
            "reviewer_template": (PLUGIN_ROOT / "templates" / "review-template.md").read_text(),
            "agent_template": (PLUGIN_ROOT / "templates" / "reviewer-agent-template.md").read_text(),
            "reviewers_mode": "files" if isinstance((((cfg.raw.get("review") or {}).get("reviewer")) or {}).get("list"), list) else "inline",
            "problems": cfg.problems, "principle_problems": cfg.principle_problems,
            "learnings": {s: briefs.parse_entries(body.get(s, "")) for s in ("Learnings", "Answers")},
            "usage": {"month": Store(cfg).month_tokens()}}


class Handler(http.server.BaseHTTPRequestHandler):
    token = None
    root = None
    origin = None

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json"
                                                      else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, q):
        t = self.headers.get("X-SDLC-Token") or (q.get("token") or [None])[0]
        return t and secrets.compare_digest(t, self.token)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._authorized(q):
            return self._send(403, {"error": "missing or wrong token"})
        cfg = config_mod.load(self.root, lenient=True)
        if u.path == "/":
            return self._send(200, (PLUGIN_ROOT / "templates" / "dashboard.html").read_text(), "text/html")
        if u.path == "/api/board":
            return self._send(200, board(cfg))
        if u.path == "/api/job":
            meta = Store(cfg).load_job(q["id"][0])
            return self._send(200, job_summary(cfg, meta, detail=True))
        if u.path == "/api/file":
            d = Store(cfg).job_dir(q["job"][0]).resolve()
            f = (d / q["path"][0]).resolve()
            if d not in f.parents or f.suffix != ".md" or not f.exists():
                return self._send(404, {"error": "not found"})
            return self._send(200, f.read_text(errors="replace"), "text/plain")
        if u.path == "/api/settings":
            return self._send(200, settings(cfg))
        if u.path == "/api/reviewer":
            return self._send(200, reviewer_file(cfg, q["name"][0]))
        if u.path == "/api/help":
            return self._send(200, (PLUGIN_ROOT / "reference" / "help.md").read_text(), "text/plain")
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self._authorized({}):
            return self._send(403, {"error": "missing or wrong token"})
        if self.headers.get("Origin") not in (None, self.origin):
            return self._send(403, {"error": "cross-origin request refused"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self._send(415, {"error": "JSON only"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "bad JSON"})
        cfg = config_mod.load(self.root, lenient=True)
        try:
            if u.path == "/api/settings/preview":
                text = (cfg.root / "SDLC.md").read_text()
                new = configedit.apply(text, req.get("changes", {}))
                todo = configedit._files_to_write(cfg.root, req.get("files"), new)
                configedit._validate_text(cfg.root, new, todo)
                return self._send(200, {"before": text, "after": new,
                                        "writes": configedit.describe_writes(cfg.root, todo)})
            if u.path == "/api/settings":
                h = configedit.save(cfg, req.get("changes", {}), req.get("hash"), files=req.get("files"))
                return self._send(200, {"saved": True, "hash": h})
            if u.path == "/api/reviewer/save":
                rel = reviewers_mod.save(cfg, req.get("name"), req.get("front") or {}, req.get("body") or "",
                                         req.get("agent_content"))
                return self._send(200, {"saved": rel})
            if u.path == "/api/reviewer/delete":
                return self._send(200, {"removed": reviewers_mod.delete(cfg, req.get("name"))})
            if u.path == "/api/reviewers/convert":
                return self._send(200, reviewers_mod.convert_legacy(cfg))
            if u.path == "/api/principle/save":
                return self._send(200, {"saved": principles_mod.save(cfg, req.get("name"), req.get("front") or {},
                                                                     req.get("body") or "")})
            if u.path == "/api/principle/delete":
                return self._send(200, {"removed": principles_mod.delete(cfg, req.get("name"))})
            if u.path == "/api/backlog":
                return self._send(200, backlog_action(cfg, req))
            if u.path == "/api/alerts/clear":
                Store(cfg).load_job(req["job"])
                return self._send(200, {"cleared": Store(cfg).clear_alerts(req["job"], req.get("ids"))})
            if u.path == "/api/job/delete":
                return self._send(200, delete_job(cfg, req.get("id")))
            if u.path == "/api/learnings/update":
                return self._send(200, update_learning(cfg, req.get("id"), req.get("roles"), req.get("text")))
            if u.path == "/api/learnings/delete":
                return self._send(200, delete_learning(cfg, req.get("id")))
        except (configedit.EditError, config_mod.ConfigError, ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})


def reviewer_file(cfg, name):
    """The editable file behind a custom reviewer: its instructions, or its subagent definition."""
    r = cfg.reviewers.get(name) or {}
    rel = r.get("instructions") or (f".claude/agents/{r['agent']}.md" if r.get("agent") else None)
    if not rel:
        return {"path": None, "content": ""}
    f = (cfg.root / rel).resolve()
    if cfg.root.resolve() not in f.parents or f.suffix != ".md":
        raise ValueError("not an editable reviewer file")
    return {"path": rel, "content": f.read_text(errors="replace") if f.exists() else ""}


def backlog_action(cfg, req):
    st, action = Store(cfg), req.get("action")
    pri = req.get("priority")
    if pri not in (None, "", "low", "medium", "high"):
        raise ValueError("priority must be low, medium or high")
    if action == "add":
        if not (req.get("title") or "").strip():
            raise ValueError("title is required")
        return {"id": st.add_backlog(req["title"], req.get("description") or "", pri or "")}
    if action == "update":
        st.update_backlog(req.get("id"), req.get("title"), req.get("description"), pri)
        return {"saved": True}
    if action == "delete":
        st.delete_backlog(req.get("id"))
        return {"deleted": True}
    raise ValueError("action must be add, update or delete")


def delete_job(cfg, job_id):
    """Remove a finished job's folder (events, reports, alerts). The month's token ledger, the
    merged code and its branch are left alone. Only a done job can be deleted."""
    import shutil
    store = Store(cfg)
    store.load_job(job_id)                                    # KeyError -> 400 when unknown
    if pipeline.Pipeline(cfg, job_id).state().stage != "done":
        raise ValueError(f"job {job_id} isn't done; cancel it instead")
    shutil.rmtree(store.job_dir(job_id))
    return {"deleted": job_id}


def delete_learning(cfg, entry_id):
    """Remove one Learning or Answer line from SDLC.md."""
    path = cfg.root / "SDLC.md"
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines(keepends=True)
    keep = [l for l in lines if not (briefs.ENTRY.match(l) and briefs.ENTRY.match(l).group("id") == entry_id)]
    if len(keep) == len(lines):
        raise KeyError(f"no learning or answer {entry_id}")
    path.write_text("".join(keep))
    return {"deleted": entry_id}


def update_learning(cfg, entry_id, roles, text):
    """Rewrite one Learning or Answer line, keeping its id."""
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("text is required")
    roles = ", ".join(str(r).strip() for r in (roles or []) if str(r).strip()) or "all"
    path = cfg.root / "SDLC.md"
    lines = (path.read_text() if path.exists() else "").splitlines(keepends=True)
    for i, l in enumerate(lines):
        m = briefs.ENTRY.match(l)
        if m and m.group("id") == entry_id:
            lines[i] = f"- [{entry_id}] {roles} · {text}\n"
            path.write_text("".join(lines))
            return {"updated": entry_id}
    raise KeyError(f"no learning or answer {entry_id}")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def serve(cfg, port=0, open_browser=False):
    token = secrets.token_urlsafe(18)
    srv = Server(("127.0.0.1", port), Handler)
    Handler.token, Handler.root = token, cfg.root
    Handler.origin = f"http://127.0.0.1:{srv.server_address[1]}"
    url = f"{Handler.origin}/?token={token}"
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "dashboard.url").write_text(url + "\n")
    print(json.dumps({"url": url}), flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    srv.serve_forever()
