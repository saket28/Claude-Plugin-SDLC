import unittest
from helpers import ROOT  # noqa: F401
from sdlc import configedit, yamlish

DOC = """---
# team settings
global:
  command:
    test: npm test   # keep this comment
  budget: { per_job: 1500000 }
release:
  gate: { mode: human }
---

## Pipeline rules
- Keep it small.
"""


def fm(text):
    return yamlish.parse_frontmatter(text)[0]


class ConfigEditTest(unittest.TestCase):
    def test_changes_only_touched_keys_and_keeps_comments(self):
        out = configedit.apply(DOC, {"global.command.lint": "npm run lint", "spec.gate.mode": "human"})
        self.assertIn("# team settings", out)
        self.assertIn("test: npm test   # keep this comment", out)
        self.assertIn("- Keep it small.", out)
        d = fm(out)
        self.assertEqual(d["global"]["command"], {"test": "npm test", "lint": "npm run lint"})
        self.assertEqual(d["spec"], {"gate": {"mode": "human"}})
        self.assertEqual(d["release"], {"gate": {"mode": "human"}})

    def test_default_values_are_removed(self):
        out = configedit.apply(DOC, {"release.gate.mode": "human"})        # human is the default
        self.assertNotIn("release", fm(out))
        out = configedit.apply(DOC, {"global.budget.per_job": 2000000})
        self.assertNotIn("budget", fm(out)["global"])
        self.assertEqual(fm(out)["global"]["command"], {"test": "npm test"})
        out = configedit.apply(DOC, {"spec.agent.critical_thinking": None})  # unset = inherit, nothing to write
        self.assertNotIn("spec", fm(out))

    def test_deep_and_new_keys(self):
        out = configedit.apply(DOC, {"global.budget.orchestrate": 800000, "global.pipeline.max_rounds": 4})
        d = fm(out)
        self.assertEqual(d["global"]["budget"], {"per_job": 1500000, "orchestrate": 800000})
        self.assertEqual(d["global"]["pipeline"]["max_rounds"], 4)

    def test_remove_key(self):
        out = configedit.apply(DOC, {"global.command.test": None})
        self.assertNotIn("test", fm(out))

    def test_hash(self):
        self.assertEqual(configedit.file_hash(DOC), configedit.file_hash(DOC))
        self.assertNotEqual(configedit.file_hash(DOC), configedit.file_hash(DOC + " "))

    def test_reviewer_kind_and_path_can_be_changed(self):
        doc = "---\nreview:\n  reviewer:\n    list:\n      ux:\n        instructions: a.md\n        at: review\n---\n"
        out = configedit.apply(doc, {"review.reviewer.list.ux.instructions": None, "review.reviewer.list.ux.agent": None,
                                     "review.reviewer.list.ux.run": "npm run ux"})
        fm = yamlish.loads(yamlish.split_frontmatter(out)[0])
        self.assertEqual(fm["review"]["reviewer"]["list"]["ux"], {"at": "review", "run": "npm run ux"})
        out = configedit.apply(doc, {"review.reviewer.list.ux.instructions": "b.md", "review.reviewer.list.ux.agent": None,
                                     "review.reviewer.list.ux.run": None})
        self.assertEqual(yamlish.loads(yamlish.split_frontmatter(out)[0])["review"]["reviewer"]["list"]["ux"]["instructions"], "b.md")

    def test_new_reviewer_file_is_created_only_under_claude_reviewers(self):
        import tempfile
        from pathlib import Path
        from sdlc import config
        root = Path(tempfile.mkdtemp())
        (root / "SDLC.md").write_text("---\n---\n")
        cfg = config.Config(root, config.DEFAULTS, "", {})
        change = {"review.reviewer.list.a11y": {"instructions": ".claude/reviewers/a11y.md"}}
        with self.assertRaises(config.ConfigError):                     # file missing, nothing to create
            configedit.save(cfg, change, None)
        configedit.save(cfg, change, None, files={".claude/reviewers/a11y.md": "# a11y\n"})
        self.assertEqual((root / ".claude/reviewers/a11y.md").read_text(), "# a11y\n")
        with self.assertRaises(configedit.EditError):
            configedit.save(cfg, {"x": 1}, None, files={"../evil.md": "x"})
        self.assertIn(".claude/reviewers/**", config.DEFAULTS["global"]["guardrail"]["protected_paths"])


if __name__ == "__main__":
    unittest.main()
