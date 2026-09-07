"""Slot-level divergence: the sensor `CONTRADICTS` is not.

E0 returned a headroom of exactly 0.0000 with 681/681 family differences at
zero, and `stale_item` scored 0/23760 under every one of the 30 arms. The
measured cause is not a threshold:

  * `_contradiction_pairs` gates on *negation polarity* first --
    `bool(left & NEGATION) == bool(right & NEGATION)` -> `continue` -- and 75.8%
    of `stale_item` item pairs are same-polarity, so they never reach the
    overlap test at all. The gold pair (`m_stale` vs `m_current`) is one of
    them: neither text carries a negation word.
  * Of the 24.2% that do differ in polarity, every single one falls at or below
    the 0.3 overlap floor. Across all 3600 pairs the maximum Jaccard is 0.2326,
    median 0.0680.

So `CONTRADICTS` models "same content, opposite polarity" (A vs not-A) while
staleness is "same slot, different value" (lives in Seattle vs lives in Austin).
Those are different relations, and no threshold converts one into the other. The
gold pair shares exactly three tokens -- `in`, `m`, `the` -- all stopwords, so a
signal built on token overlap cannot see it at any threshold.

This module adds the missing relation as a *new* predicate rather than retuning
the old one. `state_executor.py:89` is inside the E-1 freeze and the project
rule forbids lowering `_SAME_SLOT_OVERLAP` after seeing these numbers; a
successor protocol registers a new sensor instead.

**What the sensor may not read.** Two shortcuts are available in this dataset
and both are forbidden: 66.7% of `stale_item` texts are prefixed `M_old:` /
`M_new:`, and `memory_id` takes only three values (`m_stale`, `m_current`,
`m_haystack`). Those are construction markers, not runtime signals. A predicate
reading either would score 1.0 here and 0.0 in deployment, which is writing the
answer into the instrument. The tests below pin that the sensor ignores them.
"""

import unittest

from cmd_audit.counterfactual.slot_divergence import (
    SLOT_DIVERGENCE_VERSION,
    SlotClaim,
    divergent_slot_pairs,
    extract_slot_claims,
)


#: "Caller said nothing", as distinct from "caller passed None". A helper using
#: None as its own default would make `store=None` untestable, and None is a
#: value the real read surface can produce -- `getattr(item, "store", None)`.
_UNSET = object()


def _item(item_id: str, text: str, rank: int = 0, store: object = _UNSET):
    """A minimal stand-in for `RepairStateItem`'s read surface.

    `store` defaults to a distinct per-rank timestamp because that is what the
    real data carries: `stale_item` puts an ISO timestamp there on 1200/1200
    cases. A helper defaulting it to None would make every pair untimestamped
    and every divergence test vacuously empty.
    """

    class _Item:
        pass

    obj = _Item()
    obj.item_id = item_id
    obj.text = text
    obj.rank = rank
    obj.store = f"2026-01-{rank + 1:02d}T00:00:00Z" if store is _UNSET else store
    return obj


#: The real gold pair from `data/probe_cases/stale_item_cases.json`, verbatim.
#: Jaccard 0.0857, shared tokens {in, m, the}, neither side negated.
STALE_TEXT = "M_old: I've been based in Seattle for the last few years."
CURRENT_TEXT = (
    "M_new: I just finished updating my address after settling into my new "
    "place in Austin, and I’m trying to get set up with the local "
    "utilities and services here."
)
HAYSTACK_TEXT = (
    "[user] I'm looking for some healthy meal prep ideas. I've been trying to "
    "eat more plant-based lately, and I recently attended a class on vegan "
    "cuisine that got me really inspired."
)


