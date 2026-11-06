"""Local dashboard server (INSTRUCTIONS.md §9, E1). Standard library only.

Listens on 127.0.0.1, random port, with a per-run token. Reads job folders on
every request (no stored board), and writes only SDLC.md and
learnings-pending.md, and only through configedit / learn.
"""

import datetime as dt
import http.server
import json
import re
import secrets
import socketserver
import urllib.parse
from pathlib import Path

from . import briefs, config as config_mod, configedit, learn, pipeline, runners
from .store import Store

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
COLUMNS = ["queued", "spec", "build", "verify", "review", "merge", "done"]


def _ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


EXPLAIN = {
    "max-rounds": "The builder and reviewers went back and forth the maximum number of automatic rounds and "
                  "the reviewers still have blocking findings. Decide whether the remaining findings matter.",
    "budget": "The job hit its token budget. Raise the budget to continue, or send it back with new direction.",
    "spec-max": "The spec reviewers still object after the maximum number of spec revisions.",
    "rules": "A merge rule (see config) is not satisfied, so the job cannot merge by itself.",
    "disputed": "The reviewers and the tests disagree, so a person has to break the tie.",
    "adapter": "A git, PR or CI step failed in a way the pipeline could not recover from.",
    "setup": "The setup command failed, so the worktree is not ready.",
}


def _findings(folder):
    """Open review findings of one round: [{reviewer, verdict, severity, title, why}]."""
    out = []
    for f in sorted(folder.glob("review-*.md")):
        front, body = runners.read_artifact(f)
        if not front or not body:
            continue
        reviewer, sev, item = f.stem[len("review-"):], "", None
        for line in body.splitlines():
            m = re.match(r"##\s+(.*)", line)
            if m:
                sev, item = m.group(1).strip(), None
                continue
            m = re.match(r"\d+\.\s+(.*)", line)
            if m and sev:
                item = {"reviewer": reviewer, "verdict": front.get("verdict"), "severity": sev,
                        "title": m.group(1).strip(), "why": ""}
                out.append(item)
            elif item and line.startswith((" ", "\t")) and not item["why"]:
                item["why"] = line.strip()
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


def help_for(p, st, status, gate, detail):
    """What a person must do for this job, in plain words, plus the exact commands."""
    j = p.job_id
    if status == "needs-human":
        reason = (st.needs_human or {}).get("reason", "")
        approve, rework = f"/sdlc:approve {j} ", f'/sdlc:rework {j} "what to change"'
        acts = {"budget": [approve + "budget +500k   (raise the budget)", rework],
                "spec-max": [approve + "spec   (accept the spec as it is)", rework],
                "max-rounds": [approve + "override   (merge despite the open findings)",
                               rework + "   (send back with your own direction)"],
                "rules": [approve + "override", rework],
                "disputed": [approve + "override", rework],
                "adapter": [approve + "retry"]}.get(reason, [f"/sdlc:status {j}"])
        return {"title": f"Needs a decision: {reason}", "explain": EXPLAIN.get(reason, ""),
                "history": (st.needs_human or {}).get("detail", ""), "actions": acts}
    if gate:
        cmd = f"/sdlc:approve {j} {gate}"
        return {"title": f"Waiting for your approval: {gate} gate",
                "explain": ("Read the spec and approve it, or send it back." if gate == "spec" else
                            "Review the branch or PR and approve it to merge."),
                "history": "", "actions": [cmd, f'/sdlc:rework {j} "what to change"']}
    if status == "failed":
        return {"title": "Failed", "explain": (st.failed or {}).get("reason", ""), "history": "",
                "actions": [f"/sdlc:start {j}   (retry)"]}
    return None


def job_summary(cfg, meta, detail=False):
    p = pipeline.Pipeline(cfg, meta["id"])
    st = p.state()
    total, by_stage = p.job_usage()
    events = st.events
    status = ("cancelled" if st.cancelled else "failed" if st.failed else
              "needs-human" if st.needs_human else st.stage)
    last = max((_ts(e["ts"]) for e in events if _ts(e.get("ts", ""))), default=None)
    session = meta.get("session") or {}
    stalled = bool(session and not session.get("closing") and last and
                   (dt.datetime.now(dt.timezone.utc) - last).total_seconds() > 600)
    reviewers = {}
    for e in events:
        if e["type"] == "selection" and e.get("round") in (st.round, st.spec_rev):
            reviewers.setdefault(e["reviewer"], "skipped" if not e["selected"] else "pending")
    folder = p.round_dir(st.round)
    for name in list(reviewers):
        fr, _ = runners.read_artifact(folder / f"review-{name}.md")
        if fr:
            reviewers[name] = fr.get("verdict", "pending")
    waiting = None
    for g, s in st.gates.items():
        if s == "waiting":
            waiting = g
    out = {"id": meta["id"], "title": meta["title"], "status": status, "stage": st.stage,
           "round": st.round, "tokens": total, "budget": cfg["budgets"]["per_job"] + st.budget_extra,
           "reviewers": reviewers, "gate": waiting, "stalled": stalled,
           "why": (st.needs_human or {}).get("reason") or (st.failed or {}).get("reason"),
           "detail": (st.needs_human or {}).get("detail", ""), "updated": last.isoformat() if last else None,
           "help": help_for(p, st, status, waiting, "")}
    if out["help"] and detail:
        out["help"]["findings"] = _findings(folder)
    if detail:
        out.update(source=meta.get("source"), created=meta.get("created"), owner=meta.get("owner"),
                   branch=meta["branch"], pr=meta.get("pr"), by_stage=by_stage,
                   rounds=_rounds(p, events), timeline=timeline(events, by_stage), overrides=st.overrides,
                   files=sorted(str(f.relative_to(p.dir)) for f in p.dir.rglob("*.md")
                                if "briefs" not in f.parts),
                   blocks=[e for e in events if e["type"] in ("guardrail_block", "error", "retry")][-20:],
                   selections=[e for e in events if e["type"] == "selection"],
                   applied=sorted(set(_applied(p.dir))))
    return out


