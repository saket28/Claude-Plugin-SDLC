"""SDLC.md loading: defaults, overrides, and the main checkout.

Settings are always read from the *main checkout's* SDLC.md, never from a job
worktree (INSTRUCTIONS.md §8: commands come only from the main checkout).
"""

import copy
import os
import subprocess
from pathlib import Path

from . import yamlish

# Every setting is <tab>.<section>.<setting>. The dashboard has a tab per first part, a block per
# second part, and shows only the last part. `list` (reviewers, principles) is a collection with
# its own editor. Anything inside FREEFORM sections is passed through (adapter ops, stage maps ...).
DEFAULTS = {
    "global": {
        "pipeline": {"max_rounds": 3,            # build -> verify -> review passes before a person decides
                     "critical_thinking": 1,     # 0 = follow instructions as given ... 5 = challenge everything
                     "judge_model": "haiku",
                     "agent_attempts": 2},       # runs of an agent for one step before the job fails
        "learning": {"mode": "auto", "on": ["done", "failed"]},
        "repo": {"base_branch": "main", "branch_prefix": "sdlc/", "branch_name": "{prefix}job-{id}",
                 "work_dir": ".sdlc", "worktree_dir": None},     # worktree_dir computed: ../<repo>.sdlc-worktrees
        "command": {"setup": None, "build": None, "lint": None, "test": None, "timeout": 1800},
        "board": {"adapter": "files"},
        "budget": {"per_job": 2_000_000, "per_month": 50_000_000, "orchestrate": 200_000,
                   "on_exceed": "escalate", "warn_at": 0.8},
        "dashboard": {"stall_after": 10},        # minutes
        "wait": {"interval": 60, "max_hours": 12},
        "guardrail": {
            "protected_paths": ["SDLC.md", "REVIEW.md", "review-*.md", "CLAUDE.md", ".github/workflows/**",
                                ".claude/reviewers/**", ".claude/agents/**", ".claude/principles/**"],
            "blocked_commands": ["git push --force", "git push -f", "git reset --hard",
                                 "git push origin {base}", "git checkout {base}", "git switch {base}"]},
        "principle": {"list": []},               # paths of .claude/principles/<name>.md; resolved to {name: spec}
    },
    "spec": {"agent": {"model": "opus", "critical_thinking": None},
             "gate": {"mode": "none"}, "format": {"sections": []}, "budget": {"tokens": 150_000}},
    "implement": {"agent": {"model": "opus", "critical_thinking": None}, "budget": {"tokens": 1_000_000}},
    "test": {"agent": {"model": "sonnet", "critical_thinking": None},
             "scope": {"paths": ["tests/**", "test/**", "**/*.test.*", "**/*_test.*", "**/test_*.py"]},
             "verify": {"order": ["lint", "build", "test"], "stop_on_failure": True},
             "budget": {"tokens": 300_000}},
    "review": {
        "agent": {"model": "sonnet", "critical_thinking": None}, "severity": {"nits_block": False},
        "budget": {"tokens": 400_000},
        "reviewer": {"list": {
            "code": {"builtin": "code-review", "at": "review", "when": "always", "effort": "high"},
            "security": {"builtin": "security-review", "at": "review", "when": "risk>=medium"},
        }},
    },
    "release": {"gate": {"mode": "human"},
                "merge": {"strategy": "squash", "message": "{title} (job-{id})", "delete_branch": True},
                "pr": {"draft": False, "labels": [], "reviewers": []},
                "ci": {"required": True}, "cleanup": {"worktree": True},
                "host": {"adapter": None},       # host adapter computed: github if remote + gh, else local
                "budget": {"tokens": 100_000}},
}
FREEFORM = {("global", "board"), ("release", "host")}

STAGES = ("spec", "implement", "test", "review", "release")
# The agent behind each settings stage, and the runtime stage each settings stage budgets.
ROLE_STAGE = {"spec-writer": "spec", "builder": "implement", "tester": "test", "reviewer": "review"}
RUNTIME_STAGE = {"spec": "spec", "build": "implement", "verify": "test", "review": "review", "merge": "release"}

