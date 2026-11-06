"""`when` / `human_when` rules (INSTRUCTIONS.md §2).

    always | never | touches-ui | risk>=low|medium|high | paths:[glob, ...]
combined with "or".
"""

import re

RISK = {"low": 0, "medium": 1, "high": 2}


class RuleError(ValueError):
    pass


def _split_or(s):
    parts, depth, cur = [], 0, []
    tokens = re.split(r"(\[|\]|\s+or\s+)", s)
    for t in tokens:
        if t == "[":
            depth += 1
        elif t == "]":
            depth -= 1
        if depth == 0 and re.fullmatch(r"\s+or\s+", t or ""):
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(t or "")
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def parse(rule):
    if rule is None:
        return [("always",)]
    if not isinstance(rule, str):
        raise RuleError(f"rule must be a string, got {rule!r}")
    terms = []
    for part in _split_or(rule.strip()):
        if part in ("always", "never", "touches-ui"):
            terms.append((part,))
            continue
        m = re.fullmatch(r"risk\s*>=\s*(low|medium|high)", part)
        if m:
            terms.append(("risk", RISK[m.group(1)]))
            continue
        m = re.fullmatch(r"paths\s*:\s*\[(.*)\]", part)
        if m:
            globs = [g.strip().strip("'\"") for g in m.group(1).split(",") if g.strip()]
            if not globs:
                raise RuleError("paths:[] needs at least one glob")
            terms.append(("paths", globs))
            continue
        raise RuleError(f"unknown rule '{part}' (expected always, never, touches-ui, "
                        f"risk>=low|medium|high or paths:[globs])")
    if not terms:
        raise RuleError("empty rule")
    return terms


def evaluate(rule, ctx):
    """ctx: {'risk': 'low'|..., 'touches_ui': bool, 'paths': [changed or planned paths]}"""
    for term in parse(rule):
        kind = term[0]
        if kind == "always":
            return True
        if kind == "touches-ui" and ctx.get("touches_ui"):
            return True
        if kind == "risk" and RISK.get(str(ctx.get("risk", "low")), 0) >= term[1]:
            return True
        if kind == "paths" and any(glob_match(g, p) for g in term[1] for p in ctx.get("paths", [])):
            return True
    return False


_cache = {}


def glob_to_regex(glob):
    if glob in _cache:
        return _cache[glob]
    i, out = 0, []
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    rx = re.compile("^" + "".join(out) + "$")
    _cache[glob] = rx
    return rx


def glob_match(glob, path):
    if path.startswith("./"):
        path = path[2:]
    rx = glob_to_regex(glob)
    if rx.match(path):
        return True
    # a planned path like "src/api/**" matches a glob like "src/**"
    return "*" in path and glob_to_regex(glob).match(path.replace("**", "x").replace("*", "x")) is not None
