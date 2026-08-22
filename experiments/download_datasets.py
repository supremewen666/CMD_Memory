"""Acquire public experiment inputs, without executing downloaded code.

Files go under ignored ``data/external``.  A closed manifest pins sources and
hashes. LongMemEval oracle is stored separately for offline scoring only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_ROOT = Path("data/external")
AGENT = "CMD-dataset-acquisition/2026-08-21"
API = "https://api.github.com/repos"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=90) as response:
        return json.load(response)


def _response_total(response: Any, offset: int) -> int:
    """Return the authoritative object length from a Range/full response."""
    content_range = response.headers.get("Content-Range")
    if content_range:
        matched = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        if matched:
            return int(matched.group(3))
    length = response.headers.get("Content-Length")
    if length is None:
        raise urllib.error.URLError("source omitted Content-Range and Content-Length")
    return offset + int(length) if response.status == 206 else int(length)


def _migrate_invalid_target(target: Path, partial: Path) -> None:
    """Preserve an invalid published file as resumable input, never delete it."""
    if not partial.exists():
        target.replace(partial)
    else:
        target.replace(target.with_suffix(target.suffix + ".invalid"))


def _confirm_complete_without_416_length(url: str, partial: Path, offset: int) -> bool:
    """Confirm completion when a 416 response omits ``Content-Range``.

    Some redirect/CDN paths strip the useful 416 header.  A one-byte probe at
    the end of the local prefix still provides an authoritative remote length;
    matching that byte prevents publishing a same-length wrong object.
    """
    if offset < 1 or not partial.exists():
        return False
    request = urllib.request.Request(
        url,
        headers={"User-Agent": AGENT, "Range": f"bytes={offset - 1}-{offset - 1}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            if response.status != 206 or _response_total(response, offset - 1) != offset:
                return False
            remote_last = response.read()
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        return False
    with partial.open("rb") as handle:
        handle.seek(offset - 1)
        return len(remote_last) == 1 and handle.read(1) == remote_last


def fetch(
    url: str,
    target: Path,
    force: bool,
    *,
    validator: Callable[[Path], None] | None = None,
    attempts: int = 4,
) -> None:
    """Range-download *url* and atomically publish only a complete valid file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".partial")
    if target.exists() and not force:
        try:
            if validator:
                validator(target)
            return
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _migrate_invalid_target(target, temporary)
    if force:
        temporary.unlink(missing_ok=True)
    expected: int | None = None
    for attempt in range(attempts):
        offset = temporary.stat().st_size if temporary.exists() else 0
        headers = {"User-Agent": AGENT, "Range": f"bytes={offset}-"}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=180) as response:
                if response.status == 416:
                    total = _response_total(response, offset)
                    if offset != total:
                        raise urllib.error.URLError(f"416 with partial={offset}, total={total}")
                    expected = total
                else:
                    total = _response_total(response, offset)
                    if offset and response.status != 206:
                        # A redirect is followed by urllib; a final 200 means that
                        # server ignored Range, so retaining the prefix would corrupt.
                        raise urllib.error.URLError("server ignored Range request")
                    mode = "ab" if offset else "wb"
                    with temporary.open(mode) as output:
                        shutil.copyfileobj(response, output)
                    expected = total
        except urllib.error.HTTPError as error:
            # urllib raises rather than returns a response for 416.  A 416 can
            # mean a prior short response actually completed the object.
            matched = re.fullmatch(r"bytes \*/(\d+)", error.headers.get("Content-Range", ""))
            if error.code == 416 and (
                (matched and offset == int(matched.group(1)))
                or _confirm_complete_without_416_length(url, temporary, offset)
            ):
                expected = offset
            elif attempt + 1 == attempts:
                raise urllib.error.URLError(f"download failed after {attempts} attempts: {error}") from error
            else:
                continue
        except (OSError, urllib.error.URLError) as error:
            if attempt + 1 == attempts:
                raise urllib.error.URLError(f"download failed after {attempts} attempts: {error}") from error
            continue
        actual = temporary.stat().st_size if temporary.exists() else 0
        if expected is not None and actual == expected:
            if validator:
                validator(temporary)
            temporary.replace(target)
            return
        if expected is not None and actual > expected:
            raise urllib.error.URLError(f"download exceeded source length: {actual}>{expected}")
    raise urllib.error.URLError("download ended without a complete object")


