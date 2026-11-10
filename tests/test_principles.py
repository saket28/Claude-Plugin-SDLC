import shutil
from pathlib import Path
import unittest

from helpers import make_repo
from sdlc import config, pipeline, principles, yamlish
from sdlc.store import Store
from test_pipeline import Agents, base_md, drive

ARCH = "---\nsummary: Layering rules\napplies_to: [builder, reviewer]\n---\nUI never imports from db.\n"
UI = "---\nsummary: Spacing tokens\nwhen: touches-ui\n---\nUse the spacing scale.\n"
LISTED = ("---\nglobal:\n  principle:\n    list: [.claude/principles/architecture.md, .claude/principles/ui.md, "
          ".claude/principles/personas.md, .claude/principles/gone.md]\n---\n")


class PrinciplesTest(unittest.TestCase):
    def repo(self, sdlc=LISTED):
        files = {".claude/principles/architecture.md": ARCH, ".claude/principles/ui.md": UI}
        repo = make_repo(sdlc, files)
        principles.ensure_defaults(repo)             # personas etc. are comment-only placeholders
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        return repo, config.load(repo)

    def test_load_applicable_and_placeholders(self):
        repo, cfg = self.repo()
        specs = cfg.v("global.principle.list")
        self.assertEqual(list(specs), ["architecture", "ui", "personas"])
        self.assertEqual([p["name"] for p in cfg.principle_problems], ["gone"])       # missing file: a problem, not a crash
        names = lambda role, ctx=None: [Path(s["file"]).stem for s in principles.applicable(specs, role, ctx)]
        self.assertEqual(names("builder"), ["architecture", "ui"])                     # personas is a placeholder: never listed
        self.assertEqual(names("tester"), ["ui"])                                      # architecture is not for testers
        self.assertEqual(names("builder", {"risk": "low", "touches_ui": False, "paths": []}), ["architecture"])
        self.assertEqual(names("builder", {"risk": "low", "touches_ui": True, "paths": []}), ["architecture", "ui"])

    def test_bad_frontmatter_is_a_problem(self):
        repo, cfg = self.repo()
        (repo / ".claude/principles/bad.md").write_text("---\napplies_to: [wizard]\n---\nx\n")
        _, _, problems = principles.load_list(repo, [".claude/principles/bad.md"])
        self.assertIn("applies_to", problems[0]["error"])

    def test_save_and_delete_keep_sdlc_md_and_files_in_step(self):
        repo, cfg = self.repo("---\n---\n")
        rel = principles.save(cfg, "api", {"summary": "  REST   rules ", "applies_to": ["builder"], "when": "paths:[src/api/**]"},
                              "Use plural nouns.")
        self.assertEqual(rel, ".claude/principles/api.md")
        cfg = config.load(repo)
        self.assertEqual(cfg.raw["global"]["principle"]["list"], [rel])
        self.assertEqual(cfg.v("global.principle.list")["api"]["summary"], "REST rules")
        principles.save(cfg, "api", {"summary": "New"}, "Changed.")                    # edit in place, no duplicate
        cfg = config.load(repo)
        self.assertEqual(cfg.raw["global"]["principle"]["list"], [rel])
        self.assertEqual(cfg.v("global.principle.list")["api"]["body"], "Changed.")
        for bad in (("api!", {}, "x"), ("ok", {"applies_to": ["x"]}, "x"), ("ok", {"when": "sometimes"}, "x"), ("ok", {}, "  ")):
            with self.assertRaises(ValueError):
                principles.save(cfg, *bad)
        principles.delete(cfg, "api")
        self.assertNotIn("global", config.load(repo).raw)                              # the list was the only setting
        self.assertTrue((repo / rel).exists())                                          # the file is kept

    def test_starters_are_listed_once(self):
        repo = make_repo("---\n---\n")
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        cfg = config.load(repo)
        self.assertEqual(len(principles.add_starters(cfg)), 5)
        self.assertEqual(principles.add_starters(config.load(repo)), [])
        cfg = config.load(repo)
        self.assertEqual(list(cfg.v("global.principle.list")), list(principles.STARTERS))
        self.assertEqual(principles.applicable(cfg.v("global.principle.list"), "builder"), [])      # all placeholders

    def test_init_adds_the_starters_to_an_existing_sdlc_md_once(self):
        import contextlib, io, json, os, types
        from sdlc import cli
        repo = make_repo("---\nglobal: { command: { test: npm test } }\n---\n\n## Learnings\n")
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(repo)
        run = lambda: json.loads(self._init(cli, io, contextlib))
        self.assertEqual(len([c for c in run()["created"] if "principles" in c]), 5)
        self.assertEqual(list(config.load(repo).v("global.principle.list")), list(principles.STARTERS))
        (repo / ".claude/principles/personas.md").write_text("---\nsummary: Who\n---\nOur users are X.\n")
        _, listed = principles._listed(repo)
        principles._write_list(repo, [p for p in listed if "testing" not in p])    # the user removed one
        self.assertEqual([c for c in run()["created"] if "principles" in c], [])    # init doesn't re-add it
        self.assertNotIn("testing", config.load(repo).v("global.principle.list"))

    @staticmethod
    def _init(cli, io, contextlib):
        import types
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_init(types.SimpleNamespace(detect=False, write=None))
        return buf.getvalue()

    def test_principles_are_protected_from_agents(self):
        self.assertIn(".claude/principles/**", config.DEFAULTS["global"]["guardrail"]["protected_paths"])

    def test_briefs_point_each_agent_at_its_principles(self):
        files = {".claude/principles/architecture.md": ARCH, ".claude/principles/ui.md": UI}
        settings = yamlish.dumps({"release": {"gate": {"mode": "none"}},
                                  "global": {"command": {"test": "python3 -c 'print(1)'"}, "principle": {"list": list(files)}}})
        repo = make_repo(f"---\n{settings}\n---\n", files)
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        cfg = config.load(repo)
        job = Store(cfg).create_job("Say hello")["id"]
        drive(cfg, job, Agents())
        briefs = {p.stem: p.read_text() for p in (Store(cfg).job_dir(job) / "briefs").glob("*.md")}
        spec, build, test = briefs["spec-r1"], briefs["build-r1"], briefs["test-r1"]
        arch = f"`{repo}/.claude/principles/architecture.md`: Layering rules"
        self.assertIn("## Principles for this repo", build)
        self.assertIn(arch, build)
        self.assertNotIn(".claude/principles/ui.md", build)           # the spec says touches_ui: false
        self.assertIn("ui.md`: Spacing tokens", spec)                 # no spec yet, so `when` can't filter; roles still do
        self.assertNotIn("architecture.md", spec)                     # that one is for builder and reviewer only
        self.assertNotIn("architecture.md", test)                      # not for testers
        self.assertLess(build.index("Principles for this repo"), build.index("## Spec"))


if __name__ == "__main__":
    unittest.main()
