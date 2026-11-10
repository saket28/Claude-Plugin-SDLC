"""templates/sdlc.schema.json is the one place for a setting's type, allowed values, range, help text
and where it appears in the settings page. The dashboard renders from it and validation reads it, so
a value added there is accepted everywhere. Defaults live in config.DEFAULTS (a test keeps the two
in step)."""

import json
import string
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent.parent / "templates" / "sdlc.schema.json"


@lru_cache(maxsize=1)
def load():
    return json.loads(PATH.read_text())


def fields():
    """{path: field} for every setting in the form."""
    return {f["path"]: f for t in load()["tabs"] for s in t["sections"] for f in s.get("fields", [])}


def roles():
    return tuple(load()["roles"])


def reviewer_field(key):
    return next(f for f in load()["reviewer_fields"] if f["key"] == key)


def options(path_or_reviewer_key):
    f = fields().get(path_or_reviewer_key) or reviewer_field(path_or_reviewer_key)
    return tuple(f["options"])


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(path, f, value, nullable=False):
    """What is wrong with `value` for setting `f`, or None."""
    if value is None:
        return None if nullable else f"{path} needs a value"
    t = f["type"]
    if t == "enum":
        if f.get("value_type") == "int":
            ok = isinstance(value, int) and not isinstance(value, bool) and value in f["options"]
        else:
            ok = value in f["options"] or (f.get("allow_custom") and isinstance(value, str) and value)
        return None if ok else f"{path} must be one of: {', '.join(str(o) for o in f['options'])}"
    if t == "bool":
        return None if isinstance(value, bool) else f"{path} must be true or false"
    if t in ("int", "float"):
        if not _num(value) or (t == "int" and not isinstance(value, int)):
            return f"{path} must be {'a whole number' if t == 'int' else 'a number'}"
        if "min" in f and value < f["min"]:
            return f"{path} must be at least {f['min']}"
        if "max" in f and value > f["max"]:
            return f"{path} must be at most {f['max']}"
        return None
    if t == "string":
        if not isinstance(value, str):
            return f"{path} must be text"
        for _, name, _, _ in string.Formatter().parse(value):
            if name is not None and name not in f.get("placeholders", ()):
                return (f"{path}: unknown placeholder {{{name}}}"
                        + (f" (use {', '.join('{' + p + '}' for p in f['placeholders'])})" if f.get("placeholders") else ""))
        return None
    if t == "list":
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            return f"{path} must be a list of text"
        bad = [v for v in value if "item_options" in f and v not in f["item_options"]]
        return f"{path}: {', '.join(bad)} not allowed (use {', '.join(f['item_options'])})" if bad else None
    return None