def commit(repo: str) -> str:
    return str(get_json(f"{API}/{repo}/commits/main")["sha"])


def json_rows(path: Path) -> list[Any]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path} is not a JSON list")
    return rows


def validate_longmemeval(path: Path) -> None:
    rows = json_rows(path)
    ids = [str(row["question_id"]) for row in rows if isinstance(row, dict) and "question_id" in row]
    if len(ids) != len(rows) or len(set(ids)) != len(rows):
        raise ValueError(f"{path}: expected one unique question_id per row")
    if any(not isinstance(row.get("haystack_session_ids", []), list) for row in rows):
        raise ValueError(f"{path}: haystack_session_ids must be lists")


def record(path: Path, url: str, kind: str, count: int | None) -> dict[str, Any]:
    return {"path": str(path), "official_url": url, "sha256": sha256(path), "bytes": path.stat().st_size,
            "kind": kind, "row_or_item_count": count}


def longmemeval(root: Path, force: bool) -> dict[str, Any]:
    repo_revision = commit("xiaowu0162/LongMemEval")
    info = get_json("https://huggingface.co/api/datasets/xiaowu0162/longmemeval-cleaned")
    revision = str(info["sha"])
    names = {"input/longmemeval_s_cleaned.json": "longmemeval_s_cleaned.json",
             "input/longmemeval_m_cleaned.json": "longmemeval_m_cleaned.json",
             "oracle/longmemeval_oracle.json": "longmemeval_oracle.json"}
    base = f"https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/{revision}"
    ids: dict[str, set[str]] = {}
    session_ids: dict[str, set[str]] = {}
    files = []
    for relative, remote in names.items():
        target, url = root / "longmemeval" / relative, f"{base}/{remote}"
        fetch(url, target, force, validator=validate_longmemeval)
        rows = json_rows(target)
        ids[remote] = {str(row["question_id"]) for row in rows if isinstance(row, dict) and "question_id" in row}
        if len(ids[remote]) != len(rows):
            raise ValueError(f"{target}: expected unique question_id per row")
        session_ids[remote] = {str(value) for row in rows for value in row.get("haystack_session_ids", [])}
        files.append(record(target, url, "json", len(rows)))
    oracle = ids["longmemeval_oracle.json"]
    if any(ids[name] != oracle for name in ("longmemeval_s_cleaned.json", "longmemeval_m_cleaned.json")):
        raise ValueError("LongMemEval S/M/oracle question_id sets do not align")
    oracle_sessions = session_ids["longmemeval_oracle.json"]
    return {"official_repository": "https://github.com/xiaowu0162/LongMemEval", "repository_commit": repo_revision,
            "dataset_revision": revision, "license": "MIT (official repository LICENSE; HF card not asserted)", "files": files,
            "validation": {"question_id_alignment": "exact", "question_count": len(oracle),
                           "oracle_sessions_present_in_s": len(oracle_sessions & session_ids["longmemeval_s_cleaned.json"]),
                           "oracle_sessions_present_in_m": len(oracle_sessions & session_ids["longmemeval_m_cleaned.json"]),
                           "oracle_usage": "offline scoring sidecar only; never runner input"}}


MEMFAIL = ("datasets/coexisting_facts/coexisting_facts_dataset.csv",
           "datasets/conditional_facts/easy/conditional_facts_dataset_easy.csv",
           "datasets/conditional_facts/hard/conditional_facts_dataset_hard.csv",
           "datasets/custom_persona_retrieval/persona_dataset.csv",
           "datasets/long_hop/long_hop_chains.csv", "datasets/long_hop/long_hop_chains_meta.json")


