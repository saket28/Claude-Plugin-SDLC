"""The conversation between a job's agents, kept by the director.

Agents write comments in their own report files, under `## Comments`, one per line:

    - new -> builder, feedback, blocking: Batch processors are never flushed
    - I1 -> code-reviewer, pushback: AC-9 asks for a slug-free reason
    - I1 -> builder, accept

`<id|new> -> <to>, <type>[, <severity>]: <text>`. The sender is the file's author. The director (this
module, plain code) is the only writer of `threads.jsonl`: it numbers new issues (I1, I2 ...), checks each
`to`, applies the rules below and leaves a decision line. `conversation.md` is the readable copy.

Rules: the raiser's `accept` closes an issue; a pushback on a nit closes it ("not pursued"); a spec
`ruling: keep` closes it and `ruling: change` goes to the human; a checker that re-reviews and says nothing
about an issue addressed to it closes it as "no reply". Issues belong to a round (a full review pass); the
pipeline gives each round up to `max_exchanges` exchanges to resolve them, then escalates what is left.
Humans answer through `override` and `rework`; a rework is the final exchange and closes the issue.
"""

import json
import re

from . import runners

TYPES = ("feedback", "pushback", "fixed", "accept", "ruling")
COMMENT = re.compile(r"^\s*[-*]\s+\**(new|I\d+)\**\s*(?:->|→)\s*([\w-]+)\s*,\s*(" + "|".join(TYPES) +
                     r")\s*(?:,\s*(blocking|nit))?\s*(?::\s*(.*))?$", re.I)
SOURCES = {"build.md": "builder", "test.md": "tester", "spec-ruling.md": "spec-writer"}


