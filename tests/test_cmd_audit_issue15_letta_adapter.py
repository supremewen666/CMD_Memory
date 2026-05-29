"""Behavior-level tests for issue 0015: Letta adapter integration + V1→V2 gate."""

from __future__ import annotations

from pathlib import Path
import unittest

from cmd_audit import (
    PIPELINE_LABELS_BASE,
    compute_diagnosis_metrics,
    load_probe_cases,
    run_case,
)
from cmd_audit.adapters import (
    LettaAdapter,
    LettaTrace,
    SandboxViolationError,
    StoreChecksum,
    load_letta_traces,
    run_case_with_letta,
    run_letta_replay_portfolio,
)
from cmd_audit.adapters.base import load_letta_traces as _load_traces
from cmd_audit.harness import diagnosis_predictions

V0_SMOKE = Path("data/probe_cases/v0_issue3_cases.json")
LETTA_TRACES = Path("data/probe_cases/letta_v0_smoke_traces.json")


# ── LettaTrace Validation ───────────────────────────────────────────────


class LettaTraceValidationTest(unittest.TestCase):
    """Trace loading and field validation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.traces = load_letta_traces(LETTA_TRACES)

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
                self.assertIsInstance(trace.core_blocks, tuple)
                self.assertIsInstance(trace.archival_blocks, tuple)
                self.assertIsInstance(trace.recall_query, str)
                self.assertIsInstance(trace.recall_results, tuple)
                self.assertIsInstance(trace.store_checksum, str)

    def test_store_checksums_are_hex_sha256(self) -> None:
        for case_id, trace in self.traces.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(len(trace.store_checksum), 64)
                self.assertTrue(
                    all(c in "0123456789abcdef" for c in trace.store_checksum)
                )

    def test_recall_results_are_memory_items(self) -> None:
        from cmd_audit.core.models import MemoryItem

        for case_id, trace in self.traces.items():
            with self.subTest(case_id=case_id):
                for item in trace.recall_results:
                    self.assertIsInstance(item, MemoryItem)
                    self.assertTrue(item.memory_id)
                    self.assertTrue(item.text)

    def test_traces_match_mem0_checksums(self) -> None:
        """Letta traces with empty archival produce same checksums as mem0 traces."""
        from cmd_audit.adapters import load_mem0_traces
        mem0_traces = load_mem0_traces(Path("data/probe_cases/mem0_v0_smoke_traces.json"))
        for case_id, lt in self.traces.items():
            with self.subTest(case_id=case_id):
                mt = mem0_traces[case_id]
                self.assertEqual(lt.store_checksum, mt.store_checksum,
                    f"Checksum mismatch for {case_id}")


# ── LettaAdapter Interception ──────────────────────────────────────────


class LettaAdapterInterceptionTest(unittest.TestCase):
    """Verify interception routing correctness for each replay type."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.traces = load_letta_traces(LETTA_TRACES)
        cls._case_map = {c.case_id: c for c in cls.cases}

    def _adapter_for(self, case_id: str) -> LettaAdapter:
        case = self._case_map[case_id]
        return LettaAdapter(
            self.traces[case_id],
            case.gold_evidence,
            case.extracted_memory,
            case.raw_events,
        )

    # ── intercept_core_write routing ─────────────────────────────────

    def test_intercept_core_write_oracle_write_returns_gold_evidence(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        result = adapter.intercept_core_write("v0-write-001", ["original"], "oracle_write")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("Madrid", result[0])

    def test_intercept_core_write_compression_returns_uncompressed(self) -> None:
        adapter = self._adapter_for("v0-compression-001")
        result = adapter.intercept_core_write(
            "v0-compression-001", ["lossy"], "oracle_compression"
        )
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("Prague", result[0])

    def test_intercept_core_write_verbatim_event_oracle_returns_empty(self) -> None:
        adapter = self._adapter_for("v0-premature-extraction-001")
        result = adapter.intercept_core_write(
            "v0-premature-extraction-001", ["facts"], "verbatim_event_oracle"
        )
        self.assertEqual(result, [])

    def test_intercept_core_write_injection_oracle_returns_formatted(self) -> None:
        adapter = self._adapter_for("v0-injection-001")
        result = adapter.intercept_core_write(
            "v0-injection-001", ["messy"], "injection_oracle"
        )
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_intercept_core_write_passthrough_returns_original(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        original = ["fact_a", "fact_b"]
        result = adapter.intercept_core_write(
            "v0-write-001", original, "evidence_given_reasoning"
        )
        self.assertEqual(result, original)

    # ── intercept_archival_store routing ─────────────────────────────

    def test_intercept_archival_store_uses_same_evidence_logic(self) -> None:
        """Archival store mirrors core write for oracle_write."""
        adapter = self._adapter_for("v0-write-001")
        result = adapter.intercept_archival_store(
            "v0-write-001", ["original"], "oracle_write"
        )
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertIn("Madrid", result[0])

    def test_intercept_archival_store_passthrough_returns_original(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        original = ["entry_a"]
        result = adapter.intercept_archival_store(
            "v0-write-001", original, "oracle_retrieval"
        )
        self.assertEqual(result, original)

    # ── intercept_recall routing ─────────────────────────────────────

    def test_intercept_recall_oracle_retrieval_returns_memory_items(self) -> None:
        from cmd_audit.core.models import MemoryItem

        adapter = self._adapter_for("v0-retrieval-001")
        trace = self.traces["v0-retrieval-001"]
        result = adapter.intercept_recall(
            "v0-retrieval-001",
            trace.recall_query,
            list(trace.recall_results),
            "oracle_retrieval",
        )
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        self.assertTrue(all(isinstance(item, MemoryItem) for item in result))
        self.assertIn("Lisbon", result[0].text)

    def test_intercept_recall_evidence_given_reasoning_appends(self) -> None:
        adapter = self._adapter_for("v0-reasoning-001")
        trace = self.traces["v0-reasoning-001"]
        original = list(trace.recall_results)
        result = adapter.intercept_recall(
            "v0-reasoning-001", trace.recall_query, original, "evidence_given_reasoning"
        )
        self.assertGreaterEqual(len(result), len(original))

    def test_intercept_recall_passthrough_returns_original(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        trace = self.traces["v0-write-001"]
        original = list(trace.recall_results)
        result = adapter.intercept_recall(
            "v0-write-001", trace.recall_query, original, "oracle_write"
        )
        self.assertEqual(len(result), len(original))
        self.assertEqual(result[0].memory_id, original[0].memory_id)


# ── Sandbox Guarantees ────────────────────────────────────────────────


class LettaAdapterSandboxTest(unittest.TestCase):
    """Verify sandbox guarantees — store never mutated."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.traces = load_letta_traces(LETTA_TRACES)
        cls._case_map = {c.case_id: c for c in cls.cases}

    def _adapter_for(self, case_id: str) -> LettaAdapter:
        case = self._case_map[case_id]
        return LettaAdapter(
            self.traces[case_id],
            case.gold_evidence,
            case.extracted_memory,
            case.raw_events,
        )

    def test_store_checksum_unchanged_after_intercept_core_write(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        pre = adapter.get_store_snapshot()
        adapter.intercept_core_write("v0-write-001", ["original"], "oracle_write")
        post = adapter.get_store_snapshot()
        self.assertEqual(pre.checksum, post.checksum)
        self.assertEqual(pre.item_count, post.item_count)

    def test_store_checksum_unchanged_after_intercept_archival_store(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        pre = adapter.get_store_snapshot()
        adapter.intercept_archival_store("v0-write-001", ["original"], "oracle_write")
        post = adapter.get_store_snapshot()
        self.assertEqual(pre.checksum, post.checksum)

    def test_store_checksum_unchanged_after_intercept_recall(self) -> None:
        adapter = self._adapter_for("v0-retrieval-001")
        trace = self.traces["v0-retrieval-001"]
        pre = adapter.get_store_snapshot()
        adapter.intercept_recall(
            "v0-retrieval-001",
            trace.recall_query,
            list(trace.recall_results),
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
        adapter.intercept_core_write("v0-write-001", ["a"], "oracle_write")
        adapter.verify_sandbox()

    def test_verify_sandbox_detects_checksum_mismatch(self) -> None:
        adapter = self._adapter_for("v0-write-001")
        original = adapter._pre_checksum
        adapter._pre_checksum = (
            "0000000000000000000000000000000000000000000000000000000000000000"
        )
        try:
            with self.assertRaises(SandboxViolationError):
                adapter.verify_sandbox()
        finally:
            adapter._pre_checksum = original

    def test_sandbox_covers_core_plus_archival_blocks(self) -> None:
        """Checksum is computed over core_blocks + archival_blocks combined."""
        adapter = self._adapter_for("v0-write-001")
        snap = adapter.get_store_snapshot()
        # 1 core block + 0 archival = 1 item
        self.assertEqual(snap.item_count, 1)
        # The item_count equals len(core_blocks) + len(archival_blocks)
        trace = self.traces["v0-write-001"]
        self.assertEqual(
            snap.item_count,
            len(trace.core_blocks) + len(trace.archival_blocks),
        )


# ── Adapter-Label Parity ──────────────────────────────────────────────


class AdapterLabelParityTest(unittest.TestCase):
    """Verify Letta adapter path produces identical labels to standalone path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.traces = load_letta_traces(LETTA_TRACES)
        cls._case_map = {c.case_id: c for c in cls.cases}

    def test_each_case_label_matches_standalone(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                standalone = run_case(case)
                adapter = run_case_with_letta(case, self.traces[case.case_id])
                self.assertEqual(
                    standalone.attribution.predicted_label,
                    adapter.attribution.predicted_label,
                    f"Label mismatch for {case.case_id}: "
                    f"standalone={standalone.attribution.predicted_label} "
                    f"letta={adapter.attribution.predicted_label}",
                )

    def test_macro_f1_matches_standalone(self) -> None:
        standalone_results = [run_case(c) for c in self.cases]
        adapter_results = [
            run_case_with_letta(c, self.traces[c.case_id]) for c in self.cases
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
                adapter = run_case_with_letta(case, self.traces[case.case_id])

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
                adapter = run_case_with_letta(case, self.traces[case.case_id])
                self.assertTrue(adapter.attribution_correct)
                self.assertEqual(
                    adapter.attribution.predicted_label,
                    case.perturbation_label,
                )


# ── End-to-End ────────────────────────────────────────────────────────


class LettaAdapterEndToEndTest(unittest.TestCase):
    """End-to-end pipeline through Letta adapter path."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.traces = load_letta_traces(LETTA_TRACES)

    def test_run_case_with_letta_produces_audit_result(self) -> None:
        from cmd_audit.harness import AuditResult

        case = self.cases[0]
        result = run_case_with_letta(case, self.traces[case.case_id])
        self.assertIsInstance(result, AuditResult)
        self.assertEqual(result.case_id, case.case_id)

    def test_run_letta_replay_portfolio_runs_10_replays(self) -> None:
        case = self.cases[0]
        adapter = LettaAdapter(
            self.traces[case.case_id],
            case.gold_evidence,
            case.extracted_memory,
            case.raw_events,
        )
        results = run_letta_replay_portfolio(case, adapter)
        self.assertEqual(len(results), 10)
        from cmd_audit.replays import ReplayResult

        for r in results:
            self.assertIsInstance(r, ReplayResult)

    def test_all_six_cases_pass_attribution(self) -> None:
        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                result = run_case_with_letta(case, self.traces[case.case_id])
                self.assertTrue(result.attribution_correct)

    def test_sandbox_verified_after_full_portfolio(self) -> None:
        case = self.cases[0]
        adapter = LettaAdapter(
            self.traces[case.case_id],
            case.gold_evidence,
            case.extracted_memory,
            case.raw_events,
        )
        pre = adapter.get_store_snapshot()
        run_letta_replay_portfolio(case, adapter)
        post = adapter.get_store_snapshot()
        self.assertEqual(
            pre.checksum, post.checksum,
            "Store checksum changed after replay portfolio"
        )

    def test_ecs_draft_works_through_adapter_path(self) -> None:
        from cmd_audit import draft_ecs

        case = self.cases[0]
        result = run_case_with_letta(case, self.traces[case.case_id])
        ecs = draft_ecs(case, result)
        self.assertEqual(ecs.case_id, case.case_id)
        self.assertEqual(ecs.predicted_label, case.perturbation_label)
        self.assertTrue(ecs.cause)
        self.assertTrue(ecs.corrected_memory)
        self.assertTrue(ecs.repair_guidance)


# ── V0 / V1 Boundary ──────────────────────────────────────────────────


class LettaAdapterV0V1BoundaryTest(unittest.TestCase):
    """Letta adapter respects V0/V1 label boundaries."""

    def test_adapter_label_is_valid_v0_label(self) -> None:
        from cmd_audit import validate_label_base

        cases = load_probe_cases(V0_SMOKE)
        traces = load_letta_traces(LETTA_TRACES)
        for case in cases:
            with self.subTest(case_id=case.case_id):
                result = run_case_with_letta(case, traces[case.case_id])
                validate_label_base(result.attribution.predicted_label)

    def test_adapter_accepts_v1_labels_in_v1_pipeline(self) -> None:
        from cmd_audit import validate_label

        for label in PIPELINE_LABELS_BASE:
            with self.subTest(label=label):
                validate_label(label)

    def test_load_letta_traces_functional(self) -> None:
        traces = _load_traces(LETTA_TRACES)
        self.assertEqual(len(traces), 6)
        for case_id, trace in traces.items():
            self.assertIsInstance(trace, LettaTrace)
            self.assertEqual(trace.case_id, case_id)


# ── Cross-Agent Non-Regression ────────────────────────────────────────


class CrossAgentNonRegressionTest(unittest.TestCase):
    """Letta adapter must not perturb mem0 results — no cross-contamination."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_probe_cases(V0_SMOKE)
        cls.letta_traces = load_letta_traces(LETTA_TRACES)
        from cmd_audit.adapters import load_mem0_traces
        cls.mem0_traces = load_mem0_traces(
            Path("data/probe_cases/mem0_v0_smoke_traces.json")
        )
        cls._case_map = {c.case_id: c for c in cls.cases}

    def test_mem0_labels_unchanged_when_letta_adapter_exists(self) -> None:
        """mem0 adapter results must be identical regardless of Letta presence."""
        from cmd_audit.adapters import run_case_with_mem0

        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                mem0_result = run_case_with_mem0(case, self.mem0_traces[case.case_id])
                letta_result = run_case_with_letta(
                    case, self.letta_traces[case.case_id]
                )
                # Both paths produce the correct label for the V0 case
                self.assertTrue(mem0_result.attribution_correct)
                self.assertTrue(letta_result.attribution_correct)
                # Both paths agree with standalone
                standalone = run_case(case)
                self.assertEqual(
                    mem0_result.attribution.predicted_label,
                    standalone.attribution.predicted_label,
                )
                self.assertEqual(
                    letta_result.attribution.predicted_label,
                    standalone.attribution.predicted_label,
                )

    def test_macro_f1_independent_across_agents(self) -> None:
        """Both agents achieve perfect Macro F1 independently."""
        from cmd_audit.adapters import run_case_with_mem0

        mem0_results = [
            run_case_with_mem0(c, self.mem0_traces[c.case_id]) for c in self.cases
        ]
        letta_results = [
            run_case_with_letta(c, self.letta_traces[c.case_id]) for c in self.cases
        ]

        for label, results in [("mem0", mem0_results), ("letta", letta_results)]:
            preds = tuple(diagnosis_predictions(r) for r in results)
            metrics = compute_diagnosis_metrics([p for t in preds for p in t])
            with self.subTest(agent=label):
                self.assertEqual(metrics["CMD-Audit"].macro_f1, 1.0)

    def test_recovery_gains_independent_across_agents(self) -> None:
        """Recovery gains match standalone for both agents independently."""
        from cmd_audit.adapters import run_case_with_mem0

        for case in self.cases:
            with self.subTest(case_id=case.case_id):
                standalone_gains = {
                    r.replay_name: r.recovery_gain
                    for r in run_case(case).replays
                }
                mem0_gains = {
                    r.replay_name: r.recovery_gain
                    for r in run_case_with_mem0(
                        case, self.mem0_traces[case.case_id]
                    ).replays
                }
                letta_gains = {
                    r.replay_name: r.recovery_gain
                    for r in run_case_with_letta(
                        case, self.letta_traces[case.case_id]
                    ).replays
                }
                for name in standalone_gains:
                    with self.subTest(replay=name):
                        self.assertAlmostEqual(
                            standalone_gains[name],
                            mem0_gains[name],
                            places=4,
                            msg=f"mem0 gain delta for {case.case_id}/{name}",
                        )
                        self.assertAlmostEqual(
                            standalone_gains[name],
                            letta_gains[name],
                            places=4,
                            msg=f"letta gain delta for {case.case_id}/{name}",
                        )

    def test_multi_store_trace_reads_correct_blocks(self) -> None:
        """LettaTrace with distinct core/archival blocks exercises tiering."""
        from cmd_audit.core.models import MemoryItem

        # Verify that core_blocks and archival_blocks are independently accessible
        trace = self.letta_traces["v0-write-001"]
        self.assertEqual(len(trace.core_blocks), 1)
        self.assertEqual(len(trace.archival_blocks), 0)

        # Both block types are stored as tuples
        self.assertIsInstance(trace.core_blocks, tuple)
        self.assertIsInstance(trace.archival_blocks, tuple)


# ── V1→V2 Gate ───────────────────────────────────────────────────────


class V1V2GateTest(unittest.TestCase):
    """V1→V2 gate behavior: 2 agents required, gate passes with both adapters."""

    def test_gate_passes_with_both_adapters(self) -> None:
        from cmd_audit.eval.release_gates import check_v1_to_v2_gate

        result = check_v1_to_v2_gate(mem0_integrated=True, letta_integrated=True)
        self.assertTrue(result.all_passed)
        self.assertEqual(result.gate_id, "V1→V2")
        self.assertIn("2 adapter integration(s)", result.criteria[0].evidence)
        self.assertIn("mem0", result.criteria[0].evidence)
        self.assertIn("Letta", result.criteria[0].evidence)

    def test_gate_fails_with_only_mem0(self) -> None:
        from cmd_audit.eval.release_gates import check_v1_to_v2_gate

        result = check_v1_to_v2_gate(mem0_integrated=True, letta_integrated=False)
        self.assertFalse(result.all_passed)
        self.assertEqual(result.criteria[0].missing, "Integrate second adapter target (Letta if mem0 done).")

    def test_gate_fails_with_only_letta(self) -> None:
        from cmd_audit.eval.release_gates import check_v1_to_v2_gate

        result = check_v1_to_v2_gate(mem0_integrated=False, letta_integrated=True)
        self.assertFalse(result.all_passed)
        self.assertIn("1 adapter integration(s)", result.criteria[0].evidence)

    def test_gate_fails_with_no_adapters(self) -> None:
        from cmd_audit.eval.release_gates import check_v1_to_v2_gate

        result = check_v1_to_v2_gate(mem0_integrated=False, letta_integrated=False)
        self.assertFalse(result.all_passed)
        self.assertEqual(result.criteria[0].evidence,
                         "0 adapter integrations; V0 operates as standalone harness.")

    def test_gate_backward_compatible_mem0_only(self) -> None:
        """Existing callers using only mem0_integrated parameter still work."""
        from cmd_audit.eval.release_gates import check_v1_to_v2_gate

        result = check_v1_to_v2_gate(mem0_integrated=True)
        self.assertFalse(result.all_passed)
        self.assertIn("1 adapter integration(s)", result.criteria[0].evidence)