def memfail(root: Path, force: bool) -> dict[str, Any]:
    revision = commit("ishirgarg/MemFail")
    files = []
    for relative in MEMFAIL:
        target, url = root / "memfail" / relative, f"https://raw.githubusercontent.com/ishirgarg/MemFail/{revision}/{relative}"
        fetch(url, target, force)
        if target.suffix == ".csv":
            with target.open(encoding="utf-8", newline="") as handle:
                count = sum(1 for _ in csv.DictReader(handle))
            if not count:
                raise ValueError(f"{target}: no data rows")
        else:
            content = json.loads(target.read_text(encoding="utf-8")); count = len(content) if isinstance(content, (list, dict)) else None
        files.append(record(target, url, target.suffix.lstrip("."), count))
    return {"official_repository": "https://github.com/ishirgarg/MemFail", "repository_commit": revision,
            "license": "NOASSERTION (no LICENSE file at pinned revision)", "files": files}


def evobench(root: Path, force: bool) -> dict[str, Any]:
    revision = commit("RUCAIBox/Evo-Bench")
    selected = ("LICENSE", "README.md", "benchmark/suites/evobench_validation.json", "policy_harness_seed/harness.json")
    files = []
    for relative in selected:
        target, url = root / "evobench" / "public_validation" / relative, f"https://raw.githubusercontent.com/RUCAIBox/Evo-Bench/{revision}/{relative}"
        fetch(url, target, force)
        content = json.loads(target.read_text(encoding="utf-8")) if target.suffix == ".json" else None
        count = (len(content["validation"]) if relative.endswith("evobench_validation.json")
                 else len(content) if isinstance(content, (list, dict)) else None)
        files.append(record(target, url, target.suffix.lstrip(".") or "text", count))
    return {"official_repository": "https://github.com/RUCAIBox/Evo-Bench", "repository_commit": revision,
            "license": "Apache-2.0 (official LICENSE)", "files": files,
            "sealed_boundary": "evaluation suite/assets intentionally not acquired or used"}


ACQUIRERS = {"longmemeval": longmemeval, "memfail": memfail, "evobench": evobench}


def download_all(output_root: str | Path = DEFAULT_ROOT, *, force: bool = False) -> dict[str, Any]:
    """Backward-compatible programmatic entry point for the official inputs."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return {name: acquire(root, force) for name, acquire in ACQUIRERS.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["all", *ACQUIRERS], default="all")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="replace only acquisition-managed targets")
    args = parser.parse_args(argv)
    root, manifest_path = args.output_root.resolve(), args.output_root.resolve() / "dataset_acquisition_manifest.json"
    if args.verify_only:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected, datasets = set(ACQUIRERS), manifest.get("datasets", {})
        if manifest.get("failures") or set(datasets) != expected:
            missing = sorted(expected - set(datasets))
            raise ValueError(f"manifest is not closed: missing={missing}, failures={manifest.get('failures', {})}")
        for dataset in manifest["datasets"].values():
            for entry in dataset["files"]:
                path = Path(entry["path"])
                if not path.exists() or sha256(path) != entry["sha256"]:
                    raise ValueError(f"manifest verification failed: {path}")
        print(f"verified {manifest_path}"); return 0
    root.mkdir(parents=True, exist_ok=True)
    wanted = ACQUIRERS if args.dataset == "all" else {args.dataset: ACQUIRERS[args.dataset]}
    previous: dict[str, Any] = {}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    datasets = dict(previous.get("datasets", {}))
    failures = dict(previous.get("failures", {}))
    for name, acquire in wanted.items():
        try:
            datasets[name] = acquire(root, args.force)
            failures.pop(name, None)
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as error:
            failures[name] = f"{type(error).__name__}: {error}"
    manifest = {"schema_version": "cmd-dataset-acquisition-v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "output_root": str(root), "datasets": datasets, "failures": failures, "remote_code_executed": False}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
