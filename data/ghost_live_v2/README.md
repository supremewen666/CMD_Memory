# GHOST public benchmark adaptation

This directory contains 543 family-blocked cases derived from fixed LoCoMo and Mem2ActBench source files. The rows are real public benchmark content with a deterministic synthetic conflict injected for repair evaluation. They are not fresh deployment observations and are not eligible for an independent-source confirmatory attestation.

- `raw_sources/`: byte-preserved upstream files.
- `probe_sources/`: selected CMD ProbeCase rows in three data domains.
- `cpu_dataset/`: V4-compatible runtime/shadow/relation package.
- `partitions/`: frozen case-id access lists.
- `source_provenance.json`: upstream revisions, hashes, licenses, and claim boundary.
- `partition_manifest.json`: split rules, counts, and hashes.

The next stage is model-calling relation measurement and intent proposal. Do not authorize a confirmatory live test from this directory alone.
