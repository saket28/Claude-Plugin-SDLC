"""What the repo-facing settings actually change."""
import contextlib
import io
import json
import os
import shutil
import types
import unittest

from helpers import make_repo, sh
from sdlc import cli, config, pipeline, runners, schema
from sdlc.store import Store
from test_pipeline import Agents, base_md, drive


class SettingsBehaviourTest(unittest.TestCase):
    def repo(self, extra=None, gate="none", rounds=3, files=None):
        repo = make_repo(base_md(gate, rounds, extra), files)
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        return repo, config.load(repo)

    def job(self, cfg, text="Say hello"):
        return Store(cfg).create_job(text)

    # ---- validation comes from the schema ---------------------------------
    def test_invalid_values_are_rejected_with_the_allowed_ones(self):
        cases = {
            ("release", "gate", "mode"): "auto", ("spec", "gate", "mode"): {"human_when": "x"},
            ("global", "pipeline", "critical_thinking"): 6, ("spec", "agent", "model"): "gpt",
            ("global", "repo", "branch_name"): "{nope}", ("release", "merge", "message"): "{title} {x}",
            ("global", "learning", "on"): ["cancelled"], ("test", "verify", "order"): ["deploy"],
            ("release", "pr", "draft"): "yes", ("global", "command", "timeout"): 0,
            ("global", "dashboard", "stall_after"): "soon",
        }
        for (tab, sec, key), value in cases.items():
            with self.assertRaises(config.ConfigError, msg=f"{tab}.{sec}.{key}={value!r}"):
                self.repo({tab: {sec: {key: value}}})
        with self.assertRaises(config.ConfigError) as e:
            self.repo({"release": {"gate": {"mode": "auto"}}})
        self.assertIn("none, human", str(e.exception))

    def test_a_value_added_to_the_schema_is_accepted(self):
        f = schema.fields()["spec.agent.model"]
        self.assertEqual(tuple(f["options"]), schema.options("spec.agent.model"))
        self.assertIn("haiku", f["options"])
        self.repo({"spec": {"agent": {"model": "haiku"}}})           # no second list to update

    def test_reviewer_values_are_checked_against_the_schema_too(self):
        with self.assertRaises(config.ConfigError):
            self.repo({"review": {"reviewer": {"list": {"x": {"run": "true", "model": "gpt"}}}}})
        with self.assertRaises(config.ConfigError):
            self.repo({"review": {"reviewer": {"list": {"x": {"run": "true", "critical_thinking": 9}}}}})

    # ---- commands ---------------------------------------------------------
    def test_verify_order_and_stop_on_failure(self):
        for stop, expect in ((True, ["lint"]), (False, ["lint", "test"])):
            repo, cfg = self.repo({"global": {"command": {"lint": "false", "build": "true", "test": "false"}},
                                   "test": {"verify": {"order": ["lint", "test"], "stop_on_failure": stop}}})
            ok = runners.verify(cfg, repo, repo / "v.md")
            front, _ = runners.read_artifact(repo / "v.md")
            self.assertEqual((ok, front["commands"]), (False, expect))           # build is not in the order: skipped

    def test_command_timeout(self):
        repo, cfg = self.repo({"global": {"command": {"test": "sleep 5", "timeout": 1}}})
        self.assertFalse(runners.verify(cfg, repo, repo / "v.md"))
        self.assertIn("timed out after 1s", (repo / "v.md").read_text())

    # ---- pipeline ---------------------------------------------------------
    def test_max_exchanges_bounds_the_reply_cycles_per_round(self):
        for cap in (1, 3):
            repo, cfg = self.repo({"global": {"pipeline": {"max_exchanges": cap}}})
            job = self.job(cfg)["id"]
            agents = Agents()
            agents.test_verdict, agents.dispute, agents.upheld = "FAIL", True, 1
            act = drive(cfg, job, agents)
            self.assertIn("stalemate", act["reason"])
            self.assertEqual(agents.calls.count("sdlc:builder"), 1 + cap)        # the build, then `cap` exchanges

    def test_max_rounds_bounds_the_full_review_passes(self):
        repo, cfg = self.repo({"global": {"pipeline": {"max_rounds": 1}}})
        job = self.job(cfg)["id"]
        agents = Agents()
        agents.test_verdict = "FAIL"
        self._pass_after_first_failure(agents)
        self.assertEqual(drive(cfg, job, agents)["action"], "done")
        self.assertEqual([e["r"] for e in Store(cfg).events(job["id"] if isinstance(job, dict) else job) if e["type"] == "round"], [1, 1])   # no second review round

    def _pass_after_first_failure(self, agents):
        real = agents.run

        def run(action):
            real(action)
            if action["agents"][0]["agent"] == "sdlc:tester":
                agents.test_verdict = "PASS"
        agents.run = run

    def test_agent_attempts(self):
        for attempts, spawns in ((1, 1), (3, 3)):
            repo, cfg = self.repo({"global": {"pipeline": {"agent_attempts": attempts}}})
            job = self.job(cfg)["id"]
            acts = [pipeline.Pipeline(cfg, job).next_action() for _ in range(spawns + 1)]    # the agent never writes
            self.assertEqual([a["action"] for a in acts], ["spawn"] * spawns + ["failed"])
            self.assertIn(f"{attempts} attempt", acts[-1]["reason"])

    def test_nits_block_only_when_asked(self):
        files = {"review-arch.md": "Check it."}
        base = {"review": {"reviewer": {"list": {"code": {"when": "never"}, "arch": {"instructions": "review-arch.md"}}}}}
        for block, expect in ((False, "done"), (True, "wait")):
            repo, cfg = self.repo({**base, "review": {**base["review"], "severity": {"nits_block": block}}},
                                  rounds=1, files=files)
            agents = Agents()
            agents.nits = 2
            act = drive(cfg, self.job(cfg)["id"], agents)
            self.assertEqual(act["action"], expect, act)

    def test_spec_format_sections_reach_the_spec_writer(self):
        repo, cfg = self.repo({"spec": {"format": {"sections": ["Rollout plan", "Risks"]}}})
        job = self.job(cfg)["id"]
        pipeline.Pipeline(cfg, job).next_action()
        brief = (Store(cfg).job_dir(job) / "briefs" / "spec-r1.md").read_text()
        self.assertIn("Extra sections this repo requires", brief)
        self.assertIn("- Rollout plan", brief)

    def test_branch_name_template(self):
        repo, cfg = self.repo({"global": {"repo": {"branch_prefix": "feat/", "branch_name": "{prefix}{id}-{slug}"}}})
        self.assertEqual(self.job(cfg, "Add a Login page!")["branch"], "feat/1-add-a-login-page")
        repo, cfg = self.repo()
        self.assertEqual(self.job(cfg)["branch"], "sdlc/job-1")
        self.assertEqual(Store(cfg).create_job("x", job_id="ABC-9")["branch"], "sdlc/ABC-9")   # a ticket id is the name

    def test_merge_message_and_branch_and_worktree_cleanup(self):
        repo, cfg = self.repo({"release": {"merge": {"message": "feat: {title} [{id}]"}}})
        job = self.job(cfg)
        self.assertEqual(drive(cfg, job["id"], Agents())["action"], "done")
        self.assertEqual(sh("git log -1 --format=%s main", repo), "feat: Say hello [1]")
        self.assertEqual(sh(f"git branch --list {job['branch']}", repo), "")            # deleted by default
        self.assertFalse(os.path.exists(job["worktree"]))                                # and its checkout removed
        repo, cfg = self.repo({"release": {"merge": {"delete_branch": False}, "cleanup": {"worktree": False}}})
        job = self.job(cfg)
        drive(cfg, job["id"], Agents())
        self.assertIn(job["branch"], sh(f"git branch --list {job['branch']}", repo))
        self.assertTrue(os.path.exists(job["worktree"]))

    def test_retro_runs_only_for_the_configured_outcomes(self):
        for on, ran in (([], False), (["done"], True)):
            repo, cfg = self.repo({"global": {"learning": {"on": on}}})
            job = self.job(cfg)["id"]
            drive(cfg, job, Agents())
            old = os.getcwd()
            os.chdir(repo)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.cmd_next(types.SimpleNamespace(job=job, session=None))
            finally:
                os.chdir(old)
            self.assertEqual(any(e["type"] == "retro" for e in Store(cfg).events(job)), ran)


if __name__ == "__main__":
    unittest.main()
