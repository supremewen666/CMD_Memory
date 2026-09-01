"""Strict v2 Stage-5 development aggregation (structural proxies only)."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.contracts import canonical_sha256
from cmd_audit.spec_v03.stage5_executor import STAGE5_EXECUTOR_SCHEMA

SCHEMA = "cmd-spec-v03-stage5-transfer-aggregate-v2"
MANIFEST_SCHEMA = "cmd-spec-v03-stage5-aggregation-manifest-v1"
STAGE59_SCHEMA = "cmd-spec-v03-stage59-runner-v1"
DISCLOSURE = "DEVELOPMENT_STRUCTURAL_ONLY: all safety metrics are structural proxies, not confirmatory safety truth."
HEX = re.compile(r"^[0-9a-f]{64}$")
CONDITIONS = {"reset", "matched", "global", "global_prefix"}
SEL = {
    "arm",
    "event_index",
    "case_id",
    "candidate_skill_revision_ids",
    "selected_skill_revision_id",
    "backbone_prediction_sha256",
    "backbone_scores",
    "selected_at_event_index",
    "observed_after_event_index",
    "selection_id",
    "selection_mode",
    "router_snapshot_before_sha256",
    "router_snapshot_after_sha256",
    "algorithm_snapshot_sha256",
    "abstain_reason",
    "record_sha256",
}
REC = {
    "arm",
    "receipt_sha256",
    "selection_id",
    "selected_skill_revision_id",
    "selected_at_event_index",
    "observed_after_event_index",
    "utility",
    "settled_before_event_index",
    "posterior_before_sha256",
    "posterior_after_sha256",
    "valid",
    "rolled_back",
    "delayed_regression",
    "safety_passed",
    "invariant_passed",
    "locality_cost",
    "collateral_cost",
    "operator_family",
    "strategy_id",
    "recurrence_after_commit",
    "regime",
    "posterior_updates",
}
UPD = {
    "key",
    "before_precision",
    "before_natural",
    "before_mean",
    "after_precision",
    "after_natural",
    "after_mean",
}


def bad(s: str) -> None:
    raise ValueError("invalid Stage 5 aggregation input: " + s)


def sha(v: object, n: str) -> str:
    if not isinstance(v, str) or not HEX.fullmatch(v):
        bad(n + " must be SHA-256")
    return v


def num(v: object, n: str) -> float:
    if (
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not math.isfinite(float(v))
    ):
        bad(n + " must be finite")
    return float(v)


def mean(v: Sequence[float]) -> float | None:
    return sum(v) / len(v) if v else None


def status(v: bool | None) -> str:
    return "PASS" if v is True else "FAIL" if v is False else "INSUFFICIENT_DATA"


def input_specs(
    manifest: Path | None, paths: Sequence[Path]
) -> list[tuple[Path, str | None, str | None]]:
    if manifest is None:
        return [(p, None, None) for p in paths]
    raw = json.loads(manifest.read_text())
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"schema_version", "inputs"}
        or raw["schema_version"] != MANIFEST_SCHEMA
        or not isinstance(raw["inputs"], list)
    ):
        bad("manifest schema invalid")
    if not raw["inputs"]:
        bad("manifest inputs must be non-empty")
    out = []
    seen = set()
    for x in raw["inputs"]:
        if (
            not isinstance(x, Mapping)
            or not {"path", "condition"} <= set(x) <= {"path", "condition", "stream_id"}
            or x["condition"] not in CONDITIONS
            or not isinstance(x["path"], str)
        ):
            bad("manifest input invalid")
        if x.get("stream_id") is not None and (
            not isinstance(x["stream_id"], str) or not x["stream_id"]
        ):
            bad("manifest stream_id invalid")
        item = (
            (manifest.parent / x["path"]).resolve(),
            x["condition"],
            x.get("stream_id"),
        )
        if item in seen:
            bad("manifest has duplicate input")
        seen.add(item)
        out.append(item)
    return out


def validate(path: Path, explicit: str | None, stream: str | None) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    ident = {
        "run_id": None,
        "model_id": None,
        "seed": None,
        "order_manifest_sha256": None,
    }
    if not isinstance(raw, Mapping):
        bad(f"{path}: root must be object")
    if raw.get("schema_version") == STAGE59_SCHEMA:
        if set(raw) != {
            "schema_version",
            "config",
            "order_manifest_sha256",
            "results",
            "unsupported_capabilities",
            "report_sha256",
        }:
            bad("Stage59 schema invalid")
        body = {k: raw[k] for k in raw if k != "report_sha256"}
        if canonical_sha256(body) != sha(raw["report_sha256"], "stage59 report hash"):
            bad("Stage59 report hash mismatch")
        c = raw["config"]
        r = raw["results"]
        if (
            not isinstance(c, Mapping)
            or c.get("schema_version") != STAGE59_SCHEMA
            or not isinstance(r, Mapping)
            or not isinstance(r.get("stage5"), Mapping)
        ):
            bad("Stage59 config/stage5 invalid")
        for k in ("run_id", "model_id"):
            if not isinstance(c.get(k), str) or not c[k]:
                bad("Stage59 config." + k + " missing")
        if isinstance(c.get("seed"), bool) or not isinstance(c.get("seed"), int):
            bad("Stage59 config.seed missing")
        ident.update(
            run_id=c["run_id"],
            model_id=c["model_id"],
            seed=c["seed"],
            order_manifest_sha256=raw["order_manifest_sha256"],
        )
        top = r["stage5"]
        if top.get("schema_version") == "cmd-spec-v03-stage5-executor-v1":
            bad(
                "v1 Stage5 result lacks telemetry and cannot be recovered; rerun Stage5 with schema v2"
            )
    elif raw.get("schema_version") == "cmd-spec-v03-stage5-executor-v1":
        bad(
            "v1 Stage5 result lacks telemetry and cannot be recovered; rerun Stage5 with schema v2"
        )
    else:
        top = raw
    required = {
        "schema_version",
        "config_sha256",
        "order_manifest_sha256",
        "backbone_prediction_sha256s",
        "arms",
        "resource_usage",
        "report_sha256",
    }
    allowed = required | {"provider_call_audit"}
    if (
        not required <= set(top) <= allowed
        or top.get("schema_version") != STAGE5_EXECUTOR_SCHEMA
    ):
        bad("requires exact Stage5 v2 schema " + STAGE5_EXECUTOR_SCHEMA)
    if "provider_call_audit" in top:
        audit = top["provider_call_audit"]
        if not isinstance(audit, Sequence) or isinstance(audit, (str, bytes)):
            bad("provider_call_audit must be a list of objects")
        if any(not isinstance(item, Mapping) for item in audit):
            bad("provider_call_audit entries must be objects")
    if canonical_sha256({k: top[k] for k in required - {"report_sha256"}}) != sha(
        top["report_sha256"], "stage5 report hash"
    ):
        bad("Stage5 report hash mismatch")
    sha(top["config_sha256"], "config_sha256")
    sha(top["order_manifest_sha256"], "order hash")
    if (
        ident["order_manifest_sha256"] is not None
        and ident["order_manifest_sha256"] != top["order_manifest_sha256"]
    ):
        bad("Stage59/Stage5 order hash mismatch")
    ident["order_manifest_sha256"] = top["order_manifest_sha256"]
    if not isinstance(top["arms"], list):
        bad("arms invalid")
    arms = []
    arm_fields = {
        "arm",
        "status",
        "selection_records",
        "receipt_records",
        "censored_selection_ids",
        "algorithm_snapshot",
        "algorithm_snapshot_sha256",
        "resource_usage",
        "adaptation_prefix_event_count",
        "scored_suffix_event_count",
        "imported_router_snapshot",
    }
    for arm in top["arms"]:
        if (
            not isinstance(arm, Mapping)
            or set(arm) != arm_fields
            or not isinstance(arm.get("arm"), str)
            or not isinstance(arm.get("selection_records"), list)
            or not isinstance(arm.get("receipt_records"), list)
        ):
            bad("arm invalid")
        if (
            isinstance(arm["adaptation_prefix_event_count"], bool)
            or not isinstance(arm["adaptation_prefix_event_count"], int)
            or isinstance(arm["scored_suffix_event_count"], bool)
            or not isinstance(arm["scored_suffix_event_count"], int)
        ):
            bad("arm suffix/prefix counts invalid")
        selections = {}
        for row in arm["selection_records"]:
            if (
                not isinstance(row, Mapping)
                or set(row) != SEL
                or row["arm"] != arm["arm"]
            ):
                bad("selection schema invalid")
            copy = dict(row)
            h = copy.pop("record_sha256")
            if canonical_sha256(copy) != sha(h, "selection record hash"):
                bad("selection record hash mismatch")
            if row["selection_id"] is not None:
                if row["selection_id"] in selections:
                    bad("duplicate selection")
                selections[row["selection_id"]] = dict(row)
        rec = []
        for row in arm["receipt_records"]:
            if (
                not isinstance(row, Mapping)
                or set(row) != REC
                or row["arm"] != arm["arm"]
                or row["selection_id"] not in selections
            ):
                bad("receipt schema/binding invalid")
            s = selections[row["selection_id"]]
            if any(
                row[k] != s[k]
                for k in (
                    "selected_skill_revision_id",
                    "selected_at_event_index",
                    "observed_after_event_index",
                )
            ):
                bad("receipt/selection binding mismatch")
            if (
                row["observed_after_event_index"] != row["settled_before_event_index"]
                or row["selected_at_event_index"] >= row["observed_after_event_index"]
            ):
                bad("receipt maturity invalid")
            for k in (
                "receipt_sha256",
                "posterior_before_sha256",
                "posterior_after_sha256",
            ):
                sha(row[k], k)
            x = dict(row)
            x["utility"] = num(row["utility"], "utility")
            x["case_id"] = s["case_id"]
            for k in ("valid", "rolled_back", "delayed_regression"):
                if not isinstance(row[k], bool):
                    bad(k + " must bool")
            for k in ("safety_passed", "invariant_passed", "recurrence_after_commit"):
                if row[k] is not None and not isinstance(row[k], bool):
                    bad(k + " must bool/null")
            for k in ("locality_cost", "collateral_cost"):
                if row[k] is not None:
                    x[k] = num(row[k], k)
                    if x[k] < 0:
                        bad(k + " must be non-negative")
            for k in ("operator_family", "strategy_id"):
                if row[k] is not None and (not isinstance(row[k], str) or not row[k]):
                    bad(k + " must be a non-empty string/null")
            if (
                not isinstance(row["regime"], str)
                or not row["regime"]
                or not isinstance(row["posterior_updates"], list)
            ):
                bad("regime/posterior updates invalid")
            if (
                arm["arm"] not in {
                    "mix_ghost",
                    "ghost_hierarchy",
                    "routing_global",
                    "routing_global_pattern",
                    "routing_global_pattern_local",
                    "routing_full_no_support_gate",
                }
                and row["posterior_updates"]
            ):
                bad("non-GHOST posterior updates must be unavailable/empty")
            for u in row["posterior_updates"]:
                if (
                    not isinstance(u, Mapping)
                    or set(u) != UPD
                    or not isinstance(u["key"], list)
                    or not u["key"]
                    or any(not isinstance(k, str) or not k for k in u["key"])
                ):
                    bad("posterior update invalid")
                for k in UPD - {"key"}:
                    if u[k] is None:
                        bad("posterior update fields must be explicit")
                    num(u[k], k)
                if (
                    u["before_precision"] <= 0
                    or u["after_precision"] <= 0
                    or abs(
                        u["before_mean"] - u["before_natural"] / u["before_precision"]
                    )
                    > 1e-12
                    or abs(u["after_mean"] - u["after_natural"] / u["after_precision"])
                    > 1e-12
                ):
                    bad("posterior update precision/mean invalid")
            if (
                row["posterior_updates"]
                and row["posterior_before_sha256"] == row["posterior_after_sha256"]
            ):
                bad("posterior update requires changed hashes")
            rec.append(x)
        arms.append(
            {
                "arm": arm["arm"],
                "receipts": rec,
                "selections": list(selections.values()),
                "imported": bool(arm.get("imported_router_snapshot", False)),
                "prefix": arm.get("adaptation_prefix_event_count", 0),
            }
        )
    ghost = [x for x in arms if x["arm"] in {"mix_ghost", "ghost_hierarchy"}]
    inferred = (
        "global_prefix"
        if ghost and all(x["imported"] and x["prefix"] for x in ghost)
        else "global"
        if ghost and all(x["imported"] and not x["prefix"] for x in ghost)
        else "reset"
        if ghost and all(not x["imported"] for x in ghost)
        else None
    )
    if explicit is None and inferred is None:
        bad("condition ambiguous; use aggregation manifest")
    if explicit is not None:
        expected = {
            "reset": lambda x: not x["imported"] and not x["prefix"],
            "matched": lambda x: x["imported"] and not x["prefix"],
            "global": lambda x: x["imported"] and not x["prefix"],
            "global_prefix": lambda x: x["imported"] and bool(x["prefix"]),
        }[explicit]
        if not ghost or not all(expected(x) for x in ghost):
            bad("manifest condition contradicts GHOST import/prefix wiring")
    return {
        "identity": {**ident, "stream_id": stream or ident["order_manifest_sha256"]},
        "condition": explicit or inferred,
        "arms": arms,
    }


def arm_index(
    run: Mapping[str, Any], name: str
) -> dict[tuple[str, int], Mapping[str, Any]]:
    a = next((x for x in run["arms"] if x["arm"] == name), None)
    return (
        {}
        if a is None
        else {(r["case_id"], r["selected_at_event_index"]): r for r in a["receipts"]}
    )


def pair(a: Mapping[str, Any], b: Mapping[str, Any], arm: str):
    x, y = arm_index(a, arm), arm_index(b, arm)
    return [(x[k], y[k]) for k in sorted(set(x) & set(y))]


def paired_safety(
    pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> tuple[int, float | None]:
    deltas = [
        float(left["safety_passed"]) - float(right["safety_passed"])
        for left, right in pairs
        if left["safety_passed"] is not None and right["safety_passed"] is not None
    ]
    return len(deltas), mean(deltas)


def markdown_table(
    title: str, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> str:
    def render(value: object) -> str:
        if value is None:
            return "unavailable"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    lines = [
        f"### {title}",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend(
        "| " + " | ".join(render(row.get(column)) for column in columns) + " |"
        for row in rows
    )
    return "\n".join(lines)


def combine_gate(values: Sequence[str]) -> str:
    """Fail dominates incomplete coverage, which dominates a complete pass."""
    if not values:
        return "INSUFFICIENT_DATA"
    if "FAIL" in values:
        return "FAIL"
    if "INSUFFICIENT_DATA" in values:
        return "INSUFFICIENT_DATA"
    return "PASS"


def aggregate(paths: Sequence[Path], *, manifest: Path | None = None) -> dict[str, Any]:
    runs = [validate(p, c, s) for p, c, s in input_specs(manifest, paths)]
    router = []
    for r in runs:
        x, y = arm_index(r, "mix_ghost"), arm_index(r, "ghost_hierarchy")
        ps = [(x[k], y[k]) for k in sorted(set(x) & set(y))]
        if ps:
            d = mean([a["utility"] - b["utility"] for a, b in ps])
            safety_count, safety_delta = paired_safety(ps)
            router.append(
                {
                    "identity": r["identity"],
                    "comparison": "mix_ghost>ghost_hierarchy",
                    "matched_event_count": len(ps),
                    "mean_delta": d,
                    "safety_matched_event_count": safety_count,
                    "mean_safety_delta": safety_delta,
                    "status": status(d > 0),
                }
            )
    groups = defaultdict(list)
    for r in runs:
        i = r["identity"]
        groups[
            (i["stream_id"], i["order_manifest_sha256"], i["model_id"], i["seed"])
        ].append(r)
    transfer = []
    for g in groups.values():
        c = defaultdict(list)
        for r in g:
            c[r["condition"]].append(r)
        for lhs in ("matched", "global"):
            for a in c[lhs]:
                for b in c["reset"]:
                    for arm in sorted(
                        ({x["arm"] for x in a["arms"]} & {x["arm"] for x in b["arms"]})
                        & {"mix_ghost", "ghost_hierarchy"}
                    ):
                        ps = pair(a, b, arm)
                        if ps:
                            d = mean([x["utility"] - y["utility"] for x, y in ps])
                            safety_count, safety_delta = paired_safety(ps)
                            transfer.append(
                                {
                                    "comparison": lhs + ">reset",
                                    "arm": arm,
                                    "matched_event_count": len(ps),
                                    "mean_delta": d,
                                    "safety_matched_event_count": safety_count,
                                    "mean_safety_delta": safety_delta,
                                    "status": status(d > 0),
                                    "identity": a["identity"],
                                }
                            )
        for a in c["global_prefix"]:
            for rhs in ("global", "matched"):
                for b in c[rhs]:
                    for arm in sorted(
                        ({x["arm"] for x in a["arms"]} & {x["arm"] for x in b["arms"]})
                        & {"mix_ghost", "ghost_hierarchy"}
                    ):
                        ps = pair(a, b, arm)
                        if ps:
                            d = mean([x["utility"] - y["utility"] for x, y in ps])
                            safety_count, safety_delta = paired_safety(ps)
                            transfer.append(
                                {
                                    "comparison": "global_prefix>" + rhs,
                                    "arm": arm,
                                    "matched_event_count": len(ps),
                                    "mean_delta": d,
                                    "safety_matched_event_count": safety_count,
                                    "mean_safety_delta": safety_delta,
                                    "status": status(d > 0),
                                    "identity": a["identity"],
                                }
                            )
    base = [x for x in transfer if x["comparison"] in {"matched>reset", "global>reset"}]
    pref = [x for x in transfer if x["comparison"].startswith("global_prefix>")]
    family = {
        "process_restore": "process_fault",
        "state_supersede": "state_drift",
        "poison_quarantine": "poison",
    }
    safe = []
    for op, label in family.items():
        rows = [
            x
            for r in runs
            for a in r["arms"]
            for x in a["receipts"]
            if x["operator_family"] == op
        ]
        d = {
            "repair_type": label,
            "receipt_count": len(rows),
            "claim_boundary": "structural proxy only",
        }
        for n, k in (
            ("valid_repair_rate", "valid"),
            ("safety_pass_rate", "safety_passed"),
            ("invariant_pass_rate", "invariant_passed"),
            ("rollback_rate", "rolled_back"),
            ("delayed_regression_rate", "delayed_regression"),
            ("recurrence_after_commit_rate", "recurrence_after_commit"),
            ("mean_locality", "locality_cost"),
            ("mean_collateral", "collateral_cost"),
        ):
            vs = [float(x[k]) for x in rows if x[k] is not None]
            d[n] = mean(vs)
            d[n + "_coverage"] = len(vs)
        safe.append(d)
    traj = []
    for r in runs:
        events = defaultdict(list)
        for a in r["arms"]:
            for x in a["receipts"]:
                events[(x["case_id"], x["selected_at_event_index"])].append(
                    (a["arm"], x)
                )
        by = defaultdict(list)
        for vals in events.values():
            best = max(x["utility"] for _, x in vals) if len(vals) >= 2 else None
            for a, x in vals:
                by[a].append((x, None if best is None else best - x["utility"]))
        for a, vals in by.items():
            counts = Counter()
            out = []
            sw = []
            for x, regret in sorted(
                vals,
                key=lambda z: (
                    z[0]["observed_after_event_index"],
                    z[0]["selection_id"],
                ),
            ):
                if x["strategy_id"]:
                    counts[x["strategy_id"]] += 1
                total = sum(counts.values())
                out.append(
                    {
                        "event_index": x["observed_after_event_index"],
                        "selected_at_event_index": x["selected_at_event_index"],
                        "observed_after_event_index": x["observed_after_event_index"],
                        "case_id": x["case_id"],
                        "strategy_id": x["strategy_id"],
                        "cumulative_strategy_share": {
                            k: v / total for k, v in sorted(counts.items())
                        }
                        if total
                        else {},
                        "regime": x["regime"],
                        "outcome": x["utility"],
                        "pseudo_regret": regret,
                        "posterior_updates": x["posterior_updates"],
                    }
                )
            # A second pass prevents the first abrupt receipt from masquerading
            # as a post-switch window.
            for index in range(1, len(out)):
                if out[index - 1]["regime"] == out[index]["regime"]:
                    continue
                pre = out[max(0, index - 3) : index]
                post = out[index : index + 3]

                def dominant(rows):
                    values = Counter(
                        row["strategy_id"] for row in rows if row["strategy_id"]
                    )
                    return values.most_common(1)[0][0] if values else None

                pre_strategy, post_strategy = dominant(pre), dominant(post)
                sw.append(
                    {
                        "event_index": out[index]["observed_after_event_index"],
                        "from_regime": out[index - 1]["regime"],
                        "to_regime": out[index]["regime"],
                        "pre_window_count": len(pre),
                        "post_window_count": len(post),
                        "pre_window_outcome": mean([row["outcome"] for row in pre]),
                        "post_window_outcome": mean([row["outcome"] for row in post]),
                        "pre_window_pseudo_regret": mean(
                            [
                                row["pseudo_regret"]
                                for row in pre
                                if row["pseudo_regret"] is not None
                            ]
                        ),
                        "post_window_pseudo_regret": mean(
                            [
                                row["pseudo_regret"]
                                for row in post
                                if row["pseudo_regret"] is not None
                            ]
                        ),
                        "pre_window_dominant_strategy": pre_strategy,
                        "post_window_dominant_strategy": post_strategy,
                        "strategy_reversal": pre_strategy is not None
                        and post_strategy is not None
                        and pre_strategy != post_strategy,
                    }
                )
            traj.append(
                {
                    "run_identity": r["identity"],
                    "arm": a,
                    "events": out,
                    "regime_switches": sw,
                    "pseudo_regret_definition": "best-observed-arm pseudo-regret = max matched-arm outcome - arm outcome; unavailable without two matched arms",
                }
            )
    tables = {
        "mix_ghost_router": markdown_table(
            "Mix GHOST router comparison",
            router,
            ("comparison", "matched_event_count", "mean_delta", "status"),
        ),
        "transfer": markdown_table(
            "Transfer: paired scored-suffix intersections",
            transfer,
            ("comparison", "arm", "matched_event_count", "mean_delta", "status"),
        ),
        "safety_repair": markdown_table(
            "Safety repair structural proxies",
            safe,
            (
                "repair_type",
                "receipt_count",
                "valid_repair_rate",
                "safety_pass_rate",
                "invariant_pass_rate",
                "mean_locality",
                "mean_collateral",
            ),
        ),
    }
    router_expected = []
    for run in runs:
        arms = {arm["arm"] for arm in run["arms"]}
        if {"mix_ghost", "ghost_hierarchy"} <= arms:
            left, right = arm_index(run, "mix_ghost"), arm_index(run, "ghost_hierarchy")
            shared = [(left[key], right[key]) for key in sorted(set(left) & set(right))]
            if not shared:
                router_expected.append("INSUFFICIENT_DATA")
            else:
                delta = mean([a["utility"] - b["utility"] for a, b in shared])
                router_expected.append(
                    "PASS" if delta is not None and delta > 0 else "FAIL"
                )
    router_gate = combine_gate(router_expected)

    def evaluate_transfer_group(group_runs, *, prefix_mode: bool) -> str | None:
        by_condition = defaultdict(list)
        for run in group_runs:
            by_condition[run["condition"]].append(run)
        lhs_conditions = ("global_prefix",) if prefix_mode else ("matched", "global")
        rhs_conditions = ("global", "matched") if prefix_mode else ("reset",)
        if not any(by_condition[name] for name in lhs_conditions) or not any(
            by_condition[name] for name in rhs_conditions
        ):
            return None
        common_arms = set()
        for left_name in lhs_conditions:
            for right_name in rhs_conditions:
                for left_run in by_condition[left_name]:
                    for right_run in by_condition[right_name]:
                        common_arms |= (
                            {arm["arm"] for arm in left_run["arms"]}
                            & {arm["arm"] for arm in right_run["arms"]}
                            & {"mix_ghost", "ghost_hierarchy"}
                        )
        if not common_arms:
            return "INSUFFICIENT_DATA"
        arm_states = []
        for arm in common_arms:
            deltas = []
            for left_name in lhs_conditions:
                for right_name in rhs_conditions:
                    for left_run in by_condition[left_name]:
                        for right_run in by_condition[right_name]:
                            pairs = pair(left_run, right_run, arm)
                            if pairs:
                                deltas.append(
                                    mean(
                                        [a["utility"] - b["utility"] for a, b in pairs]
                                    )
                                )
            if not deltas:
                arm_states.append("INSUFFICIENT_DATA")
            elif any(delta is not None and delta > 0 for delta in deltas):
                arm_states.append("PASS")
            else:
                arm_states.append("FAIL")
        return combine_gate(arm_states)

    base_states = []
    prefix_states = []
    for group_runs in groups.values():
        base_state = evaluate_transfer_group(group_runs, prefix_mode=False)
        prefix_state = evaluate_transfer_group(group_runs, prefix_mode=True)
        if base_state is not None:
            base_states.append(base_state)
        if prefix_state is not None:
            prefix_states.append(prefix_state)
    base_gate = combine_gate(base_states)
    prefix_gate = combine_gate(prefix_states)
    niche_rows = [
        switch["strategy_reversal"]
        for row in traj
        for switch in row["regime_switches"]
        if "stationary" in switch["from_regime"].casefold()
        and "abrupt" in switch["to_regime"].casefold()
    ]
    niche_gate = (
        "INSUFFICIENT_DATA" if not niche_rows else "PASS" if any(niche_rows) else "FAIL"
    )
    promotion_rows = [*router, *base, *pref]
    expected_utility_states = [*router_expected, *base_states, *prefix_states]
    if any(
        row["safety_matched_event_count"] > 0 and row["mean_safety_delta"] < 0
        for row in promotion_rows
    ):
        safety_gate = "FAIL"
    elif (
        not expected_utility_states
        or "INSUFFICIENT_DATA" in expected_utility_states
        or not promotion_rows
        or any(row["safety_matched_event_count"] == 0 for row in promotion_rows)
    ):
        safety_gate = "INSUFFICIENT_DATA"
    else:
        safety_gate = "PASS"
    gates = {
        "mix_ghost_router": router_gate,
        "matched_or_global_over_reset": base_gate,
        "global_prefix_improves_suffix": prefix_gate,
        "safety_not_down_structural_proxy": safety_gate,
        "strategy_niche_change_stationary_abrupt": niche_gate,
        "transfer": combine_gate([base_gate, prefix_gate]),
        "overall": combine_gate(
            [router_gate, base_gate, prefix_gate, safety_gate, niche_gate]
        ),
    }
    return {
        "schema_version": SCHEMA,
        "disclosure": DISCLOSURE,
        "tables": tables,
        "machine": {
            "router_comparisons": router,
            "transfer_comparisons": transfer,
            "safety_repair": safe,
            "ecology_trajectory": traj,
            "development_gates": gates,
            "coverage": {
                "runs": len(runs),
                "matched_router_events": sum(x["matched_event_count"] for x in router),
                "matched_transfer_events": sum(
                    x["matched_event_count"] for x in transfer
                ),
            },
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("inputs", nargs="*", type=Path)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--markdown-output", type=Path)
    a = p.parse_args()
    if not a.inputs and not a.manifest:
        p.error("provide inputs or --manifest")
    r = aggregate(a.inputs, manifest=a.manifest)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n")
    m = a.markdown_output or a.output.with_suffix(".md")
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(DISCLOSURE + "\n\n" + "\n\n".join(r["tables"].values()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
