"""Same-slot supersession judged from item text, one model call per pair.

This is the second attempt at the sensor `CONTRADICTS` is not. The first read
`store` and was withdrawn: `store` turned out to be bijective with `memory_id`
in `stale_item`, so 1200/1200 coverage collapsed to 0 exact-gold-pair once the
timestamp was re-derived from text
(`docs/evolution/BUILD_SPEC_ROUTE_A_SUCCESSOR_SLOT_SENSOR.md`).

The lesson that shapes this module: enumerating known shortcuts was not enough.
So the decoupling counterfactual is a *test here*, not a post-hoc check. Every
metadata field is withheld from the judge by construction -- it sees two texts
and nothing else -- and `MetadataIsUnreachableTest` pins that permuting item IDs,
stores, and ranks cannot change a verdict.

Why a model call, stated plainly: the real pairs supersede on *implicit* slots.
"I'm usually in bed by 10:30 PM" vs "my alarm goes off at 3:45, so I've become
obsessive about prepping" is a sleep-schedule replacement in which the slot name
appears in neither text and the two share almost no content word. A vocabulary
cannot name the slot and overlap cannot find it. That is what makes this
expensive and it is the finding the withdrawal produced, not a design preference.

Budget: 5955 item pairs across all six shipped datasets, ~1985 on D_dev. One
verdict per unordered text pair, cached, so the 30 arms share one judgment
rather than multiplying it.
"""

import unittest

import pytest

# Kept as a historical artifact rather than deleted: its API embedded a
# direction claim ("supersession") that a symmetric text-only relation sensor
# cannot make.  Its valid text-isolation cases now live in test_slot_relation.
pytest.skip(
    "withdrawn supersession contract; replaced by direction-free slot_relation tests",
    allow_module_level=True,
)

from cmd_audit.counterfactual.slot_supersession import (  # noqa: E402
    SLOT_SUPERSESSION_VERSION,
    SupersessionVerdict,
    parse_judge_response,
    supersession_prompt,
    superseded_pairs,
)


class _RecordingClient:
    """A judge stand-in that records what it was shown.

    The recording is the point: several tests below assert on the prompts rather
    than the verdicts, because what the judge may read is the property under
    test.
    """

    def __init__(self, responses=None, default='{"superseded": false}'):
        self.responses = dict(responses or {})
        self.default = default
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.prompts.append(prompt)
        for needle, response in self.responses.items():
            if needle in prompt:
                return response
        return self.default


def _item(item_id: str, text: str, rank: int = 0, store: str = "episodic"):
    class _Item:
        pass

    obj = _Item()
    obj.item_id = item_id
    obj.text = text
    obj.rank = rank
    obj.store = store
    return obj


#: A real pair, verbatim from `stale_item_cases.json`. The slot is sleep
#: schedule; the words "sleep" and "schedule" appear in neither side.
EARLY_TEXT = "I try to keep a consistent schedule, so I'm usually in bed by 10:30 PM."
LATE_TEXT = (
    "Ever since I started opening the bakery, my alarm goes off at 3:45, so I've "
    "become a little obsessive about getting everything prepped the night before."
)
UNRELATED_TEXT = (
    "[user] I'm considering redoing my living room and I'm stuck on choosing a "
    "coffee table. Can you suggest a few options?"
)


class PromptTest(unittest.TestCase):
    def test_the_prompt_carries_both_texts(self) -> None:
        prompt = supersession_prompt(EARLY_TEXT, LATE_TEXT)
        self.assertIn(EARLY_TEXT, prompt)
        self.assertIn(LATE_TEXT, prompt)

    def test_the_prompt_carries_no_item_id_store_or_rank(self) -> None:
        """The withdrawn sensor's failure mode, closed at the source: if a
        metadata field never reaches the judge, no amount of correlation between
        that field and the answer can be exploited."""
        prompt = supersession_prompt(EARLY_TEXT, LATE_TEXT)
        for marker in ("m_stale", "m_current", "m_haystack", "episodic",
                       "rank", "store", "memory_id"):
            self.assertNotIn(marker, prompt, f"prompt leaked {marker!r}")

    def test_the_prompt_strips_the_construction_prefix(self) -> None:
        """66.7% of this dataset's texts are prefixed `M_old:` / `M_new:`. A
        judge shown that prefix would answer from it."""
        prompt = supersession_prompt(f"M_old: {EARLY_TEXT}", f"M_new: {LATE_TEXT}")
        self.assertNotIn("M_old", prompt)
        self.assertNotIn("M_new", prompt)

    def test_the_prompt_does_not_say_which_side_is_older(self) -> None:
        """Order is recall rank, which is not evidence about currency. Telling
        the judge "A is older" would hand it the answer it is meant to derive,
        and on a dataset where stale always comes first that scores perfectly
        for the wrong reason."""
        prompt = supersession_prompt(EARLY_TEXT, LATE_TEXT).casefold()
        for leak in ("older", "newer", "earlier", "later", "first", "second",
                     "stale", "current", "outdated"):
            self.assertNotIn(leak, prompt, f"prompt leaked ordering hint {leak!r}")


