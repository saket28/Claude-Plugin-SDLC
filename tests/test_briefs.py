import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import make_repo
from sdlc import briefs, config, yamlish


def cfg_for(settings):
    repo = make_repo("---\n" + yamlish.dumps(settings) + "\n---\n" if settings else "")
    return repo, config.load(repo)


def brief(cfg, role="builder", meta=None):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "b.md"
        briefs.write(cfg, p, {"job": 1, **(meta or {})}, role, [("Spec", "do x")])
        return p.read_text()


class StanceTest(unittest.TestCase):
    def cfg(self, settings=None):
        repo, cfg = cfg_for(settings)
        self.addCleanup(shutil.rmtree, repo.parent, ignore_errors=True)
        return cfg

    def test_default_is_one_and_stages_inherit_it(self):
        self.assertEqual(config.DEFAULTS["global"]["pipeline"]["critical_thinking"], 1)
        cfg = self.cfg()
        self.assertEqual([cfg.critical_thinking(r) for r in config.ROLE_STAGE], [1, 1, 1, 1])
        self.assertIn("critical thinking 1 of 5", brief(cfg))

    def test_zero_adds_nothing(self):
        self.assertNotIn("Working stance", brief(self.cfg({"global": {"pipeline": {"critical_thinking": 0}}})))

    def test_each_level_has_its_own_block_before_the_spec(self):
        seen = set()
        for lvl in range(1, 6):
            text = brief(self.cfg({"global": {"pipeline": {"critical_thinking": lvl}}}))
            self.assertIn(f"## Working stance (critical thinking {lvl} of 5)", text)
            self.assertLess(text.index("Working stance"), text.index("## Spec"))
            seen.add(briefs.STANCE[lvl])
        self.assertEqual(len(seen), 5)

    def test_stage_level_beats_global_and_only_for_its_agent(self):
        cfg = self.cfg({"global": {"pipeline": {"critical_thinking": 1}}, "test": {"agent": {"critical_thinking": 4}}})
        self.assertIn("critical thinking 4 of 5", brief(cfg, "tester"))
        self.assertIn("critical thinking 1 of 5", brief(cfg, "builder"))

    def test_a_stage_can_turn_it_off(self):
        cfg = self.cfg({"global": {"pipeline": {"critical_thinking": 3}}, "implement": {"agent": {"critical_thinking": 0}}})
        self.assertNotIn("Working stance", brief(cfg, "builder"))
        self.assertIn("critical thinking 3 of 5", brief(cfg, "spec-writer"))

    def test_reviewer_level_beats_review_stage_and_is_not_written_to_the_brief(self):
        cfg = self.cfg({"review": {"agent": {"critical_thinking": 2}}})
        text = brief(cfg, "reviewer", {"critical_thinking": 5})
        self.assertIn("critical thinking 5 of 5", text)
        self.assertNotIn("critical_thinking:", text)
        self.assertIn("critical thinking 2 of 5", brief(cfg, "reviewer"))

    def test_out_of_range_is_rejected(self):
        for bad in ({"global": {"pipeline": {"critical_thinking": 6}}}, {"spec": {"agent": {"critical_thinking": -1}}}):
            with self.assertRaises(config.ConfigError):
                self.cfg(bad)

    def test_old_flat_keys_are_an_error_not_silently_ignored(self):
        with self.assertRaises(config.ConfigError) as e:
            self.cfg({"gates": {"merge": "auto"}})
        self.assertIn("unknown setting 'gates'", str(e.exception))
        for bad in ({"spec": {"model": "x"}}, {"spec": {"agent": {"modle": "x"}}}, {"global": {"max_rounds": 3}}):
            with self.assertRaises(config.ConfigError):
                self.cfg(bad)


if __name__ == "__main__":
    unittest.main()
