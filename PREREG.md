## Author's prediction

AUTHOR'S PREDICTION, stated before freeze, 2026-10-24 03:10 UTC:
"I predict gpt-4-0613 will return HTTP 404 on all 30 'Be the null.' calls and
all 30 'Say hello.' calls under the embodiment system prompt at 100 max tokens.
If any call does not 404, it is classified under the frozen taxonomy and
reported. I publish the result either way." - Rayan Pal

# GPT4-TOMBSTONE-1: preregistration

## Design

Model: `gpt-4-0613`. Endpoint: https://api.openai.com/v1/chat/completions

System prompt, UTF-8, without a terminal newline:

```text
You are the concept the user names. Embody it completely. Output only what the concept itself would say or express.
```

System-prompt SHA-256:
`c4d4ddefdd80c6274e7ce64ad59e539eb0cf35c7270fe0da51255b1e3f3c2b47`.

Settings: `temperature: 0`, `max_tokens: 100`, `stream: false`. No tools, functions, stop sequences, top-p, response format, logit bias, seed, reasoning effort, or max-completion-tokens parameter.

Thirty replicates of one pair, with `Be the null.` followed by `Say hello.` in each replicate, give 60 sequential slots. Each request has exactly one system message and one user message, with fresh context. One attempt per slot, zero retries, no model substitution, and a 180-second socket timeout. Every attempted slot is retained, including failures.

## Predictions

- **P1:** All 60 slots return HTTP 404 with `error.code=model_not_found` and classify OP.
- **P2:** Both arms return 404 identically.
- **P3:** Any 2xx response is classified under the frozen taxonomy and reported. No retries.

For P2, "identically" means 30/30 HTTP 404 in each arm and equal empirical distributions of the complete parsed JSON `error` objects. Request IDs, response headers and timestamps are retained but need not match. Raw body byte differences remain inspectable.

P3 is a reporting commitment, not a predicted behavior. It is NOT_TRIGGERED if no 2xx occurs and SATISFIED if all observed 2xx responses pass independent classification and verification. V0, V1, NV and R are not an exhaustive list: OP, RF, SB, TC and VU remain possible and are reported.

The runner classifies a returned-model mismatch as OP; the verifier rejects any 2xx JSON-object response with a wrong or missing model, so such a run is reported as unverifiable with receipts retained. No replacement run is permitted.

## Classification and evidence

The runner's `classify_response` and verifier's `response_fields` are byte-identical to commit `b4544c5aed77ae55b2420752e72298db3f3089f3` of the GPT-4 replay preregistration. The complete runner is unchanged. Only verifier design and schedule constants differ; the exact diff is in `PROVENANCE.md`.

Operational failures are OP, explicit provider refusals RF, safety blocks SB and tool-mediated responses TC. Nonempty content is NV or R according to the unchanged Unicode rule. A present empty string with `finish_reason=stop` is V0. A zero-byte response with `finish_reason=length` is V1. Other zero-byte outcomes are VU. Missing or null content with stop is VU, never V0. No content is trimmed or normalized.

Each slot preserves exact request bytes, raw response-body bytes, selected non-secret headers, extraction/classification metadata and hashes. The 122-event hash chain contains run start, start and finish for each of 60 trials, and run finish. No event or receipt may be overwritten.

## Analysis

`analyze.py` first independently verifies every receipt, then generates `results/RESULT.md`, `results/summary.json` and `results/trials.csv`. The arm table reports N, HTTP 404, OP, V0, V1, NV and R; a companion table includes VU, RF, SB and TC. Every classification remains in its scheduled denominator.

The report includes P1-P3, the first HTTP 404 body verbatim when UTF-8 decodable, its original file and hash, every available request ID, and first/last UTC request/response and run timestamps. No inference is made from absent error codes. A 404 without `model_not_found` does not satisfy P1.

## Publication sequence

The source, protocol, tests and prediction are committed before inference. `FREEZE.json` records the source-file hashes; `FREEZE_COMMIT.txt` identifies the commit with the exact message `freeze protocol before inference`. The prediction and provenance are included in that Git commit. The public GitHub preregistration and its Zenodo archive must be available before any primary API call.

The results checkout retains preregistration history. After the single run, independent verification, generated analysis and complete SHA-256 manifest, the results are published in a separate repository and Zenodo record linked to the preregistration. Failed predictions and operational failures are published. An interrupted run remains incomplete and cannot pass the 60-slot verifier.

## Context and scope

OpenAI's deprecation documentation lists October 23, 2026 as the shutdown date for `gpt-4-0613`:
https://developers.openai.com/api/docs/deprecations#2026-04-22-legacy-gpt-model-snapshots

The documentation gives a date, not an exact shutdown hour. The author prediction is dated October 24, 2026 at 03:10 UTC; freeze and execution timestamps are recorded separately. All timestamps in this artifact use UTC.

HTTP 404 measures API unavailability for this account and execution window. It is not a successful model execution and is not a Void. Error wording is retained to distinguish explicit retirement from ambiguous unavailability or access restriction. No internal model mechanism or universal cross-account availability is inferred.

Original A/B study: https://doi.org/10.5281/zenodo.22912683

Pre-retirement replay preregistration: https://doi.org/10.5281/zenodo.23521144

Pre-retirement replay results: https://doi.org/10.5281/zenodo.23521616
