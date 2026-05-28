"""RepairAction + adapter.apply_repair tests — Issue 0020-B."""

from pathlib import Path
import unittest

from cmd_audit import (
    REPAIR_ACTION_TOOL_DEFINITION,
    REPAIR_ACTION_TYPES,
    RepairAction,
    RepairActionResult,
    RepairActionTypeError,
    UnsupportedActionError,
    load_probe_cases,
    validate_repair_action_type,
)
from cmd_audit.core.labels import LabelValidationError

from cmd_audit.adapters.base import (
    AdapterRepairMixin,
    LettaTrace,
    Mem0Trace,
    RepairAction,
    SandboxViolationError,
)
from cmd_audit.adapters.mem0 import Mem0Adapter
from cmd_audit.adapters.letta import LettaAdapter


# ── RepairAction Core Tests ─────────────────────────────────────────────


class RepairActionTypeValidationTest(unittest.TestCase):
    """AC: RepairAction accepts 5 action types, rejects invalid."""

    def test_all_five_valid_action_types(self) -> None:
        for action_type in REPAIR_ACTION_TYPES:
            with self.subTest(action_type=action_type):
                validated = validate_repair_action_type(action_type)
                self.assertEqual(validated, action_type)

    def test_invalid_action_type_raises(self) -> None:
        with self.assertRaises(RepairActionTypeError):
            validate_repair_action_type("delete")

    def test_repair_action_dataclass_accepts_valid(self) -> None:
        for action_type in REPAIR_ACTION_TYPES:
            with self.subTest(action_type=action_type):
                action = RepairAction(
                    action_type=action_type,
                    target_item_id="mem_001",
                    target_store="episodic",
                    content="test content",
                    label="retrieval_error",
                    reasoning="test reasoning",
                )
                self.assertEqual(action.action_type, action_type)

    def test_repair_action_rejects_invalid_type(self) -> None:
        with self.assertRaises(RepairActionTypeError):
            RepairAction(
                action_type="invalid",
                target_item_id=None,
                target_store="episodic",
                content="test",
                label="retrieval_error",
            )

    def test_repair_action_rejects_invalid_label(self) -> None:
        with self.assertRaises(LabelValidationError):
            RepairAction(
                action_type="append",
                target_item_id=None,
                target_store="episodic",
                content="test",
                label="invalid_label",
            )

    def test_repair_action_is_frozen(self) -> None:
        action = RepairAction(
            action_type="append",
            target_item_id=None,
            target_store="episodic",
            content="test",
            label="retrieval_error",
        )
        with self.assertRaises(AttributeError):
            action.content = "new content"


class RepairActionToolDefinitionTest(unittest.TestCase):
    """AC: REPAIR_ACTION_TOOL_DEFINITION is valid JSON-schema-like dict."""

    def test_tool_definition_has_required_fields(self) -> None:
        self.assertIn("name", REPAIR_ACTION_TOOL_DEFINITION)
        self.assertIn("description", REPAIR_ACTION_TOOL_DEFINITION)
        self.assertIn("parameters", REPAIR_ACTION_TOOL_DEFINITION)

    def test_tool_definition_parameters_has_action_type_enum(self) -> None:
        params = REPAIR_ACTION_TOOL_DEFINITION["parameters"]
        self.assertIn("properties", params)
        action_type_def = params["properties"]["action_type"]
        self.assertEqual(action_type_def["enum"], list(REPAIR_ACTION_TYPES))

    def test_tool_definition_has_required_list(self) -> None:
        params = REPAIR_ACTION_TOOL_DEFINITION["parameters"]
        self.assertIn("required", params)
        self.assertIn("action_type", params["required"])
        self.assertIn("content", params["required"])
        self.assertIn("label", params["required"])


class RepairActionResultTest(unittest.TestCase):
    """AC: RepairActionResult captures apply_repair outcome."""

    def test_result_fields(self) -> None:
        action = RepairAction(
            action_type="append",
            target_item_id=None,
            target_store="episodic",
            content="test",
            label="retrieval_error",
        )
        result = RepairActionResult(
            success=True,
            action=action,
            store_checksum_before="abc",
            store_checksum_after="def",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.store_checksum_before, "abc")
        self.assertEqual(result.store_checksum_after, "def")


