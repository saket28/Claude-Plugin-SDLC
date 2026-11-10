"""Agent briefs (INSTRUCTIONS.md §4).

Order is cache-friendly: stable content first (pipeline rules, Answers, role
Learnings, spec), then what changed this round. Settings frontmatter is left out.
"""

import re
from pathlib import Path

from . import yamlish

ENTRY = re.compile(r"^\s*-\s*\[(?P<id>[LA]-[0-9a-f]+)\]\s*(?P<rest>.*)$")


def parse_entries(text):
    """Learnings look like '- [L-9c1e] builder, tester · rule (evidence)'."""
    out = []
    for line in (text or "").splitlines():
        m = ENTRY.match(line)
        if not m:
            continue
        rest = m.group("rest")
        roles, _, rule = rest.partition(" · ")
        if not rule:
            roles, rule = "all", rest
        out.append({"id": m.group("id"), "roles": [r.strip() for r in roles.split(",") if r.strip()],
                    "text": rule.strip(), "line": line.strip()})
    return out


def role_learnings(cfg, role):
    body = cfg.body_sections()
    entries = parse_entries(body.get("Learnings", ""))
    seen, out = set(), []
    for e in entries:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        if role in e["roles"] or "all" in e["roles"]:
            out.append(e["line"])
    return out


def answers(cfg):
    body = cfg.body_sections()
    return [e["line"] for e in parse_entries(body.get("Answers", ""))]


STANCE = {
    1: "Follow your instructions. If one clearly contradicts the spec, the code or the repo's rules, "
       "say so instead of complying silently.",
    2: "Before acting, check your instructions against the spec, the code and the repo's rules. "
       "Flag contradictions and gaps, and say what you did about each.",
    3: "Do not take instructions, specs or earlier findings on trust. Look for what is wrong, "
       "missing or under-specified, and push back on weak points. Every objection must cite a file, "
       "line or spec clause and say what you would change.",
    4: "Be sceptical by default. Assume the instructions, the spec and earlier work contain "
       "mistakes until you have checked. Push back early and say what would change your mind. "
       "Cite evidence for every objection.",
    5: "Challenge every assumption, including the task itself. If the approach is wrong, say so "
       "and propose a better one before doing the work. Cite evidence for every objection.",
}
STANCE_TAIL = ("Use the channel your role already has (a dispute, a question, a finding, a spec gap) "
               "for each objection. Do not invent objections: with no evidence, do the work. "
               "If you disagree, still do the part you can; block only on a real conflict.")


def stance(level):
    return f"{STANCE[level]} {STANCE_TAIL}" if level else ""


def write(cfg, path, meta, role, sections, principles=()):
    """sections: list of (title, text) in order, appended after the stable part.
    principles: [{path, summary}] the agent should know about (see principles.py).
    meta may carry `critical_thinking` (a reviewer's own level); it is not written to the brief."""
    body = cfg.body_sections()
    meta = dict(meta)
    level = meta.pop("critical_thinking", None)      # a reviewer's own level beats its stage's
    if level is None:
        level = cfg.critical_thinking(role)
    parts = ["---", yamlish.dumps(meta), "---", ""]
    if level:
        parts += [f"## Working stance (critical thinking {level} of 5)", stance(level), ""]
    rules = body.get("Pipeline rules", "").strip()
    if rules:
        parts += ["## Pipeline rules (from SDLC.md)", rules, ""]
    if principles:
        parts += ["## Principles for this repo",
                  "How this repo does things. Read the ones relevant to your task before you start, and "
                  "name the one behind any objection you raise.",
                  *[f"- `{p['path']}`" + (f": {p['summary']}" if p.get("summary") else "") for p in principles], ""]
    ans = answers(cfg)
    if ans:
        parts += ["## Answers already given (do not ask these again)", *ans, ""]
    learn = role_learnings(cfg, role)
    if learn:
        parts += [f"## Learnings for the {role}", "Cite the IDs you rely on as `applied: L-xxxx`.",
                  *learn, ""]
    for title, text in sections:
        if text and str(text).strip():
            parts += [f"## {title}", str(text).rstrip(), ""]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(parts))
    return str(path)


def prompt_line(job_id, stage, rnd, brief_path):
    return (f"SDLC-JOB: {job_id} STAGE: {stage} ROUND: {rnd}. Read {brief_path} first and follow it. "
            f"When done, reply with one line: the path of the file you wrote.")