def _applied(d):
    import re
    for f in d.rglob("*.md"):
        if "briefs" in f.parts:
            continue
        yield from re.findall(r"applied:\s*([LA]-[0-9a-f]+)", f.read_text(errors="replace"))


def timeline(events, by_stage):
    steps, cur = [], None
    for e in events:
        if e["type"] == "stage":
            if cur:
                cur["end"] = e["ts"]
            cur = {"stage": e["stage"], "start": e["ts"], "end": None, "agents": [], "verdicts": []}
            steps.append(cur)
        elif cur and e["type"] == "agent":
            d = e.get("data") or {}
            cur["agents"].append({"agent": e.get("agent"), "model": d.get("model"),
                                  "tokens": d.get("tokens", 0), "cache_read": d.get("cache_read", 0),
                                  "duration_s": d.get("duration_s"), "reviewer": e.get("reviewer")})
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
            "month": {"tokens": st.month_tokens(), "budget": cfg["budgets"]["per_month"]},
            "trends": trends, "repo": cfg.root.name}


def settings(cfg):
    text = (cfg.root / "SDLC.md").read_text() if (cfg.root / "SDLC.md").exists() else ""
    schema = json.loads((PLUGIN_ROOT / "templates" / "sdlc.schema.json").read_text())
    body = cfg.body_sections()
    pend = briefs.pending_sections(cfg)
    return {"schema": schema, "effective": cfg.data, "overrides": cfg.raw, "hash": configedit.file_hash(text),
            "defaults": config_mod.DEFAULTS,
            "learnings": {s: briefs.parse_entries(body.get(s, "")) for s in ("Learnings", "Answers")},
            "pending": {s: briefs.parse_entries(pend.get(s, "")) for s in learn.SECTIONS},
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
        cfg = config_mod.load(self.root)
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
        cfg = config_mod.load(self.root)
        try:
            if u.path == "/api/settings/preview":
                text = (cfg.root / "SDLC.md").read_text()
                new = configedit.apply(text, req.get("changes", {}))
                configedit._validate_text(cfg.root, new)
                return self._send(200, {"before": text, "after": new})
            if u.path == "/api/settings":
                h = configedit.save(cfg, req.get("changes", {}), req.get("hash"))
                return self._send(200, {"saved": True, "hash": h})
            if u.path == "/api/backlog":
                return self._send(200, backlog_action(cfg, req))
            if u.path == "/api/learnings":
                return self._send(200, learnings_action(cfg, req))
        except (configedit.EditError, config_mod.ConfigError, ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})


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


def learnings_action(cfg, req):
    from .cli import approve_learnings
    ids, action = req.get("ids") or [], req.get("action")
    if action == "accept":
        return approve_learnings(cfg, ids)
    if action == "reject":
        p = cfg.work_dir / "learnings-pending.md"
        secs = briefs.pending_sections(cfg)
        moved = []
        text = p.read_text()
        for sec in ("Proposals", "Learnings", "Answers"):
            for e in briefs.parse_entries(secs.get(sec, "")):
                if e["id"] in ids:
                    text = text.replace(e["line"] + "\n", "")
                    moved.append(e["line"])
        text = text.replace("## Rejected\n", "## Rejected\n" + "".join(l + "\n" for l in moved), 1)
        p.write_text(text)
        return {"rejected": len(moved)}
    raise ValueError("action must be accept or reject")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def serve(cfg, port=0, open_browser=False):
    token = secrets.token_urlsafe(18)
    srv = Server(("127.0.0.1", port), Handler)
    Handler.token, Handler.root = token, cfg.root
    Handler.origin = f"http://127.0.0.1:{srv.server_address[1]}"
    url = f"{Handler.origin}/?token={token}"
    (cfg.work_dir / "dashboard.url").write_text(url + "\n")
    print(json.dumps({"url": url}), flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    srv.serve_forever()