# ── Adapter Tests ───────────────────────────────────────────────────────


class Mem0AdapterRepairTest(unittest.TestCase):
    """AC: Mem0Adapter supports append/replace, enforces sandbox."""

    def setUp(self) -> None:
        self.trace = Mem0Trace(
            case_id="test_001",
            add_inputs=["original fact"],
            search_query="test query",
            search_results=tuple(),
            store_checksum="abc123",
        )
        self.adapter = Mem0Adapter(
            self.trace,
            gold_evidence=tuple(),
            extracted_memory=tuple(),
            raw_events=tuple(),
        )

    def test_supported_actions(self) -> None:
        self.assertEqual(self.adapter.supported_actions, ("append", "replace"))

    def test_append_action(self) -> None:
        action = RepairAction(
            action_type="append",
            target_item_id=None,
            target_store="episodic",
            content="new fact",
            label="write_error",
        )
        result = self.adapter.apply_repair(action)
        self.assertIn("append", result)
        self.assertIn("new", result)

    def test_reject_unsupported_action(self) -> None:
        action = RepairAction(
            action_type="relocate",
            target_item_id=None,
            target_store="episodic",
            content="test",
            label="retrieval_error",
        )
        with self.assertRaises(UnsupportedActionError) as ctx:
            self.adapter.apply_repair(action)
        self.assertIn("not supported", str(ctx.exception))


class LettaAdapterRepairTest(unittest.TestCase):
    """AC: LettaAdapter supports 4 actions, enforces sandbox."""

    def setUp(self) -> None:
        self.trace = LettaTrace(
            case_id="test_001",
            core_blocks=["original block"],
            archival_blocks=[],
            recall_query="test query",
            recall_results=tuple(),
            store_checksum="abc123",
        )
        self.adapter = LettaAdapter(
            self.trace,
            gold_evidence=tuple(),
            extracted_memory=tuple(),
            raw_events=tuple(),
        )

    def test_supported_actions(self) -> None:
        self.assertEqual(
            self.adapter.supported_actions,
            ("append", "replace", "relocate", "update_routing"),
        )

    def test_append_action(self) -> None:
        action = RepairAction(
            action_type="append",
            target_item_id=None,
            target_store="core",
            content="new block",
            label="write_error",
        )
        result = self.adapter.apply_repair(action)
        self.assertIn("append", result)

    def test_relocate_action(self) -> None:
        action = RepairAction(
            action_type="relocate",
            target_item_id=None,
            target_store="archival",
            content="moved block",
            label="route_error",
        )
        result = self.adapter.apply_repair(action)
        self.assertIn("relocate", result)

    def test_update_routing_action(self) -> None:
        action = RepairAction(
            action_type="update_routing",
            target_item_id=None,
            target_store="archival",
            content="routing config",
            label="route_error",
        )
        result = self.adapter.apply_repair(action)
        self.assertIn("update_routing", result)

    def test_reject_unsupported_action(self) -> None:
        action = RepairAction(
            action_type="update_template",
            target_item_id=None,
            target_store="core",
            content="template",
            label="injection_error",
        )
        with self.assertRaises(UnsupportedActionError) as ctx:
            self.adapter.apply_repair(action)
        self.assertIn("not supported", str(ctx.exception))


class AdapterRepairMixinTest(unittest.TestCase):
    """AC: AdapterRepairMixin defines the protocol."""

    def test_mixin_requires_implementation(self) -> None:
        class DummyAdapter(AdapterRepairMixin):
            supported_actions = ("append",)

        adapter = DummyAdapter()
        with self.assertRaises(NotImplementedError):
            adapter.apply_repair(
                RepairAction(
                    action_type="append",
                    target_item_id=None,
                    target_store="test",
                    content="test",
                    label="write_error",
                )
            )


if __name__ == "__main__":
    unittest.main()