class SlotExtractionTest(unittest.TestCase):
    """A slot is a (relation, value) reading of one item."""

    def test_a_location_claim_yields_a_place_slot(self) -> None:
        claims = extract_slot_claims(STALE_TEXT)
        self.assertTrue(claims, "no slot extracted from a location statement")
        self.assertIn("location", {claim.slot for claim in claims})

    def test_the_value_is_the_specific_place_not_the_whole_sentence(self) -> None:
        """The value has to be comparable across two paraphrases. A slot whose
        value is the sentence would make every pair of distinct sentences
        divergent, which is the same failure as no sensor at all."""
        claims = [c for c in extract_slot_claims(STALE_TEXT) if c.slot == "location"]
        self.assertEqual({c.value for c in claims}, {"seattle"})

    def test_a_paraphrased_update_yields_the_same_slot_with_a_new_value(self) -> None:
        """The property literal overlap cannot have: the update shares no
        content word with the original, yet lands on the same slot."""
        old = {c.value for c in extract_slot_claims(STALE_TEXT) if c.slot == "location"}
        new = {c.value for c in extract_slot_claims(CURRENT_TEXT) if c.slot == "location"}
        self.assertEqual(old, {"seattle"})
        self.assertEqual(new, {"austin"})

    def test_an_unrelated_item_yields_no_location_slot(self) -> None:
        """The haystack item is the third member of every `stale_item` case. A
        sensor that read a location out of it would report a divergence against
        both real claims and make the predicate fire everywhere."""
        claims = extract_slot_claims(HAYSTACK_TEXT)
        self.assertNotIn("location", {claim.slot for claim in claims})

    def test_extraction_ignores_the_dataset_prefix(self) -> None:
        """`M_old:` / `M_new:` prefix 66.7% of this dataset's texts. Reading it
        would score perfectly here and zero in deployment."""
        with_prefix = extract_slot_claims(STALE_TEXT)
        without = extract_slot_claims(STALE_TEXT.replace("M_old: ", ""))
        self.assertEqual(
            {(c.slot, c.value) for c in with_prefix},
            {(c.slot, c.value) for c in without},
        )


class DivergentPairTest(unittest.TestCase):
    """The predicate: two items claiming different values in one slot."""

    def test_the_real_gold_pair_is_divergent(self) -> None:
        """The case `CONTRADICTS` cannot see. Same slot, different value,
        neither negated, Jaccard 0.0857."""
        matched = divergent_slot_pairs(
            (
                _item("m_stale", STALE_TEXT, rank=0),
                _item("m_current", CURRENT_TEXT, rank=1),
            )
        )
        self.assertEqual(matched, {"m_stale", "m_current"})

    def test_the_haystack_item_is_not_implicated(self) -> None:
        """Filler carries `store="haystack"`, not a timestamp, on all 1200 real
        cases -- that unparseable value is exactly what keeps it out. The store
        is passed verbatim here rather than left to the helper's default, since
        a timestamped haystack is a case the dataset never produces.
        """
        matched = divergent_slot_pairs(
            (
                _item("m_stale", STALE_TEXT, rank=0),
                _item("m_current", CURRENT_TEXT, rank=1),
                _item("m_haystack", HAYSTACK_TEXT, rank=2, store="haystack"),
            )
        )
        self.assertEqual(matched, {"m_stale", "m_current"})

    def test_an_untimestamped_item_carries_no_temporal_evidence(self) -> None:
        """The general form of the test above. Two items that genuinely disagree
        are invisible to this sensor when either lacks a parseable time -- the
        signal is temporal, so no timestamp means no verdict rather than a
        fallback to text similarity.
        """
        matched = divergent_slot_pairs(
            (
                _item("a", STALE_TEXT, rank=0, store="unknown"),
                _item("b", CURRENT_TEXT, rank=1),
            )
        )
        self.assertEqual(matched, set())

    def test_an_item_whose_only_content_word_is_the_prefix_carries_no_content(
        self,
    ) -> None:
        """The degenerate case where the construction prefix would change a
        verdict, and the only one there is.

        `M_old:` tokenizes to `{m, old}`. `m` is a stopword; `old` is not. So an
        item that is otherwise all stopwords has an empty content set once the
        prefix is stripped and a non-empty one if it is not -- and content
        emptiness is a gate. A sensor reading the prefix would call this pair
        divergent on the strength of the marker alone.
        """
        matched = divergent_slot_pairs(
            (
                _item("a", "M_old: the", rank=0),
                _item("b", CURRENT_TEXT, rank=1),
            )
        )
        self.assertEqual(matched, set())

    def test_a_non_string_store_is_not_a_timestamp(self) -> None:
        """`store` is typed `object` on the read surface and this data puts a
        string there, but a caller passing an int or None must get "no temporal
        evidence" rather than an AttributeError -- an unparseable time is a
        normal state for filler, not a fault."""
        for store in (None, 0, 20260101, [], {"t": "2026-01-01"}):
            with self.subTest(store=store):
                matched = divergent_slot_pairs(
                    (
                        _item("a", STALE_TEXT, rank=0, store=store),
                        _item("b", CURRENT_TEXT, rank=1),
                    )
                )
                self.assertEqual(matched, set())

    def test_two_items_agreeing_on_a_slot_are_not_divergent(self) -> None:
        """Agreement is the control. A sensor that fired on any two items
        sharing a slot would flag corroboration as conflict."""
        matched = divergent_slot_pairs(
            (
                _item("a", "I've been based in Seattle for the last few years."),
                _item("b", "I still live in Seattle, same apartment as before."),
            )
        )
        self.assertEqual(matched, set())

    def test_items_in_different_slots_are_not_divergent(self) -> None:
        matched = divergent_slot_pairs(
            (
                _item("a", "I live in Seattle."),
                _item("b", "I work as a data engineer."),
            )
        )
        self.assertEqual(matched, set())

    def test_a_single_item_cannot_diverge(self) -> None:
        self.assertEqual(divergent_slot_pairs((_item("a", STALE_TEXT),)), set())

    def test_no_items_is_not_an_error(self) -> None:
        self.assertEqual(divergent_slot_pairs(()), set())

    def test_the_pair_is_symmetric(self) -> None:
        """Order is recall rank, which is not evidence about which value is
        current. Both members are named; deciding which to demote is fitness's
        job, not the sensor's."""
        forward = divergent_slot_pairs(
            (_item("x", STALE_TEXT, 0), _item("y", CURRENT_TEXT, 1))
        )
        backward = divergent_slot_pairs(
            (_item("y", CURRENT_TEXT, 0), _item("x", STALE_TEXT, 1))
        )
        self.assertEqual(forward, backward)

    def test_divergence_does_not_read_the_memory_id(self) -> None:
        """`memory_id` takes three values in this dataset. Renaming both items
        to neutral IDs must not change the verdict."""
        matched = divergent_slot_pairs(
            (_item("q1", STALE_TEXT, 0), _item("q2", CURRENT_TEXT, 1))
        )
        self.assertEqual(matched, {"q1", "q2"})


