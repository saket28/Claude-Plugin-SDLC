"""The director: comment parsing, the rules, and how threads read."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sdlc import thread  # noqa: E402

KNOWN = {"builder", "tester", "spec-writer", "director", "code-reviewer"}
RAISE = "---\nverdict: CHANGES\n---\n## Comments\n- new -> builder, feedback, blocking: Bad redirect\n    Tabs bypass it.\n- new -> builder, feedback, nit: Tiny\n"


class ParseTest(unittest.TestCase):
    def test_comments_have_a_target_type_severity_and_detail(self):
        cs = thread.parse_comments(RAISE)
        self.assertEqual([(c["id"], c["to"], c["type"], c["severity"]) for c in cs],
                         [("new", "builder", "feedback", "blocking"), ("new", "builder", "feedback", "nit")])
        self.assertEqual((cs[0]["text"], cs[0]["detail"]), ("Bad redirect", "Tabs bypass it."))

    def test_replies_may_have_no_text(self):
        cs = thread.parse_comments("## Comments\n- i3 -> builder, accept\n- I4 -> spec-writer, pushback: the spec is silent\n")
        self.assertEqual([(c["id"], c["to"], c["type"], c["text"]) for c in cs],
                         [("I3", "builder", "accept", ""), ("I4", "spec-writer", "pushback", "the spec is silent")])


class DirectorTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)

    def round(self, n, **files):
        rd = self.d / f"round-{n}"
        rd.mkdir(exist_ok=True)
        for name, text in files.items():
            (rd / (name[:-3].replace("_", "-") + ".md" if name.endswith("_md") else name.replace("_", "-") + ".md")).write_text(text)
        thread.ingest(self.d, rd, n, KNOWN)
        return thread.load(self.d)[0]

    def test_new_issues_are_numbered_and_addressed(self):
        iss = self.round(1, review_code=RAISE)
        self.assertEqual([(i["id"], i["severity"], i["by"]) for i in iss.values()],
                         [("I1", "blocking", "code-reviewer"), ("I2", "nit", "code-reviewer")])
        self.assertEqual([i["id"] for i in thread.awaiting(self.d, "builder")], ["I1", "I2"])

    def test_an_unknown_addressee_is_redirected(self):
        self.round(1, review_code="---\nverdict: CHANGES\n---\n## Comments\n- new -> nobody, feedback, blocking: x\n")
        self.assertEqual([i["id"] for i in thread.awaiting(self.d, "builder")], ["I1"])

    def test_the_raisers_accept_closes_it(self):
        self.round(1, review_code=RAISE)
        self.round(2, build_md="---\nround: 2\n---\n## Comments\n- I1 -> code-reviewer, fixed: done\n")
        iss = self.round(2, review_code="---\nverdict: PASS\n---\n## Comments\n- I1 -> builder, accept\n")
        self.assertEqual((iss["I1"]["state"], iss["I1"]["lines"][-1]["text"]), ("closed", "closed, accepted by code-reviewer"))

    def test_silence_closes_it_as_no_reply(self):
        self.round(1, review_code=RAISE)
        self.round(2, build_md="---\nround: 2\n---\n## Comments\n- I1 -> code-reviewer, fixed: done\n")
        iss = self.round(2, review_code="---\nverdict: PASS\n---\n")
        self.assertEqual((iss["I1"]["state"], iss["I1"]["flag"]), ("closed", "no_reply"))

    def test_pushbacks_go_back_and_forth_until_the_pipeline_escalates(self):
        self.round(1, review_code=RAISE)
        self.round(2, build_md="---\nround: 2\n---\n## Comments\n- I1 -> code-reviewer, pushback: validated upstream\n")
        iss = self.round(2, review_code="---\nverdict: CHANGES\n---\n## Comments\n- I1 -> builder, pushback: not on this path\n")
        self.assertEqual(iss["I1"]["state"], "open")                         # the director leaves the count to the pipeline
        thread.escalate(self.d, "still open after 2 exchange(s)")
        self.assertIn("I1 Bad redirect", thread.human_detail(self.d))
        self.assertEqual([i["id"] for i in thread.awaiting_human(self.d)], ["I1"])

    def test_a_new_issue_in_an_exchange_is_still_taken(self):
        self.round(1, review_code=RAISE)
        rd = self.d / "round-2"
        rd.mkdir()
        (rd / "review-code.md").write_text("---\nverdict: CHANGES\n---\n## Comments\n- new -> builder, feedback, blocking: late find\n")
        thread.ingest(self.d, rd, 2, KNOWN, r=1, exchange=True)
        self.assertEqual(sorted(thread.load(self.d)[0]), ["I1", "I2", "I3"])  # nothing a checker raises is dropped

    def test_a_command_reviewer_answers_with_its_verdict(self):
        self.round(1, review_code=RAISE)
        rd = self.d / "round-2"
        rd.mkdir()
        (rd / "build.md").write_text("---\nround: 2\n---\n## Comments\n- I1 -> code-reviewer, fixed: done\n")
        thread.ingest(self.d, rd, 2, KNOWN, r=1, exchange=True)
        (rd / "review-code.md").write_text("---\nverdict: PASS\nengine: run\n---\n")
        thread.ingest(self.d, rd, 2, KNOWN, r=1, exchange=True)
        self.assertEqual(thread.load(self.d)[0]["I1"]["lines"][-1]["text"], "closed, accepted by code-reviewer")

    def test_a_ruling_closes_what_it_reopened_even_if_nobody_answers(self):
        self.round(1, review_code=RAISE)
        thread.escalate(self.d, "still open")
        thread.human_decision(self.d, "rework", "do it my way")
        self.assertEqual(thread.load(self.d)[0]["I1"]["state"], "open")
        thread.close_ruled(self.d)
        self.assertEqual(thread.load(self.d)[0]["I1"]["lines"][-1]["text"], "closed, human ruling applied")

    def test_issues_are_grouped_by_round(self):
        self.round(1, review_code=RAISE)
        rd = self.d / "round-2"
        rd.mkdir()
        (rd / "review-code.md").write_text("---\nverdict: CHANGES\n---\n## Comments\n- new -> builder, feedback, blocking: another\n")
        thread.ingest(self.d, rd, 2, KNOWN, r=2)
        text = thread.render(self.d)
        self.assertLess(text.index("r1"), text.index("I1"))
        self.assertLess(text.index("I2"), text.index("r2"))
        self.assertLess(text.index("r2"), text.index("I3"))

    def test_the_human_overrides_or_reworks(self):
        for kind, state in (("override", "closed"), ("rework", "open")):
            self.d = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, self.d)
            self.round(1, review_code=RAISE)
            thread.escalate(self.d, "rounds used up")
            self.assertEqual(thread.load(self.d)[0]["I1"]["state"], "human")
            self.assertEqual(thread.load(self.d)[0]["I2"]["state"], "open")      # a nit is never escalated
            thread.human_decision(self.d, kind, "remove the redirect")
            i = thread.load(self.d)[0]["I1"]
            self.assertEqual(i["state"], state)
            self.assertIn("human", [x["from"] for x in i["lines"]])
            if kind == "rework":
                self.assertEqual(thread.awaiting(self.d, "builder")[0]["id"], "I1")

    def test_a_checker_failing_with_no_comments_still_makes_an_issue(self):
        iss = self.round(1, review_code="---\nverdict: CHANGES\n---\nbody\n")
        self.assertEqual(iss["I1"]["severity"], "blocking")

    def test_severity_changes_only_by_the_raiser(self):
        self.round(1, review_code=RAISE)
        self.round(2, build_md="---\nround: 2\n---\n## Comments\n- I1 -> code-reviewer, pushback: only a nit\n")
        iss = self.round(2, review_code="---\nverdict: PASS\n---\n## Comments\n- I1 -> builder, feedback, nit: fair\n")
        self.assertEqual(iss["I1"]["severity"], "nit")

    def test_readable_copy(self):
        self.round(1, review_code=RAISE)
        text = thread.render(self.d)
        self.assertIn("r1\n\n  [ ] I1 - Bad redirect - blocking\n      - code-reviewer - builder - feedback: Bad redirect", text)


if __name__ == "__main__":
    unittest.main()