class ParseTest(unittest.TestCase):
    def test_a_clean_positive_verdict_parses(self) -> None:
        verdict = parse_judge_response('{"superseded": true, "slot": "sleep schedule"}')
        self.assertTrue(verdict.superseded)
        self.assertEqual(verdict.slot, "sleep schedule")

    def test_a_clean_negative_verdict_parses(self) -> None:
        self.assertFalse(parse_judge_response('{"superseded": false}').superseded)

    def test_prose_around_the_json_is_tolerated(self) -> None:
        """Local models wrap JSON in commentary. Failing on that would make the
        sensor's coverage a property of the model's formatting discipline."""
        verdict = parse_judge_response(
            'Looking at both: they conflict.\n{"superseded": true, "slot": "diet"}\nDone.'
        )
        self.assertTrue(verdict.superseded)

    def test_an_unparseable_response_is_abstention_not_a_verdict(self) -> None:
        """Fail closed. A judge that returned garbage did not say "superseded",
        and treating an unreadable answer as either verdict would put model
        noise into the measurement."""
        for response in ("", "I cannot tell", "{broken", '{"superseded": "maybe"}'):
            with self.subTest(response=response):
                verdict = parse_judge_response(response)
                self.assertFalse(verdict.superseded)
                self.assertTrue(verdict.abstained)


class PairSelectionTest(unittest.TestCase):
    def test_a_judged_supersession_names_both_items(self) -> None:
        """Both members, never just one: which to demote is fitness's call, and
        a sensor that pre-empted it would smuggle a policy into a measurement."""
        client = _RecordingClient({"10:30": '{"superseded": true, "slot": "sleep"}'})
        matched = superseded_pairs(
            (_item("a", EARLY_TEXT), _item("b", LATE_TEXT)),
            judge=client,
        )
        self.assertEqual(matched, {"a", "b"})

    def test_a_negative_verdict_selects_nothing(self) -> None:
        matched = superseded_pairs(
            (_item("a", EARLY_TEXT), _item("b", UNRELATED_TEXT)),
            judge=_RecordingClient(),
        )
        self.assertEqual(matched, set())

    def test_one_call_per_unordered_pair(self) -> None:
        """Three items is three pairs, not six. The budget is 5955 pairs across
        the shipped data and judging each ordering twice would double it for no
        information."""
        client = _RecordingClient()
        superseded_pairs(
            (
                _item("a", EARLY_TEXT),
                _item("b", LATE_TEXT),
                _item("c", UNRELATED_TEXT),
            ),
            judge=client,
        )
        self.assertEqual(len(client.prompts), 3)

    def test_repeated_text_pairs_are_judged_once(self) -> None:
        """The cache is what makes 30 arms affordable: the same two texts get
        one verdict no matter how many times the search revisits them."""
        client = _RecordingClient()
        cache: dict = {}
        for _ in range(4):
            superseded_pairs(
                (_item("a", EARLY_TEXT), _item("b", LATE_TEXT)),
                judge=client,
                cache=cache,
            )
        self.assertEqual(len(client.prompts), 1)

    def test_an_empty_text_is_not_judged(self) -> None:
        """No content, no comparison -- and spending a call to be told so would
        waste budget on every case with a placeholder item."""
        client = _RecordingClient()
        matched = superseded_pairs(
            (_item("a", "   "), _item("b", LATE_TEXT)),
            judge=client,
        )
        self.assertEqual(matched, set())
        self.assertEqual(client.prompts, [])

    def test_a_single_item_cannot_supersede(self) -> None:
        client = _RecordingClient()
        self.assertEqual(superseded_pairs((_item("a", EARLY_TEXT),), judge=client), set())
        self.assertEqual(client.prompts, [])

    def test_no_items_is_not_an_error(self) -> None:
        self.assertEqual(superseded_pairs((), judge=_RecordingClient()), set())

    def test_a_judge_failure_is_abstention_not_a_crash(self) -> None:
        """A sensor that raised on a transport error would abort a 1985-call
        sweep at the first flake."""

        class _Failing:
            def generate(self, prompt, *, system=None):
                raise RuntimeError("502 Bad Gateway")

        matched = superseded_pairs(
            (_item("a", EARLY_TEXT), _item("b", LATE_TEXT)),
            judge=_Failing(),
        )
        self.assertEqual(matched, set())


