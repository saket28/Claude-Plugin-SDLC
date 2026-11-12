"""End-to-end pipeline tests with simulated agents and a fake `claude` CLI.
No model is called; agents are replaced by functions that write their outputs."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import make_repo, sh
from sdlc import config, learn, pipeline, thread, yamlish
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

def base_md(merge_gate="human", max_rounds=3, extra=None):
    """SDLC.md for tests. `extra` is a nested dict of settings merged over the base ones."""
    settings = config.deep_merge({"release": {"gate": {"mode": merge_gate}},
                                  "global": {"pipeline": {"max_rounds": max_rounds},
                                             "command": {"test": "python3 -c 'print(1)'"}}}, extra or {})
    return "---\n" + yamlish.dumps(settings) + "\n---\n\n## Pipeline rules\n- Keep it small.\n"


class Agents:
    """Simulated agents. Tweak attributes to change behaviour."""

    def __init__(self):
        self.questions = False
        self.review_verdict = "PASS"
        self.test_verdict = "PASS"
        self.touch = "app.py"
        self.dispute = False         # the builder disputes findings from round 2 on
        self.upheld = 0              # the tester and reviewers keep what the builder disputed
        self.nits = 0                # Nit findings the reviewers report
        self.ruling = "keep"         # what the spec-writer rules when asked
        self.calls = []
        self.briefs = []             # every brief the agents were given

    def run(self, action):
        for a in action["agents"]:
            brief = re.search(r"Read (\S+) first", a["prompt"]).group(1)
            front, body = yamlish.parse_frontmatter(Path(brief).read_text())
            self.calls.append(a["agent"])
            self.briefs.append((a["agent"], body))
            out = Path(front["output"])
            out.parent.mkdir(parents=True, exist_ok=True)
            wt = Path(front["worktree"])
            if a["agent"] == "sdlc:spec-writer" and front.get("mode") != "ruling":
                q = self.questions and "Answers to your questions" not in body
                out.write_text(SPEC.format(rev=front["revision"], risk="low", q=1 if q else 0,
                                           questions="1. Which greeting?" if q else ""))
            elif a["agent"] == "sdlc:builder":
                f = wt / self.touch
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(f"print('hello job {front['job']} round {front['round']}')\n")
                sh(f"git add -A && git commit -qm 'build {front['round']}'", wt)
                mine = re.findall(r"^(I\d+) \[blocking\].*\n\s+([\w-]+) -> builder", body, flags=re.M)
                verb = "pushback: The check is wrong, the spec never asks for it." if self.dispute else "fixed: done."
                replies = "".join(f"- {i} -> {to}, {verb}\n" for i, to in mine)
                out.write_text(f"---\nround: {front['round']}\n---\n## Changes\nhello\n"
                               f"{'## Comments' + chr(10) + replies if replies else ''}"
                               "## Learning candidates\n- The integration tests need the seed script first.\n")
            elif a["agent"] == "sdlc:tester":
                asked = re.search(r"^(I\d+) \[", body, flags=re.M)
                if self.test_verdict == "FAIL" and self.upheld and asked:
                    c = f"- {asked.group(1)} -> builder, pushback: The spec does ask for it (AC-1).\n"
                elif self.test_verdict == "FAIL":
                    c = "- new -> builder, feedback, blocking: greeting is wrong\n"
                elif asked:
                    c = f"- {asked.group(1)} -> builder, accept\n"
                else:
                    c = ""
                out.write_text(f"---\nverdict: {self.test_verdict}\n---\n## Coverage\nAC-1 ok\n"
                               f"{'## Comments' + chr(10) + c if c else ''}")
            elif a["agent"] == "sdlc:spec-writer" and front.get("mode") == "ruling":
                ids = re.findall(r"^(I\d+) \[", body, flags=re.M)
                out.write_text("---\nround: 1\n---\n## Comments\n" +
                               "".join(f"- {i} -> director, ruling: {self.ruling}: because\n" for i in ids))
            else:
                imp = 1 if self.review_verdict == "CHANGES" else 0
                asked = re.search(r"^(I\d+) \[", body, flags=re.M)
                who = f"{front['reviewer']}-reviewer"
                if imp and self.upheld and asked:
                    c = f"- {asked.group(1)} -> builder, pushback: Still a bug: the input is not validated.\n"
                elif imp:
                    c = "- new -> builder, feedback, blocking: input is not validated\n"
                elif asked:
                    c = f"- {asked.group(1)} -> builder, accept\n"
                else:
                    c = ""
                c += "- new -> builder, feedback, nit: naming\n" * self.nits
                out.write_text(f"---\nverdict: {self.review_verdict}\nimportant: {imp}\nnit: {self.nits}\n---\n"
                               f"{'## Comments' + chr(10) + c if c else ''}")


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

    def repo(self, merge_gate="human", max_rounds=3, extra=None, files=None):
        repo = make_repo(base_md(merge_gate, max_rounds, extra), files)
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

    def _merged_commits(self, extra):
        repo, cfg = self.repo(merge_gate="none", extra=extra)
        job = self.new_job(cfg)
        self.assertEqual(drive(cfg, job, Agents())["action"], "done")
        msgs = sh("git log --format=%s main", repo).split("\n")
        branch = Store(cfg).load_job(job)["branch"]
        return repo, [m for m in msgs if m], branch

    def test_merge_strategy_squash_is_default_and_one_commit(self):
        repo, msgs, _ = self._merged_commits({})
        self.assertEqual(len(msgs), 2)                       # init + one squash commit
        self.assertIn("(job-", msgs[0])

    def test_merge_strategy_rebase_keeps_commits_and_merge_makes_merge_commit(self):
        repo, msgs, branch = self._merged_commits({"release": {"merge": {"strategy": "rebase", "delete_branch": False}}})
        tip = subprocess.run(["git", "rev-parse", "main", branch], cwd=repo, capture_output=True, text=True).stdout.split()
        self.assertEqual(tip[0], tip[1])                      # main is the job branch, nothing rewritten
        repo, msgs, branch = self._merged_commits({"release": {"merge": {"strategy": "merge"}}})
        parents = subprocess.run(["git", "log", "-1", "--format=%P", "main"], cwd=repo, capture_output=True, text=True).stdout.split()
        self.assertEqual(len(parents), 2)

    def test_spawn_carries_the_configured_model(self):
        repo, cfg = self.repo(extra={"spec": {"agent": {"model": "haiku"}}, "review": {"reviewer": {"list": {"arch": {"run": "true"}}}}})
        job = self.new_job(cfg)
        act = pipeline.Pipeline(cfg, job).next_action()
        self.assertEqual(act["action"], "spawn")
        self.assertEqual(act["agents"][0]["model"], "haiku")

    def test_review_issues_use_up_their_exchanges_then_a_human_decides(self):
        repo, cfg = self.repo(merge_gate="none", extra={"global": {"pipeline": {"max_exchanges": 2}}})
        job = self.new_job(cfg)
        agents = Agents()
        agents.review_verdict, agents.dispute, agents.upheld = "CHANGES", True, 1
        os.environ["FAKE_REVIEW"] = "important"
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "wait")
        self.assertIn("stalemate", act["reason"])
        self.assertEqual(agents.calls.count("sdlc:builder"), 3)                # the build, then 2 exchanges
        pipeline.approve(cfg, job, "override")
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        front, _ = yamlish.parse_frontmatter((Store(cfg).job_dir(job) / "decision.md").read_text())
        self.assertTrue(front["override"])

    def test_stalemate_when_the_tester_holds_out_against_a_dispute(self):
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict, agents.dispute, agents.upheld = "FAIL", True, 1
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "wait")
        self.assertIn("stalemate", act["reason"])
        self.assertEqual(agents.calls.count("sdlc:builder"), 3)              # the build, then max_exchanges (2) exchanges
        st = pipeline.Pipeline(cfg, job).state()
        self.assertEqual((st.needs_human["reason"], st.stage), ("stalemate", "review"))
        self.assertIn("I1 greeting is wrong", st.needs_human["detail"])
        self.assertIn("still open after 2 exchange(s)", st.needs_human["detail"])
        view = (Store(cfg).job_dir(job) / "conversation.md").read_text()      # the readable copy
        self.assertIn("[ ] I1 - greeting is wrong - blocking", view)
        self.assertIn("- builder - tester - pushback: The check is wrong", view)
        self.assertIn("- director - human - help: still open after 2 exchange(s)", view)
        pipeline.approve(cfg, job, "override")                                # the human sides with the builder
        self.assertEqual(drive(cfg, job, agents)["action"], "done")
        view = (Store(cfg).job_dir(job) / "conversation.md").read_text()
        self.assertIn("[x] I1", view)
        self.assertIn("- human - director - override", view)

    def test_stalemate_when_a_reviewer_holds_out_against_a_dispute(self):
        repo, cfg = self.repo(merge_gate="none", files={"review-arch.md": "Check the architecture."},
                              extra={"review": {"reviewer": {"list": {"code": {"when": "never"},
                                                                     "arch": {"instructions": "review-arch.md"}}}}})
        job = self.new_job(cfg)
        agents = Agents()
        agents.review_verdict, agents.dispute, agents.upheld = "CHANGES", True, 1
        os.environ["FAKE_REVIEW"] = "important"
        act = drive(cfg, job, agents)
        self.assertIn("stalemate", act["reason"])
        self.assertEqual(pipeline.Pipeline(cfg, job).state().stage, "review")

    def _escalated(self, ruling):
        """A tester that fails round 1 then asks the spec writer to rule; returns (cfg, job, action)."""
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict = "FAIL"
        for _ in range(10):                                                  # until the tester has failed round 1
            act = pipeline.Pipeline(cfg, job).next_action()
            agents.run(act)
            if (Store(cfg).job_dir(job) / "round-1" / "test.md").exists():
                break
        pipeline.Pipeline(cfg, job).ingest(1)
        thread._append(Store(cfg).job_dir(job), {"kind": "comment", "id": "I1", "from": "tester",
                       "to": "spec-writer", "type": "pushback", "text": "AC-1 contradicts AC-2", "detail": ""})
        act = pipeline.Pipeline(cfg, job).next_action()
        self.assertEqual(act["action"], "spawn")
        self.assertEqual(act["agents"][0]["agent"], "sdlc:spec-writer")
        self.assertIn("spec-ruling", act["agents"][0]["output"])
        agents.ruling = ruling
        agents.run(act)
        return cfg, job, pipeline.Pipeline(cfg, job).next_action()

    def test_a_spec_ruling_keep_closes_the_issue_and_the_job_goes_on(self):
        cfg, job, act = self._escalated("keep")
        i = thread.load(Store(cfg).job_dir(job))[0]["I1"]
        self.assertEqual((i["state"], i["lines"][-1]["text"]), ("closed", "closed, spec upheld"))
        st = pipeline.Pipeline(cfg, job).state()
        self.assertEqual((st.stage, st.r, st.skip_build), ("verify", 2, True))   # all closed: the code is reviewed again

    def test_a_spec_ruling_change_goes_to_the_human(self):
        cfg, job, act = self._escalated("change")
        self.assertIn("stalemate", act["reason"])
        self.assertIn("spec", pipeline.Pipeline(cfg, job).state().needs_human["detail"])

    def test_a_nit_pushback_is_not_pursued_and_never_reaches_a_human(self):
        d = tempfile.mkdtemp()
        job_dir = Path(d)
        (job_dir / "round-1").mkdir()
        (job_dir / "round-1" / "review-code.md").write_text(
            "---\nverdict: PASS\n---\n## Comments\n- new -> builder, feedback, nit: naming\n")
        known = {"builder", "tester", "code-reviewer", "director"}
        thread.ingest(job_dir, job_dir / "round-1", 1, known)
        (job_dir / "round-2").mkdir()
        (job_dir / "round-2" / "build.md").write_text("---\nround: 2\n---\n## Comments\n- I1 -> code-reviewer, pushback: house style\n")
        thread.ingest(job_dir, job_dir / "round-2", 2, known)
        i = thread.load(job_dir)[0]["I1"]
        self.assertEqual((i["state"], i["flag"]), ("closed", "nit"))
        shutil.rmtree(d)

    def _flip_tests_after_first_run(self, agents):
        real = agents.run

        def run(action):
            real(action)
            if action["agents"][0]["agent"] == "sdlc:tester":
                agents.test_verdict = "PASS"
        agents.run = run

    def test_max_exchanges_is_a_setting(self):
        repo, cfg = self.repo(merge_gate="none", extra={"global": {"pipeline": {"max_exchanges": 1}}})
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict, agents.dispute, agents.upheld = "FAIL", True, 1
        act = drive(cfg, job, agents)
        self.assertIn("stalemate", act["reason"])
        self.assertEqual(agents.calls.count("sdlc:builder"), 2)              # the build, then one exchange

    def test_resolved_issues_lead_to_another_review_round_then_the_merge(self):
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict = "FAIL"
        self._flip_tests_after_first_run(agents)
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        self.assertEqual(agents.calls.count("sdlc:builder"), 2)              # the build, then the exchange that fixed it
        rounds = [(e["r"], e["x"], bool(e.get("skip_build"))) for e in Store(cfg).events(job) if e["type"] == "round"]
        self.assertEqual(rounds, [(1, 0, False), (1, 1, False), (2, 0, True)])  # the second review round builds nothing
        view = (Store(cfg).job_dir(job) / "conversation.md").read_text()
        self.assertIn("[x] I1", view)
        self.assertIn("director: closed, accepted by tester", view)

    def test_a_human_ruling_is_the_final_exchange_and_closes_the_issue(self):
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict, agents.dispute, agents.upheld = "FAIL", True, 1
        self.assertIn("stalemate", drive(cfg, job, agents)["reason"])
        pipeline.rework(cfg, job, "do it the tester's way")
        agents.test_verdict, agents.dispute, agents.upheld = "PASS", False, 0
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        view = (Store(cfg).job_dir(job) / "conversation.md").read_text()
        self.assertIn("- human - director - rework: do it the tester's way", view)
        self.assertIn("[x] I1", view)
        self.assertIn("do it the tester's way", [b for a, b in agents.briefs if a == "sdlc:builder"][-1])

    def test_a_human_ruling_can_change_the_spec_first(self):
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict, agents.dispute, agents.upheld = "FAIL", True, 1
        drive(cfg, job, agents)
        pipeline.rework(cfg, job, "AC-1 should say hello twice", spec=True)
        agents.test_verdict, agents.dispute, agents.upheld = "PASS", False, 0
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        spec_briefs = [b for a, b in agents.briefs if a == "sdlc:spec-writer"]
        self.assertIn("Human ruling", spec_briefs[-1])
        self.assertEqual(Store(cfg).events(job)[-1]["type"], "stage")
        self.assertIn("revision: 2", (Store(cfg).job_dir(job) / "spec.md").read_text())

    def test_no_stalemate_without_a_dispute(self):
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict, agents.upheld = "FAIL", 1                        # the builder only ever says "fixed"
        act = drive(cfg, job, agents)
        self.assertIn("stalemate", act["reason"])                            # the tester still disagrees after 2 exchanges
        self.assertEqual(agents.calls.count("sdlc:builder"), 3)

    def test_failed_tests_start_a_new_round(self):
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict = "FAIL"
        act = drive(cfg, job, agents)
        self.assertIn("stalemate", act["reason"])
        self.assertEqual(agents.calls.count("sdlc:tester"), 3)               # the pass, then the tester joins each exchange

    def test_later_briefs_carry_only_the_open_items(self):
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        agents = Agents()
        agents.test_verdict = "FAIL"
        drive(cfg, job, agents)
        builds = [b for a, b in agents.briefs if a == "sdlc:builder"]
        testers = [b for a, b in agents.briefs if a == "sdlc:tester"]
        self.assertNotIn("Open threads addressed to you", builds[0])
        self.assertIn("Open threads addressed to you", builds[1])
        self.assertIn("I1 [blocking] greeting is wrong", builds[1])
        self.assertNotIn("AC-1 ok", builds[1])                               # the coverage table is not resent
        self.assertNotIn("AC-1 ok", testers[1])

    def test_spec_questions_are_asked_then_answered(self):
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        agents = Agents()
        agents.questions = True
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "done", act)
        self.assertEqual(agents.calls.count("sdlc:spec-writer"), 2)
        self.assertTrue(any(e["type"] == "question" for e in Store(cfg).events(job)))

    def test_budget_stops_then_approve_reruns_stage(self):
        repo, cfg = self.repo(merge_gate="none", extra={"global": {"budget": {"per_job": 500}}})
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
        repo, cfg = self.repo(merge_gate="none", extra={"global": {"guardrail": {"protected_paths": ["config/**"]}}})
        job = self.new_job(cfg)
        agents = Agents()
        agents.touch = "config/secret.yml"          # as if written through Bash
        act = drive(cfg, job, agents)
        self.assertEqual(act["action"], "wait")
        self.assertIn("rules", act["reason"])
        self.assertIn("config/secret.yml", act["detail"])

    def test_dirty_checkout_waits_and_keeps_user_changes(self):
        repo, cfg = self.repo(merge_gate="none")
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
        repo, cfg = self.repo(merge_gate="none", files={"review-arch.md": "Check the architecture."},
                              extra={"review": {"reviewer": {"list": {"arch": {"instructions": "review-arch.md", "at": "spec",
                                                                              "condition": "adds a service"}}}}})
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
        repo, cfg = self.repo(merge_gate="none", max_rounds=1,
                              extra={"review": {"reviewer": {"list": {"lic": {"run": "exit 3", "at": "merge"}}}}})
        job = self.new_job(cfg)
        act = drive(cfg, job, Agents())
        self.assertIn("stalemate", act["reason"])                            # the command reviewer still fails after 2 exchanges

    def test_native_code_review_output_is_parsed_and_classified(self):
        # live run 1: /code-review returns a bare list without severities
        repo, cfg = self.repo(merge_gate="none", max_rounds=1)
        os.environ["FAKE_REVIEW"] = "native"
        job = self.new_job(cfg)
        act = drive(cfg, job, Agents())
        self.assertIn("stalemate", act["reason"])             # the important finding blocks
        front, body = yamlish.parse_frontmatter(
            (Store(cfg).job_dir(job) / "round-1/review-code.md").read_text())
        self.assertEqual((front["important"], front["nit"]), (1, 1))
        self.assertIn("crashes on empty input", body)
        usage = [e for e in Store(cfg).events(job) if e.get("agent") == "headless:code"]
        self.assertEqual(usage[0]["data"]["tokens"], 160 + 160)   # review + classification, via modelUsage

    def test_dirty_tracked_files_in_worktree_are_not_a_conflict(self):
        # live run 1: tracked .pyc files changed by test runs made `git rebase` refuse
        repo, cfg = self.repo(merge_gate="none", files={"cache.bin": "v1"})
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

    def test_the_human_gate_lists_every_open_issue(self):
        repo, cfg = self.repo(merge_gate="none", max_rounds=2)
        agents = Agents()
        agents.test_verdict = "FAIL"
        act = drive(cfg, self.new_job(cfg), agents)
        self.assertIn("greeting is wrong", act["detail"])
        self.assertIn("still open after 2 exchange(s)", act["detail"])

    def test_deleting_a_job_folder_breaks_nothing(self):
        repo, cfg = self.repo(merge_gate="none")
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

    def test_retro_writes_learnings_to_sdlc_md_and_briefs_use_them(self):
        repo, cfg = self.repo(merge_gate="none")
        job = self.new_job(cfg)
        drive(cfg, job, Agents())
        res = learn.retro(cfg, job)
        self.assertEqual(len(res["added"]), 1)
        self.assertIn("seed script", res["added"][0])
        self.assertIn("Run the seed script", (repo / "SDLC.md").read_text())  # written to SDLC.md, not committed
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
