"""End-to-end pipeline tests with simulated agents and a fake `claude` CLI.
No model is called; agents are replaced by functions that write their outputs."""

import json
import os
import re
import shutil
import unittest
from pathlib import Path

from helpers import make_repo, sh
from sdlc import config, learn, pipeline, yamlish
from sdlc.store import Store

SPEC = """---
revision: {rev}
risk: {risk}
touches_ui: false
change_type: feat
breaking: false
paths: ["app.py"]
open_questions: {q}
---
## Problem
Say hello.
## Acceptance criteria
1. AC-1 prints hello
## Questions
{questions}
"""

BASE_MD = """
---
commands:
  test: "python3 -c 'print(1)'"
gates: {{ merge: {merge_gate} }}
max_rounds: {max_rounds}
{extra}
---

## Pipeline rules
- Keep it small.
"""


class Agents:
    """Simulated agents. Tweak attributes to change behaviour."""

    def __init__(self):
        self.questions = False
        self.review_verdict = "PASS"
        self.test_verdict = "PASS"
        self.touch = "app.py"
        self.calls = []

    def run(self, action):
        for a in action["agents"]:
            brief = re.search(r"Read (\S+) first", a["prompt"]).group(1)
            front, body = yamlish.parse_frontmatter(Path(brief).read_text())
            self.calls.append(a["agent"])
            out = Path(front["output"])
            out.parent.mkdir(parents=True, exist_ok=True)
            wt = Path(front["worktree"])
            if a["agent"] == "sdlc:spec-writer":
                q = self.questions and "Answers to your questions" not in body
                out.write_text(SPEC.format(rev=front["revision"], risk="low", q=1 if q else 0,
                                           questions="1. Which greeting?" if q else ""))
            elif a["agent"] == "sdlc:builder":
                f = wt / self.touch
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(f"print('hello job {front['job']} round {front['round']}')\n")
                sh(f"git add -A && git commit -qm 'build {front['round']}'", wt)
                out.write_text(f"---\nround: {front['round']}\n---\n## Changes\nhello\n"
                               "## Learning candidates\n- The integration tests need the seed script first.\n")
            elif a["agent"] == "sdlc:tester":
                out.write_text(f"---\nverdict: {self.test_verdict}\n---\n## Coverage\nAC-1 ok\n")
            else:
                imp = 1 if self.review_verdict == "CHANGES" else 0
                out.write_text(f"---\nverdict: {self.review_verdict}\nimportant: {imp}\nnit: 0\n---\n")


def drive(cfg, job, agents, max_steps=40, answers="A: hello"):
    for _ in range(max_steps):
        act = pipeline.Pipeline(cfg, job, session_id="sess-1").next_action()
        if act["action"] == "spawn":
            agents.run(act)
        elif act["action"] == "ask":
            f = cfg.work_dir / "answers.txt"
            f.write_text(answers)
            pipeline.report_answers(cfg, job, f.read_text())
        else:
            return act
    raise AssertionError("pipeline did not settle")


