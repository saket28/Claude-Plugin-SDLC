"""Retro and learnings (INSTRUCTIONS.md §7).

collect -> phrase (one cheap headless call, skipped when there's nothing new)
-> learnings-pending.md -> SDLC.md (local host: commit; PR host: rolling PR, v1.1).
IDs are a hash of the text at creation and never change afterwards.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import briefs, runners
from .config import git
from .store import Store, locked, write_atomic

SECTIONS = ("Learnings", "Answers", "Proposals", "Rejected")


def entry_id(kind, text):
    return f"{'A' if kind == 'answer' else 'L'}-{hashlib.sha1(text.strip().lower().encode()).hexdigest()[:4]}"


def _norm(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def collect(cfg, job_id):
    st = Store(cfg)
    d = st.job_dir(job_id)
    cands = []
    for f in sorted(d.rglob("*.md")):
        if f.parent.name == "briefs":
            continue
        m = re.search(r"^##\s+Learning candidates\s*$(.*?)(?=^##\s|\Z)", f.read_text(), flags=re.M | re.S)
        if m:
            for line in m.group(1).splitlines():
                line = line.strip().lstrip("-*0123456789. ").strip()
                if line:
                    cands.append({"source": str(f.relative_to(d)), "text": line})
    for e in st.events(job_id):
        if e["type"] == "learning_candidate":
            cands.append({"source": e.get("source", "event"), "text": e["text"]})
        elif e["type"] == "question":
            cands.append({"source": "answered question", "text": e.get("answers", ""), "answer": True})
        elif e["type"] in ("failed", "guardrail_block") or (e["type"] == "budget" and e.get("level") == "exceeded"):
            cands.append({"source": e["type"], "text": json.dumps({k: v for k, v in e.items() if k != "ts"})})
        elif e["type"] == "round" and e.get("auto"):
            cands.append({"source": "rework round", "text": e.get("reason", "")})
    return cands


def existing_texts(cfg):
    body = cfg.body_sections()
    pend = briefs.pending_sections(cfg)
    out = set()
    for sec in SECTIONS:
        for e in briefs.parse_entries(body.get(sec, "")) + briefs.parse_entries(pend.get(sec, "")):
            out.add(e["id"])
            out.add(_norm(e["text"]))
    return out


def phrase(cfg, job_id, cands):
    prompt = (
        "You turn raw notes from one software job into durable guidance for future AI agents.\n"
        "Return only JSON: {\"entries\": [{\"kind\": \"learning\"|\"answer\"|\"proposal\", "
        "\"roles\": [\"builder\"|\"tester\"|\"spec-writer\"|\"reviewer\"|\"all\"], \"text\": \"...\", "
        "\"evidence\": \"short plain-text evidence\"}]}\n"
        "Rules: keep only things that will help a FUTURE job (skip one-offs). Write each as an "
        "instruction an agent can follow. 'answer' = a question a human answered, phrased as Q? A. "
        "'proposal' = a change to settings, REVIEW.md or CLAUDE.md (always needs approval; include the "
        "exact change and any command in full). Return {\"entries\": []} if nothing qualifies.\n\n"
        f"Job {job_id} notes:\n" + json.dumps(cands, indent=1)[:20000])
    text, use = runners.headless(prompt, cfg.root, model=cfg["models"].get("judge"))
    Store(cfg).record_usage(job_id, f"retro-{job_id}", use.get("tokens", 0))
    Store(cfg).event(job_id, "agent", key=f"retro-{job_id}", agent="headless:retro", stage="retro", data=use)
    m = re.search(r"\{.*\}", text, flags=re.S)
    return (json.loads(m.group(0)) if m else {}).get("entries", [])


def add_pending(cfg, entries, job_id):
    """Append new entries to learnings-pending.md. Returns the lines added."""
    p = cfg.work_dir / "learnings-pending.md"
    seen = existing_texts(cfg)
    added = []
    with locked(p):
        secs = briefs.pending_sections(cfg) if p.exists() else {}
        secs = {s: secs.get(s, "").rstrip() for s in SECTIONS}
        for e in entries:
            text = (e.get("text") or "").strip()
            if not text or _norm(text) in seen:
                continue
            kind = e.get("kind", "learning")
            eid = entry_id(kind, text)
            if eid in seen:
                continue
            roles = ", ".join(e.get("roles") or ["all"])
            ev = e.get("evidence") or ""
            line = f"- [{eid}] {roles} · {text} (job-{job_id}{', ' + ev if ev else ''})"
            sec = {"answer": "Answers", "proposal": "Proposals"}.get(kind, "Learnings")
            if sec == "Learnings" and cfg["learning"] == "propose":
                sec = "Proposals"
            secs[sec] = (secs[sec] + "\n" + line).strip()
            seen.update({eid, _norm(text)})
            added.append(line)
        out = ["# Pending learnings", "",
               "Applied to agent briefs immediately. Moved into SDLC.md by the retro (§7).", ""]
        for s in SECTIONS:
            out += [f"## {s}", secs[s], ""]
        write_atomic(p, "\n".join(out))
    return added


def retro(cfg, job_id):
    st = Store(cfg)
    if any(e["type"] == "retro" for e in st.events(job_id)):
        return {"skipped": "retro already ran"}
    if cfg["learning"] == "off":
        st.event(job_id, "retro", added=0)
        return {"added": []}
    cands = collect(cfg, job_id)
    added = []
    if cands:
        try:
            entries = phrase(cfg, job_id, cands)
        except Exception as e:  # noqa: BLE001
            st.event(job_id, "error", stage="retro", detail=str(e))
            return {"error": str(e)}
        added = add_pending(cfg, entries, job_id)
    (st.job_dir(job_id) / "retro.md").write_text(
        "---\nadded: %d\n---\n\n# Retro\n\n%s\n" % (len(added), "\n".join(added) or "Nothing new to learn."))
    st.event(job_id, "retro", added=len(added))
    promote(cfg)
    return {"added": added}


def _insert_entries(text, moves):
    for sec, lines in moves.items():
        if not lines:
            continue
        m = re.search(rf"^## {sec}\s*$", text, flags=re.M)
        block = "\n".join(lines)
        if m:
            nxt = re.search(r"^## ", text[m.end():], flags=re.M)
            at = m.end() + (nxt.start() if nxt else len(text) - m.end())
            text = text[:at].rstrip("\n") + "\n" + block + "\n\n" + text[at:].lstrip("\n")
        else:
            text = text.rstrip("\n") + f"\n\n## {sec}\n{block}\n"
    return text


def _pending_moves(cfg):
    pend = briefs.pending_sections(cfg)
    return {s: [e["line"] for e in briefs.parse_entries(pend.get(s, ""))] for s in ("Learnings", "Answers")}


def promote(cfg):
    """Move pending Learnings/Answers towards SDLC.md (§7)."""
    if cfg["host"]["adapter"] == "local":
        return promote_local(cfg)
    return promote_pr(cfg)


def promote_pr(cfg):
    """One rolling PR per person on sdlc/learnings-<user>, rebuilt from base + pending each time."""
    import os
    from . import adapters
    moves = _pending_moves(cfg)
    if not any(moves.values()):
        return {"moved": 0}
    root, base = cfg.root, cfg["base_branch"]
    user = re.sub(r"[^a-z0-9-]", "-", (os.environ.get("USER") or "me").lower())
    branch = f"{cfg['branch_prefix']}learnings-{user}"
    wt = cfg.worktree_dir / f"learnings-{user}"
    subprocess.run(["git", "fetch", "origin", base], cwd=root, capture_output=True)
    start = f"origin/{base}" if git("rev-parse", "--verify", "--quiet", f"origin/{base}", cwd=root, check=False) else base
    if not wt.exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "-B", branch, str(wt), start, cwd=root)
    else:
        git("checkout", "-B", branch, start, cwd=wt)
    f = wt / "SDLC.md"
    f.write_text(_insert_entries(f.read_text() if f.exists() else "", moves))
    git("add", "SDLC.md", cwd=wt)
    if not git("diff", "--cached", "--name-only", cwd=wt, check=False):
        return {"moved": 0, "note": "already in SDLC.md"}
    git("commit", "-m", "sdlc: learnings from recent jobs", cwd=wt)
    subprocess.run(["git", "push", "--force-with-lease", "-u", "origin", branch], cwd=wt,
                   capture_output=True, text=True)
    body = wt.parent / f"learnings-{user}-pr.md"
    body.write_text("Learnings and answers collected by the sdlc retro. Review them like code: "
                    "remove any line you disagree with before merging; removed lines won't be "
                    "proposed again.\n\n" + "\n".join(sum(moves.values(), [])) + "\n")
    try:
        r = adapters.host(cfg).call("open_pr", branch=branch, base=base, title="sdlc: learnings",
                                    body_file=str(body), worktree=str(wt))
    except adapters.AdapterError as e:
        return {"error": str(e)}
    return {"pr": r.get("pr"), "entries": sum(len(v) for v in moves.values())}


def sync_pending(cfg):
    """After a learnings PR is merged or closed: drop entries now in SDLC.md, and mark the
    rest rejected so they aren't proposed again. Called when a job starts."""
    if cfg["host"]["adapter"] == "local":
        return {}
    import os
    user = re.sub(r"[^a-z0-9-]", "-", (os.environ.get("USER") or "me").lower())
    branch = f"{cfg['branch_prefix']}learnings-{user}"
    r = subprocess.run(["gh", "pr", "view", branch, "--json", "state"], cwd=cfg.root,
                       capture_output=True, text=True)
    state = json.loads(r.stdout or "{}").get("state") if r.returncode == 0 else None
    if state not in ("MERGED", "CLOSED"):
        return {}
    subprocess.run(["git", "fetch", "origin", cfg["base_branch"]], cwd=cfg.root, capture_output=True)
    merged = git("show", f"origin/{cfg['base_branch']}:SDLC.md", cwd=cfg.root, check=False)
    ids = set(re.findall(r"\[([LA]-[0-9a-f]+)\]", merged))
    p = cfg.work_dir / "learnings-pending.md"
    with locked(p):
        secs = briefs.pending_sections(cfg)
        rejected = secs.get("Rejected", "").rstrip()
        keep = {s: [] for s in SECTIONS}
        for sec in ("Learnings", "Answers"):
            for e in briefs.parse_entries(secs.get(sec, "")):
                if e["id"] not in ids:
                    rejected += "\n" + e["line"]
        keep["Proposals"] = [secs.get("Proposals", "").strip()]
        out = ["# Pending learnings", ""]
        for sec in SECTIONS:
            content = rejected.strip() if sec == "Rejected" else "\n".join(x for x in keep[sec] if x)
            out += [f"## {sec}", content, ""]
        write_atomic(p, "\n".join(out))
    return {"state": state}


def promote_local(cfg):
    """Commit SDLC.md alone, only when the checkout is on base_branch and SDLC.md is clean."""
    root, base = cfg.root, cfg["base_branch"]
    if git("rev-parse", "--abbrev-ref", "HEAD", cwd=root, check=False) != base:
        return {"skipped": f"checkout isn't on {base}"}
    if git("status", "--porcelain", "--", "SDLC.md", cwd=root, check=False):
        return {"skipped": "SDLC.md has uncommitted edits"}
    moves = _pending_moves(cfg)
    if not any(moves.values()):
        return {"moved": 0}
    path = root / "SDLC.md"
    path.write_text(_insert_entries(path.read_text() if path.exists() else "", moves))
    git("add", "SDLC.md", cwd=root)
    subprocess.run(["git", "commit", "-m", "sdlc: learnings from the retro", "--", "SDLC.md"],
                   cwd=root, capture_output=True, text=True)
    # remove what moved from the pending file
    p = cfg.work_dir / "learnings-pending.md"
    with locked(p):
        keep = [l for l in p.read_text().splitlines()
                if not any(l.strip() == x for ls in moves.values() for x in ls)]
        write_atomic(p, "\n".join(keep) + "\n")
    return {"moved": sum(len(v) for v in moves.values())}