class ConstructionMarkerTest(unittest.TestCase):
    """Why this sensor is withdrawn, pinned so the number cannot be re-cited.

    See `docs/evolution/BUILD_SPEC_ROUTE_A_SUCCESSOR_SLOT_SENSOR.md` §3. The
    100% coverage below is real but it is not a measurement of staleness: in
    `stale_item`, `store` stands in bijection with `memory_id` (`m_stale` is the
    constant `2026-01-01T00:00:00Z` on all 1200 cases), so reading `store` and
    reading the item ID carry identical information. The tests in
    `DivergentPairTest` establish that the sensor does not read `memory_id`;
    these establish that it did not need to.
    """

    def test_decoupling_store_from_the_item_id_destroys_the_signal(self) -> None:
        """The decisive counterfactual.

        Timestamps stay ISO, parseable, and pairwise distinct -- only derived
        from each item's *text* instead of its construction identity. If the
        sensor were reading temporal evidence this would barely move; if it were
        reading the bijection, the verdict inverts. It inverts: the gold pair
        goes to zero and the haystack is implicated almost everywhere.
        """
        import hashlib
        import json
        import pathlib

        path = pathlib.Path("data/probe_cases/stale_item_cases.json")
        if not path.is_file():
            self.skipTest(f"{path} not present")
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload if isinstance(payload, list) else payload.get("cases", payload)

        def content_store(case_id: str, text: str) -> str:
            digest = hashlib.sha256(f"{case_id}|{text}".encode("utf-8")).hexdigest()
            return f"2026-01-{int(digest[:6], 16) % 28 + 1:02d}T00:00:00Z"

        exact = 0
        with_haystack = 0
        for case in cases:
            items = tuple(
                _item(
                    row["memory_id"],
                    row.get("text", ""),
                    rank,
                    store=content_store(
                        str(case.get("case_id", "")), row.get("text", "")
                    ),
                )
                for rank, row in enumerate(case.get("extracted_memory", []))
            )
            matched = divergent_slot_pairs(items)
            if matched == {"m_stale", "m_current"}:
                exact += 1
            if "m_haystack" in matched:
                with_haystack += 1

        self.assertEqual(
            exact,
            0,
            "named the gold pair without the store/memory_id bijection; if this "
            "ever passes, re-read the withdrawal -- the sensor would be reading "
            "something other than the construction marker",
        )
        self.assertGreater(
            with_haystack,
            len(cases) // 2,
            f"implicated the haystack in {with_haystack}/{len(cases)} cases; the "
            "haystack was excluded only because its store holds the literal "
            "'haystack', not because it lacks temporal evidence",
        )

    def test_the_signal_is_absent_where_store_carries_provenance(self) -> None:
        """`store` is nominally a provenance field, and on the datasets that use
        it that way the sensor is silent. Two of the shipped sets put `episodic`
        in every item, 0% parseable, 0 firings -- so the 100% below is a property
        of one dataset's construction, not of the relation."""
        import json
        import pathlib

        for name in ("real_multihop_cases", "real_recurrent_cases"):
            path = pathlib.Path(f"data/probe_cases/{name}.json")
            if not path.is_file():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            cases = payload if isinstance(payload, list) else payload.get("cases", payload)
            fired = 0
            for case in cases:
                items = tuple(
                    _item(
                        row.get("memory_id", ""),
                        row.get("text", ""),
                        rank,
                        store=row.get("store"),
                    )
                    for rank, row in enumerate(case.get("extracted_memory", []))
                )
                if divergent_slot_pairs(items):
                    fired += 1
            with self.subTest(dataset=name):
                self.assertEqual(
                    fired,
                    0,
                    f"{name}: fired on {fired}/{len(cases)} despite `store` "
                    "carrying provenance rather than a timestamp",
                )


