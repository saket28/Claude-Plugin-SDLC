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
        out = configedit.apply(DOC, {"budgets.per_stage.build": 800000, "max_rounds": 3})
        d = fm(out)
        self.assertEqual(d["budgets"]["per_stage"]["build"], 800000)
        self.assertEqual(d["budgets"]["per_job"], 1500000)
        self.assertEqual(d["max_rounds"], 3)

    def test_remove_key(self):
        out = configedit.apply(DOC, {"commands.test": None})
        self.assertNotIn("commands", fm(out))

    def test_hash(self):
        self.assertEqual(configedit.file_hash(DOC), configedit.file_hash(DOC))
        self.assertNotEqual(configedit.file_hash(DOC), configedit.file_hash(DOC + " "))


if __name__ == "__main__":
    unittest.main()
