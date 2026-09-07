from __future__ import annotations

from cmd_audit.eval.successor_query_ledger import (
    QUERY_LEDGER_GENESIS_SHA256,
    QUERY_LEDGER_SCHEMA_DESCRIPTOR,
    QueryReadLedger,
)


def test_query_ledger_claim_is_single_use_and_terminal(tmp_path) -> None:
    ledger = QueryReadLedger(tmp_path / "query.sqlite")
    claim = ledger.claim(
        protocol_manifest_sha256="f" * 64,
        input_sha256="a" * 64,
        family_block_sha256="b" * 64,
        winner_sha256="c" * 64,
    )
    assert claim.claimed is True
    assert claim.claim_row_sha256 is not None
    assert ledger.finish(claim, success=True, artifact_sha256="d" * 64) is True
    terminal = ledger.row("f" * 64)
    assert terminal is not None
    assert terminal.state == "SUCCESS"
    assert terminal.artifact_sha256 == "d" * 64
    assert terminal.row_sha256 != claim.claim_row_sha256
    assert (
        ledger.claim(
            protocol_manifest_sha256="f" * 64,
            input_sha256="e" * 64,
            family_block_sha256="b" * 64,
            winner_sha256="c" * 64,
        ).claimed
        is False
    )


def test_failed_claim_is_also_terminal(tmp_path) -> None:
    ledger = QueryReadLedger(tmp_path / "query.sqlite")
    claim = ledger.claim(
        protocol_manifest_sha256="f" * 64,
        input_sha256="a" * 64,
        family_block_sha256="b" * 64,
        winner_sha256="c" * 64,
    )
    assert ledger.finish(claim, success=False, artifact_sha256="e" * 64) is True
    assert (
        ledger.claim(
            protocol_manifest_sha256="f" * 64,
            input_sha256="a" * 64,
            family_block_sha256="b" * 64,
            winner_sha256="c" * 64,
        ).claimed
        is False
    )


def test_query_ledger_genesis_is_schema_descriptor_not_mutable_database_bytes() -> None:
    assert QUERY_LEDGER_SCHEMA_DESCRIPTOR["table"] == "successor_v3_query_reads"
    assert len(QUERY_LEDGER_GENESIS_SHA256) == 64
    assert QUERY_LEDGER_GENESIS_SHA256 != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_query_ledger_rejects_non_hash_identity_and_empty_terminal_hash(tmp_path) -> None:
    ledger = QueryReadLedger(tmp_path / "query.sqlite")
    try:
        ledger.claim(
            protocol_manifest_sha256="not-a-hash",
            input_sha256="a" * 64,
            family_block_sha256="b" * 64,
            winner_sha256="c" * 64,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("invalid protocol hash was accepted")

    claim = ledger.claim(
        protocol_manifest_sha256="f" * 64,
        input_sha256="a" * 64,
        family_block_sha256="b" * 64,
        winner_sha256="c" * 64,
    )
    try:
        ledger.finish(claim, success=False, artifact_sha256="")
    except ValueError:
        pass
    else:
        raise AssertionError("empty terminal artifact hash was accepted")
