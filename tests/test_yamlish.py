import unittest
from helpers import ROOT  # noqa: F401
from sdlc import yamlish


class YamlishTest(unittest.TestCase):
    def test_nested_inline_and_lists(self):
        doc = """
commands:
  test: npm test   # comment
  lint: "npm run lint"
gates:
  merge: { human_when: "paths:[src/auth/**, src/payments/**]" }
reviewers:
  licenses: { run: "npx license-checker --failOn GPL", at: merge, when: "paths:[package.json]" }
budgets: { per_job: 1_500_000, warn_at: 0.8 }
protected_paths: ["**/migrations/**", SDLC.md]
checks:
  - { name: health, run: "curl -fsS https://x/health" }
  - name: errors
    run: ./check.sh
flag: true
empty:
"""
        d = yamlish.loads(doc)
        self.assertEqual(d["commands"], {"test": "npm test", "lint": "npm run lint"})
        self.assertEqual(d["gates"]["merge"]["human_when"], "paths:[src/auth/**, src/payments/**]")
        self.assertEqual(d["reviewers"]["licenses"]["at"], "merge")
        self.assertEqual(d["budgets"], {"per_job": 1500000, "warn_at": 0.8})
        self.assertEqual(d["protected_paths"], ["**/migrations/**", "SDLC.md"])
        self.assertEqual(d["checks"][1], {"name": "errors", "run": "./check.sh"})
        self.assertIs(d["flag"], True)
        self.assertIsNone(d["empty"])

    def test_rejects_unsupported(self):
        for bad in ("a: &x 1", "a: [1, 2", "a:\n\tb: 1", "a: 1\na: 2"):
            with self.assertRaises(yamlish.YamlError, msg=bad):
                yamlish.loads(bad)

    def test_round_trip(self):
        d = {"commands": {"test": "npm test"}, "paths": ["src/**"], "risk": "medium", "n": 3,
             "msg": "has: colon", "nested": {"a": {"b": [1, 2]}}}
        self.assertEqual(yamlish.loads(yamlish.dumps(d)), d)

    def test_frontmatter(self):
        fm, body = yamlish.parse_frontmatter("---\nverdict: PASS\n---\n\n# Hi\n")
        self.assertEqual(fm, {"verdict": "PASS"})
        self.assertIn("# Hi", body)
        self.assertEqual(yamlish.parse_frontmatter("no frontmatter"), ({}, "no frontmatter"))


if __name__ == "__main__":
    unittest.main()
