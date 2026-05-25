"""Behavior-level tests for issue 0014: mem0 adapter integration."""

from __future__ import annotations

from pathlib import Path
import unittest

from cmd_audit import (
    V0_PIPELINE_LABELS,
    compute_diagnosis_metrics,
    diagnosis_predictions,
    load_probe_cases,
    run_case,
)
from cmd_audit.adapters import (
    Mem0Adapter,
    Mem0Trace,
    SandboxViolationError,
    StoreChecksum,
    load_mem0_traces,
    run_case_with_mem0,
    run_mem0_replay_portfolio,
)
from cmd_audit.adapters.base import load_mem0_traces as _load_traces

V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")
MEM0_TRACES = Path("data/probe_cases/mem0_v0_smoke_traces.json")


# ── Mem0Trace Validation ──────────────────────────────────────────────


class Mem0TraceValidationTest(unittest.TestCase):
    """Trace loading and field validation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.traces = load_mem0_traces(MEM0_TRACES)

    def test_six_traces_exist(self) -> None:
        self.assertEqual(len(self.traces), 6)

    def test_traces_have_required_keys(self) -> None:
        expected_ids = {
            "v0-write-001",
            "v0-compression-001",
            "v0-premature-extraction-001",
            "v0-retrieval-001",
            "v0-injection-001",
            "v0-reasoning-001",
        }
        self.assertEqual(set(self.traces), expected_ids)

    def test_each_trace_has_required_fields(self) -> None:
        for case_id, trace in self.traces.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(trace.case_id, case_id)
                self.assertIsInstance(trace.add_inputs, tuple)
                self.assertIsInstance(trace.search_query, str)
                self.assertIsInstance(trace.search_results, tuple)
                self.assertIsInstance(trace.store_checksum, str)

    def test_store_checksums_are_hex_sha256(self) -> None:
        for case_id, trace in self.traces.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(len(trace.store_checksum), 64)
                self.assertTrue(
                    all(c in "0123456789abcdef" for c in trace.store_checksum)
                )

    def test_search_results_are_memory_items(self) -> None:
        from cmd_audit.models import MemoryItem

        for case_id, trace in self.traces.items():
            with self.subTest(case_id=case_id):
                for item in trace.search_results:
                    self.assertIsInstance(item, MemoryItem)
                    self.assertTrue(item.memory_id)
                    self.assertTrue(item.text)


# ── Mem0Adapter Interception ──────────────────────────────────────────


class Mem0AdapterInterceptionTest(unittest.TestCase):
    """Verify interception routing correctness for each replay type."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.traces = load_mem0_traces(MEM0_TRACES)
        cls._case_map = {c.case_id: c for c in cls.cases}

    def _adapter_for(self, case_id: str) -> Mem0Adapter:
        case = self._case_map[case_id]
        return Mem0Adapter(
            self.traces[case_id],
            case.gold_evidence,
            case.extracted_memory,
            case.raw_events,
        )

    # ── intercept_add routing ──────────────────────────────────────

    def test_intercept_add_oracle_write_returns_gold_evidence(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        result = adapter.intercept_add("v0-write-001", ["original"], "oracle_write")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("Madrid", result[0])

    def test_intercept_add_compression_returns_uncompressed(self) -> None:
        adapter = self._adapter_for("v0-compression-001")
        result = adapter.intercept_add(
            "v0-compression-001", ["lossy"], "oracle_compression"
        )
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("Prague", result[0])

    def test_intercept_add_verbatim_event_oracle_returns_empty(self) -> None:
        adapter = self._adapter_for("v0-premature-extraction-001")
        result = adapter.intercept_add(
            "v0-premature-extraction-001", ["facts"], "verbatim_event_oracle"
        )
        self.assertEqual(result, [])

    def test_intercept_add_injection_oracle_returns_formatted(self) -> None:
        adapter = self._adapter_for("v0-injection-001")
        result = adapter.intercept_add(
            "v0-injection-001", ["messy"], "injection_oracle"
        )
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_intercept_add_passthrough_returns_original(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        original = ["fact_a", "fact_b"]
        result = adapter.intercept_add(
            "v0-write-001", original, "evidence_given_reasoning"
        )
        self.assertEqual(result, original)

    # ── intercept_search routing ──────────────────────────────────

    def test_intercept_search_oracle_retrieval_returns_memory_items(self) -> None:
        from cmd_audit.models import MemoryItem

        adapter = self._adapter_for("v0-retrieval-001")
        trace = self.traces["v0-retrieval-001"]
        result = adapter.intercept_search(
            "v0-retrieval-001",
            trace.search_query,
            list(trace.search_results),
            "oracle_retrieval",
        )
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertTrue(all(isinstance(item, MemoryItem) for item in result))
        self.assertIn("Lisbon", result[0].text)

    def test_intercept_search_evidence_given_reasoning_appends(self) -> None:
        adapter = self._adapter_for("v0-reasoning-001")
        trace = self.traces["v0-reasoning-001"]
        original = list(trace.search_results)
        result = adapter.intercept_search(
            "v0-reasoning-001", trace.search_query, original, "evidence_given_reasoning"
        )
        self.assertGreaterEqual(len(result), len(original))

    def test_intercept_search_passthrough_returns_original(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        trace = self.traces["v0-write-001"]
        original = list(trace.search_results)
        result = adapter.intercept_search(
            "v0-write-001", trace.search_query, original, "oracle_write"
        )
        self.assertEqual(len(result), len(original))
        self.assertEqual(result[0].memory_id, original[0].memory_id)


# ── Sandbox Guarantees ────────────────────────────────────────────────


class Mem0AdapterSandboxTest(unittest.TestCase):
    """Verify sandbox guarantees — store never mutated."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.traces = load_mem0_traces(MEM0_TRACES)
        cls._case_map = {c.case_id: c for c in cls.cases}

    def _adapter_for(self, case_id: str) -> Mem0Adapter:
        case = self._case_map[case_id]
        return Mem0Adapter(
            self.traces[case_id],
            case.gold_evidence,
            case.extracted_memory,
            case.raw_events,
        )

    def test_store_checksum_unchanged_after_intercept_add(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        pre = adapter.get_store_snapshot()
        adapter.intercept_add("v0-write-001", ["original"], "oracle_write")
        post = adapter.get_store_snapshot()
        self.assertEqual(pre.checksum, post.checksum)
        self.assertEqual(pre.item_count, post.item_count)

    def test_store_checksum_unchanged_after_intercept_search(self) -> None:
        adapter = self._adapter_for("v0-retrieval-001")
        trace = self.traces["v0-retrieval-001"]
        pre = adapter.get_store_snapshot()
        adapter.intercept_search(
            "v0-retrieval-001",
            trace.search_query,
            list(trace.search_results),
            "oracle_retrieval",
        )
        post = adapter.get_store_snapshot()
        self.assertEqual(pre.checksum, post.checksum)

    def test_get_store_snapshot_returns_correct_data(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        snap = adapter.get_store_snapshot()
        self.assertIsInstance(snap, StoreChecksum)
        self.assertEqual(snap.item_count, 1)
        self.assertEqual(len(snap.checksum), 64)

    def test_verify_sandbox_passes_when_no_mutation(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        adapter.intercept_add("v0-write-001", ["a"], "oracle_write")
        # Should not raise
        adapter.verify_sandbox()

    def test_verify_sandbox_detects_checksum_mismatch(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        original = adapter._pre_checksum
        # Overwrite the pre_checksum to simulate a store mutation being detected
        adapter._pre_checksum = (
            "0000000000000000000000000000000000000000000000000000000000000000"
        )
        try:
            with self.assertRaises(SandboxViolationError):
                adapter.verify_sandbox()
        finally:
            adapter._pre_checksum = original


# ── Adapter-Label Parity ──────────────────────────────────────────────


class AdapterLabelParityTest(unittest.TestCase):
    """Verify adapter path produces identical labels to standalone path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.traces = load_mem0_traces(MEM0_TRACES)
        cls._case_map = {c.case_id: c for c in cls.cases}

    def test_each_case_label_matches_standalone(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                standalone = run_case(case)
                adapter = run_case_with_mem0(case, self.traces[case.case_id])
                self.assertEqual(
                    standalone.attribution.predicted_label,
                    adapter.attribution.predicted_label,
                    f"Label mismatch for {case.case_id}: "
                    f"standalone={standalone.attribution.predicted_label} "
                    f"adapter={adapter.attribution.predicted_label}",
                )

    def test_macro_f1_matches_standalone(self) -> None:
        standalone_results = [run_case(c) for c in self.cases]
        adapter_results = [
            run_case_with_mem0(c, self.traces[c.case_id]) for c in self.cases
        ]

        s_preds = tuple(diagnosis_predictions(r) for r in standalone_results)
        a_preds = tuple(diagnosis_predictions(r) for r in adapter_results)

        s_metrics = compute_diagnosis_metrics([p for t in s_preds for p in t])
        a_metrics = compute_diagnosis_metrics([p for t in a_preds for p in t])

        standalone_f1 = s_metrics["CMD-Audit"].macro_f1
        adapter_f1 = a_metrics["CMD-Audit"].macro_f1

        self.assertEqual(standalone_f1, 1.0)
        self.assertEqual(adapter_f1, 1.0)
        self.assertEqual(standalone_f1, adapter_f1)

    def test_recovery_gains_close_to_standalone(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                standalone = run_case(case)
                adapter = run_case_with_mem0(case, self.traces[case.case_id])

                s_gains = {r.replay_name: r.recovery_gain for r in standalone.replays}
                a_gains = {r.replay_name: r.recovery_gain for r in adapter.replays}

                for name in s_gains:
                    with self.subTest(replay=name):
                        self.assertAlmostEqual(
                            s_gains[name],
                            a_gains[name],
                            places=4,
                            msg=f"Recovery gain delta for {case.case_id}/{name}",
                        )

    def test_attribution_correct_for_all_v0_labels(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                adapter = run_case_with_mem0(case, self.traces[case.case_id])
                self.assertTrue(adapter.attribution_correct)
                self.assertEqual(
                    adapter.attribution.predicted_label,
                    case.perturbation_label,
                )


# ── End-to-End ────────────────────────────────────────────────────────


class Mem0AdapterEndToEndTest(unittest.TestCase):
    """End-to-end pipeline through adapter path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.traces = load_mem0_traces(MEM0_TRACES)

    def test_run_case_with_mem0_produces_audit_result(self) -> None:
        from cmd_audit.harness import AuditResult

        case = self.cases[0]
        result = run_case_with_mem0(case, self.traces[case.case_id])
        self.assertIsInstance(result, AuditResult)
        self.assertEqual(result.case_id, case.case_id)

    def test_run_mem0_replay_portfolio_runs_10_replays(self) -> None:
        case = self.cases[0]
        adapter = Mem0Adapter(
            self.traces[case.case_id],
            case.gold_evidence,
            case.extracted_memory,
            case.raw_events,
        )
        results = run_mem0_replay_portfolio(case, adapter)
        self.assertEqual(len(results), 10)
        from cmd_audit.replays import ReplayResult

        for r in results:
            self.assertIsInstance(r, ReplayResult)

    def test_all_six_cases_pass_attribution(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                result = run_case_with_mem0(case, self.traces[case.case_id])
                self.assertTrue(result.attribution_correct)

    def test_sandbox_verified_after_full_portfolio(self) -> None:
        case = self.cases[0]
        adapter = Mem0Adapter(
            self.traces[case.case_id],
            case.gold_evidence,
            case.extracted_memory,
            case.raw_events,
        )
        pre = adapter.get_store_snapshot()
        run_mem0_replay_portfolio(case, adapter)
        post = adapter.get_store_snapshot()
        self.assertEqual(
            pre.checksum, post.checksum, "Store checksum changed after replay portfolio"
        )

    def test_ecs_draft_works_through_adapter_path(self) -> None:
        from cmd_audit import draft_ecs

        case = self.cases[0]
        result = run_case_with_mem0(case, self.traces[case.case_id])
        ecs = draft_ecs(case, result)
        self.assertEqual(ecs.case_id, case.case_id)
        self.assertEqual(ecs.predicted_label, case.perturbation_label)
        self.assertTrue(ecs.cause)
        self.assertTrue(ecs.corrected_memory)
        self.assertTrue(ecs.repair_guidance)


# ── V0 / V1 Boundary ──────────────────────────────────────────────────


class Mem0AdapterV0V1BoundaryTest(unittest.TestCase):
    """Adapter respects V0/V1 label boundaries."""

    def test_adapter_label_is_valid_v0_label(self) -> None:
        from cmd_audit import validate_v0_label

        cases = load_probe_cases(V0_SMOKE)
        traces = load_mem0_traces(MEM0_TRACES)
        for case in cases:
            with self.subTest(case_id=case.case_id):
                result = run_case_with_mem0(case, traces[case.case_id])
                # All labels should be valid V0 labels (adapter parity with standalone)
                validate_v0_label(result.attribution.predicted_label)

    def test_adapter_accepts_v1_labels_in_v1_pipeline(self) -> None:
        from cmd_audit import validate_v1_label

        for label in V0_PIPELINE_LABELS:
            with self.subTest(label=label):
                validate_v1_label(label)

    def test_load_mem0_traces_functional(self) -> None:
        traces = _load_traces(MEM0_TRACES)
        self.assertEqual(len(traces), 6)
        for case_id, trace in traces.items():
            self.assertIsInstance(trace, Mem0Trace)
            self.assertEqual(trace.case_id, case_id)
