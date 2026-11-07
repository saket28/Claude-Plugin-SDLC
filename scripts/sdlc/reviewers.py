"""Reviewers that live in files: `.claude/reviewers/<name>.md`.

The frontmatter holds the reviewer's settings (at, when, condition, blocking, model, and one of
`builtin`, `agent` or `run`); the markdown body is its instructions. SDLC.md only lists the paths.
A file with none of builtin/agent/run is an instructions reviewer, and the body is the instructions.
"""

import re
from pathlib import Path

from . import yamlish

KINDS = ("builtin", "agent", "run")
FRONT_KEYS = ("builtin", "agent", "run", "at", "when", "condition", "blocking", "model", "effort", "timeout")
# written in this order
FOLDER = ".claude/reviewers"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def path_for(name):
    return f"{FOLDER}/{name}.md"


def agent_path(agent):
    return f".claude/agents/{agent}.md"


def kind_of(spec):
    return next((k for k in ("builtin", "agent", "run") if k in spec), "instructions")


def read(root, rel):
    """(name, spec, body) of one reviewer file. `spec` carries `file` and `body`; an instructions
    reviewer also gets `instructions: <file>` so the rest of the pipeline treats it like before."""
    text = (Path(root) / rel).read_text()
    front, body = yamlish.parse_frontmatter(text)
    spec = {k: v for k, v in front.items() if k in FRONT_KEYS}
    spec["file"], spec["body"] = rel, body.strip("\n")
    if kind_of(spec) == "instructions":
        spec["instructions"] = rel
    return Path(rel).stem, spec, body


def load_list(root, paths):
    """Reviewers named by SDLC.md's `reviewers:` list -> ({name: spec}, [warnings]).
    A missing or unreadable file is a warning, not a crash, so the dashboard still opens."""
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
                name, spec, _ = read(root, rel)
            except yamlish.YamlError as e:
                bad(rel, str(e))
                continue
            if name in out:
                bad(rel, f"listed twice (same name as {out[name]['file']})")
                continue
            out[name] = spec
    return out, warnings, problems


def extra_instructions(spec):
    """A built-in reviewer's own text, minus HTML comments (those are notes for humans)."""
    return re.sub(r"<!--.*?-->", "", spec.get("body") or "", flags=re.S).strip()


def instructions_text(root, spec):
    """What an instructions reviewer is told: the file body (settings stripped)."""
    if "body" in spec:
        return spec["body"]
    return (Path(root) / spec["instructions"]).read_text()


def render(front, body):
    keys = [k for k in FRONT_KEYS if front.get(k) not in (None, "")]
    head = "\n".join(f"{k}: {yamlish.dump_inline(front[k])}" for k in keys)
    return f"---\n{head}\n---\n{body.strip(chr(10))}\n" if body.strip() else f"---\n{head}\n---\n"


def clean_front(front):
    """Only known, non-empty settings, with `at` and `blocking` always written out so the file is
    the whole truth about the reviewer."""
    out = {k: v for k, v in front.items() if k in FRONT_KEYS and v not in (None, "")}
    out.setdefault("at", "review")
    out.setdefault("blocking", True)
    return out


# ---------------------------------------------------------------- editing --

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_NAMES = ("code", "security")


def template(name):
    """(front, body) of a shipped starter: `code`, `security`, or `instructions` for a new reviewer."""
    if name in DEFAULT_NAMES:
        front, body = yamlish.parse_frontmatter((PLUGIN_ROOT / "templates" / "reviewers" / f"{name}.md").read_text())
        return front, body.strip("\n")
    return {}, (PLUGIN_ROOT / "templates" / "review-template.md").read_text()


def agent_template():
    return (PLUGIN_ROOT / "templates" / "reviewer-agent-template.md").read_text()


def ensure_defaults(root):
    """Write the shipped code and security reviewer files if missing. Returns their paths."""
    root, paths = Path(root), []
    for name in DEFAULT_NAMES:
        rel = path_for(name)
        if not (root / rel).exists():
            front, body = template(name)
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(render(front, body))
        paths.append(rel)
    return paths


def _sdlc_list(root):
    from . import configedit
    text = (Path(root) / "SDLC.md").read_text() if (Path(root) / "SDLC.md").exists() else "---\n---\n"
    raw, _ = yamlish.parse_frontmatter(text)
    return text, raw.get("reviewers")


