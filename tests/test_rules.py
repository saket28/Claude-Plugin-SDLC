import unittest
from helpers import ROOT  # noqa: F401
from sdlc import rules


class RulesTest(unittest.TestCase):
    def test_forms(self):
        ctx = {"risk": "medium", "touches_ui": False, "paths": ["src/auth/login.py", "README.md"]}
        self.assertTrue(rules.evaluate("always", ctx))
        self.assertFalse(rules.evaluate("never", ctx))
        self.assertFalse(rules.evaluate("touches-ui", ctx))
        self.assertTrue(rules.evaluate("risk>=medium", ctx))
        self.assertFalse(rules.evaluate("risk>=high", ctx))
        self.assertTrue(rules.evaluate("paths:[src/auth/**]", ctx))
        self.assertTrue(rules.evaluate("risk>=high or paths:[src/auth/**, x/**]", ctx))
        self.assertFalse(rules.evaluate("paths:[src/api/**]", ctx))

    def test_globs(self):
        self.assertTrue(rules.glob_match("**/*.test.ts", "a/b/c.test.ts"))
        self.assertTrue(rules.glob_match("**/*.test.ts", "c.test.ts"))
        self.assertTrue(rules.glob_match("review-*.md", "review-a11y.md"))
        self.assertFalse(rules.glob_match("review-*.md", "docs/review-a11y.md"))
        self.assertTrue(rules.glob_match(".github/workflows/**", ".github/workflows/ci.yml"))
        self.assertTrue(rules.glob_match("src/**", "src/api/**"))       # planned paths in specs

    def test_errors(self):
        for bad in ("sometimes", "risk>=huge", "paths:[]", ""):
            with self.assertRaises(rules.RuleError, msg=bad):
                rules.parse(bad)


if __name__ == "__main__":
    unittest.main()
