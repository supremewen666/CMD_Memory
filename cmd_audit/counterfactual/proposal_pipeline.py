"""§9.2's proposal pipeline: the eight steps every E3 proposal passes.

The pipeline is the boundary between an open search and an unbounded one. A
proposer emits text; this module decides whether that text is a program inside
the registered space, and records the decision either way.

Three design points are worth stating, because each closes a way the search could
quietly leave its declared envelope.

**The stage order is load-bearing, not cosmetic.** §9.2 lists parse, type-check,
denylist, canonicalize, resource-check, fingerprint, evaluate, ledger. Evaluating
before the resource check would spend an evaluator batch -- the resource §16
bounds -- on a program that cannot legally execute. Fingerprinting before
canonicalizing would let two spellings of one program occupy two behavior
classes, inflating the novelty §11.3 asks the artifact to demonstrate. So
`PIPELINE_STAGES` is a module constant and the order is tested.

**Every proposal is ledgered, accepted or not.** §9.2's step 8 says *every
proposed program*. A pipeline recording only survivors would report a
150-proposal seed as however many happened to compile, and the acceptance rate --
the one number that says whether the proposer understood the grammar -- would not
be recoverable from the artifact. The ledger is also the budget's only
enforcement point: counting only accepted proposals would let a proposer emitting
garbage run without bound.

**Rejection reasons are sanitized.** §9.2 permits the proposer to read "static
compile/runtime errors without case content". Python's own exception strings
happily quote the offending value, so a proposal carrying a memory ID would leak
it back through the one channel the contract leaves open. `_sanitize` reports the
stage and the offending *key*, never the value.

Zero LLM calls: the proposer is upstream of this module. Nothing here reads a
runtime case, an intent, or a per-case outcome.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from .behavior_fingerprint import behavior_fingerprint
from .program_ir import (
    REGISTERED_BOUNDS,
    IdentityActionError,
    Program,
    ProgramBoundsError,
    ProgramParseError,
    canonical_ast_hash,
    canonicalize,
    check_resource_bounds,
    parse_program,
    program_to_mapping,
)
__all__ = [
    "PIPELINE_STAGES",
    "PROPOSAL_PIPELINE_VERSION",
    "RejectionStage",
    "ProposalOutcome",
    "ProposalPipeline",
    "proposal_ledger_row",
]

PROPOSAL_PIPELINE_VERSION = "route-a-proposal-pipeline-v1"

#: §9.2's eight steps, in the order the spec lists them. The order is asserted in
#: tests rather than left to reading order here: two of the pairs are load-bearing
#: (see the module docstring) and a refactor that reordered them would otherwise
#: be invisible.
PIPELINE_STAGES = (
    "parse",
    "type_check",
    "denylist",
    "canonicalize",
    "resource_check",
    "fingerprint",
    "evaluate",
    "ledger",
)

#: §16's per-seed cap. The pipeline enforces it, since the ledger is the only
#: place that sees every proposal.
MAX_PROPOSALS_PER_SEED = 150


class RejectionStage(str, Enum):
    """Which of §9.2's steps refused.

    `PARSE` covers type and key violations too: `parse_program` validates against
    an exact allowed-key set (§8.2), so an invented key and a malformed one are
    the same refusal and splitting them here would claim a distinction the parser
    does not draw.
    """

    PARSE = "parse"
    TYPE_CHECK = "type_check"
    DENYLIST = "denylist"
    CANONICALIZE = "canonicalize"
    RESOURCE_CHECK = "resource_check"
    IDENTITY = "identity"


@dataclass(frozen=True)
class ProposalOutcome:
    """One proposal's fate. Frozen: it is a ledger row, and §9.2 calls the
    ledger immutable."""

    seed: int
    accepted: bool
    reason: str
    rejected_at: RejectionStage | None = None
    canonical_ast_hash: str | None = None
    behavior_fingerprint: str | None = None
    duplicate: bool = False
    program: Program | None = None


def _sanitize(error: Exception, *, stage: RejectionStage) -> str:
    """A proposer-visible reason with no case content (§9.2).

    Exception strings quote the offending value, which is exactly what may not
    cross back. The stage plus the exception *type* is enough for a proposer to
    correct a program -- it says which contract was broken -- and carries nothing
    about the data the program was written against.
    """
    return f"{stage.value}: {type(error).__name__}"


def _identity_rejection() -> str:
    """The identity refusal, spelled out for the proposer.

    Worth more than the exception type: "your program does nothing" is
    actionable, and it carries no case content because it describes the program.
    """
    return (
        f"{RejectionStage.IDENTITY.value}: every action is an identity action, so "
        "the program cannot change a state; `abstain-preserve` is already in the "
        "initial population under its own name (§9.1)"
    )


class ProposalPipeline:
    """§9.2's pipeline for one synthesis seed.

    Holds the ledger and the budget. One instance per seed, because §9.1 says
    only the RNG/proposal seed differs across runs and a shared instance would
    let one seed's budget consume another's.
    """

    def __init__(self, *, seed: int, max_proposals: int = MAX_PROPOSALS_PER_SEED):
        if max_proposals <= 0:
            raise ValueError("a seed with no proposal budget cannot run")
        self.seed = seed
        self.max_proposals = max_proposals
        self._ledger: list[ProposalOutcome] = []
        self._seen_hashes: set[str] = set()

    @property
    def ledger(self) -> tuple[ProposalOutcome, ...]:
        """A snapshot. Returning the live list would let a caller drop a
        rejection after the fact, which §9.2's "immutable" forbids."""
        return tuple(self._ledger)

    @property
    def submitted(self) -> int:
        """Every proposal, accepted or not -- this is what §16 caps."""
        return len(self._ledger)

    @property
    def exhausted(self) -> bool:
        return self.submitted >= self.max_proposals

    @property
    def unique_accepted(self) -> tuple[ProposalOutcome, ...]:
        """Accepted proposals with a canonical hash not seen earlier in the run.

        Duplicates stay in the ledger -- the proposer cannot see it, so
        resubmitting is not an error -- but they are not new behavior classes and
        must not be counted as exploration.
        """
        return tuple(
            row for row in self._ledger if row.accepted and not row.duplicate
        )

    def submit(self, payload: object) -> ProposalOutcome:
        """Run one proposal through §9.2's steps 1-8.

        Steps 7 (evaluate) and 8 (ledger) split: this method takes the proposal
        as far as its fingerprint and ledgers the outcome. Evaluation needs the
        hidden batches, which live with the evaluator, so the runner calls it on
        the accepted set. Ledgering here rather than there is what makes a
        rejection impossible to lose.
        """
        if self.exhausted:
            raise RuntimeError(
                f"seed {self.seed} has used its {self.max_proposals}-proposal "
                "budget (§16); no further proposal may be submitted"
            )

        outcome = self._evaluate_statically(payload)
        self._ledger.append(outcome)
        if outcome.accepted and outcome.canonical_ast_hash:
            self._seen_hashes.add(outcome.canonical_ast_hash)
        return outcome

    def _evaluate_statically(self, payload: object) -> ProposalOutcome:
        # -- steps 1-3: parse, type-check, denylist ------------------------
        # `parse_program` does all three: it validates node types and checks
        # every node against an exact allowed-key set, so a case literal has
        # nowhere to be written (§8.2).
        try:
            program = parse_program(payload)
        except (ProgramParseError, ValueError, TypeError, KeyError) as error:
            return ProposalOutcome(
                seed=self.seed,
                accepted=False,
                reason=_sanitize(error, stage=RejectionStage.PARSE),
                rejected_at=RejectionStage.PARSE,
            )

        # -- step 4: canonicalize -----------------------------------------
        # An identity-only program raises `IdentityActionError` here rather than
        # reaching the check below: `canonicalize` refuses it, because a program
        # whose every action is `keep`/`preserve` cannot change a state and is
        # `abstain-preserve`, which §9.1 already registers in the population
        # under its own name. The stage is reported as `identity` rather than
        # `canonicalize` so the ledger says *why* it was refused -- a proposer
        # reading "canonicalize failed" would look for a malformed AST.
        try:
            canonical = canonicalize(program)
        except IdentityActionError:
            return ProposalOutcome(
                seed=self.seed,
                accepted=False,
                reason=_identity_rejection(),
                rejected_at=RejectionStage.IDENTITY,
            )
        except (ValueError, TypeError) as error:
            return ProposalOutcome(
                seed=self.seed,
                accepted=False,
                reason=_sanitize(error, stage=RejectionStage.CANONICALIZE),
                rejected_at=RejectionStage.CANONICALIZE,
            )

        # -- step 5: resource check ---------------------------------------
        try:
            check_resource_bounds(canonical, bounds=REGISTERED_BOUNDS)
        except ProgramBoundsError as error:
            return ProposalOutcome(
                seed=self.seed,
                accepted=False,
                reason=_sanitize(error, stage=RejectionStage.RESOURCE_CHECK),
                rejected_at=RejectionStage.RESOURCE_CHECK,
            )

        # -- step 6: fingerprint ------------------------------------------
        # `canonical_ast_hash` canonicalizes internally, so passing the canonical
        # program is idempotent rather than redundant -- and passing the raw one
        # would be equally correct. The canonical form is passed because that is
        # what §9.2's order says step 6 operates on, and because a future hash
        # that stopped canonicalizing would then still be right here.
        ast_hash = canonical_ast_hash(canonical)
        try:
            fingerprint = behavior_fingerprint(canonical)
        except (IdentityActionError, ProgramBoundsError, ValueError) as error:
            # A program can be statically legal and still fail on a probe. That
            # is a runtime error the proposer may see (§9.2), and the probes
            # carry no case content, so nothing leaks.
            return ProposalOutcome(
                seed=self.seed,
                accepted=False,
                reason=_sanitize(error, stage=RejectionStage.RESOURCE_CHECK),
                rejected_at=RejectionStage.RESOURCE_CHECK,
            )

        return ProposalOutcome(
            seed=self.seed,
            accepted=True,
            reason="accepted",
            canonical_ast_hash=ast_hash,
            behavior_fingerprint=fingerprint,
            duplicate=ast_hash in self._seen_hashes,
            program=canonical,
        )


