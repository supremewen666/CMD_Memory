"""§5.4 dataset validity audit.

The audit's job is to refuse a dataset, so every test here is about a *failure*
being caught rather than a clean dataset passing. Three checks carry most of the
weight and get the most tests:

  * pairing (§5.4 item 1) -- an unpaired case is silently unscoreable, and a
    doubly-paired one makes `state_success` depend on dict ordering;
  * identity scope (item 2) -- an intent naming an item the runtime does not
    carry is an intent the evaluator can never satisfy;
  * the shortcut probe (item 5) -- the one check that can pass for the wrong
    reason, since a fixed probe list cannot prove absence.

`SHORTCUT_MAX_HIT_RATE` is pinned separately: it is a preregistered threshold,
so an edit there changes every verdict without changing any logic.
"""

import unittest

from experiments.validate_tier3_dataset import (
    SHORTCUT_MAX_HIT_RATE,
    SHORTCUT_PROBES,
    DatasetValidityError,
    check_dependency_groups,
    check_family_counts,
    check_forbidden_fields,
    check_injector_attestation,
    check_intent_identity_scope,
    check_intent_pairing,
    check_no_deterministic_shortcut,
    check_template_hints,
    seal_decision,
)


def _case(case_id, family_id="f1", item_ids=("a", "b"), event_ids=("e1",), ranks=None):
    ranks = ranks or list(range(len(item_ids)))
    return {
        "case_id": case_id,
        "family_id": family_id,
        "query": "q",
        "token_budget": 1000,
        "runtime_surface": "route-a-runtime-v1",
        "items": [
            {
                "item_id": item_id,
                "text": f"text for {item_id}",
                "source_event_ids": list(event_ids),
                "store": "default",
                "rank": rank,
                "retrieved": True,
            }
            for item_id, rank in zip(item_ids, ranks)
        ],
        "raw_events": [{"event_id": e, "text": f"event {e}"} for e in event_ids],
    }


def _intent(case_id, family_id="f1", target="a", required=("b",), added=()):
    return {
        "case_id": case_id,
        "family_id": family_id,
        "required_items": [
            {
                "source_memory_id": item_id,
                "required_phrases": ["text"],
                "allowed_dispositions": ["active"],
            }
            for item_id in required
        ],
        "perturbations": [
            {
                "target_item_id": target,
                "allowed_resolutions": ["demoted", "suppressed"],
                "replacement_item_ids": [],
            }
        ],
        "protected_item_ids": [],
        "allowed_added_item_ids": list(added),
        "required_provenance_hashes": [],
        "token_budget": 1000,
        "null_case": False,
        "schema_version": "state-intent-v1",
    }


class FrozenThresholdTest(unittest.TestCase):
    def test_the_shortcut_threshold_is_pinned(self) -> None:
        self.assertEqual(SHORTCUT_MAX_HIT_RATE, 0.50)

    def test_the_probe_list_is_not_empty(self) -> None:
        """An empty probe list would make the shortcut check vacuously pass."""
        self.assertGreater(len(SHORTCUT_PROBES), 0)


class PairingTest(unittest.TestCase):
    """§5.4 item 1: exactly one matching hidden intent per runtime case."""

    def test_a_one_to_one_dataset_passes(self) -> None:
        result = check_intent_pairing([_case("c1"), _case("c2")], [_intent("c1"), _intent("c2")])
        self.assertTrue(result.passed, result.detail)

    def test_a_case_without_an_intent_fails(self) -> None:
        result = check_intent_pairing([_case("c1"), _case("c2")], [_intent("c1")])
        self.assertFalse(result.passed)
        self.assertIn("c2", result.detail)

    def test_an_intent_without_a_case_fails(self) -> None:
        """An orphan intent means the sealed side and the runtime side were
        built from different case lists."""
        result = check_intent_pairing([_case("c1")], [_intent("c1"), _intent("c9")])
        self.assertFalse(result.passed)
        self.assertIn("c9", result.detail)

    def test_two_intents_for_one_case_fails(self) -> None:
        """"Exactly one" is the spec's wording, and a duplicate makes the
        evaluated intent depend on iteration order."""
        result = check_intent_pairing([_case("c1")], [_intent("c1"), _intent("c1")])
        self.assertFalse(result.passed)

    def test_a_family_id_disagreement_fails(self) -> None:
        """The pair must agree on the family, or family-blocked statistics group
        the same case two ways."""
        result = check_intent_pairing([_case("c1", family_id="f1")], [_intent("c1", family_id="f2")])
        self.assertFalse(result.passed)
        self.assertIn("family", result.detail.lower())


