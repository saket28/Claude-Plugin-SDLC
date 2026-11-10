import shutil
import unittest

from helpers import make_repo
from sdlc import config, reviewers, yamlish

LIST = """\
---
review:
  reviewer:
    list: [.claude/reviewers/code.md, .claude/reviewers/ux.md, .claude/reviewers/gone.md]
---
"""


class ReviewerFilesTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(LIST, {
            ".claude/reviewers/code.md": "---\nbuiltin: code-review\nat: review\nwhen: always\n---\nExtra words.\n",
            ".claude/reviewers/ux.md": "---\nat: merge\nwhen: \"paths:[src/**, *.css]\"\nblocking: false\nmodel: haiku\n---\n# UX\nCheck focus order.\n"})
        self.addCleanup(shutil.rmtree, self.repo.parent, ignore_errors=True)

    def test_list_of_paths_loads_files_with_their_own_settings(self):
        cfg = config.load(self.repo)
        self.assertEqual(list(cfg.reviewers), ["code", "ux"])             # security not listed: not a reviewer
        ux = cfg.reviewers["ux"]
        self.assertEqual((ux["at"], ux["blocking"], ux["model"]), ("merge", False, "haiku"))
        self.assertEqual(ux["when"], "paths:[src/**, *.css]")
        self.assertEqual(reviewers.instructions_text(self.repo, ux), "# UX\nCheck focus order.")
        self.assertEqual(cfg.reviewers["code"]["body"], "Extra words.")
        self.assertTrue(any("gone.md" in w for w in cfg.warnings))             # missing file: a warning, not a crash

    def test_builtin_extra_instructions_ignore_html_comments(self):
        self.assertEqual(reviewers.extra_instructions({"body": "<!--\nnotes\n-->\n"}), "")
        self.assertEqual(reviewers.extra_instructions({"body": "<!-- n -->\nAlso check X."}), "Also check X.")
        _, body = reviewers.template("code")
        self.assertEqual(reviewers.extra_instructions({"body": body}), "")

    def test_save_delete_and_convert_round_trip(self):
        cfg = config.load(self.repo)
        reviewers.save(cfg, "a11y", {"at": "review", "when": "touches-ui", "model": "sonnet"}, "# a11y\nCheck alt text.")
        text = (self.repo / ".claude/reviewers/a11y.md").read_text()
        front, body = yamlish.parse_frontmatter(text)
        self.assertEqual((front["at"], front["blocking"], front["model"]), ("review", True, "sonnet"))
        cfg = config.load(self.repo)
        self.assertIn(".claude/reviewers/a11y.md", cfg.raw["review"]["reviewer"]["list"])
        with self.assertRaises(ValueError):
            reviewers.save(cfg, "bad", {"run": "x", "agent": "y"}, "")
        with self.assertRaises(ValueError):
            reviewers.save(cfg, "a11y", {"when": "sometimes maybe"}, "x")
        reviewers.delete(cfg, "a11y")
        self.assertNotIn(".claude/reviewers/a11y.md", config.load(self.repo).raw["review"]["reviewer"]["list"])
        self.assertTrue((self.repo / ".claude/reviewers/a11y.md").exists())     # the file is kept

    def test_convert_inline_reviewers(self):
        repo = make_repo("---\nreview:\n  reviewer:\n    list:\n      arch:\n        instructions: review-arch.md\n        at: spec\n      lic: { run: \"true\", blocking: false }\n      code: { when: never }\n---\n",
                         {"review-arch.md": "# Arch\nCheck layering.\n"})
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        res = reviewers.convert_legacy(config.load(repo))
        self.assertEqual(res["listed"][:2], [".claude/reviewers/code.md", ".claude/reviewers/security.md"])
        cfg = config.load(repo)
        self.assertEqual(cfg.reviewers["code"]["when"], "never")           # the tweak moved into the file
        self.assertEqual(cfg.reviewers["arch"]["at"], "spec")
        self.assertEqual(reviewers.instructions_text(repo, cfg.reviewers["arch"]), "# Arch\nCheck layering.")
        self.assertFalse(cfg.reviewers["lic"]["blocking"])


if __name__ == "__main__":
    unittest.main()
