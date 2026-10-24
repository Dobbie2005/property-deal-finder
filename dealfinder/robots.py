"""robots.txt checking per the standard (RFC 9309), as Google does it: the longest matching rule wins,
and Allow wins a tie. (Python's built-in robotparser uses the first match and ignores '*', which wrongly
blocks pages some sites explicitly allow, e.g. 'Disallow: /results/' + 'Allow: /results/*/'.)"""
import re
from functools import lru_cache

UA_TOKEN = "property-deal-finder"


def _groups(text: str) -> list[tuple[list[str], list[tuple[bool, str]]]]:
    groups, agents, rules, last_was_agent = [], [], [], False
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, value = (x.strip() for x in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            last_was_agent = True
        elif field in ("allow", "disallow"):
            last_was_agent = False
            if value or field == "allow":
                rules.append((field == "allow", value))
        else:
            last_was_agent = False
    if agents or rules:
        groups.append((agents, rules))
    return groups


@lru_cache(maxsize=4096)
def _rx(pattern: str):
    anchored = pattern.endswith("$")
    body = re.escape(pattern[:-1] if anchored else pattern).replace(r"\*", ".*")
    return re.compile(body + ("$" if anchored else ""))


def allowed(robots_text: str, path: str, ua: str = UA_TOKEN) -> bool:
    groups = _groups(robots_text)
    mine = [g for g in groups if any(a != "*" and a in ua.lower() for a in g[0])]
    rules = [r for g in (mine or [g for g in groups if "*" in g[0]]) for r in g[1]]
    best = None  # (length, allow)
    for allow, pattern in rules:
        if pattern and _rx(pattern).match(path):
            cand = (len(pattern), allow)
            if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                best = cand
    return True if best is None else best[1]