def _write_list(root, paths):
    from . import configedit
    text, _ = _sdlc_list(root)
    new = configedit.apply(text, {"reviewers": paths})
    configedit._validate_text(Path(root), new)
    p = Path(root) / "SDLC.md"
    tmp = p.with_suffix(".md.tmp")
    tmp.write_text(new)
    tmp.replace(p)


def save(cfg, name, front, body="", agent_content=None):
    """Create or update one reviewer file (and its agent definition) and make sure SDLC.md lists it."""
    from . import config, rules
    root = Path(cfg.root)
    _, listed = _sdlc_list(root)
    if not isinstance(listed, list):
        raise ValueError("reviewers are still inline in SDLC.md; convert them to files first")
    existing = (cfg["reviewers"].get(name) or {}).get("file")
    if not existing and not NAME_RE.match(name or ""):
        raise ValueError("name: letters, digits, - and _ only")
    rel = existing or path_for(name)
    if not existing and (root / rel).exists():
        raise ValueError(f"{rel} already exists")
    front = clean_front(front)
    kinds = [k for k in KINDS if k in front]
    if len(kinds) > 1:
        raise ValueError("a reviewer is exactly one of: instructions, agent or command")
    kind = kinds[0] if kinds else "instructions"
    if kind == "instructions" and not (body or "").strip():
        raise ValueError("the instructions can't be empty")
    if kind == "run" and not str(front["run"]).strip():
        raise ValueError("give the command to run")
    if kind == "agent":
        if not NAME_RE.match(str(front["agent"])):
            raise ValueError("agent name: letters, digits, - and _ only")
        if agent_content is not None and not agent_content.strip():
            raise ValueError("the agent definition can't be empty")
    if front.get("at", "review") not in config.STEPS:
        raise ValueError(f"trigger must be one of {', '.join(config.STEPS)}")
    if front.get("model") not in (None, "opus", "sonnet", "haiku", "fable", "inherit"):
        raise ValueError("model must be opus, sonnet, haiku, fable or inherit")
    if "when" in front:
        try:
            rules.parse(front["when"])
        except rules.RuleError as e:
            raise ValueError(f"when: {e}")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(render(front, body if kind != "run" else body))
    if kind == "agent" and agent_content is not None:
        ap = root / agent_path(front["agent"])
        ap.parent.mkdir(parents=True, exist_ok=True)
        ap.write_text(agent_content if agent_content.endswith("\n") else agent_content + "\n")
    if rel not in listed:
        _write_list(root, listed + [rel])
    return rel


def delete(cfg, name):
    """Stop using a reviewer: drop it from SDLC.md's list. Its file stays on disk."""
    _, listed = _sdlc_list(cfg.root)
    rel = (cfg["reviewers"].get(name) or {}).get("file")
    if not isinstance(listed, list) or rel not in listed:
        raise ValueError(f"no file-backed reviewer named {name}")
    _write_list(cfg.root, [p for p in listed if p != rel])
    return rel


def convert_legacy(cfg):
    """Turn inline reviewers (the built-ins plus anything under `reviewers:` as a map) into files
    and replace the map in SDLC.md with the list of paths."""
    root = Path(cfg.root)
    _, listed = _sdlc_list(root)
    if isinstance(listed, list):
        raise ValueError("reviewers are already files")
    created, kept, paths = [], [], []
    for name, spec in cfg["reviewers"].items():
        rel = path_for(name)
        paths.append(rel)
        if (root / rel).exists() and not (spec.get("instructions") == rel):
            kept.append(rel)
            continue
        front = {k: v for k, v in spec.items() if k in FRONT_KEYS}
        body = ""
        if "builtin" in spec:
            tfront, body = template("code" if spec["builtin"] == "code-review" else "security")
            front = {**tfront, **front}
        elif "instructions" in spec:
            for cand in dict.fromkeys([spec["instructions"], f"{FOLDER}/review-{name}.md", rel, f"review-{name}.md"]):
                if (root / cand).exists():
                    _, body = yamlish.parse_frontmatter((root / cand).read_text())
                    break
            else:
                body = template(name)[1].replace("<Name>", name)
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(render(clean_front(front), body))
        created.append(rel)
    _write_list(root, paths)
    return {"created": created, "kept": kept, "listed": paths}