class IdentityScopeTest(unittest.TestCase):
    """§5.4 item 2: intents name only runtime identities."""

    def test_an_in_scope_intent_passes(self) -> None:
        result = check_intent_identity_scope([_case("c1")], [_intent("c1", target="a", required=("b",))])
        self.assertTrue(result.passed, result.detail)

    def test_a_target_outside_the_runtime_items_fails(self) -> None:
        result = check_intent_identity_scope([_case("c1")], [_intent("c1", target="zz")])
        self.assertFalse(result.passed)
        self.assertIn("zz", result.detail)

    def test_a_required_item_outside_the_runtime_items_fails(self) -> None:
        result = check_intent_identity_scope([_case("c1")], [_intent("c1", required=("zz",))])
        self.assertFalse(result.passed)

    def test_an_allowed_added_item_outside_the_runtime_items_fails(self) -> None:
        """`allowed_added_item_ids` names things a program may pull in, so it has
        to be in the candidate pool. Naming an item that is nowhere in the case
        makes the addition unreachable."""
        result = check_intent_identity_scope([_case("c1")], [_intent("c1", added=("zz",))])
        self.assertFalse(result.passed)

    def test_a_case_without_an_intent_is_skipped_not_crashed(self) -> None:
        """Pairing has its own check; this one must not raise a KeyError on the
        same defect and mask which check failed."""
        result = check_intent_identity_scope([_case("c1"), _case("c2")], [_intent("c1")])
        self.assertTrue(result.passed, result.detail)

    def test_an_orphan_intent_is_skipped_rather_than_scored_against_nothing(self) -> None:
        """An intent whose case is absent has no runtime identities to be in
        scope of. Substituting an empty case would make every ID it names look
        out of scope and report this as an identity defect, when the actual
        defect is the missing case -- which `check_intent_pairing` reports.
        """
        result = check_intent_identity_scope([_case("c1")], [_intent("c9", target="a")])
        self.assertTrue(result.passed, result.detail)


class ForbiddenFieldTest(unittest.TestCase):
    """§5.4 item 6 / §3.1."""

    def test_a_clean_case_passes(self) -> None:
        self.assertTrue(check_forbidden_fields([_case("c1")]).passed)

    def test_a_top_level_gold_answer_fails(self) -> None:
        case = _case("c1")
        case["gold_answer"] = "Austin"
        result = check_forbidden_fields([case])
        self.assertFalse(result.passed)
        self.assertIn("gold_answer", result.detail)

    def test_a_forbidden_field_nested_in_an_item_fails(self) -> None:
        """The list is enforced at every depth: burying `target_item_id` inside
        an item is the same leak with one more level of indirection."""
        case = _case("c1")
        case["items"][0]["target_item_id"] = "a"
        result = check_forbidden_fields([case])
        self.assertFalse(result.passed)
        self.assertIn("target_item_id", result.detail)

    def test_a_forbidden_field_nested_in_a_list_of_dicts_fails(self) -> None:
        case = _case("c1")
        case["raw_events"][0]["required_phrases"] = ["x"]
        self.assertFalse(check_forbidden_fields([case]).passed)


class TemplateHintTest(unittest.TestCase):
    """§5.4 item 6 / §5.3: injector v1 conventions must not survive."""

    def test_clean_text_passes(self) -> None:
        self.assertTrue(check_template_hints([_case("c1")]).passed)

    def test_an_m_new_prefix_fails(self) -> None:
        case = _case("c1")
        case["items"][0]["text"] = "M_new: I moved to Austin"
        result = check_template_hints([case])
        self.assertFalse(result.passed)
        self.assertIn("M_new:", result.detail)

    def test_a_hint_in_an_event_text_fails(self) -> None:
        case = _case("c1")
        case["raw_events"][0]["text"] = "corrupted_item was written here"
        self.assertFalse(check_template_hints([case]).passed)

    def test_a_hint_in_an_item_id_fails(self) -> None:
        """§5.3 also requires opaque IDs, so a hint in the identifier is as much
        a leak as one in the text.

        The item's text is overwritten with clean prose, because `_case` derives
        text from the ID -- leaving it would put the marker in both fields and a
        check that skipped `item_id` entirely would still pass.
        """
        case = _case("c1", item_ids=("gold_item_1", "b"))
        case["items"][0]["text"] = "she lives in Austin now"
        result = check_template_hints([case])
        self.assertFalse(result.passed)
        self.assertIn("item_id", result.detail)


