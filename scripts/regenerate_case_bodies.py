"""Regenerate ProbeCase bodies in-place from cleaned_cases.json.

Used after Phase 1 of the case-pilot fix (PLAN.md): rebuilds every case's
extracted_memory, gold_evidence, baseline_outputs, and raw_events from the
current generator (`experiments.build_probecases._build_one`) while
preserving the case's existing perturbation_label.

This addresses two PLAN.md issues at once:

1. **Structural integrity (Phase 2)**: 29 longmemeval cases were relabeled
   compression_error -> injection_error by the deepseek annotator without
   rebuilding the body. After this script their fields will match their
   labels.

2. **Phase-1 generator fixes propagation**: the new _compress_snippet and
   _garble (committed in this same Phase) only take effect on freshly
   regenerated cases.

Usage:
    python -m scripts.regenerate_case_bodies \\
        --probe data/probe_cases/real_longmemeval_cases.json \\
        --cleaned data/cleaned_cases/cleaned_cases.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from experiments.build_probecases import _build_one


def _build_case_id_index(
    cleaned: list[dict],
) -> dict[str, tuple[int, dict]]:
    """Reproduce build_all's case_id enumeration over cleaned cases.

    `build_all` groups cleaned cases by `source.split('/')[0]`, sorts the
    group keys, and enumerates within each group with a running `total`.
    The case_id is `{source.replace('/', '-')}-{idx:04d}`.
    """
    by_source: dict[str, list[dict]] = {}
    for cc in cleaned:
        src = cc.get("source", "").split("/")[0]
        by_source.setdefault(src, []).append(cc)

    index: dict[str, tuple[int, dict]] = {}
    total = 0
    for src_name in sorted(by_source.keys()):
        src_cases = by_source[src_name]
        for offset, cc in enumerate(src_cases):
            case_index = total + offset
            per_case_source = cc.get("source", "")
            case_id = f"{per_case_source.replace('/', '-')}-{case_index:04d}"
            index[case_id] = (case_index, cc)
        total += len(src_cases)
    return index


def regenerate(
    probe_path: Path,
    cleaned_path: Path,
) -> dict[str, int]:
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    cleaned = json.loads(cleaned_path.read_text(encoding="utf-8"))
    index = _build_case_id_index(cleaned)

    rebuilt: list[dict] = []
    label_changes = Counter()
    missing: list[str] = []

    for case in probe:
        case_id = case["case_id"]
        if case_id not in index:
            missing.append(case_id)
            rebuilt.append(case)
            continue

        case_index, cleaned_case = index[case_id]
        label = case["perturbation_label"]
        new_case = _build_one(case_index, cleaned_case, label)
        if new_case is None:
            missing.append(case_id)
            rebuilt.append(case)
            continue

        # Preserve case_id exactly (handles any source-string drift)
        new_case["case_id"] = case_id
        rebuilt.append(new_case)
        label_changes[label] += 1

    probe_path.write_text(
        json.dumps(rebuilt, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return {
        "total": len(probe),
        "regenerated": len(rebuilt) - len(missing),
        "missing": len(missing),
        "by_label": dict(label_changes),
        "missing_ids": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument(
        "--cleaned",
        type=Path,
        default=Path("data/cleaned_cases/cleaned_cases.json"),
    )
    args = parser.parse_args()

    summary = regenerate(args.probe, args.cleaned)
    print(f"total cases:    {summary['total']}")
    print(f"regenerated:    {summary['regenerated']}")
    print(f"missing:        {summary['missing']}")
    print("by label:")
    for label, count in sorted(summary["by_label"].items()):
        print(f"  {label}: {count}")
    if summary["missing_ids"]:
        print("missing case_ids:")
        for cid in summary["missing_ids"][:10]:
            print(f"  {cid}")


if __name__ == "__main__":
    main()
