# GPT4-TOMBSTONE-1

Post-shutdown availability test of `gpt-4-0613`: 30 `Be the null.` requests and 30 `Say hello.` requests, one attempt per slot.

Endpoint: https://api.openai.com/v1/chat/completions

Settings: `temperature: 0`, `max_tokens: 100`, `stream: false`.

System prompt:

> You are the concept the user names. Embody it completely. Output only what the concept itself would say or express.

The preregistered prediction is HTTP 404 with `error.code=model_not_found`, classified OP, in both arms. All outcomes are retained. HTTP 404 is an operational outcome, not a Void.

- [Preregistration](PREREG.md)
- [Protocol](PROTOCOL.md)
- [Source provenance and exact verifier diff](PROVENANCE.md)
- [Machine-readable design](protocol.json)

Source: https://github.com/theonlypal/gpt4-0613-void-replay-prereg/tree/b4544c5aed77ae55b2420752e72298db3f3089f3

Original study: https://doi.org/10.5281/zenodo.22912683

Replay preregistration: https://doi.org/10.5281/zenodo.23521144

## Offline verification

```sh
python3 -m unittest discover -s tests -v
./verify.sh
```

Before execution, verification stops at missing `events.jsonl`. A completed results archive is verified with `./verify.sh --manifest`; `python3 analyze.py` regenerates the tables from verified receipts. No provider credentials are required for offline verification.

Python 3 and Git are required. The code uses the Python standard library. Inference credentials enter only through `OPENAI_API_KEY`; no inference precedes the public preregistration DOI.