class PipelineTest(unittest.TestCase):
    def setUp(self):
        os.environ.pop("FAKE_REVIEW", None)
        os.environ.pop("FAKE_JUDGE", None)

    def repo(self, merge_gate="human", max_rounds=2, extra="", files=None):
        repo = make_repo(BASE_MD.format(merge_gate=merge_gate, max_rounds=max_rounds, extra=extra), files)
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        return repo, config.load(repo)

    def new_job(self, cfg, text="Say hello"):
        return Store(cfg).create_job(text)["id"]

    def test_happy_path_local_merge(self):
        repo, cfg = self.repo()
        job = self.new_job(cfg)
        agents = Agents()
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "wait")
        self.assertIn("merge gate", act["reason"])
        print(pipeline.approve(cfg, job, "merge"))
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        self.assertEqual(agents.calls, ["sdlc:spec-writer", "sdlc:builder", "sdlc:tester"])
        self.assertIn("hello", (repo / "app.py").read_text())                 # fast-forwarded main
        d = Store(cfg).job_dir(job)
        for f in ("spec.md", "round-1/build.md", "round-1/verify.md", "round-1/test.md",
                  "round-1/review-code.md", "decision.md"):
            self.assertTrue((d / f).exists(), f)
        self.assertFalse((d / "round-1/review-security.md").exists())       # risk low: not selected
        usage = [e for e in Store(cfg).events(job) if e["type"] == "agent"]
        self.assertTrue(any(e["agent"] == "headless:code" for e in usage))
        self.assertGreater(Store(cfg).month_tokens(), 0)

    def test_review_changes_hit_max_rounds_then_override(self):
        repo, cfg = self.repo(merge_gate="auto", max_rounds=1)
        job = self.new_job(cfg)
        agents = Agents()
        agents.review_verdict = "CHANGES"
        os.environ["FAKE_REVIEW"] = "important"
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "wait")
        self.assertIn("max-rounds", act["reason"])
        self.assertEqual(agents.calls.count("sdlc:builder"), 2)                # 1 + max_rounds
        pipeline.approve(cfg, job, "override")
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        front, _ = yamlish.parse_frontmatter((Store(cfg).job_dir(job) / "decision.md").read_text())
        self.assertTrue(front["override"])

    def test_failed_tests_start_a_new_round(self):
        repo, cfg = self.repo(merge_gate="auto")
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict = "FAIL"
        act = drive(cfg, job, agents)
        self.assertIn("max-rounds", act["reason"])
        self.assertEqual(agents.calls.count("sdlc:tester"), 3)

    def test_spec_questions_are_asked_then_answered(self):
        repo, cfg = self.repo(merge_gate="auto")
        job = self.new_job(cfg)
        agents = Agents()
        agents.questions = True
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        self.assertEqual(agents.calls.count("sdlc:spec-writer"), 2)
        self.assertTrue(any(e["type"] == "question" for e in Store(cfg).events(job)))

    def test_budget_stops_then_approve_reruns_stage(self):
        repo, cfg = self.repo(merge_gate="auto", extra="budgets: { per_job: 500 }")
        job = self.new_job(cfg)
        agents = Agents()
        st = Store(cfg)
        p = pipeline.Pipeline(cfg, job)
        act = p.next_action()                      # spawns the spec-writer
        agents.run(act)
        st.event(job, "agent", key="a1", agent="sdlc:spec-writer", stage="spec", data={"tokens": 900})
        act = drive(cfg, job, agents)
        self.assertIn("budget", act["reason"])
        self.assertEqual(agents.calls, ["sdlc:spec-writer"])
        pipeline.approve(cfg, job, "budget", "+10k")
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        self.assertIn("sdlc:builder", agents.calls)

    def test_protected_path_change_is_caught_at_merge(self):
        repo, cfg = self.repo(merge_gate="auto", extra='protected_paths: ["config/**"]')
        job = self.new_job(cfg)
        agents = Agents()
        agents.touch = "config/secret.yml"          # as if written through Bash
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "wait")
        self.assertIn("rules", act["reason"])
        self.assertIn("config/secret.yml", act["detail"])

    def test_dirty_checkout_waits_and_keeps_user_changes(self):
        repo, cfg = self.repo(merge_gate="auto")
        job = self.new_job(cfg)
        agents = Agents()
        (repo / "notes.txt").write_text("x")
        sh("git add notes.txt && git commit -qm notes", repo)
        (repo / "notes.txt").write_text("my unsaved work")
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "wait")
        self.assertIn("uncommitted", act["reason"])
        self.assertEqual((repo / "notes.txt").read_text(), "my unsaved work")

    def test_condition_reviewer_at_spec_step(self):
        (Path("/tmp") / "x").exists()
        repo, cfg = self.repo(merge_gate="auto", files={"review-arch.md": "Check the architecture."},
                              extra='reviewers:\n  arch: { instructions: review-arch.md, at: spec, '
                                    'condition: "adds a service" }')
        os.environ["FAKE_JUDGE"] = "yes"
        cfg = config.load(repo)
        job = self.new_job(cfg)
        agents = Agents()
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        self.assertIn("sdlc:reviewer", agents.calls)
        sel = [e for e in Store(cfg).events(job) if e["type"] == "selection" and e["reviewer"] == "arch"]
        self.assertTrue(sel[0]["selected"])

    def test_run_reviewer_at_merge_blocks(self):
        repo, cfg = self.repo(merge_gate="auto", max_rounds=0,
                              extra="reviewers:\n  lic: { run: \"exit 3\", at: merge }")
        job = self.new_job(cfg)
        act = drive(cfg, job, Agents())
        self.assertIn("max-rounds", act["reason"])

    def test_native_code_review_output_is_parsed_and_classified(self):
        # live run 1: /code-review returns a bare list without severities
        repo, cfg = self.repo(merge_gate="auto", max_rounds=0)
        os.environ["FAKE_REVIEW"] = "native"
        job = self.new_job(cfg)
        act = drive(cfg, job, Agents())
        self.assertIn("max-rounds", act["reason"])            # the important finding blocks
        front, body = yamlish.parse_frontmatter(
            (Store(cfg).job_dir(job) / "round-1/review-code.md").read_text())
        self.assertEqual((front["important"], front["nit"]), (1, 1))
        self.assertIn("crashes on empty input", body)
        usage = [e for e in Store(cfg).events(job) if e.get("agent") == "headless:code"]
        self.assertEqual(usage[0]["data"]["tokens"], 160 + 160)   # review + classification, via modelUsage

    def test_dirty_tracked_files_in_worktree_are_not_a_conflict(self):
        # live run 1: tracked .pyc files changed by test runs made `git rebase` refuse
        repo, cfg = self.repo(merge_gate="auto", files={"cache.bin": "v1"})
        job = self.new_job(cfg)
        agents = Agents()
        real_run = agents.run

        def run(action):
            real_run(action)
            if action["agents"][0]["agent"] == "sdlc:builder":
                wt = Path(Store(cfg).load_job(job)["worktree"])
                (wt / "cache.bin").write_text("changed by a test run")
                (repo / "other.txt").write_text("main moved on")      # so a rebase is really needed
                sh("git add other.txt && git commit -qm 'main moves'", repo)
        agents.run = run
        act = drive(cfg, job, agents)
        self.assertIn("main moved on", (repo / "other.txt").read_text())
        self.assertIn("hello", (repo / "app.py").read_text())             # job merged on top of main
        self.assertEqual(act["action"], "done", act)
        self.assertEqual(agents.calls.count("sdlc:builder"), 1)

    def test_max_rounds_message_lists_every_round_reason(self):
        repo, cfg = self.repo(merge_gate="auto", max_rounds=1)
        agents = Agents()
        agents.test_verdict = "FAIL"
        act = drive(cfg, self.new_job(cfg), agents)
        self.assertIn("round 2: tests failed", act["detail"])
        self.assertIn("then: tests failed", act["detail"])

    def test_deleting_a_job_folder_breaks_nothing(self):
        repo, cfg = self.repo(merge_gate="auto")
        st = Store(cfg)
        j1 = self.new_job(cfg)
        drive(cfg, j1, Agents())
        tokens = st.month_tokens()
        shutil.rmtree(st.job_dir(j1))
        self.assertEqual(st.jobs(), [])
        self.assertEqual(st.month_tokens(), tokens)                         # ledger keeps the spend
        j2 = self.new_job(cfg)
        self.assertEqual(int(j2), int(j1) + 1)                              # number never reused
        self.assertEqual(drive(cfg, j2, Agents())["action"], "done")

    def test_retro_adds_pending_learnings_and_briefs_use_them(self):
        repo, cfg = self.repo(merge_gate="auto")
        job = self.new_job(cfg)
        drive(cfg, job, Agents())
        res = learn.retro(cfg, job)
        self.assertEqual(len(res["added"]), 1)
        self.assertIn("seed script", res["added"][0])
        self.assertIn("Run the seed script", (repo / "SDLC.md").read_text())  # local host: committed
        self.assertEqual(learn.retro(cfg, job), {"skipped": "retro already ran"})
        cfg2 = config.load(repo)
        j2 = self.new_job(cfg2, "Second")
        agents = Agents()
        agents.run(pipeline.Pipeline(cfg2, j2).next_action())           # spec-writer
        act = pipeline.Pipeline(cfg2, j2).next_action()                 # builder
        self.assertEqual(act["agents"][0]["agent"], "sdlc:builder")
        build_brief = next((Store(cfg2).job_dir(j2) / "briefs").glob("build-*.md")).read_text()
        self.assertIn("seed script", build_brief)                           # builder sees the learning


if __name__ == "__main__":
    unittest.main()