def section(md, title):
    m = re.search(rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", md or "", flags=re.M | re.S)
    return m.group(1).strip() if m else ""


def trim(md, titles):
    """Only the named sections of a report, each under its heading."""
    return "\n\n".join(f"## {t}\n{s}" for t in titles if (s := section(md, t)))


def parse_comments(body):
    """The comments in a report's `## Comments`: [{id, to, type, severity, text, detail}]."""
    out, cur = [], None
    for line in section(body, "Comments").splitlines():
        m = COMMENT.match(line)
        if m:
            cur = {"id": m.group(1) if m.group(1).lower() == "new" else m.group(1).upper(),
                   "to": m.group(2).lower(), "type": m.group(3).lower(),
                   "severity": (m.group(4) or "").lower(), "text": (m.group(5) or "").strip(), "detail": ""}
            out.append(cur)
        elif cur and line.startswith((" ", "\t")) and line.strip():
            cur["detail"] = (cur["detail"] + " " + line.strip()).strip()[:600]
    return out


# ------------------------------------------------------------------ store --

def _path(job_dir):
    return job_dir / "threads.jsonl"


def _records(job_dir):
    p = _path(job_dir)
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def _append(job_dir, *recs):
    with open(_path(job_dir), "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def load(job_dir):
    """Replay the log: {id: issue}, each issue with its ordered `lines` (comments and decisions)."""
    issues, done = {}, set()
    for r in _records(job_dir):
        k = r["kind"]
        if k == "src":
            done.add(r["src"])
        elif k == "issue":
            issues[r["id"]] = {"id": r["id"], "title": r["title"], "severity": r["severity"], "by": r["by"],
                               "round": r.get("round"), "r": r.get("r", 1), "state": "open", "flag": "", "lines": []}
        elif r.get("id") in issues:
            i = issues[r["id"]]
            if k == "comment":
                i["lines"].append({k2: r.get(k2, "") for k2 in ("from", "to", "type", "text", "detail")})
            elif k == "severity":
                i["severity"] = r["severity"]
                i["lines"].append({"from": "director", "to": "", "type": "decision",
                                   "text": f"severity changed to {r['severity']} by {r['by']}", "detail": ""})
            elif k == "decision":
                i["state"], i["flag"] = r["state"], r.get("flag", "")
                if not r.get("quiet"):
                    i["lines"].append({"from": "director", "to": "", "type": "decision", "text": r["text"], "detail": ""})
    return issues, done


def _last_comment(issue):
    return next((x for x in reversed(issue["lines"]) if x["type"] != "decision"), None)


def _awaiting(issue):
    c = _last_comment(issue)
    return c["to"] if c else None


# --------------------------------------------------------------- director --

class _Log:
    """Appends to the log while keeping an in-memory copy in step, so rules see their own writes."""

    def __init__(self, job_dir):
        self.dir = job_dir
        self.issues, self.done = load(job_dir)

    def put(self, *recs):
        _append(self.dir, *recs)
        self.issues, self.done = load(self.dir)

    def decide(self, i, state, text, flag="", help_=False):
        recs = [{"kind": "decision", "id": i, "state": state, "text": text, "flag": flag, "quiet": help_}]
        if help_:
            recs.insert(0, {"kind": "comment", "id": i, "from": "director", "to": "human", "type": "help",
                            "text": text, "detail": ""})
        self.put(*recs)


def _clip(s, n=240):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def ingest(job_dir, rd, rnd, known_to, r=1, exchange=False, advisory=()):
    """Read a cycle's reports not yet seen, in a fixed order (builder, tester, reviewers, spec ruling),
    and apply the rules. `known_to` is the set of names a comment may be addressed to; `r` the review
    round the cycle belongs to; in an `exchange` a built-in or command reviewer, which cannot answer a
    thread, answers with its verdict; an `advisory` checker's issues are always nits."""
    log = _Log(job_dir)
    files = [rd / "build.md", rd / "test.md", *sorted(rd.glob("review-*.md")), rd / "spec-ruling.md"]
    for f in files:
        src = f"{rd.name}/{f.name}"
        if not f.exists() or src in log.done:
            continue
        frm = SOURCES.get(f.name) or f"{f.stem[len('review-'):]}-reviewer"
        front, body = runners.read_artifact(f)
        comments = parse_comments(body or "")
        checker = frm not in ("builder", "spec-writer")
        failing = str((front or {}).get("verdict", "")).upper() in ("FAIL", "CHANGES")
        if checker and failing and not comments:
            comments = [{"id": "new", "to": "builder", "type": "feedback", "severity": "blocking",
                         "text": f"{f.stem} says {(front or {}).get('verdict')} and gave no comments (see {src})",
                         "detail": ""}]
        if checker and (front or {}).get("engine") and exchange:
            # a built-in or command reviewer re-runs fresh and cannot answer a thread: its verdict is the answer
            ok = str((front or {}).get("verdict", "")).upper() == "PASS"
            comments = [{"id": i["id"], "to": "builder", "type": "accept" if ok else "pushback", "severity": "",
                         "text": "" if ok else f"still reported when run again (see {src})", "detail": ""}
                        for i in log.issues.values() if i["state"] == "open" and _awaiting(i) == frm]
        touched = set()
        for c in comments:
            if frm in advisory and c["id"] == "new":
                c = {**c, "severity": "nit"}
            _apply(log, c, frm, rnd, known_to, touched, r)
        if checker:
            for i in list(log.issues.values()):
                if i["state"] == "open" and i["id"] not in touched and _awaiting(i) == frm:
                    log.decide(i["id"], "closed", f"closed, no reply from {frm}", flag="no_reply")
        if f.name == "spec-ruling.md":
            for i in list(log.issues.values()):
                if i["state"] == "open" and _awaiting(i) == "spec-writer":
                    log.decide(i["id"], "human", "the spec-writer gave no ruling", help_=True)
        log.put({"kind": "src", "src": src})
    return log.issues


def _apply(log, c, frm, rnd, known_to, touched, r):
    default_to = "builder" if frm != "builder" else None
    if c["id"] == "new":
        if c["type"] != "feedback" or frm == "builder":
            return
        n = len(log.issues) + 1
        iid = f"I{n}"
        sev = c["severity"] if c["severity"] in ("blocking", "nit") else "blocking"
        to = c["to"] if c["to"] in known_to else "builder"
        title = re.sub(r"^`[^`]+`\s*", "", c["text"]) or c["text"]       # the summary, without the leading `path:line`
        log.put({"kind": "issue", "id": iid, "title": _clip(title, 100), "severity": sev, "by": frm, "round": rnd, "r": r},
                {"kind": "comment", "id": iid, "from": frm, "to": to, "type": "feedback", "text": _clip(c["text"], 400),
                 "detail": c["detail"]})
        touched.add(iid)
        return
    i = log.issues.get(c["id"])
    if not i or i["state"] in ("closed", "human"):
        return
    touched.add(i["id"])
    to = c["to"] if c["to"] in known_to else (i["by"] if frm == "builder" else (default_to or "builder"))
    if c["type"] == "ruling":
        to = "director"
    log.put({"kind": "comment", "id": i["id"], "from": frm, "to": to, "type": c["type"],
             "text": _clip(c["text"], 400), "detail": c["detail"]})
    if c["severity"] and c["severity"] != i["severity"] and frm == i["by"]:
        log.put({"kind": "severity", "id": i["id"], "severity": c["severity"], "by": frm})
    i = log.issues[i["id"]]
    if c["type"] == "accept" and frm == i["by"]:
        log.decide(i["id"], "closed", f"closed, accepted by {frm}")
    elif c["type"] == "pushback":
        if i["severity"] == "nit":
            log.decide(i["id"], "closed", "closed, nit not pursued", flag="nit")
    elif c["type"] == "ruling":
        if c["text"].lower().startswith("change"):
            log.decide(i["id"], "human", "spec-writer says the spec should change", help_=True)
        else:
            log.decide(i["id"], "closed", "closed, spec upheld")


def escalate(job_dir, why):
    """Rounds are used up: every unresolved blocking issue goes to the human."""
    log = _Log(job_dir)
    for i in list(log.issues.values()):
        if i["state"] == "open" and i["severity"] == "blocking":
            log.decide(i["id"], "human", why, help_=True)


def human_decision(job_dir, kind, text="", by="human"):   # `by` is who, the comment is always from "human"
    """The human's answer to every issue waiting on them: `override` (accept the work as it is) or
    `rework` (the checker is right: send the issue back to the builder with this ruling)."""
    log = _Log(job_dir)
    for i in list(log.issues.values()):
        if i["state"] != "human":
            continue
        log.put({"kind": "comment", "id": i["id"], "from": "human", "to": "director", "type": kind,
                 "text": _clip(text or kind, 400), "detail": ""})
        if kind == "override":
            log.decide(i["id"], "closed", "closed, human overrode")
        else:
            log.decide(i["id"], "open", "reopened by human", flag="ruled")
            log.put({"kind": "comment", "id": i["id"], "from": "director", "to": "builder", "type": "feedback",
                     "text": _clip(text, 400), "detail": ""})
    write_view(job_dir)


# ------------------------------------------------------------------- views --

def awaiting_human(job_dir):
    return [i for i in load(job_dir)[0].values() if i["state"] == "human"]


def awaiting(job_dir, who):
    return [i for i in load(job_dir)[0].values() if i["state"] == "open" and _awaiting(i) == who]


def unresolved(job_dir, by, nits=False):
    """Issues a checker raised that still hold the job back: blocking ones (and nits, if they block)
    that are open or with the human."""
    return [i for i in load(job_dir)[0].values()
            if i["by"] == by and (nits or i["severity"] == "blocking") and i["state"] in ("open", "human")]


def raised_by(job_dir, by):
    return [i for i in load(job_dir)[0].values() if i["by"] == by]


def open_blocking(job_dir, r, nits=False):
    """The issues of review round `r` still being worked on (blocking ones, and nits if they block)."""
    return [i for i in load(job_dir)[0].values()
            if i["r"] == r and i["state"] == "open" and (nits or i["severity"] == "blocking")]


def raised_blocking(job_dir, r):
    return any(i["r"] == r and i["severity"] == "blocking" for i in load(job_dir)[0].values())


def close_ruled(job_dir):
    """The human's ruling was the final exchange: close every issue it reopened."""
    log = _Log(job_dir)
    for i in list(log.issues.values()):
        if i["state"] == "open" and i["flag"] == "ruled":
            log.decide(i["id"], "closed", "closed, human ruling applied")
    write_view(job_dir)


def human_detail(job_dir):
    out = []
    for i in awaiting_human(job_dir):
        d = next((x["text"] for x in reversed(i["lines"]) if x["type"] in ("help", "decision")), "")
        out.append(f"{i['id']} {i['title']} ({d})")
    return " | ".join(out)[:900]


def brief(job_dir, who, nits=False):
    """The open threads addressed to one agent, as a compact text: the whole exchange per issue."""
    out = []
    for i in awaiting(job_dir, who):
        if i["severity"] == "nit" and not nits:
            continue
        out.append(f"{i['id']} [{i['severity']}] {i['title']}")
        for x in i["lines"]:
            if x["type"] == "decision":
                out.append(f"  director: {x['text']}")
            else:
                out.append(f"  {x['from']} -> {x['to']} ({x['type']}): {x['text']}"
                           + (f" ({x['detail']})" if x["detail"] else ""))
    return "\n".join(out)


def threads(job_dir):
    """Every issue with its exchange, for the dashboard: unresolved first."""
    order = {"human": 0, "open": 1, "noted": 2, "closed": 3}
    out = []
    for i in load(job_dir)[0].values():
        view = dict(i)
        if i["state"] == "open" and i["severity"] == "nit":
            view["state"] = "noted"
        out.append(view)
    return sorted(out, key=lambda i: (i["r"], order[i["state"]], i["severity"] != "blocking", int(i["id"][1:])))


def render(job_dir):
    """The readable copy: issues grouped by round, one block per issue."""
    out, group = [], None
    for i in threads(job_dir):
        if i["r"] != group:
            group = i["r"]
            out += [f"r{group}", ""]
        out.append(f"  [{'x' if i['state'] == 'closed' else ' '}] {i['id']} - {i['title']} - {i['severity']}")
        for x in i["lines"]:
            if x["type"] == "decision":
                out.append(f"      - director: {x['text']}")
            else:
                out.append(f"      - {x['from']} - {x['to']} - {x['type']}: {x['text']}")
        out.append("")
    return "\n".join(out).rstrip() + "\n" if out else ""


def write_view(job_dir):
    text = render(job_dir)
    if text:
        (job_dir / "conversation.md").write_text(text)
