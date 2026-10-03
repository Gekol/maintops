"""Runtime check that figures Manny states about a handyman are that handyman's own.

The model sees three candidates at once and can mix them up ("Milan: 96% across 464 jobs" where 96 and 464
belong to Nina). Before a reply is sent, every sentence that names exactly one candidate and compares nothing is
checked: each number in it must be one of that candidate's values (or a plain rounding of one), or a shared value
such as the incident id. Deterministic, no LLM. The release gate checks the same property in eval/eval_checks.py
with its own implementation, so it does not depend on this code being right.
"""

import math
import re

# A sentence ends at . ! ? before a space (not the point in "4.51", not "vs." / "e.g." / "i.e." / "approx." / "ca.")
_SENTENCE_END = re.compile(r"(?<!\bvs)(?<!\be\.g)(?<!\bi\.e)(?<!\bapprox)(?<!\bca)[.!?](?=\s|$)|\n")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
_LIST_MARKER = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+")
_RATING_SCALE = re.compile(r"\b1\s*(?:–|-|to)\s*5\b|(?:\bout of|/)\s*5\b", re.I)
_COMPARISON = re.compile(r"\b(than|vs\.?|versus|compared|others?|rest|runner-up|of the three|both)\b", re.I)
_ALL_CANDIDATES = re.compile(r"\ball\s+(?:the\s+)?(?:three\s+)?(?:candidates|handymen|of them)|\beach of them", re.I)


def numbers_in(text: str) -> list[float]:
    text = _RATING_SCALE.sub(" ", _LIST_MARKER.sub(" ", text or ""))
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    return [float(n.replace(",", ".")) for n in _NUMBER.findall(text)]


def _values(data) -> set[float]:
    found: set[float] = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            found.add(float(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple, set)):
            for v in x:
                walk(v)
        else:
            found.update(numbers_in(str(x)))
    walk(data)
    return found


def _grounded(n: float, allowed: set[float]) -> bool:
    if n in allowed:
        return True
    for v in allowed:
        if v != int(v) and n in (round(v), math.floor(v), math.ceil(v), round(v, 1), round(v, 2)):
            return True
        if 0 < v < 1 and n == round(v * 100):
            return True
    return False


def _sentences(text: str) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        bounds.append((start, m.start()))
        start = m.end()
    bounds.append((start, len(text)))
    return bounds


def misattributed_figures(reply: str, candidates: list[dict], shared: set[float]) -> list[str]:
    """['Milan Kuhne: 96, 464'] for each sentence that gives one candidate figures that are not theirs."""
    errors = []
    for start, end in _sentences(reply):
        sentence = reply[start:end]
        if _COMPARISON.search(sentence) or _ALL_CANDIDATES.search(sentence):
            continue
        full = {c["handyman_id"]: c for c in candidates if re.search(rf"\b{re.escape(c['name'])}\b", sentence)}
        if not full:
            full = {c["handyman_id"]: c for c in candidates
                    if re.search(rf"\b{re.escape(c['name'].split()[0])}\b", sentence)}
        if len(full) != 1:
            continue
        c = next(iter(full.values()))
        own = _values({k: v for k, v in c.items() if k != "factors"}) | shared | {112.0}
        bad = [f"{n:g}" for n in numbers_in(sentence) if not _grounded(n, own)]
        if bad:
            errors.append(f"{c['name']}: {', '.join(bad)}")
    return errors


def candidate_summary(incident_id, candidates: list[dict]) -> str:
    """A reply built only from the data, for when the model keeps mixing candidates up."""
    top = candidates[0]
    parts = [f"I have logged incident #{incident_id}.",
             "The app shows the three candidates as cards.",
             f"The top-ranked match is {top['name']}"]
    facts = []
    if top.get("success_rate_percent") is not None:
        facts.append(f"{top['success_rate_percent']}% success")
    if top.get("jobs_of_this_type"):
        facts.append(f"{top['jobs_of_this_type']} jobs of this type")
    if top.get("travel_minutes") is not None:
        facts.append(f"a {round(top['travel_minutes'])}-minute drive away")
    text = " ".join(parts) + (": " + ", ".join(facts) if facts else "") + "."
    return text + " Which handyman would you like to go with?"
