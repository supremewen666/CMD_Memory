"""Slot-level divergence: the sensor `CONTRADICTS` is not (successor protocol).

E0 measured a headroom of exactly 0.0000 -- 681/681 family differences at zero --
and `stale_item` scored 0/23760 under all 30 arms. The cause was measured, not
guessed, and it is not a threshold.

`_contradiction_pairs` in `state_executor.py` gates on negation polarity
*before* it measures overlap:

    if bool(left & _NEGATION_WORDS) == bool(right & _NEGATION_WORDS):
        continue

75.8% of `stale_item`'s 3600 item pairs are same-polarity and never reach the
overlap test. The gold pair is one of them -- neither "I've been based in
Seattle" nor "settling into my new place in Austin" carries a negation word. Of
the 24.2% that do differ in polarity, every one falls at or below the 0.3 floor;
across all 3600 pairs the maximum Jaccard is 0.2326 and the median is 0.0680.

So `CONTRADICTS` models *same content, opposite polarity* (A vs not-A) while
staleness is *same slot, different value* (Seattle vs Austin). Different
relations. No threshold converts one into the other: the gold pair shares
exactly three tokens -- `in`, `m`, `the`, all stopwords -- so nothing built on
token overlap can see it at any setting.

**Why this is a new module and not an edit.** `_SAME_SLOT_OVERLAP` sits at
`state_executor.py:89`, inside the E-1 freeze, and lowering it after seeing
these numbers would be tuning a frozen evaluator against dev outcomes. A
successor protocol registers a new sensor; the frozen one keeps its behavior and
its recorded result.

**WITHDRAWN as a staleness sensor.** See
`docs/evolution/BUILD_SPEC_ROUTE_A_SUCCESSOR_SLOT_SENSOR.md`. This module fires
on 1200/1200 `stale_item` cases and names the exact gold pair every time, and
that number is void: in this dataset `store` stands in bijection with
`memory_id` -- `m_stale` carries the single constant `2026-01-01T00:00:00Z` on
all 1200 cases, `m_haystack` the literal `"haystack"` -- so reading `store` and
reading the item ID carry identical information. It is the shortcut the tests
below forbid, entering through a different field.

The decisive measurement: re-derive each timestamp from the item's *text*
instead of its construction identity, keeping them ISO, parseable, and pairwise
distinct. Exact-gold-pair goes 1200 -> 0; haystack false positives go 0 -> 1199.
All discriminative power was in the bijection. The filler was excluded because
its field says `"haystack"`, not because it lacks temporal evidence. On the two
shipped datasets that use `store` for provenance as intended (`episodic` on every
item) the sensor fires 0/240 and 0/600.

The module and its tests are kept: the code is not the error, the diagnosis of
`CONTRADICTS` below is unaffected, and `ConstructionMarkerTest` pins the
withdrawal so the 100% cannot be re-cited on its own. What is retracted is the
claim that any of this measures staleness.

The temporal reading also does not, after all, correct the frozen module's
comment. `_temporally_dominated` says "A `RuntimeMemoryItem` carries no
timestamp, so recall rank is the only ordering signal available." A
timestamp-shaped value does sit in `store` on one dataset, but it is a
construction marker there and absent everywhere else, so the frozen comment is
closer to right than this module first claimed. It is not edited either way --
it is inside the E-1 freeze.

**What this sensor may not read.** Two shortcuts exist in this dataset and both
are refused *by construction*: 66.7% of texts are prefixed `M_old:` / `M_new:`,
and `memory_id` takes only three values (`m_stale`, `m_current`, `m_haystack`).
The prefix is stripped before comparison and the item ID is never inspected. The
lesson of the withdrawal is that enumerating known shortcuts is not enough -- a
third one was present in a field nobody had listed, and only the decoupling
counterfactual found it.

Zero LLM calls: every signal is a timestamp parse or a token-set operation. That
property is why the temporal shortcut was attractive, and its collapse is the
real finding: a same-slot detector has to read the values being asserted on both
sides, which costs a model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "SLOT_DIVERGENCE_VERSION",
    "SlotClaim",
    "divergent_slot_pairs",
    "extract_slot_claims",
    "parse_store_timestamp",
]

#: A successor sensor, distinguishable from `route-a-ir-v1` in any artifact that
#: used it. A later reader must be able to tell which sensor produced a number.
SLOT_DIVERGENCE_VERSION = "route-a-slot-divergence-v1"

#: Dataset construction markers. Stripped, never read as signal.
_CONSTRUCTION_PREFIX = re.compile(r"^\s*M_(?:old|new)\s*:\s*", re.IGNORECASE)

_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")

#: Tokens carrying no slot identity. Deliberately short: this is a stopword
#: list, not a domain vocabulary. The gold pair's entire overlap is
#: {in, m, the}, so leaving these in would make agreement and disagreement
#: indistinguishable.
_STOPWORDS = frozenset(
    {
        "a", "about", "after", "all", "am", "an", "and", "any", "are", "as",
        "at", "be", "been", "being", "but", "by", "can", "did", "do", "does",
        "for", "from", "get", "got", "had", "has", "have", "here", "how",
        "i", "if", "im", "in", "into", "is", "it", "its", "just", "last",
        "lately", "like", "m", "me", "more", "my", "new", "now", "of", "on",
        "one", "or", "our", "out", "over", "re", "really", "s", "so", "some",
        "still", "t", "that", "the", "their", "them", "then", "there",
        "these", "they", "this", "to", "up", "us", "ve", "very", "was",
        "we", "well", "were", "what", "when", "where", "which", "while",
        "who", "will", "with", "would", "year", "years", "you", "your",
    }
)

@dataclass(frozen=True)
class SlotClaim:
    """One item's reading as a (slot, value) pair.

    Frozen: a claim is evidence about an item, and a caller mutating one would
    change what the sensor reported after the fact.
    """

    slot: str
    value: str


def _strip_construction_prefix(text: str) -> str:
    return _CONSTRUCTION_PREFIX.sub("", text)


def _content_tokens(text: str) -> set[str]:
    """Tokens after stripping the dataset prefix and stopwords."""
    stripped = _strip_construction_prefix(text)
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(stripped)
        if token.casefold() not in _STOPWORDS
    }


def parse_store_timestamp(store: object) -> datetime | None:
    """The ISO timestamp parked in `store`, or None.

    `store` is overloaded in this data: it holds an ISO timestamp for real items
    and a literal like `"haystack"` for filler. Returning None rather than
    raising is the point -- an item with no parseable time is not an error, it
    simply carries no temporal evidence and cannot anchor a divergence.
    """
    if not isinstance(store, str):
        return None
    candidate = store.strip()
    if not candidate:
        return None
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def extract_slot_claims(text: str) -> tuple[SlotClaim, ...]:
    """Slot readings for one item.

    Kept deliberately narrow. This is *not* the divergence signal -- the
    timestamp is (see `divergent_slot_pairs`) -- it is the auditable part: a
    reader asking "what did the sensor think this item claimed?" gets an answer.
    Only relations expressible without a domain vocabulary are extracted, and
    `location` is the one this dataset's gold pair turns on.

    """
    stripped = _strip_construction_prefix(text)
    claims: list[SlotClaim] = []
    for match in re.finditer(
        r"\b(?:in|to|at|from)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)",
        stripped,
    ):
        place = match.group(1).strip()
        if place.casefold() in _STOPWORDS:
            continue
        claims.append(SlotClaim(slot="location", value=place.casefold()))
    return tuple(claims)


def divergent_slot_pairs(items) -> set[str]:
    """Item IDs in a same-slot, different-value disagreement.

    The relation `CONTRADICTS` cannot express. Three conditions, all required:

      1. **Both items carry a parseable timestamp and the timestamps differ.**
         This is the domain-blind core. Two items asserting a state at different
         times are candidates for supersession regardless of what the slot is.
         Filler with an unparseable `store` drops out here, which is what keeps
         the haystack item from being implicated.
      2. **Both carry at least one content word.** An item that is entirely
         stopwords supports no comparison. Content tokens are used rather than
         raw ones because the gold pair's whole overlap is `{in, m, the}`.

    A corroboration gate (skip pairs whose content overlap is high, on the
    theory that they restate one value rather than replace it) was written and
    then removed: across all 1200 real cases the maximum content overlap among
    pairs reaching it was 0.1600, so at any plausible threshold it never fired.
    Keeping it would have left an unexercised free parameter in the sensor --
    the same shape of mistake as `_SAME_SLOT_OVERLAP`.

    Both members are returned, never just the older one: recall rank and
    timestamp order say which is earlier, not which the repair should demote.
    That is fitness's decision (§8), and a sensor that pre-empted it would be
    smuggling a policy into a measurement. The item ID is used only as an
    output key -- never inspected -- and the `M_old:` / `M_new:` prefix is
    stripped before any comparison.
    """
    matched: set[str] = set()
    readings = []
    for item in items:
        text = getattr(item, "text", "") or ""
        readings.append(
            (
                getattr(item, "item_id", None),
                _content_tokens(text),
                parse_store_timestamp(getattr(item, "store", None)),
            )
        )

    for index, (left_id, left_tokens, left_time) in enumerate(readings):
        for right_id, right_tokens, right_time in readings[index + 1 :]:
            if left_time is None or right_time is None:
                continue
            if left_time == right_time:
                continue
            if not left_tokens or not right_tokens:
                continue
            matched.add(left_id)
            matched.add(right_id)
    return matched
