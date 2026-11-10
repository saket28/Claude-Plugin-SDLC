import json
import unittest

from helpers import ROOT
from sdlc import config


class SchemaConventionTest(unittest.TestCase):
    """Every setting is <tab>.<section>.<setting>, in SDLC.md, the defaults and the settings form."""

    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / "templates" / "sdlc.schema.json").read_text())

    def test_every_field_path_follows_tab_section_setting(self):
        for tab in self.schema["tabs"]:
            for sec in tab["sections"]:
                for f in sec.get("fields", []):
                    parts = f["path"].split(".")
                    self.assertEqual(parts[:2], [tab["id"], sec["id"]], f["path"])
                    self.assertEqual(len(parts), 3, f["path"])
                    cur = config.DEFAULTS
                    for k in parts:
                        self.assertIn(k, cur, f"{f['path']} is not a default")
                        cur = cur[k]

    def test_every_default_setting_is_in_the_form(self):
        form = {f["path"] for t in self.schema["tabs"] for s in t["sections"] for f in s.get("fields", [])}
        collections = {f"{t['id']}.{s['id']}" for t in self.schema["tabs"] for s in t["sections"] if s.get("collection")}
        for tab, secs in config.DEFAULTS.items():
            for sec, settings in secs.items():
                if (tab, sec) in config.FREEFORM:
                    continue
                if f"{tab}.{sec}" in collections:
                    self.assertEqual(list(settings), ["list"], f"{tab}.{sec}")
                    continue
                for name in settings:
                    self.assertIn(f"{tab}.{sec}.{name}", form, "not in the settings form")

    def test_inherited_settings_point_at_real_settings(self):
        for t in self.schema["tabs"]:
            for s in t["sections"]:
                for f in s.get("fields", []):
                    if f.get("inherits"):
                        cur = config.DEFAULTS
                        for k in f["inherits"].split("."):
                            cur = cur[k]


if __name__ == "__main__":
    unittest.main()