def proposal_ledger_row(outcome: ProposalOutcome, *, index: int) -> dict[str, object]:
    """One JSONL row for `synthesis/seed_<seed>/proposal_ledger.jsonl` (§13).

    The program is recorded only when it parsed. A rejected proposal's raw text
    is deliberately absent: it is proposer output that never became a program,
    and it is the one place unsanitized model text could reach the artifact.
    """
    row: dict[str, object] = {
        "index": index,
        "seed": outcome.seed,
        "accepted": outcome.accepted,
        "duplicate": outcome.duplicate,
        "reason": outcome.reason,
        "rejected_at": outcome.rejected_at.value if outcome.rejected_at else None,
        "canonical_ast_hash": outcome.canonical_ast_hash,
        "behavior_fingerprint": outcome.behavior_fingerprint,
    }
    if outcome.program is not None:
        row["program"] = program_to_mapping(outcome.program)
    return row


def ledger_digest(outcomes: tuple[ProposalOutcome, ...]) -> str:
    """SHA-256 over the ledger, for the seed's winner artifact.

    Lets §10.3's freeze manifest bind a winner to the exact search that produced
    it, so a ledger edited after the fact stops matching.
    """
    payload = json.dumps(
        [proposal_ledger_row(row, index=i) for i, row in enumerate(outcomes)],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
