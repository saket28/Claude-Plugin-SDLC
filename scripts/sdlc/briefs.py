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


def pending_sections(cfg):
    p = cfg.work_dir / "learnings-pending.md"
    if not p.exists():
        return {}
    sections, cur, buf = {}, None, []
    for line in p.read_text().splitlines():
        if line.startswith("## "):
            if cur:
                sections[cur] = "\n".join(buf)
            cur, buf = line[3:].strip(), []
        elif cur:
            buf.append(line)
    if cur:
        sections[cur] = "\n".join(buf)
    return sections


def role_learnings(cfg, role):
    body = cfg.body_sections()
    pend = pending_sections(cfg)
    entries = parse_entries(body.get("Learnings", "")) + parse_entries(pend.get("Learnings", ""))
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
    pend = pending_sections(cfg)
    return [e["line"] for e in parse_entries(body.get("Answers", "")) + parse_entries(pend.get("Answers", ""))]


def write(cfg, path, meta, role, sections):
    """sections: list of (title, text) in order, appended after the stable part."""
    body = cfg.body_sections()
    parts = ["---", yamlish.dumps(meta), "---", ""]
    rules = body.get("Pipeline rules", "").strip()
    if rules:
        parts += ["## Pipeline rules (from SDLC.md)", rules, ""]
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
