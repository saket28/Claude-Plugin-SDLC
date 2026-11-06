"""The pipeline as code (INSTRUCTIONS.md §4).

`next_action(job)` runs every scripted step it can and returns the single action
that needs Claude, as a dict:

    {"action": "spawn",  "agents": [{"agent", "prompt", "output", "key"}]}
    {"action": "ask",    "questions": [...], "file": path}      # answers -> report
    {"action": "convert","raw": path, "output": path}            # rare review fallback
    {"action": "wait",   "reason": ..., "hint": ..., "waiter": cmd | None}
    {"action": "done"}  /  {"action": "failed", "reason": ...}

State is derived from the job's events every time, so the loop is resumable at
any point and a deleted job folder simply stops existing.
"""

import json
import re
import subprocess
from pathlib import Path

from . import adapters, briefs, rules, runners, usage, yamlish
from .config import git
from .store import Store

AGENT_FOR = {"spec": "sdlc:spec-writer", "build": "sdlc:builder", "test": "sdlc:tester",
             "reviewer": "sdlc:reviewer"}
ROLE_FOR = {"sdlc:spec-writer": "spec-writer", "sdlc:builder": "builder",
            "sdlc:tester": "tester", "sdlc:reviewer": "reviewer"}


class State:
    """Replays a job's events."""

    def __init__(self, events):
        self.events = events
        self.stage = "queued"
        self.round = 0
        self.auto_rounds = 0
        self.spec_rev = 0
        self.spec_revisions = 0
        self.needs_human = None
        self.failed = None
        self.cancelled = False
        self.gates = {}
        self.spawns = {}
        self.selected = {}         # (step, round) -> {name: {"selected": bool, "reason": str}}
        self.budget_extra = 0
        self.skip_rules = False
        self.overrides = []
        self.setup_done = False
        self.answers_rev = 0
        self.head = {}
        self.warned = set()
        for e in events:
            self.apply(e)

    def apply(self, e):
        t = e["type"]
        if t == "stage":
            self.stage = e["stage"]
        elif t == "round":
            self.round = e["n"]
            if e.get("auto"):
                self.auto_rounds += 1
        elif t == "spec_rev":
            self.spec_rev = e["n"]
            if e.get("counts"):
                self.spec_revisions += 1
        elif t == "needs_human":
            self.needs_human = e
        elif t == "resume":
            self.needs_human = None
        elif t == "failed":
            self.failed = e
        elif t == "retry_job":
            self.failed = None
            self.spawns = {}
        elif t == "cancelled":
            self.cancelled = True
        elif t == "gate":
            self.gates[e["gate"]] = e["status"]
        elif t == "spawn":
            self.spawns[e["key"]] = self.spawns.get(e["key"], 0) + 1
        elif t == "selection":
            self.selected.setdefault((e["step"], e["round"]), {})[e["reviewer"]] = {
                "selected": e["selected"], "reason": e.get("reason", "")}
        elif t == "budget_extra":
            self.budget_extra += int(e.get("tokens", 0))
        elif t == "override":
            self.overrides.append(e)
            if e.get("skip_rules"):
                self.skip_rules = True
        elif t == "setup":
            self.setup_done = True
        elif t == "answers":
            self.answers_rev = e["rev"]
        elif t == "head":
            self.head[e["round"]] = e["sha"]
        elif t == "budget" and e.get("level") == "warn":
            self.warned.add(e.get("scope"))


