"""Board and host adapters (INSTRUCTIONS.md §6).

Built in: `files` board, `local` and `github` hosts. Any operation can be
overridden in SDLC.md with `ops: {op: "<command template>"}`; `adapter: <path>`
runs `<path> <op>` for every operation. Commands get the request as JSON on
stdin and must print JSON on stdout. All operations must be idempotent.
"""

import json
import re
import shlex
import subprocess
from pathlib import Path

from .config import git

HOST_OPS = ("open_pr", "pr_status", "update_branch", "merge", "publish_release")
BOARD_OPS = ("create", "get", "list_queue", "move", "claim", "publish", "update_status",
             "read_approvals")


KNOWN_FIELDS = {"op", "ticket", "stage", "branch", "base", "title", "body_file", "worktree", "tag",
                "notes_file", "job"}


class AdapterError(RuntimeError):
    pass


class NeedsInstructions(Exception):
    """Raised when an operation is set to `instructions` (handled by Claude, v2)."""

    def __init__(self, kind, op, request):
        super().__init__(f"{kind}.{op} is an instructions operation")
        self.kind, self.op, self.request = kind, op, request


PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def run_command(template, request, cwd):
    """Fill {name} placeholders (shell-quoted, so use them outside quotes) and run.
    Other braces, such as JSON or jq filters, are left untouched."""
    fields = {k: shlex.quote(str(v)) for k, v in request.items() if not isinstance(v, (dict, list))}
    unknown = [m for m in PLACEHOLDER.findall(template) if m not in fields and m in KNOWN_FIELDS]
    if unknown:
        raise AdapterError(f"`{template}` uses {{{unknown[0]}}}, which this operation doesn't provide")
    cmd = PLACEHOLDER.sub(lambda m: fields.get(m.group(1), m.group(0)), template)
    r = subprocess.run(cmd, shell=True, cwd=cwd, input=json.dumps(request),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AdapterError(f"`{cmd}` exited {r.returncode}: {(r.stderr or r.stdout).strip()[:500]}")
    out = r.stdout.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise AdapterError(f"`{cmd}` did not print JSON: {out[:200]}")


class Adapter:
    kind = None
    ops = ()

    def __init__(self, cfg, settings):
        self.cfg = cfg
        self.settings = settings or {}
        self.overrides = dict(self.settings.get("ops") or {})
        adapter = self.settings.get("adapter")
        if adapter and adapter not in ("files", "local", "github", "custom"):
            for op in self.ops:
                self.overrides.setdefault(op, f"{adapter} {op}")

    def call(self, op, **request):
        if op not in self.ops:
            raise AdapterError(f"unknown {self.kind} operation {op}")
        if op in self.overrides:
            tmpl = self.overrides[op]
            if tmpl == "instructions":
                raise NeedsInstructions(self.kind, op, request)
            return run_command(tmpl, {"op": op, **request}, self.cfg.root)
        fn = getattr(self, "op_" + op, None)
        if fn is None:
            raise AdapterError(f"{self.kind} adapter '{self.settings.get('adapter')}' has no "
                               f"operation {op}; set it in SDLC.md under {self.kind}.ops")
        return fn(**request)


# ---------------------------------------------------------------- hosts ----

class LocalHost(Adapter):
    """No PRs, no CI. Merging is a fast-forward of base_branch (§4 merge stage)."""
    kind, ops = "host", HOST_OPS
    has_prs = False

    def op_open_pr(self, **_):
        return {"pr": None}

    def op_pr_status(self, **_):
        return {"ci": "none", "approved": False, "behind": False}

    def op_update_branch(self, worktree, base, **_):
        if subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=worktree,
                          capture_output=True).returncode == 0:
            return {"updated": False, "reason": f"already up to date with {base}"}
        r = subprocess.run(["git", "rebase", "--autostash", base], cwd=worktree, capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=worktree, capture_output=True)
            conflict = "CONFLICT" in (r.stdout + r.stderr)
            return {"updated": False, "conflict": conflict,
                    "reason": (r.stderr or r.stdout).strip()[:500]}
        return {"updated": True}

    def op_merge(self, branch, base, strategy="squash", message="", **_):
        """The branch was just rebased onto base, so its tree is exactly what should land.
        squash = one commit on base, merge = a merge commit, rebase = fast-forward (keeps every commit)."""
        root = self.cfg.root
        current = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root, check=False)
        if subprocess.run(["git", "merge-base", "--is-ancestor", base, branch],
                          cwd=root, capture_output=True).returncode != 0:
            return {"merged": False, "reason": f"{branch} is not ahead of {base}; rebase first"}
        tip, old = git("rev-parse", branch, cwd=root), git("rev-parse", base, cwd=root)
        message = message or f"Merge {branch}"
        if strategy == "rebase":
            new = tip
        else:
            parents = ["-p", old] + (["-p", tip] if strategy == "merge" else [])
            r = subprocess.run(["git", "commit-tree", f"{tip}^{{tree}}", *parents, "-m", message],
                               cwd=root, capture_output=True, text=True)
            if r.returncode != 0:
                return {"merged": False, "reason": r.stderr.strip()[:500]}
            new = r.stdout.strip()
        if current == base:
            if git("status", "--porcelain", "--untracked-files=no", cwd=root, check=False):
                return {"merged": False, "blocked": "checkout-dirty",
                        "reason": "the main checkout has uncommitted changes"}
            r = subprocess.run(["git", "merge", "--ff-only", new], cwd=root,
                               capture_output=True, text=True)
        else:
            # base isn't checked out here: move the ref without touching the user's work
            r = subprocess.run(["git", "update-ref", f"refs/heads/{base}", new, old], cwd=root,
                               capture_output=True, text=True)
        if r.returncode != 0:
            return {"merged": False, "reason": r.stderr.strip()[:500]}
        return {"merged": True}

    def op_publish_release(self, **_):
        return {}


