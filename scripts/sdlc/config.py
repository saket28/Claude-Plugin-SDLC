"""SDLC.md loading: defaults, overrides, and the main checkout.

Settings are always read from the *main checkout's* SDLC.md, never from a job
worktree (INSTRUCTIONS.md §8: commands come only from the main checkout).
"""

import copy
import os
import subprocess
from pathlib import Path

from . import yamlish

DEFAULTS = {
    "work_dir": ".sdlc",
    "worktree_dir": None,            # computed: ../<repo>.sdlc-worktrees
    "base_branch": "main",
    "branch_prefix": "sdlc/",
    "commands": {"setup": None, "test": None, "lint": None, "build": None},
    "test_paths": ["tests/**", "test/**", "**/*.test.*", "**/*_test.*", "**/test_*.py"],
    "board": {"adapter": "files"},
    "host": {"adapter": None},       # computed: github if remote + gh, else local
    "gates": {"spec": "auto", "merge": "human"},
    "max_rounds": 2,
    "models": {"spec-writer": "opus", "builder": "opus", "tester": "sonnet", "reviewer": "sonnet",
               "judge": "haiku"},
    "reviewers": {
        "code": {"builtin": "code-review", "at": "review", "when": "always", "effort": "high"},
        "security": {"builtin": "security-review", "at": "review", "when": "risk>=medium"},
    },
    "budgets": {
        "per_job": 2_000_000,
        "per_month": 50_000_000,
        "per_stage": {"spec": 150_000, "build": 1_000_000, "verify": 300_000,
                      "review": 400_000, "merge": 100_000, "orchestrate": 200_000},
        "on_exceed": "escalate",
        "warn_at": 0.8,
    },
    "learning": "auto",
    "protected_paths": ["SDLC.md", "REVIEW.md", "review-*.md", "CLAUDE.md", ".github/workflows/**"],
    "blocked_commands": ["git push --force", "git push -f", "git reset --hard",
                        "git push origin {base}", "git checkout {base}", "git switch {base}"],
    "notify": None,
}

REVIEWER_KINDS = ("builtin", "instructions", "agent", "run")
STEPS = ("spec", "review", "merge", "release")


class ConfigError(ValueError):
    pass


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def main_checkout(start=None):
    """Root of the main working tree, even when called from a linked worktree."""
    start = start or os.getcwd()
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=start)
    return Path(common).parent


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "reviewers":
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def merge_reviewers(defaults, over):
    out = copy.deepcopy(defaults)
    for name, spec in (over or {}).items():
        if spec is None:
            continue
        if name in out and not any(k in spec for k in ("instructions", "agent", "run")):
            out[name] = {**out[name], **spec}      # tweak a built-in
        else:
            out[name] = dict(spec)
    return out


class Config:
    def __init__(self, root, data, body, raw):
        self.root = Path(root)
        self.data = data
        self.body = body
        self.raw = raw              # overrides only, as written in SDLC.md

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)

    @property
    def work_dir(self):
        return self.root / self.data["work_dir"]

    @property
    def worktree_dir(self):
        wd = self.data.get("worktree_dir")
        return Path(wd) if wd and os.path.isabs(wd) else (
            (self.root / wd).resolve() if wd else self.root.parent / f"{self.root.name}.sdlc-worktrees")

    def body_sections(self):
        """Markdown body split by '## ' headings."""
        sections, cur, buf = {}, None, []
        for line in self.body.splitlines():
            if line.startswith("## "):
                if cur:
                    sections[cur] = "\n".join(buf).strip()
                cur, buf = line[3:].strip(), []
            elif cur:
                buf.append(line)
        if cur:
            sections[cur] = "\n".join(buf).strip()
        return sections


def detect_host(root):
    try:
        url = git("remote", "get-url", "origin", cwd=root, check=False)
    except Exception:
        url = ""
    from shutil import which
    return "github" if "github.com" in url and which("gh") else "local"


def load(root=None):
    root = Path(root) if root else main_checkout()
    path = root / "SDLC.md"
    text = path.read_text() if path.exists() else ""
    raw, body = yamlish.parse_frontmatter(text)
    data = deep_merge(DEFAULTS, {k: v for k, v in raw.items() if k != "reviewers"})
    data["reviewers"] = merge_reviewers(DEFAULTS["reviewers"], raw.get("reviewers"))
    # Safety lists only ever grow: a repo's entries are added to the defaults, never replace them.
    for key in ("protected_paths", "blocked_commands"):
        extra = raw.get(key) or []
        if not isinstance(extra, list):
            raise ConfigError(f"{key} must be a list")
        data[key] = list(dict.fromkeys(DEFAULTS[key] + extra))
    if not data["host"].get("adapter"):
        data["host"]["adapter"] = detect_host(root)
    cfg = Config(root, data, body, raw)
    validate(cfg)
    return cfg


def validate(cfg):
    from . import rules
    errs = []
    d = cfg.data
    if not isinstance(d["max_rounds"], int) or d["max_rounds"] < 0:
        errs.append("max_rounds must be a non-negative integer")
    for gate in ("spec", "merge"):
        g = d["gates"].get(gate)
        if isinstance(g, dict):
            if set(g) - {"human_when"}:
                errs.append(f"gates.{gate}: only 'human_when' is allowed in a map")
            else:
                try:
                    rules.parse(g["human_when"])
                except rules.RuleError as e:
                    errs.append(f"gates.{gate}.human_when: {e}")
        elif g not in ("auto", "human"):
            errs.append(f"gates.{gate} must be auto, human or {{human_when: <rule>}}")
    for name, r in d["reviewers"].items():
        kinds = [k for k in REVIEWER_KINDS if k in r]
        if len(kinds) != 1:
            errs.append(f"reviewers.{name}: needs exactly one of instructions, agent or run")
        if r.get("at", "review") not in STEPS:
            errs.append(f"reviewers.{name}.at must be one of {', '.join(STEPS)}")
        if "when" in r:
            try:
                rules.parse(r["when"])
            except rules.RuleError as e:
                errs.append(f"reviewers.{name}.when: {e}")
        if "instructions" in r and not (cfg.root / r["instructions"]).exists():
            errs.append(f"reviewers.{name}: instructions file {r['instructions']} not found")
    if d["budgets"].get("on_exceed") not in ("warn", "escalate"):
        errs.append("budgets.on_exceed must be warn or escalate")
    if d["learning"] not in ("auto", "propose", "off"):
        errs.append("learning must be auto, propose or off")
    if errs:
        raise ConfigError("SDLC.md is invalid:\n  - " + "\n  - ".join(errs))
