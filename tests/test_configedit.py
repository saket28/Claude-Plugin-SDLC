import unittest
from helpers import ROOT  # noqa: F401
from sdlc import configedit, yamlish

DOC = """---
# team settings
commands:
  test: npm test   # keep this comment
gates: { merge: human }
budgets: { per_job: 1500000 }
---

## Pipeline rules
- Keep it small.
"""


def fm(text):
    return yamlish.parse_frontmatter(text)[0]


class ConfigEditTest(unittest.TestCase):
    def test_changes_only_touched_keys_and_keeps_comments(self):
        out = configedit.apply(DOC, {"commands.lint": "npm run lint", "gates.spec": "human"})
        self.assertIn("# team settings", out)
        self.assertIn("test: npm test   # keep this comment", out)
        self.assertIn("- Keep it small.", out)
        d = fm(out)
        self.assertEqual(d["commands"], {"test": "npm test", "lint": "npm run lint"})
        self.assertEqual(d["gates"], {"merge": "human", "spec": "human"})

    def test_default_values_are_removed(self):
        out = configedit.apply(DOC, {"gates.merge": "human"})        # human is the default
        self.assertNotIn("gates", fm(out))
        out = configedit.apply(DOC, {"budgets.per_job": 2000000})
        self.assertNotIn("budgets", fm(out))

    def test_deep_and_new_keys(self):
        out = configedit.apply(DOC, {"budgets.per_stage.build": 800000, "max_rounds": 4})
        d = fm(out)
        self.assertEqual(d["budgets"]["per_stage"]["build"], 800000)
        self.assertEqual(d["budgets"]["per_job"], 1500000)
        self.assertEqual(d["max_rounds"], 4)

    def test_remove_key(self):
        out = configedit.apply(DOC, {"commands.test": None})
        self.assertNotIn("commands", fm(out))

    def test_hash(self):
        self.assertEqual(configedit.file_hash(DOC), configedit.file_hash(DOC))
        self.assertNotEqual(configedit.file_hash(DOC), configedit.file_hash(DOC + " "))

    def test_reviewer_kind_and_path_can_be_changed(self):
        doc = "---\nreviewers:\n  ux:\n    instructions: a.md\n    at: review\n---\n"
        out = configedit.apply(doc, {"reviewers.ux.instructions": None, "reviewers.ux.agent": None,
                                     "reviewers.ux.run": "npm run ux"})
        fm = yamlish.loads(yamlish.split_frontmatter(out)[0])
        self.assertEqual(fm["reviewers"]["ux"], {"at": "review", "run": "npm run ux"})
        out = configedit.apply(doc, {"reviewers.ux.instructions": "b.md", "reviewers.ux.agent": None,
                                     "reviewers.ux.run": None})
        self.assertEqual(yamlish.loads(yamlish.split_frontmatter(out)[0])["reviewers"]["ux"]["instructions"], "b.md")

    def test_new_reviewer_file_is_created_only_under_claude_reviewers(self):
        import tempfile
        from pathlib import Path
        from sdlc import config
        root = Path(tempfile.mkdtemp())
        (root / "SDLC.md").write_text("---\n---\n")
        cfg = config.Config(root, config.DEFAULTS, "", {})
        change = {"reviewers.a11y": {"instructions": ".claude/reviewers/a11y.md"}}
        with self.assertRaises(config.ConfigError):                     # file missing, nothing to create
            configedit.save(cfg, change, None)
        configedit.save(cfg, change, None, files={".claude/reviewers/a11y.md": "# a11y\n"})
        self.assertEqual((root / ".claude/reviewers/a11y.md").read_text(), "# a11y\n")
        with self.assertRaises(configedit.EditError):
            configedit.save(cfg, {"x": 1}, None, files={"../evil.md": "x"})
        self.assertIn(".claude/reviewers/**", config.DEFAULTS["protected_paths"])


if __name__ == "__main__":
    unittest.main()