class FamilyCountTest(unittest.TestCase):
    """§5.4 item 7: family counts meet `n_tier3`."""

    def test_exactly_n_tier3_families_passes(self) -> None:
        cases = [_case(f"c{i}", family_id=f"f{i}") for i in range(30)]
        self.assertTrue(check_family_counts(cases, n_tier3=30).passed)

    def test_one_family_short_fails(self) -> None:
        cases = [_case(f"c{i}", family_id=f"f{i}") for i in range(29)]
        result = check_family_counts(cases, n_tier3=30)
        self.assertFalse(result.passed)
        self.assertIn("29", result.detail)

    def test_many_cases_in_few_families_fails(self) -> None:
        """The requirement is on families, not cases: 300 cases in 3 families
        gives the family-blocked statistics three units, not 300."""
        cases = [_case(f"c{i}", family_id=f"f{i % 3}") for i in range(300)]
        self.assertFalse(check_family_counts(cases, n_tier3=30).passed)


class DependencyGroupTest(unittest.TestCase):
    """§5.4 item 8: dependency groups do not cross the D_confirm boundary."""

    def test_groups_inside_one_split_pass(self) -> None:
        manifest = {
            "dependency_groups": [{"group_id": "g1", "family_ids": ["f1", "f2"]}],
            "split_by_family": {"f1": "D_confirm", "f2": "D_confirm"},
        }
        self.assertTrue(check_dependency_groups(manifest).passed)

    def test_a_group_spanning_two_splits_fails(self) -> None:
        """A shared source across the boundary leaks D_confirm into D_select."""
        manifest = {
            "dependency_groups": [{"group_id": "g1", "family_ids": ["f1", "f2"]}],
            "split_by_family": {"f1": "D_select", "f2": "D_confirm"},
        }
        result = check_dependency_groups(manifest)
        self.assertFalse(result.passed)
        self.assertIn("g1", result.detail)

    def test_a_family_with_no_recorded_split_fails(self) -> None:
        """An unassigned family cannot be shown not to cross, and treating it as
        in-bounds would make the check pass by omission."""
        manifest = {
            "dependency_groups": [{"group_id": "g1", "family_ids": ["f1", "f9"]}],
            "split_by_family": {"f1": "D_confirm"},
        }
        self.assertFalse(check_dependency_groups(manifest).passed)

    def test_a_manifest_with_no_groups_declared_fails(self) -> None:
        """Absence of the key is not evidence of absence of dependencies; §5.4
        requires the groups to be declared so they can be checked."""
        self.assertFalse(check_dependency_groups({"split_by_family": {}}).passed)