REVIEWER_KINDS = ("builtin", "instructions", "agent", "run")


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
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "list":
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
    def __init__(self, root, data, body, raw, warnings=None, lenient=False):
        self.warnings = list(warnings or [])
        self.problems = []           # reviewer files that could not be used: [{name, file, error}]
        self.principle_problems = []  # the same for principle files
        self.lenient = lenient       # the dashboard opens even when a reviewer file is missing
        self.root = Path(root)
        self.data = data
        self.body = body
        self.raw = raw              # overrides only, as written in SDLC.md

    def __getitem__(self, k):
        return self.data[k]

    def v(self, path):
        """A setting by its <tab>.<section>.<setting> path."""
        cur = self.data
        for k in path.split("."):
            cur = cur[k]
        return cur

    @property
    def reviewers(self):
        return self.data["review"]["reviewer"]["list"]

    def model(self, role):
        """Model for an agent role (spec-writer, builder, tester, reviewer) or 'judge'."""
        if role == "judge":
            return self.v("global.pipeline.judge_model")
        return self.data[ROLE_STAGE[role]]["agent"]["model"]

    def critical_thinking(self, role):
        """The stage's own level, else the global one."""
        own = self.data[ROLE_STAGE[role]]["agent"]["critical_thinking"]
        return self.v("global.pipeline.critical_thinking") if own is None else own

    def gate(self, name):
        return self.data["spec" if name == "spec" else "release"]["gate"]["mode"]

    def command(self, name):
        return self.data["global"]["command"].get(name)

    def stage_budget(self, runtime_stage):
        """Token cap for a runtime stage (spec, build, verify, review, merge, orchestrate); None = per_job."""
        if runtime_stage == "orchestrate":
            return self.v("global.budget.orchestrate")
        stage = RUNTIME_STAGE.get(runtime_stage)
        return self.data[stage]["budget"]["tokens"] if stage else None

    @property
    def work_dir(self):
        return self.root / self.v("global.repo.work_dir")

    @property
    def worktree_dir(self):
        wd = self.v("global.repo.worktree_dir")
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


def resolve_reviewers(root, raw):
    """Reviewers from `review.reviewer.list`. A list of paths = file-backed reviewers (exactly those,
    built-ins included only if listed); a map or nothing = the built-ins plus inline entries."""
    if isinstance(raw, list):
        from . import reviewers
        return reviewers.load_list(root, raw)
    return merge_reviewers(DEFAULTS["review"]["reviewer"]["list"], raw), [], []


def check_keys(raw):
    """Every setting is <tab>.<section>.<setting>. Anything else is a mistake, not silently ignored."""
    errs = []
    for tab, secs in raw.items():
        if tab not in DEFAULTS:
            errs.append(f"unknown setting '{tab}': settings are <tab>.<section>.<setting>, "
                        f"and the tabs are {', '.join(DEFAULTS)}")
        elif not isinstance(secs, dict):
            errs.append(f"'{tab}' must hold sections (<tab>.<section>.<setting>)")
        else:
            for sec, settings in secs.items():
                if sec not in DEFAULTS[tab]:
                    errs.append(f"unknown section '{tab}.{sec}': the sections of {tab} are {', '.join(DEFAULTS[tab])}")
                elif not isinstance(settings, dict):
                    errs.append(f"'{tab}.{sec}' must hold settings (<tab>.<section>.<setting>)")
                elif (tab, sec) not in FREEFORM:
                    errs += [f"unknown setting '{tab}.{sec}.{k}': {tab}.{sec} has {', '.join(DEFAULTS[tab][sec])}"
                             for k in settings if k not in DEFAULTS[tab][sec]]
    return errs