class Pipeline:
    def __init__(self, cfg, job_id, session_id=None):
        self.cfg = cfg
        self.store = Store(cfg)
        self.job_id = str(job_id)
        self.session_id = session_id
        self.meta = self.store.load_job(self.job_id)
        self.dir = self.store.job_dir(self.job_id)
        self.host = adapters.host(cfg)
        self.base = cfg["base_branch"]

    # ---------------------------------------------------------- helpers --
    @property
    def wt(self):
        return Path(self.meta["worktree"])

    def ev(self, type_, **f):
        return self.store.event(self.job_id, type_, **f)

    def state(self):
        return State(self.store.events(self.job_id))

    def round_dir(self, n):
        return self.dir / f"round-{n}"

    def spec(self):
        front, body = runners.read_artifact(self.dir / "spec.md")
        return front or {}, body or ""

    def link_session(self, closing=False, spawned=None, stage=None, rnd=None):
        if not self.session_id:
            return
        self.meta = self.store.update_job(self.job_id, session={
            "id": self.session_id, "closing": closing, "spawned": spawned or [],
            "stage": stage, "round": rnd, "worktree": str(self.wt)})

    def needs_human(self, reason, step, detail="", hint=None):
        self.ev("needs_human", reason=reason, step=step, detail=detail)
        return self._wait(f"needs-human: {reason}", hint or self._approve_hint(reason), detail=detail)

    def _approve_hint(self, reason):
        j = self.job_id
        return {"budget": f"/sdlc:approve {j} budget +500k  (or /sdlc:rework {j} \"...\")",
                "spec-max": f"/sdlc:approve {j} spec  (accept the spec as it is)",
                "max-rounds": f"/sdlc:approve {j} override  (merge anyway) or /sdlc:rework {j} \"...\"",
                "rules": f"/sdlc:approve {j} override  or /sdlc:rework {j} \"...\"",
                "disputed": f"/sdlc:approve {j} override  or /sdlc:rework {j} \"...\"",
                "adapter": f"/sdlc:approve {j} retry",
                }.get(reason, f"/sdlc:status {j}")

    def _wait(self, reason, hint, waiter=None, detail=""):
        self.link_session(closing=True)
        return {"action": "wait", "job": self.job_id, "reason": reason, "hint": hint,
                "waiter": waiter, "detail": detail}

    # ------------------------------------------------------------ budgets --
    def job_usage(self):
        latest, by_stage = {}, {}
        for e in self.store.events(self.job_id):
            if e["type"] == "agent":
                latest[e.get("key") or e.get("agent_id")] = e
        for e in latest.values():
            st = e.get("stage", "other")
            by_stage[st] = by_stage.get(st, 0) + e.get("data", {}).get("tokens", 0)
        return sum(by_stage.values()), by_stage

    def budget_check(self, stage):
        b = self.cfg["budgets"]
        st = self.state()
        total, by_stage = self.job_usage()
        limits = [("job", total, b["per_job"] + st.budget_extra),
                  ("stage:" + stage, by_stage.get(stage, 0),
                   (b["per_stage"].get(stage) or b["per_job"]) + st.budget_extra),
                  ("month", self.store.month_tokens(), b["per_month"])]
        for scope, used, limit in limits:
            if limit and used >= limit:
                self.ev("budget", level="exceeded", scope=scope, used=used, limit=limit)
                if b["on_exceed"] == "escalate":
                    return self.needs_human("budget", stage, f"{scope}: {used:,} of {limit:,} tokens")
            elif limit and used >= limit * b.get("warn_at", 0.8) and scope not in st.warned:
                self.ev("budget", level="warn", scope=scope, used=used, limit=limit)
        return None

    # ------------------------------------------------------------- spawn --
    def spawn(self, items, stage, rnd):
        """items: [(key, agent, role, brief_meta, sections, output)]"""
        st = self.state()
        over = self.budget_check(stage)
        if over:
            return over
        out = []
        for key, agent, role, meta, sections, output in items:
            if st.spawns.get(key, 0) >= 2:
                self.ev("failed", reason=f"{agent} did not produce {output} after 2 attempts", key=key)
                self.link_session(closing=True)
                return {"action": "failed", "job": self.job_id,
                        "reason": f"{agent} did not produce {Path(output).name} twice. "
                                  f"Fix the cause, then run /sdlc:start {self.job_id}."}
            brief = self.dir / "briefs" / f"{key}.md"
            briefs.write(self.cfg, brief, {"job": self.job_id, "stage": stage, "round": rnd,
                                           "agent": agent, "output": str(output),
                                           "worktree": str(self.wt), **meta}, role, sections)
            self.ev("spawn", key=key, agent=agent, output=str(output), attempt=st.spawns.get(key, 0) + 1)
            out.append({"agent": agent, "key": key, "output": str(output),
                        "prompt": briefs.prompt_line(self.job_id, stage, rnd, brief)})
        self.link_session(spawned=[{"agent": o["agent"], "output": o["output"]} for o in out],
                          stage=stage, rnd=rnd)
        return {"action": "spawn", "job": self.job_id, "agents": out,
                "note": "Spawn all agents in parallel and wait for every one, then call next again."}

    # --------------------------------------------------------- worktree --
    def ensure_worktree(self):
        wt = self.wt
        if wt.exists() and (wt / ".git").exists():
            return
        wt.parent.mkdir(parents=True, exist_ok=True)
        root = self.cfg.root
        if git("rev-parse", "--verify", "--quiet", self.meta["branch"], cwd=root, check=False):
            git("worktree", "add", str(wt), self.meta["branch"], cwd=root)
        else:
            git("worktree", "add", "-b", self.meta["branch"], str(wt), self.base, cwd=root)

    def changed_paths(self):
        out = git("diff", "--name-only", f"{self.base}...{self.meta['branch']}", cwd=self.cfg.root,
                  check=False)
        return [p for p in out.splitlines() if p]

    def rule_ctx(self, step):
        front, _ = self.spec()
        paths = list(front.get("paths") or []) if step == "spec" else self.changed_paths()
        return {"risk": front.get("risk", "low"), "touches_ui": bool(front.get("touches_ui")),
                "paths": paths}

    # ------------------------------------------------------------ rounds --
    def new_round(self, reason, step, detail=""):
        st = self.state()
        if st.auto_rounds >= self.cfg["max_rounds"]:
            history = [f"round {e['n']}: {e.get('reason')}" for e in st.events
                       if e["type"] == "round" and e.get("auto")]
            return self.needs_human("max-rounds", step,
                                    "; ".join(history + [f"then: {detail or reason}"]))
        self.ev("round", n=st.round + 1, auto=True, reason=reason, step=step)
        self.ev("stage", stage="build")
        return None

    # ------------------------------------------------------------- main --
    def next_action(self):
        for _ in range(50):           # scripted steps loop until Claude is needed
            st = self.state()
            if st.cancelled:
                return {"action": "done", "job": self.job_id, "cancelled": True}
            if st.failed:
                return {"action": "failed", "job": self.job_id, "reason": st.failed.get("reason"),
                        "hint": f"/sdlc:start {self.job_id} retries it"}
            if st.needs_human:
                nh = st.needs_human
                return self._wait(f"needs-human: {nh['reason']}", self._approve_hint(nh["reason"]),
                                  detail=nh.get("detail", ""))
            handler = getattr(self, "do_" + st.stage.replace("-", "_"), None)
            if handler is None:
                return {"action": "done", "job": self.job_id}
            result = handler(st)
            if result is not None:
                return result
        raise RuntimeError("pipeline made no progress after 50 scripted steps")

    # queued -> spec
    def do_queued(self, st):
        self.ensure_worktree()
        setup = (self.cfg["commands"] or {}).get("setup")
        if setup and not st.setup_done:
            code, out, _ = runners.sh(setup, self.wt)
            if code != 0:
                return self.needs_human("setup", "queued", runners.tail(out, 20))
        self.ev("setup")
        self.ev("spec_rev", n=1)
        self.ev("stage", stage="spec")
        return None

    # spec
    def do_spec(self, st):
        front, body = self.spec()
        spec_path = self.dir / "spec.md"
        if not front or front.get("_invalid") or front.get("revision") != st.spec_rev:
            sections = [("Feature", self.meta["source"])]
            if st.spec_rev > 1 and spec_path.exists():
                sections.append(("Current spec (revise it)", spec_path.read_text()))
                prev = self.dir / "spec-review" / f"r{st.spec_rev - 1}"
                for f in sorted(prev.glob("review-*.md")) if prev.exists() else []:
                    sections.append((f"Spec review {f.stem[7:]}", f.read_text()))
            ans = self.dir / "answers.md"
            if ans.exists():
                sections.append(("Answers to your questions", ans.read_text()))
            return self.spawn([(f"spec-r{st.spec_rev}", AGENT_FOR["spec"], "spec-writer",
                                {"revision": st.spec_rev}, sections, spec_path)], "spec", 0)
        if int(front.get("open_questions") or 0) > 0 and st.answers_rev < st.spec_rev:
            qs = re.findall(r"^\s*(?:\d+\.|-)\s+(.*)$", _section(body, "Questions"), flags=re.M)
            return {"action": "ask", "job": self.job_id, "questions": qs or ["(see spec.md)"],
                    "spec": str(spec_path),
                    "note": "Answer from SDLC.md Answers/Learnings and CLAUDE.md where you can; ask the "
                            "human only what's left. Then: sdlc.py report <job> --answers <file>."}
        res = self.review_step("spec", st.spec_rev, self.dir / "spec-review" / f"r{st.spec_rev}")
        if res is not None:
            return res if isinstance(res, dict) else self._spec_changes(st, res)
        human = self.gate_is_human("spec", self.rule_ctx("spec"))
        if human and st.gates.get("spec") != "approved":
            if st.gates.get("spec") != "waiting":
                self.ev("gate", gate="spec", status="waiting")
            return self._wait("spec gate", f"Read {spec_path}, then /sdlc:approve {self.job_id} spec")
        self.ev("round", n=1, auto=False, reason="start", step="build")
        self.ev("stage", stage="build")
        return None

    def _spec_changes(self, st, failing):
        if st.spec_revisions >= self.cfg["max_rounds"]:
            return self.needs_human("spec-max", "spec", ", ".join(failing))
        self.ev("spec_rev", n=st.spec_rev + 1, counts=True, reason="review: " + ", ".join(failing))
        return None

    # build
    def do_build(self, st):
        rd = self.round_dir(st.round)
        build_md = rd / "build.md"
        if not build_md.exists():
            return self.spawn([(f"build-r{st.round}", AGENT_FOR["build"], "builder", {},
                                self._build_sections(st), build_md)], "build", st.round)
        head = git("rev-parse", self.meta["branch"], cwd=self.cfg.root)
        prev = st.head.get(st.round - 1) or git("rev-parse", self.base, cwd=self.cfg.root)
        if st.round == 1 and head == prev:
            build_md.unlink()
            self.ev("error", stage="build", detail="builder wrote build.md but made no commits")
            return None   # respawn (counts as an attempt)
        self.ev("head", round=st.round, sha=head)
        self.ev("stage", stage="verify")
        return None

    def _build_sections(self, st):
        s = [("Spec", (self.dir / "spec.md").read_text())]
        if st.round > 1:
            prev = self.round_dir(st.round - 1)
            for name in ("verify.md", "test.md"):
                f = prev / name
                if f.exists():
                    fr, _ = runners.read_artifact(f)
                    if (fr or {}).get("verdict") == "FAIL":
                        s.append((f"Previous round: {name}", f.read_text()))
            for f in sorted(prev.glob("review-*.md")):
                fr, _ = runners.read_artifact(f)
                if (fr or {}).get("verdict") == "CHANGES":
                    s.append((f"Previous round: {f.stem}", f.read_text()))
            if (prev / "build.md").exists():
                s.append(("Your previous build notes", (prev / "build.md").read_text()))
        fb = self.round_dir(st.round) / "feedback.md"
        if fb.exists():
            s.append(("Human feedback for this round", fb.read_text()))
        return s

    # verify
    def do_verify(self, st):
        rd = self.round_dir(st.round)
        vpath = rd / "verify.md"
        if not vpath.exists():
            ok = runners.verify(self.cfg, self.wt, vpath)
            self.ev("verdict", step="verify", round=st.round, verdict="PASS" if ok else "FAIL")
            if not ok:
                return self.new_round("verify failed", "verify")
        else:
            fr, _ = runners.read_artifact(vpath)
            if fr.get("verdict") != "PASS":
                return self.new_round("verify failed", "verify")
        tpath = rd / "test.md"
        if not tpath.exists():
            sections = [("Spec", (self.dir / "spec.md").read_text()),
                        ("Test paths you may write", ", ".join(self.cfg["test_paths"])),
                        ("Test command", (self.cfg["commands"] or {}).get("test") or "(none configured)")]
            prev = self.round_dir(st.round - 1) / "test.md"
            if prev.exists():
                sections.append(("Your previous test report", prev.read_text()))
            disputes = _section((rd / "build.md").read_text(), "Disputes")
            if disputes:
                sections.append(("Builder disputes (decide: withdraw or keep)", disputes))
            return self.spawn([(f"test-r{st.round}", AGENT_FOR["test"], "tester", {}, sections, tpath)],
                              "verify", st.round)
        fr, _ = runners.read_artifact(tpath)
        self.ev("verdict", step="test", round=st.round, verdict=fr.get("verdict"))
        if fr.get("verdict") != "PASS":
            return self.new_round("tests failed", "verify")
        self.ev("stage", stage="review")
        return None

    # review
    def do_review(self, st):
        res = self.review_step("review", st.round, self.round_dir(st.round))
        if res is None:
            self.ev("stage", stage="merge")
            return None
        if isinstance(res, dict):
            return res
        return self.new_round("review: " + ", ".join(res), "review")

    def review_step(self, step, rnd, folder):
        """Run a step's reviewers. Returns None (all clear), a list of blocking reviewers
        with CHANGES, or an action dict (spawn / wait / convert)."""
        reviewers = {n: r for n, r in self.cfg["reviewers"].items() if r.get("at", "review") == step}
        if not reviewers:
            return None
        st = self.state()
        sel = st.selected.get((step, rnd))
        if sel is None:
            sel = self.select(step, rnd, reviewers)
        chosen = [n for n, v in sel.items() if v["selected"]]
        folder.mkdir(parents=True, exist_ok=True)
        spawns, base_ref = [], self.base
        for name in chosen:
            spec = reviewers[name]
            out = folder / f"review-{name}.md"
            if out.exists():
                continue
            if "run" in spec:
                cwd = self.wt if step != "spec" else self.cfg.root
                r = runners.run_reviewer(name, spec, cwd, out)
                if r.get("error"):                       # not runnable: retry once, then a human
                    self.ev("retry", step=step, reviewer=name, detail=r["error"])
                    r = runners.run_reviewer(name, spec, cwd, out)
                    if r.get("error"):
                        return self.needs_human("adapter", step, r["error"])
                self.ev("verdict", step=step, round=rnd, reviewer=name, verdict=r["verdict"])
            elif "builtin" in spec:
                if step == "spec":
                    continue        # built-ins review code, not specs
                over = self.budget_check("review" if step == "review" else step)
                if over:
                    return over
                try:
                    r = runners.builtin_review(self.cfg, name, spec, self.wt, base_ref, out)
                except Exception as e:      # noqa: BLE001 - surfaced to the human
                    return self.needs_human("adapter", step, f"{name}: {e}")
                self._record_headless(f"builtin-{name}-{step}-{rnd}", step, name, r["usage"])
                if r.get("convert"):
                    return {"action": "convert", "job": self.job_id, "raw": r["convert"],
                            "output": str(out), "format": "reference/formats.md#reviews"}
            else:
                agent = spec.get("agent") or AGENT_FOR["reviewer"]
                sections = [("Review instructions", (self.cfg.root / spec["instructions"]).read_text())
                            if "instructions" in spec else None,
                            ("Spec", (self.dir / "spec.md").read_text())]
                if step != "spec":
                    sections.append(("How to see the change",
                                     f"Run `git diff {base_ref}...HEAD` in {self.wt}."))
                    build = self.round_dir(rnd) / "build.md"
                    if build.exists():
                        sections.append(("Builder notes (including any disputes)", build.read_text()))
                prev = folder.parent / (f"r{rnd - 1}" if step == "spec" else f"round-{rnd - 1}") / out.name
                if prev.exists():
                    sections.append(("Your previous review", prev.read_text()))
                spawns.append((f"{step}-{name}-r{rnd}", agent, "reviewer",
                               {"reviewer": name, "blocking": spec.get("blocking", True)},
                               [s for s in sections if s], out))
        if spawns:
            return self.spawn(spawns, "review" if step == "review" else step, rnd)
        failing = []
        for name in chosen:
            fr, _ = runners.read_artifact(folder / f"review-{name}.md")
            fr = fr or {}
            blocking = reviewers[name].get("blocking", True)
            if fr.get("verdict") == "CHANGES" and blocking:
                failing.append(name)
        return failing or None

    def select(self, step, rnd, reviewers):
        ctx = self.rule_ctx(step)
        st = self.state()
        prev = st.selected.get((step, rnd - 1)) if rnd > 1 else None
        sel, conditions = {}, {}
        for name, spec in reviewers.items():
            if prev is not None and step != "spec":
                # later rounds: only reviewers that asked for changes, plus `code`
                was = prev.get(name, {}).get("selected")
                fr, _ = runners.read_artifact(self.round_dir(rnd - 1) / f"review-{name}.md")
                again = bool(was) and ((fr or {}).get("verdict") == "CHANGES" or name == "code")
                sel[name] = {"selected": again, "reason": "re-review" if again else "passed last round"}
                continue
            hit = rules.evaluate(spec.get("when", "always" if "condition" not in spec else "never"), ctx)
            sel[name] = {"selected": hit, "reason": f"when: {spec.get('when', 'always')}" if hit else ""}
            if not hit and spec.get("condition"):
                conditions[name] = spec["condition"]
        if conditions:
            for name, (ok, why) in self.judge(conditions, step).items():
                sel[name] = {"selected": ok, "reason": f"condition: {why}"}
        for name, v in sel.items():
            self.ev("selection", step=step, round=rnd, reviewer=name, selected=v["selected"],
                    reason=v["reason"])
        return sel

    def judge(self, conditions, step):
        """Judge plain-language conditions with one cheap headless call (no orchestrator tokens)."""
        spec_text = (self.dir / "spec.md").read_text() if (self.dir / "spec.md").exists() else ""
        stat = "" if step == "spec" else git("diff", "--stat", f"{self.base}...{self.meta['branch']}",
                                              cwd=self.cfg.root, check=False)
        prompt = ("Decide for each condition whether it applies to this change. Reply with only JSON: "
                  '{"<name>": {"applies": true|false, "reason": "<short>"}}\n\nConditions:\n' +
                  json.dumps(conditions, indent=1) + "\n\nSpec:\n" + spec_text[:12000] +
                  ("\n\nDiff summary:\n" + stat[:4000] if stat else ""))
        try:
            text, use = runners.headless(prompt, self.cfg.root, model=self.cfg["models"].get("judge"))
            self._record_headless(f"judge-{step}-{self.state().round}", step, "judge", use)
            m = re.search(r"\{.*\}", text, flags=re.S)
            data = json.loads(m.group(0)) if m else {}
        except Exception as e:  # noqa: BLE001 - fail open: run the reviewer
            return {n: (True, f"could not judge ({e}); running it to be safe") for n in conditions}
        return {n: (bool((data.get(n) or {}).get("applies", True)),
                    (data.get(n) or {}).get("reason", "")) for n in conditions}

    def _record_headless(self, key, stage, name, use):
        self.ev("agent", key=key, agent=f"headless:{name}", stage=stage, data=use)
        self.store.record_usage(self.job_id, key, use.get("tokens", 0))

    # merge
    def do_merge(self, st):
        rd = self.round_dir(st.round)
        res = self.review_step("merge", st.round, rd)
        if isinstance(res, dict):
            return res
        if res:
            return self.new_round("merge review: " + ", ".join(res), "merge")
        if self.host.has_prs:
            if not self.meta.get("pr"):
                body = rd / "pr-body.md"
                body.write_text(self.pr_body(st))
                try:
                    r = self.host.call("open_pr", branch=self.meta["branch"], base=self.base,
                                       title=self.meta["title"], body_file=str(body), worktree=str(self.wt))
                except adapters.AdapterError as e:
                    return self.needs_human("adapter", "merge", str(e))
                self.meta = self.store.update_job(self.job_id, pr=r.get("pr"))
                self.ev("pr", url=r.get("pr"))
            status = self.host.call("pr_status", branch=self.meta["branch"])
            if status.get("merged"):
                return self.finish(st)
            if status["ci"] == "pending":
                return self._wait("waiting for CI", f"PR {self.meta['pr']}", waiter=self.waiter("ci"))
            if status["ci"] == "fail":
                return self.new_round("CI failed", "merge")
        if not st.skip_rules:
            problems = self.rule_check(st)
            if problems:
                return self.needs_human("rules", "merge", "; ".join(problems))
        human = self.gate_is_human("merge", self.rule_ctx("merge"))
        if self.host.has_prs:
            status = self.host.call("pr_status", branch=self.meta["branch"])
            if human and not status["approved"] and not st.skip_rules:
                return self._wait("merge gate: waiting for PR approval", f"Review {self.meta['pr']}",
                                  waiter=self.waiter("approval"))
            if status["behind"]:
                r = self.host.call("update_branch", branch=self.meta["branch"])
                if r.get("conflict"):
                    return self.new_round("merge conflict with " + self.base, "merge")
                return self._wait("branch updated; waiting for CI", self.meta["pr"], waiter=self.waiter("ci"))
            r = self.host.call("merge", branch=self.meta["branch"])
            if not r.get("merged"):
                return self._wait(f"merge refused: {r.get('reason')}", f"Check {self.meta['pr']}",
                                  waiter=self.waiter("approval"))
            return self.finish(st)
        # local host
        if human and st.gates.get("merge") != "approved":
            if st.gates.get("merge") != "waiting":
                self.ev("gate", gate="merge", status="waiting")
            return self._wait("merge gate", f"Review the branch {self.meta['branch']}, then "
                                            f"/sdlc:approve {self.job_id} merge")
        r = self.host.call("update_branch", worktree=str(self.wt), base=self.base)
        if r.get("conflict"):
            return self.new_round("rebase conflict with " + self.base, "merge")
        if not r.get("updated") and "up to date" not in r.get("reason", ""):
            return self.needs_human("adapter", "merge", "rebase failed: " + r.get("reason", ""))
        final = rd / "verify-final.md"
        if not final.exists() and not runners.verify(self.cfg, self.wt, final):
            final.rename(rd / "verify-final-failed.md")
            return self.new_round("verify failed after rebase", "merge")
        r = self.host.call("merge", branch=self.meta["branch"], base=self.base)
        if not r.get("merged"):
            return self._wait(f"cannot merge yet: {r.get('reason')}",
                              f"Fix that, then /sdlc:start {self.job_id}")
        return self.finish(st)

    def rule_check(self, st):
        problems = []
        changed = self.changed_paths()
        protected = [p for p in changed for g in self.cfg["protected_paths"] if rules.glob_match(g, p)]
        if protected:
            problems.append("protected paths changed: " + ", ".join(sorted(set(protected))))
        rd = self.round_dir(st.round)
        for name in ("verify.md", "test.md"):
            fr, _ = runners.read_artifact(rd / name)
            if (fr or {}).get("verdict") != "PASS":
                problems.append(f"{name} is not PASS")
        return problems

    def gate_is_human(self, gate, ctx):
        g = self.cfg["gates"].get(gate, "auto")
        if isinstance(g, dict):
            return rules.evaluate(g["human_when"], ctx)
        return g == "human"

    def waiter(self, what):
        return f"python3 {Path(__file__).resolve().parent.parent / 'sdlc.py'} wait {self.job_id} --for {what}"

    def pr_body(self, st):
        lines = [f"Job {self.job_id}: {self.meta['title']}", "", "## Spec", "",
                 (self.dir / "spec.md").read_text(), "", "## Reviews", ""]
        for f in sorted(self.round_dir(st.round).glob("review-*.md")):
            fr, _ = runners.read_artifact(f)
            lines.append(f"- {f.stem[7:]}: {(fr or {}).get('verdict')} "
                         f"({(fr or {}).get('important', 0)} important, {(fr or {}).get('nit', 0)} nit)")
        total, _ = self.job_usage()
        lines += ["", f"Tokens used so far: {total:,}", "", "_Opened by the sdlc plugin._"]
        return "\n".join(lines)

    def finish(self, st):
        decision = {"decision": "MERGE", "override": bool(st.overrides)}
        body = ["# Decision", "", f"Merged {self.meta['branch']} into {self.base}."]
        for o in st.overrides:
            body.append(f"- Override at {o.get('step')}: {o.get('reason')} (by {o.get('by')})")
        runners.write_artifact(self.dir / "decision.md", decision, "\n".join(body))
        self.ev("merged", branch=self.meta["branch"])
        self.ev("stage", stage="done")
        try:
            git("worktree", "remove", "--force", str(self.wt), cwd=self.cfg.root, check=False)
        except Exception:  # noqa: BLE001
            pass
        self.link_session(closing=True)
        return {"action": "done", "job": self.job_id, "merged": True,
                "decision": str(self.dir / "decision.md")}


