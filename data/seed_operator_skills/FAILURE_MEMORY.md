# Failure Memory

Seed operator-skill library distilled from Exp21 winning composite shapes
(`artifacts/sandbox/operator_headroom_detail.csv`, multihop residual 115 cases,
headroom 34/115 recovered, p~1e-10). Format is `MarkdownFailureMemoryStore` +
`format_pattern` compatible; load with the store root pointed at this
directory. Trigger fingerprints are `review_required` placeholders — seed
patterns are shape-level priors for Exp24's directed search; the store
rewrites the fingerprint on first cluster hit.

## Cases

## Patterns
- [pattern_injection_injection](patterns/pattern_injection_injection.md) - double injection re-emit across hop 1+2; 9 residual cases, avg net gain 0.441
- [pattern_injection_safety](patterns/pattern_injection_safety.md) - hop-1 injection re-emit + hop-2 safety restore; 7 residual cases, avg net gain 0.460
- [pattern_injection_demote_distractor](patterns/pattern_injection_demote_distractor.md) - hop-2 re-emit parameterized with distractor-demoting item_signal_hints; 9 residual cases, avg net gain 0.384
