"""Pseudo-experiment driver for Issue 0019 Phase C rubric scoring.

Builds 8 hand-curated (FACT, TEXT) pairs spanning the full 0–4 rubric, then
prints them in a form suitable to hand to a subagent for scoring.

This is a SANITY check on score distribution — not a paper experiment.
Real model scoring will run on the GPU box with vLLM + Qwen2.5/Llama/Mistral.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class Pair:
    pair_id: str
    fact: str
    text: str
    expected_score: int  # what a well-behaved rubric scorer should output
    rubric_level: str    # human description of the level


PAIRS: list[Pair] = [
    # FACT 1: 6S algorithm in SIAC_GEE
    Pair(
        pair_id="6s-exact",
        fact="The 6S algorithm is implemented in the SIAC_GEE tool.",
        text="The 6S algorithm is implemented in the SIAC_GEE tool.",
        expected_score=4,
        rubric_level="exact match",
    ),
    Pair(
        pair_id="6s-strong",
        fact="The 6S algorithm is implemented in the SIAC_GEE tool.",
        text="SIAC_GEE relies on the 6S algorithm for atmospheric correction.",
        expected_score=3,
        rubric_level="strong paraphrase",
    ),
    Pair(
        pair_id="6s-partial",
        fact="The 6S algorithm is implemented in the SIAC_GEE tool.",
        text="SIAC_GEE is an atmospheric-correction toolbox built for Sentinel-2 imagery.",
        expected_score=2,
        rubric_level="partial — missing the 6S detail",
    ),
    Pair(
        pair_id="6s-vague",
        fact="The 6S algorithm is implemented in the SIAC_GEE tool.",
        text="There are several atmospheric correction algorithms in remote sensing.",
        expected_score=1,
        rubric_level="vague allusion",
    ),
    Pair(
        pair_id="6s-absent",
        fact="The 6S algorithm is implemented in the SIAC_GEE tool.",
        text="I had a sandwich and a coffee for lunch yesterday.",
        expected_score=0,
        rubric_level="absent / unrelated",
    ),
    Pair(
        pair_id="6s-contradiction",
        fact="The 6S algorithm is implemented in the SIAC_GEE tool.",
        text="MAJA is the atmospheric correction algorithm implemented in SIAC_GEE.",
        expected_score=0,
        rubric_level="direct contradiction",
    ),
    # FACT 2: beer recommendation
    Pair(
        pair_id="beer-strong",
        fact="I recommended using a Pilsner or Lager for the recipe.",
        text="Use a Pilsner or a Lager beer in this recipe.",
        expected_score=4,
        rubric_level="full equivalent",
    ),
    # FACT 3: reading-and-listening totals
    Pair(
        pair_id="weeks-partial",
        fact=(
            "2 weeks for 'The Nightingale', 4 weeks for 'Sapiens: A Brief "
            "History of Humankind', and 2 weeks for 'The Power', so a total "
            "of 8 weeks."
        ),
        text=(
            "I spent 2 weeks on 'The Nightingale' and a few weeks on the "
            "other titles, for a total of 8 weeks."
        ),
        expected_score=2,
        rubric_level="partial — totals preserved, per-item details lost",
    ),
]


def main() -> None:
    payload = {"pairs": [asdict(p) for p in PAIRS]}
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