class MetadataIsUnreachableTest(unittest.TestCase):
    """The decoupling counterfactual, as a test rather than an afterthought.

    This is the check the withdrawn sensor did not have. It does not ask whether
    the sensor scores well; it asks whether the score could survive the metadata
    being scrambled. For a text-only judge the answer must be yes by
    construction, and pinning it means a later refactor that starts reading
    `store` for a tiebreak fails here.
    """

    def test_permuting_every_metadata_field_cannot_change_the_verdict(self) -> None:
        client = _RecordingClient({"10:30": '{"superseded": true, "slot": "sleep"}'})
        as_shipped = superseded_pairs(
            (
                _item("m_stale", EARLY_TEXT, rank=0, store="2026-01-01T00:00:00Z"),
                _item("m_current", LATE_TEXT, rank=1, store="2026-02-01T00:00:00Z"),
            ),
            judge=client,
        )
        scrambled = superseded_pairs(
            (
                # ids swapped, stores inverted, ranks reversed
                _item("m_current", EARLY_TEXT, rank=9, store="2026-02-01T00:00:00Z"),
                _item("m_stale", LATE_TEXT, rank=0, store="haystack"),
            ),
            judge=client,
        )
        self.assertEqual(as_shipped, {"m_stale", "m_current"})
        self.assertEqual(scrambled, {"m_current", "m_stale"})

    def test_identical_texts_under_different_metadata_share_one_cache_entry(self) -> None:
        """The cache key is the text pair. If it included an item ID, the same
        judgment would be paid for once per case and the budget claim would be
        wrong by a factor of the dataset size."""
        client = _RecordingClient()
        cache: dict = {}
        superseded_pairs(
            (_item("m_stale", EARLY_TEXT), _item("m_current", LATE_TEXT)),
            judge=client,
            cache=cache,
        )
        superseded_pairs(
            (_item("q1", EARLY_TEXT, rank=7, store="haystack"),
             _item("q2", LATE_TEXT, rank=8, store="verified")),
            judge=client,
            cache=cache,
        )
        self.assertEqual(len(client.prompts), 1)

    def test_the_pair_is_symmetric(self) -> None:
        """Recall order is not evidence. A judge asked (A, B) and (B, A) must
        yield the same set, or the verdict depends on rank."""
        client = _RecordingClient({"10:30": '{"superseded": true, "slot": "sleep"}'})
        forward = superseded_pairs(
            (_item("x", EARLY_TEXT, rank=0), _item("y", LATE_TEXT, rank=1)),
            judge=client,
        )
        backward = superseded_pairs(
            (_item("y", LATE_TEXT, rank=0), _item("x", EARLY_TEXT, rank=1)),
            judge=client,
        )
        self.assertEqual(forward, backward)


class VersionTest(unittest.TestCase):
    def test_the_sensor_carries_its_own_version(self) -> None:
        self.assertTrue(SLOT_SUPERSESSION_VERSION)
        for other in ("route-a-ir-v1", "route-a-slot-divergence-v1"):
            self.assertNotEqual(SLOT_SUPERSESSION_VERSION, other)

    def test_a_verdict_is_frozen(self) -> None:
        verdict = SupersessionVerdict(superseded=True, slot="sleep")
        with self.assertRaises(Exception):
            verdict.superseded = False  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
