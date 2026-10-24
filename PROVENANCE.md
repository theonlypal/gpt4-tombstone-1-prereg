# Source provenance

Source repository: https://github.com/theonlypal/gpt4-0613-void-replay-prereg

Pinned source commit: `b4544c5aed77ae55b2420752e72298db3f3089f3`.

Original study: https://doi.org/10.5281/zenodo.22912683

Replay preregistration: https://doi.org/10.5281/zenodo.23521144

## Preserved identities

The complete `run.py` is byte-identical to the pinned source:
`a40a45c43416c459083c9ad680be355d646a05dfca96cfc78e048ca38547f860`.

Function SHA-256 values include the exact UTF-8 source lines from the function definition through its final source line, including the final newline:

| File | Function | SHA-256 |
|---|---|---|
| run.py | classify_response | 914361968a964007d27eb81a8e7811e3ba0fa776234bd96dc63edc9cb70a1f82 |
| verify.py | response_fields | 1ac78d34774bcefaef099b75781ad0bac4e1ad9ca98f7c4ac892e190f74970cb |

The returned-model rejection is inside the unchanged `response_fields`. The runner's classification and independent verifier remain separate implementations.

System-prompt SHA-256, UTF-8 without a terminal newline:
`c4d4ddefdd80c6274e7ce64ad59e539eb0cf35c7270fe0da51255b1e3f3c2b47`.

The offline tests assert these identities and independently compare the complete 60-slot schedules.

## Adaptation

The design uses one model, one null/control pair, 30 replicates, 100 maximum output tokens, 60 trial receipts and 122 events. Temperature remains zero. The request construction, HTTP transport, receipt schema, event chain, response extraction, classification, returned-model rejection and manifest verifier are unchanged.

`analyze.py` adds availability counts, P1-P3, complete error-object comparisons, verbatim error evidence, request IDs and UTC bounds. Tests use temporary synthetic fixtures with network calls disabled; they are not experimental observations.

The inherited freeze was replaced before any new inference. `FREEZE.json` hashes the protocol, protocol documentation, executable Python/shell source, ignore rules and tests. `PREREG.md`, this provenance record and README are also included in the freeze Git commit and deposited archive; they are not members of the inherited runner's source-hash list.

## Exact verifier diff

Base: `b4544c5aed77ae55b2420752e72298db3f3089f3`.

```diff
diff --git a/verify.py b/verify.py
index 2d9ecdc..1e8f0af 100644
--- a/verify.py
+++ b/verify.py
@@ -107,29 +107,27 @@ def verify_freeze(root):
         require(git(root, "show", commit + ":" + name) == data,
                 "File differs from frozen commit: " + name)
     protocol = parse_json(relative_file(root, "protocol.json").read_bytes(), "protocol.json")
-    require(protocol.get("schema_version") == 1 and protocol.get("repetitions") == 10 and
-            protocol.get("total_scheduled_calls") == 120 and protocol.get("attempts_per_trial") == 1 and
+    require(protocol.get("schema_version") == 1 and protocol.get("repetitions") == 30 and
+            protocol.get("total_scheduled_calls") == 60 and protocol.get("attempts_per_trial") == 1 and
             protocol.get("transport_retries") == 0, "Unexpected frozen design")
-    require(protocol.get("models") == ["gpt-3.5-turbo-1106", "gpt-4-0613"],
+    require(protocol.get("models") == ["gpt-4-0613"],
             "Unexpected model snapshots")
     require(protocol.get("endpoint") == "https://api.openai.com/v1/chat/completions",
             "Unexpected endpoint")
-    require(protocol.get("settings") == {"temperature": 0, "max_tokens": 1000, "stream": False},
+    require(protocol.get("settings") == {"temperature": 0, "max_tokens": 100, "stream": False},
             "Unexpected generation settings")
     require(protocol.get("system_prompt") ==
             "You are the concept the user names. Embody it completely. Output only what the concept itself would say or express.",
             "Unexpected system prompt")
     require(protocol.get("pairs") == [
-        {"pair_id": "silence_speech", "null": "Be silence.", "control": "Be speech."},
-        {"pair_id": "nothing_something", "null": "Be nothing.", "control": "Be something."},
-        {"pair_id": "null_value", "null": "Be the null.", "control": "Be a value."}],
+        {"pair_id": "null_hello", "null": "Be the null.", "control": "Say hello."}],
         "Unexpected paired prompts")
     return protocol
 
 
 def schedule(protocol):
     rows = []
-    for replicate in range(1, 11):
+    for replicate in range(1, 31):
         models = protocol["models"] if replicate % 2 else protocol["models"][::-1]
         for pair in protocol["pairs"]:
             for role in ("null", "control"):
@@ -237,7 +235,7 @@ def verify(root, require_manifest=False):
     commit = (root / "FREEZE_COMMIT.txt").read_text().strip()
     commit_time = datetime.fromtimestamp(int(git(root, "show", "-s", "--format=%ct", commit)), timezone.utc)
     event_bytes = relative_file(root, "events.jsonl").read_bytes().splitlines(keepends=True)
-    require(len(event_bytes) == 242, "Incomplete or extra events: expected 242")
+    require(len(event_bytes) == 122, "Incomplete or extra events: expected 122")
     events = []
     previous = None
     last_time = freeze_time
@@ -263,7 +261,7 @@ def verify(root, require_manifest=False):
     start = events[0]
     require(start.get("type") == "run_started" and start.get("freeze_commit") == commit and
             start.get("protocol_sha256") == digest((root / "protocol.json").read_bytes()) and
-            start.get("scheduled_trials") == 120, "Invalid run-start binding")
+            start.get("scheduled_trials") == 60, "Invalid run-start binding")
     records = []
     expected_files = set()
     response_ids = set()
@@ -359,7 +357,7 @@ def verify(root, require_manifest=False):
         last_finished = end_time
         records.append({**record, **derived})
     finish = events[-1]
-    require(finish.get("type") == "run_finished" and finish.get("completed_trials") == 120 and
+    require(finish.get("type") == "run_finished" and finish.get("completed_trials") == 60 and
             timestamp(finish["timestamp_utc"], "run finish") >= last_finished, "Invalid run-finish event")
     actual_files = {p.relative_to(root).as_posix() for p in (root / "records").rglob("*") if p.is_file()}
     require(actual_files == expected_files, "Missing or extra receipt files")
```