class CoverageOnRealDataTest(unittest.TestCase):
    """The 100% that looked like it justified the module.

    Kept, and kept passing, because the number is real and reproducible -- but
    read it with `ConstructionMarkerTest` above, which shows what produces it.
    A reader who sees only this class would draw the withdrawn conclusion.
    """

    def test_the_sensor_fires_on_the_majority_of_stale_cases(self) -> None:
        """`CONTRADICTS` fires on 0 of 3600 real item pairs. A replacement that
        fires on a handful would not be worth a successor protocol, so the bar
        is a majority of cases -- asserted against the shipped dataset rather
        than a fixture, since the fixture is what went wrong last time.
        """
        import json
        import pathlib

        path = pathlib.Path("data/probe_cases/stale_item_cases.json")
        if not path.is_file():
            self.skipTest(f"{path} not present")
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload if isinstance(payload, list) else payload.get("cases", payload)

        fired = 0
        for case in cases:
            items = tuple(
                _item(
                    row["memory_id"],
                    row.get("text", ""),
                    rank,
                    store=row.get("store"),
                )
                for rank, row in enumerate(case.get("extracted_memory", []))
            )
            if divergent_slot_pairs(items):
                fired += 1
        self.assertGreater(
            fired,
            len(cases) // 2,
            f"fired on {fired}/{len(cases)} cases; CONTRADICTS fires on 0 and a "
            "replacement needs to see most of the domain to be worth registering",
        )

    def test_the_sensor_names_the_gold_pair_and_not_the_haystack(self) -> None:
        """Firing is necessary but not sufficient: it has to implicate the two
        items that actually disagree. A sensor that flagged the haystack would
        send repair at the wrong item and score 0 anyway."""
        import json
        import pathlib

        path = pathlib.Path("data/probe_cases/stale_item_cases.json")
        if not path.is_file():
            self.skipTest(f"{path} not present")
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload if isinstance(payload, list) else payload.get("cases", payload)

        exact = 0
        with_haystack = 0
        for case in cases:
            items = tuple(
                _item(
                    row["memory_id"],
                    row.get("text", ""),
                    rank,
                    store=row.get("store"),
                )
                for rank, row in enumerate(case.get("extracted_memory", []))
            )
            matched = divergent_slot_pairs(items)
            if not matched:
                continue
            if matched == {"m_stale", "m_current"}:
                exact += 1
            if "m_haystack" in matched:
                with_haystack += 1
        self.assertGreater(exact, 0, "never named exactly the disagreeing pair")
        self.assertEqual(
            with_haystack,
            0,
            f"implicated the haystack item in {with_haystack} case(s)",
        )


class VersionTest(unittest.TestCase):
    def test_the_sensor_carries_its_own_version(self) -> None:
        """A successor protocol has to be distinguishable from `route-a-ir-v1`
        in any artifact that used it, or a later reader cannot tell which sensor
        produced a number."""
        self.assertTrue(SLOT_DIVERGENCE_VERSION)
        self.assertNotEqual(SLOT_DIVERGENCE_VERSION, "route-a-ir-v1")

    def test_a_claim_is_frozen(self) -> None:
        claim = SlotClaim(slot="location", value="seattle")
        with self.assertRaises(Exception):
            claim.value = "austin"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
