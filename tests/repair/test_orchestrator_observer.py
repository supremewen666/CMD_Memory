from types import SimpleNamespace

from cmd_audit.repair.orchestrator import RepairOrchestrator


def test_observer_hook_receives_fixed_result_and_failure_is_isolated():
    received = []

    class Observer:
        def record_orchestrator_result(self, result):
            received.append(result)

    attribution = SimpleNamespace(close_deltas=())
    case = SimpleNamespace(case_id="case-1")
    result = RepairOrchestrator(observer=Observer()).run(
        attribution=attribution,
        case=case,
        adapter=object(),
    )
    assert received == [result]

    class BrokenObserver:
        def record_orchestrator_result(self, _result):
            raise RuntimeError("observer unavailable")

    orchestrator = RepairOrchestrator(observer=BrokenObserver())
    same = orchestrator.run(
        attribution=attribution,
        case=case,
        adapter=object(),
    )
    assert same == result
    assert orchestrator.observer_errors == (
        "RuntimeError: observer unavailable",
    )
