import shutil
import unittest

from helpers import make_repo, sh
from sdlc import cli, config

SDLC = """\
---
review:
  reviewer:
    list:
      a11y: {instructions: review-a11y.md}
      off: {run: "true", when: never}
      arch: {instructions: review-a11y.md, at: spec}
      lic: {run: "true", at: merge, blocking: false}
---
"""


class ReviewPlanTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(SDLC, {"review-a11y.md": "# a11y\n"})

    def tearDown(self):
        shutil.rmtree(self.repo.parent, ignore_errors=True)

    def test_refuses_on_base_branch(self):
        with self.assertRaisesRegex(RuntimeError, "base branch"):
            cli.review_plan(config.load(self.repo))

    def test_lists_all_reviewers_and_changed_files(self):
        sh("git checkout -qb feat/x && echo 1 > new.py && git add -A && git commit -qm x", self.repo)
        plan = cli.review_plan(config.load(self.repo))
        self.assertEqual(plan["files"], ["new.py"])
        names = {r["name"]: r for r in plan["reviewers"]}
        self.assertEqual(set(names), {"code", "security", "a11y", "lic"})   # security runs despite risk rule
        self.assertEqual(names["a11y"]["kind"], "instructions")
        self.assertFalse(names["lic"]["blocking"])
        self.assertEqual({s["name"] for s in plan["skipped"]}, {"off", "arch"})
        only = cli.review_plan(config.load(self.repo), ["a11y"])
        self.assertEqual([r["name"] for r in only["reviewers"]], ["a11y"])
        with self.assertRaisesRegex(RuntimeError, "unknown reviewer"):
            cli.review_plan(config.load(self.repo), ["nope"])
        self.assertTrue(plan["report_dir"].endswith(".sdlc/reviews/feat-x"))


if __name__ == "__main__":
    unittest.main()