class GitHubHost(Adapter):
    kind, ops = "host", HOST_OPS
    has_prs = True

    def gh(self, *args, check=True):
        r = subprocess.run(["gh", *args], cwd=self.cfg.root, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise AdapterError(f"gh {' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[:500]}")
        return r

    def _existing(self, branch):
        r = self.gh("pr", "list", "--head", branch, "--state", "all", "--json",
                    "number,url,state", "--limit", "1")
        items = json.loads(r.stdout or "[]")
        return items[0] if items else None

    def op_open_pr(self, branch, base, title, body_file, worktree=None, **_):
        subprocess.run(["git", "push", "-u", "origin", branch], cwd=worktree or self.cfg.root,
                       capture_output=True, text=True, check=False)
        pr = self._existing(branch)
        if pr and pr["state"] == "OPEN":
            return {"pr": pr["url"], "number": pr["number"]}
        opts = (["--draft"] if self.cfg.v("release.pr.draft") else [])
        for label in self.cfg.v("release.pr.labels"):
            opts += ["--label", label]
        for reviewer in self.cfg.v("release.pr.reviewers"):
            opts += ["--reviewer", reviewer]
        self.gh("pr", "create", "--head", branch, "--base", base, "--title", title,
                "--body-file", body_file, *opts)
        pr = self._existing(branch)
        return {"pr": pr["url"], "number": pr["number"]}

    def op_pr_status(self, branch, **_):
        r = self.gh("pr", "view", branch, "--json",
                    "statusCheckRollup,reviewDecision,mergeStateStatus,state")
        d = json.loads(r.stdout)
        checks = d.get("statusCheckRollup") or []
        states = [(c.get("conclusion") or c.get("state") or "").upper() for c in checks]
        if not checks:
            ci = "none"
        elif any(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED") for s in states):
            ci = "fail"
        elif any(s in ("", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED") for s in states):
            ci = "pending"
        else:
            ci = "pass"
        return {"ci": ci, "approved": d.get("reviewDecision") == "APPROVED",
                "review_required": d.get("reviewDecision") not in (None, ""),
                "behind": d.get("mergeStateStatus") == "BEHIND",
                "merged": d.get("state") == "MERGED"}

    def op_update_branch(self, branch, **_):
        r = self.gh("pr", "update-branch", branch, check=False)
        if r.returncode != 0:
            return {"updated": False, "conflict": "conflict" in (r.stderr or "").lower(),
                    "reason": (r.stderr or "").strip()[:500]}
        return {"updated": True}

    def op_merge(self, branch, strategy="squash", message="", delete_branch=True, **_):
        if self.op_pr_status(branch=branch).get("merged"):
            return {"merged": True}
        strategy = strategy if strategy in ("squash", "merge", "rebase") else "squash"
        extra = ["--subject", message] if message and strategy != "rebase" else []
        # Never pass --delete-branch: gh merges, then tries to delete the *local* branch, which the job's
        # worktree still has checked out, and exits non-zero even though the PR is merged.
        r = self.gh("pr", "merge", branch, "--" + strategy, *extra, check=False)
        if r.returncode != 0 and not self.op_pr_status(branch=branch).get("merged"):
            return {"merged": False, "reason": (r.stderr or r.stdout).strip()[:500]}
        if delete_branch:                  # the remote branch; the local one goes when the worktree does
            subprocess.run(["git", "push", "origin", "--delete", branch], cwd=self.cfg.root,
                           capture_output=True, text=True, check=False)
        return {"merged": True}

    def op_publish_release(self, tag, notes_file, **_):
        r = self.gh("release", "view", tag, check=False)
        if r.returncode == 0:
            return {"published": True}
        self.gh("release", "create", tag, "--notes-file", notes_file)
        return {"published": True}


# ---------------------------------------------------------------- boards ---

class FilesBoard(Adapter):
    """The job folders are the board; nothing to call."""
    kind, ops = "board", BOARD_OPS

    def op_create(self, **_):
        return {}

    def op_get(self, **_):
        return {}

    def op_list_queue(self, **_):
        return {}

    def op_move(self, **_):
        return {}

    def op_claim(self, **_):
        return {"claimed": True}

    def op_publish(self, **_):
        return {}

    def op_update_status(self, **_):
        return {}

    def op_read_approvals(self, **_):
        return {"approvals": []}


def host(cfg):
    s = cfg.v("release.host")
    name = s.get("adapter")
    cls = {"local": LocalHost, "github": GitHubHost}.get(name, LocalHost)
    a = cls(cfg, s)
    if name not in ("local", "github"):
        a.has_prs = bool(s.get("has_prs", True))
    return a


def board(cfg):
    s = cfg.v("global.board")
    if s.get("adapter") == "github":
        raise AdapterError("the github board adapter arrives in v2; use `global.board: {adapter: files}`")
    return FilesBoard(cfg, s)
