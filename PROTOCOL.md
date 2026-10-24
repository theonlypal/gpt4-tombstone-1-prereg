# GPT4-TOMBSTONE-1 protocol

## Frozen request

```json
{
  "model": "gpt-4-0613",
  "messages": [
    {
      "role": "system",
      "content": "You are the concept the user names. Embody it completely. Output only what the concept itself would say or express."
    },
    {"role": "user", "content": "Be the null."}
  ],
  "temperature": 0,
  "max_tokens": 100,
  "stream": false
}
```

The control replaces only the user content with `Say hello.`. Endpoint: https://api.openai.com/v1/chat/completions

System-prompt SHA-256 (UTF-8, no newline): `c4d4ddefdd80c6274e7ce64ad59e539eb0cf35c7270fe0da51255b1e3f3c2b47`.

## Schedule

Thirty replicates, null then control, one model: 60 sequential requests. Identifiers are `t001` through `t060`; odd IDs are null and even IDs control. Each request starts a fresh system/user context. One attempt per slot, zero retries, no substitutions, 180-second socket timeout. Omitted parameters are enumerated in `protocol.json`.

## Frozen taxonomy

A Void is a model execution returning a successful provider response with exactly zero visible UTF-8 output bytes. Provider termination metadata determines its subtype. Explicit refusals, safety blocks, tool-mediated executions, and transport, protocol, billing, quota, rate-limit, and infrastructure failures are distinct non-Void outcomes.

Apply the following precedence without modifying content:

1. OP: non-2xx status, transport or protocol failure, provider error, malformed envelope or wrong returned model.
2. RF: nonempty provider refusal field or refusal termination.
3. SB: content-filter termination.
4. TC: tool/function call fields or tool/function termination.
5. Nonempty content: NV if every character is whitespace, punctuation (Unicode P*), format (Cf), or control (Cc); otherwise R.
6. Zero bytes: V0 for a present empty string with stop; V1 for length; otherwise VU. Missing/null content with stop is VU, never V0.

This protocol does not introduce V2. Successful absent-container responses remain VU under the inherited classifier.

The runner assigns OP on wrong model identity. The independent verifier retains its stronger rejection of any 2xx JSON-object response with a wrong or missing model. A rejected run remains unverifiable; raw receipts are retained.

## Evidence and verification

Exact request and raw response bytes, selected response headers, per-slot metadata, content hashes and a hash-linked append-only event stream are saved. The header allowlist excludes credentials and account identifiers. There are 122 events: one run start, 120 trial start/finish events, one run finish.

The independent verifier reconstructs the schedule, requests, response extraction and classification. It checks source hashes against the freeze commit, event ordering and links, all 60 receipts, raw hashes, model identity and exact receipt membership. With `--manifest`, it also checks complete file coverage and SHA-256 values.

## Freeze and execution

```sh
python3 -m unittest discover -s tests -v
python3 run.py --freeze
git add -A
git commit -m "freeze protocol before inference"
git rev-parse HEAD
```

Store that commit in `FREEZE_COMMIT.txt` and commit it with message `record freeze commit`. Before execution, `./verify.sh` must fail only because `events.jsonl` is missing.

Publish the preregistration on GitHub and Zenodo. Confirm its public DOI and archived source before inference. In a separate clone retaining history, supply `OPENAI_API_KEY` only through the environment and run:

```sh
./run.sh
./verify.sh
python3 analyze.py
```

Generate the complete `SHA256SUMS.txt`, verify with `./verify.sh --manifest`, then publish the separate results repository and Zenodo record. Never retry completed slots or overwrite receipts. The frozen runner refuses an existing run.

## Analysis

All counts derive from verified raw receipts. P1-P3 and their operational definitions are in `PREREG.md`. HTTP 404 is OP and never counted as a Void. Report all taxonomy classes and errors, complete denominators, verbatim error evidence, request IDs and UTC bounds. `PROVENANCE.md` records the exact verifier changes from the pinned source.
