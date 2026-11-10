"""Principles that live in files: `.claude/principles/<name>.md`.

A principle is how this repo does things (architecture, design system, personas ...). Agent
instructions say why, the task says what, principles say how. The frontmatter holds `summary`,
`applies_to` (agent roles; empty = all) and `when` (a rule like reviewers use); the body is the
principle. SDLC.md lists the paths in `global.principle.list`. Briefs list the ones that apply with
their summaries, and the agent reads the relevant ones. A file with no text outside HTML comments
is a placeholder and is never listed.
"""

import re
from pathlib import Path

from . import schema, yamlish

FOLDER = ".claude/principles"
FRONT_KEYS = ("summary", "applies_to", "when")
ROLES = schema.roles()
STARTERS = ("architecture", "design-system", "personas", "security", "testing")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent


def path_for(name):
    return f"{FOLDER}/{name}.md"


def _problem(front):
    """What is wrong with a principle's frontmatter, or None."""
    from . import rules
    roles = front.get("applies_to")
    if roles is not None and (not isinstance(roles, list) or any(r not in ROLES for r in roles)):
        return f"applies_to must be a list of: {', '.join(ROLES)}"
    if front.get("when"):
        try:
            rules.parse(front["when"])
        except rules.RuleError as e:
            return f"when: {e}"
    return None


def read(root, rel):
    """(name, spec) of one principle file. `spec` has summary, applies_to, when, file and body."""
    front, body = yamlish.parse_frontmatter((Path(root) / rel).read_text())
    spec = {k: v for k, v in front.items() if k in FRONT_KEYS}
    spec["file"], spec["body"] = rel, body.strip("\n")
    bad = _problem(spec)
    if bad:
        raise yamlish.YamlError(bad)
    return Path(rel).stem, spec


def load_list(root, paths):
    """Principles named by `global.principle.list` -> ({name: spec}, warnings, problems). A missing or
    unreadable file is a warning, not a crash, so the dashboard still opens."""
    out, warnings, problems = {}, [], []

    def bad(rel, msg):
        warnings.append(f"{rel}: {msg}")
        problems.append({"name": Path(str(rel)).stem, "file": str(rel), "error": msg})

    for rel in paths or []:
        if not isinstance(rel, str):
            bad(rel, "not a path")
        elif not (Path(root) / rel).exists():
            bad(rel, "file not found")
        else:
            try:
                name, spec = read(root, rel)
            except yamlish.YamlError as e:
                bad(rel, str(e))
                continue
            if name in out:
                bad(rel, f"listed twice (same name as {out[name]['file']})")
                continue
            out[name] = spec
    return out, warnings, problems


def has_content(spec):
    """Real text outside HTML comments; the starter files are comments only until a person writes."""
    return bool(re.sub(r"<!--.*?-->", "", spec.get("body") or "", flags=re.S).strip())


def applicable(specs, role, ctx=None):
    """The principles an agent of `role` should be pointed at. `ctx` (risk, touches_ui, paths) lets
    `when` rules filter; without it (the spec doesn't exist yet) every role match is kept."""
    from . import rules
    out = []
    for spec in specs.values():
        roles = spec.get("applies_to") or list(ROLES)
        if not has_content(spec) or role not in roles:
            continue
        if ctx is not None and spec.get("when") and not rules.evaluate(spec["when"], ctx):
            continue
        out.append(spec)
    return out


# ---------------------------------------------------------------- editing --

def template(name):
    """(front, body) of a shipped starter, or an empty one for a new principle."""
    p = PLUGIN_ROOT / "templates" / "principles" / f"{name}.md"
    if name in STARTERS and p.exists():
        front, body = yamlish.parse_frontmatter(p.read_text())
        return front, body.strip("\n")
    return {}, ""


def render(front, body):
    keys = [k for k in FRONT_KEYS if front.get(k) not in (None, "", [])]
    head = "\n".join(f"{k}: {yamlish.dump_inline(front[k])}" for k in keys)
    return (f"---\n{head}\n---\n" if head else "") + (body.strip("\n") + "\n" if body.strip() else "")


def ensure_defaults(root):
    """Write the starter principle files that are missing. Returns all their paths."""
    root, paths = Path(root), []
    for name in STARTERS:
        rel = path_for(name)
        if not (root / rel).exists():
            front, body = template(name)
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(render(front, body))
        paths.append(rel)
    return paths


def _listed(root):
    text = (Path(root) / "SDLC.md").read_text() if (Path(root) / "SDLC.md").exists() else "---\n---\n"
    raw, _ = yamlish.parse_frontmatter(text)
    return text, list((((raw.get("global") or {}).get("principle")) or {}).get("list") or [])


def _write_list(root, paths):
    from . import configedit
    text, _ = _listed(root)
    new = configedit.apply(text, {"global.principle.list": paths})
    configedit._validate_text(Path(root), new)
    p = Path(root) / "SDLC.md"
    tmp = p.with_suffix(".md.tmp")
    tmp.write_text(new)
    tmp.replace(p)


def save(cfg, name, front, body=""):
    """Create or update one principle file and make sure SDLC.md lists it."""
    root = Path(cfg.root)
    _, listed = _listed(root)
    existing = (cfg.v("global.principle.list").get(name) or {}).get("file")
    if not existing and not NAME_RE.match(name or ""):
        raise ValueError("name: letters, digits, - and _ only")
    rel = existing or path_for(name)
    if not existing and (root / rel).exists():
        raise ValueError(f"{rel} already exists")
    front = {k: v for k, v in (front or {}).items() if k in FRONT_KEYS and v not in (None, "", [])}
    if "summary" in front:
        front["summary"] = " ".join(str(front["summary"]).split())
    bad = _problem(front)
    if bad:
        raise ValueError(bad)
    if not (body or "").strip():
        raise ValueError("the principle can't be empty")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(render(front, body))
    if rel not in listed:
        _write_list(root, listed + [rel])
    return rel


def delete(cfg, name):
    """Stop using a principle: drop it from SDLC.md's list. Its file stays on disk."""
    _, listed = _listed(cfg.root)
    rel = (cfg.v("global.principle.list").get(name) or {}).get("file")
    if rel not in listed:
        raise ValueError(f"no principle named {name}")
    _write_list(cfg.root, [p for p in listed if p != rel])
    return rel


def add_starters(cfg):
    """List the starter files (creating the missing ones). Returns the paths newly listed."""
    paths = ensure_defaults(cfg.root)
    _, listed = _listed(cfg.root)
    new = [p for p in paths if p not in listed]
    if new:
        _write_list(cfg.root, listed + new)
    return new
