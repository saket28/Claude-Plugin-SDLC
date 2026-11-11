import contextlib
import io
import json
import os
import shutil
import types
import unittest

from helpers import make_repo
from sdlc import cli, config
from sdlc.store import Store


class BacklogIdsTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo("---\n---\n")
        self.addCleanup(shutil.rmtree, self.repo.parent, ignore_errors=True)
        self.cfg = config.load(self.repo)
        self.st = Store(self.cfg)

    def start(self, *what):
        old = os.getcwd()
        os.chdir(self.repo)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                cli.cmd_start(types.SimpleNamespace(what=list(what)))
        finally:
            os.chdir(old)
        return json.loads(buf.getvalue())

    def test_a_card_gets_the_number_its_job_will_have(self):
        a = self.st.add_backlog("First idea", "Details here")
        b = self.st.add_backlog("Second idea")
        self.assertEqual((a, b), ("1", "2"))
        self.assertEqual(self.st.create_job("Typed directly")["id"], "3")           # numbers are never reused
        self.assertEqual([i["id"] for i in self.st.backlog_items()], ["1", "2"])

    def test_start_by_number_starts_that_card_as_that_job(self):
        self.st.add_backlog("First idea", "Details here")
        wanted = self.st.add_backlog("Second idea", "More details")
        r = self.start(wanted)
        self.assertEqual((r["job"], r["created"]), ("2", True))
        meta = self.st.load_job("2")
        self.assertEqual(meta["title"], "Second idea")                               # not the number
        self.assertIn("More details", meta["source"])
        self.assertEqual([i["title"] for i in self.st.backlog_items()], ["First idea"])   # only that card left the backlog

    def test_start_by_title_or_with_no_argument_still_works(self):
        self.st.add_backlog("First idea")
        self.st.add_backlog("Second idea")
        self.assertEqual(self.st.load_job(self.start("Second idea")["job"])["title"], "Second idea")
        self.assertEqual(self.st.load_job(self.start()["job"])["title"], "First idea")

    def test_older_hash_ids_start_the_card_too(self):
        (self.repo / ".sdlc").mkdir(exist_ok=True)
        (self.repo / ".sdlc" / "backlog.md").write_text("- [ ] Add observability\n  @id: be2cd3e\n  Grafana please\n")
        r = self.start("be2cd3e")
        self.assertEqual(self.st.load_job(r["job"])["title"], "Add observability")

    def test_an_id_that_matches_nothing_never_creates_a_job_named_after_it(self):
        for arg in ("5", "job-5", "be2cd3e"):
            r = self.start(arg)
            self.assertIn("no job or backlog item", r["error"])
        self.assertEqual(self.st.jobs(), [])
        self.assertEqual(self.start("Add a login page")["created"], True)             # a real description still starts a job

    def test_existing_jobs_are_continued_by_number(self):
        job = self.st.create_job("Real job")["id"]
        self.assertEqual(self.start(job), {"job": job, "continuing": True})


if __name__ == "__main__":
    unittest.main()