def build(root, raw):
    """Defaults + the repo's overrides -> (data, warnings, problems, principle_problems)."""
    errs = check_keys(raw)
    if errs:
        raise ConfigError("SDLC.md is invalid:\n  - " + "\n  - ".join(errs))
    over = {t: {sec: dict(v) for sec, v in secs.items()} for t, secs in raw.items()}
    rlist = (over.get("review") or {}).get("reviewer", {}).pop("list", None)
    plist = (over.get("global") or {}).get("principle", {}).pop("list", None)
    if plist is not None and not isinstance(plist, list):
        raise ConfigError("global.principle.list must be a list of file paths")
    data = deep_merge(DEFAULTS, over)
    data["review"]["reviewer"]["list"], warnings, problems = resolve_reviewers(root, rlist)
    from . import principles
    data["global"]["principle"]["list"], pwarn, pprob = principles.load_list(root, plist)
    warnings = list(warnings) + pwarn
    # Safety lists only ever grow: a repo's entries are added to the defaults, never replace them.
    for key in ("protected_paths", "blocked_commands"):
        extra = ((raw.get("global") or {}).get("guardrail") or {}).get(key) or []
        if not isinstance(extra, list):
            raise ConfigError(f"global.guardrail.{key} must be a list")
        data["global"]["guardrail"][key] = list(dict.fromkeys(DEFAULTS["global"]["guardrail"][key] + extra))
    return data, warnings, problems, pprob


def load(root=None, lenient=False):
    root = Path(root) if root else main_checkout()
    path = root / "SDLC.md"
    text = path.read_text() if path.exists() else ""
    raw, body = yamlish.parse_frontmatter(text)
    data, warnings, problems, pprob = build(root, raw)
    if not data["release"]["host"].get("adapter"):
        data["release"]["host"]["adapter"] = detect_host(root)
    cfg = Config(root, data, body, raw, warnings, lenient)
    cfg.problems = problems
    cfg.principle_problems = pprob
    validate(cfg)
    return cfg


def validate(cfg, assume_files=()):
    """Types, allowed values and ranges come from the schema; what's left here are rules that span
    settings (budgets, rule syntax, files that must exist)."""
    from . import rules, schema
    errs = []
    d = cfg.data
    budget = d["global"]["budget"]
    for path, f in schema.fields().items():
        default = DEFAULTS
        for k in path.split("."):
            default = default[k]
        bad = schema.check(path, f, cfg.v(path), nullable=default is None)
        if bad:
            errs.append(bad)
    rf = {k: schema.reviewer_field(k) for k in ("at", "model", "critical_thinking", "effort")}
    for name, r in cfg.reviewers.items():
        kinds = [k for k in REVIEWER_KINDS if k in r]
        if len(kinds) != 1:
            errs.append(f"reviewer {name}: needs exactly one of instructions, agent or run")
        for key, f in rf.items():
            value = r.get(key, "review" if key == "at" else None)
            bad = None if value is None else schema.check(f"reviewer {name}: {key}", f, value)
            if bad:
                errs.append(bad)
        if "when" in r:
            try:
                rules.parse(r["when"])
            except rules.RuleError as e:
                errs.append(f"reviewer {name}: when: {e}")
        if "instructions" in r and r["instructions"] not in assume_files and not (cfg.root / r["instructions"]).exists():
            msg = f"reviewer {name}: instructions file {r['instructions']} not found"
            if cfg.lenient:
                cfg.warnings.append(msg)
                cfg.problems.append({"name": name, "file": r["instructions"], "error": "file not found"})
            else:
                errs.append(msg)
    over = [s for s in STAGES      # only budgets the repo set itself
            if isinstance(((cfg.raw.get(s) or {}).get("budget") or {}).get("tokens"), int)
            and d[s]["budget"]["tokens"] > budget["per_job"]]
    if over:
        errs.append("stage budgets above global.budget.per_job: " + ", ".join(f"{s}.budget.tokens" for s in over))
    if errs:
        raise ConfigError("SDLC.md is invalid:\n  - " + "\n  - ".join(errs))