def _section(md, title):
    m = re.search(rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", md or "", flags=re.M | re.S)
    return m.group(1).strip() if m else ""


# ----------------------------------------------------------- human actions --

def approve(cfg, job_id, gate, value=None, by=None):
    p = Pipeline(cfg, job_id)
    st = p.state()
    nh = st.needs_human
    by = by or __import__("os").environ.get("USER", "human")
    if gate == "budget":
        tokens = _parse_tokens(value or "+500k")
        p.ev("budget_extra", tokens=tokens, by=by)
        if nh and nh["reason"] == "budget":
            p.ev("resume", by=by)
        return f"budget raised by {tokens:,} tokens"
    if gate in ("spec", "merge") and st.gates.get(gate) == "waiting":
        p.ev("gate", gate=gate, status="approved", by=by)
        return f"{gate} gate approved"
    if not nh:
        raise ValueError(f"job {job_id} isn't waiting for '{gate}'")
    reason = nh["reason"]
    if reason == "spec-max":
        p.ev("override", step="spec", reason="accepted spec after max revisions", by=by)
        p.ev("resume", by=by)
        p.ev("gate", gate="spec", status="approved", by=by)
    elif reason in ("max-rounds", "rules", "disputed"):
        p.ev("override", step=nh.get("step"), reason=f"{reason}: {nh.get('detail', '')}", by=by,
             skip_rules=True)
        p.ev("resume", by=by)
        p.ev("gate", gate="merge", status="approved", by=by)
        p.ev("stage", stage="merge")
    else:
        p.ev("resume", by=by)
    return f"{reason} approved; run /sdlc:start {job_id} to continue"


def retry(cfg, job_id):
    p = Pipeline(cfg, job_id)
    if p.state().failed:
        p.ev("retry_job")
        return True
    return False


def cancel(cfg, job_id):
    p = Pipeline(cfg, job_id)
    p.ev("cancelled")
    git("worktree", "remove", "--force", str(p.wt), cwd=cfg.root, check=False)
    p.link_session(closing=True)


def rework(cfg, job_id, feedback):
    p = Pipeline(cfg, job_id)
    st = p.state()
    n = st.round + 1
    p.round_dir(n).mkdir(parents=True, exist_ok=True)
    (p.round_dir(n) / "feedback.md").write_text(feedback.strip() + "\n")
    if st.needs_human:
        p.ev("resume", by="rework")
    p.ev("round", n=n, auto=False, reason="rework", step="human")
    p.ev("stage", stage="build")
    p.ev("learning_candidate", source="rework", text=feedback.strip())
    return f"job {job_id}: round {n} started with your feedback"


def report_answers(cfg, job_id, answers_text):
    p = Pipeline(cfg, job_id)
    st = p.state()
    path = p.dir / "answers.md"
    prev = path.read_text() if path.exists() else ""
    path.write_text(prev + f"\n## Answers for revision {st.spec_rev}\n\n" + answers_text.strip() + "\n")
    p.ev("question", rev=st.spec_rev, answers=answers_text.strip()[:2000])
    p.ev("answers", rev=st.spec_rev)
    p.ev("spec_rev", n=st.spec_rev + 1, counts=False, reason="answers")
    return "answers recorded"


def _parse_tokens(v):
    v = str(v).strip().lstrip("+").lower().replace(",", "")
    mult = 1_000_000 if v.endswith("m") else 1_000 if v.endswith("k") else 1
    return int(float(v.rstrip("mk")) * mult)