class ShortcutProbeTest(unittest.TestCase):
    """§5.4 item 5: no deterministic shortcut from runtime metadata."""

    def test_a_dataset_where_no_probe_wins_passes(self) -> None:
        """Targets spread across ranks, so no fixed positional rule wins."""
        cases, intents = [], []
        for index in range(20):
            item_ids = ("a", "b", "c", "d")
            cases.append(_case(f"c{index}", family_id=f"f{index}", item_ids=item_ids))
            intents.append(
                _intent(f"c{index}", family_id=f"f{index}", target=item_ids[index % 4], required=())
            )
        result = check_no_deterministic_shortcut(cases, intents)
        self.assertTrue(result.passed, result.detail)

    def test_a_dataset_where_the_target_is_always_rank_zero_fails(self) -> None:
        """The defect the check exists for: the repair target is findable by
        position, so a program needs no predicate reasoning at all."""
        cases, intents = [], []
        for index in range(20):
            cases.append(_case(f"c{index}", family_id=f"f{index}", item_ids=("a", "b", "c", "d")))
            intents.append(_intent(f"c{index}", family_id=f"f{index}", target="a", required=()))
        result = check_no_deterministic_shortcut(cases, intents)
        self.assertFalse(result.passed)
        self.assertIn("rank", result.detail.lower())

    def test_the_report_names_the_winning_probe_and_its_rate(self) -> None:
        cases, intents = [], []
        for index in range(20):
            cases.append(_case(f"c{index}", family_id=f"f{index}", item_ids=("a", "b", "c", "d")))
            intents.append(_intent(f"c{index}", family_id=f"f{index}", target="a", required=()))
        result = check_no_deterministic_shortcut(cases, intents)
        self.assertIn("1.00", result.detail)

    def test_a_dataset_with_no_probeable_target_fails(self) -> None:
        """With no perturbation target anywhere there is nothing to probe, so the
        check has established nothing. Passing would let a dataset satisfy §5.4
        item 5 by carrying no intents the probes can score against."""
        cases = [_case("c1")]
        intents = [dict(_intent("c1"), perturbations=[])]
        result = check_no_deterministic_shortcut(cases, intents)
        self.assertFalse(result.passed)
        self.assertIn("no case", result.detail.lower())

    def test_every_probe_rate_is_reported_even_when_the_check_passes(self) -> None:
        """The rates are the descriptive part; hiding them on a pass would make
        a near-miss invisible."""
        cases, intents = [], []
        for index in range(20):
            item_ids = ("a", "b", "c", "d")
            cases.append(_case(f"c{index}", family_id=f"f{index}", item_ids=item_ids))
            intents.append(
                _intent(f"c{index}", family_id=f"f{index}", target=item_ids[index % 4], required=())
            )
        result = check_no_deterministic_shortcut(cases, intents)
        self.assertEqual(len(result.measurements), len(SHORTCUT_PROBES))


class AttestationTest(unittest.TestCase):
    """§5.1/§5.4 item 3: the injector's independence and its own unit tests."""

    def test_a_complete_attestation_passes(self) -> None:
        manifest = {
            "attestation": {
                "resources_read": ["fault taxonomy", "RuntimeRepairCase schema"],
                "injector_unit_tests_pass": True,
            }
        }
        self.assertTrue(check_injector_attestation(manifest).passed)

    def test_a_missing_attestation_fails(self) -> None:
        self.assertFalse(check_injector_attestation({}).passed)

    def test_failing_injector_unit_tests_fail(self) -> None:
        """§5.4 item 3: the tests must *establish* the intended corruption, so a
        recorded failure is disqualifying rather than advisory."""
        manifest = {
            "attestation": {
                "resources_read": ["fault taxonomy"],
                "injector_unit_tests_pass": False,
            }
        }
        self.assertFalse(check_injector_attestation(manifest).passed)

    def test_a_forbidden_resource_in_the_attestation_fails(self) -> None:
        """§5.1 lists what the implementer may not read; declaring one is a
        self-reported independence break."""
        manifest = {
            "attestation": {
                "resources_read": ["fault taxonomy", "build_memtrace_kp_cases.py"],
                "injector_unit_tests_pass": True,
            }
        }
        result = check_injector_attestation(manifest)
        self.assertFalse(result.passed)
        self.assertIn("build_memtrace_kp_cases.py", result.detail)


class SealDecisionTest(unittest.TestCase):
    """§5.4 "before sealing": the aggregate is a conjunction."""

    def test_all_checks_passing_seals(self) -> None:
        from experiments.validate_tier3_dataset import ValidityCheck

        checks = [ValidityCheck("a", True, "ok"), ValidityCheck("b", True, "ok")]
        self.assertEqual(seal_decision(checks), "SEAL")

    def test_one_failing_check_refuses(self) -> None:
        from experiments.validate_tier3_dataset import ValidityCheck

        checks = [ValidityCheck("a", True, "ok"), ValidityCheck("b", False, "bad")]
        self.assertEqual(seal_decision(checks), "REFUSE")

    def test_an_empty_check_list_refuses_rather_than_seals(self) -> None:
        """`all([])` is `True`, so the conjunction alone would seal a dataset no
        check ever looked at."""
        self.assertEqual(seal_decision([]), "REFUSE")


class MissingInputTest(unittest.TestCase):
    def test_loading_a_nonexistent_dataset_is_a_dataset_validity_error(self) -> None:
        from pathlib import Path

        from experiments.validate_tier3_dataset import load_tier3_dataset

        with self.assertRaises(DatasetValidityError):
            load_tier3_dataset(Path("/nonexistent/tier3"))


if __name__ == "__main__":
    unittest.main()
