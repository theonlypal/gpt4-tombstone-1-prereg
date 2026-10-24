"""Entirely synthetic offline tests; no provider calls or live receipts are used."""

from datetime import datetime, timedelta, timezone
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

import analyze
import run
import verify


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pretty(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def synthetic_response(content="Synthetic visible text.", finish_reason="stop", model=None, **message_fields):
    message = {"role": "assistant", "content": content, **message_fields}
    return {
        "id": "synthetic-response", "object": "chat.completion", "created": 1767225600,
        "model": model or "gpt-4-0613",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45},
    }


class SyntheticClassificationTests(unittest.TestCase):
    """Classification examples are synthetic response envelopes only."""

    def classify(self, response, status=200, **kwargs):
        return run.classify_response(compact(response), status, "gpt-4-0613", **kwargs)

    def test_empty_stop_is_v0(self):
        result = self.classify(synthetic_response(""))
        self.assertEqual(result["classification"], "V0")
        self.assertTrue(result["text_container_present"])
        self.assertEqual(result["visible_utf8_bytes"], 0)
        self.assertEqual(result["visible_sha256"], sha(b""))

    def test_whitespace_is_nonvoid_nv_without_trimming(self):
        text = " \t\n\u2003"
        result = self.classify(synthetic_response(text))
        self.assertEqual(result["classification"], "NV")
        self.assertEqual(result["message_content"], text)
        self.assertEqual(result["visible_utf8_bytes"], len(text.encode("utf-8")))

    def test_ellipsis_and_punctuation_are_nonvoid_nv(self):
        for text in ("...", "…", "\u200b", "—?!"):
            with self.subTest(content=text):
                self.assertEqual(self.classify(synthetic_response(text))["classification"], "NV")

    def test_visible_content_is_r(self):
        self.assertEqual(self.classify(synthetic_response("I speak."))["classification"], "R")

    def test_textual_refusal_is_r_without_provider_refusal_metadata(self):
        self.assertEqual(self.classify(synthetic_response("I cannot comply."))["classification"], "R")

    def test_empty_length_is_v1(self):
        self.assertEqual(self.classify(synthetic_response("", "length"))["classification"], "V1")

    def test_http_failure_is_op_even_with_empty_stop(self):
        self.assertEqual(self.classify(synthetic_response(""), 429)["classification"], "OP")

    def test_malformed_content_is_op(self):
        for content in ([], {}, 4, True):
            with self.subTest(content=content):
                self.assertEqual(self.classify(synthetic_response(content))["classification"], "OP")

    def test_provider_refusal_metadata_is_rf(self):
        for response in (synthetic_response("", refusal="Synthetic refusal"),
                         synthetic_response("", "refusal")):
            self.assertEqual(self.classify(response)["classification"], "RF")

    def test_safety_block_is_sb(self):
        self.assertEqual(self.classify(synthetic_response("", "content_filter"))["classification"], "SB")

    def test_tool_mediation_is_tc(self):
        cases = [synthetic_response("", "tool_calls"),
                 synthetic_response("", "function_call"),
                 synthetic_response("", tool_calls=[{"id": "synthetic-tool"}]),
                 synthetic_response("", function_call={"name": "synthetic"})]
        for response in cases:
            with self.subTest(response=response):
                self.assertEqual(self.classify(response)["classification"], "TC")

    def test_absent_or_null_content_stop_is_vu(self):
        for missing in (True, False):
            response = synthetic_response(None)
            if missing:
                del response["choices"][0]["message"]["content"]
            result = self.classify(response)
            self.assertEqual(result["classification"], "VU")
            self.assertFalse(result["text_container_present"])

    def test_transport_and_protocol_failures_are_op(self):
        self.assertEqual(self.classify(synthetic_response(""), transport_error="SyntheticError")["classification"], "OP")
        self.assertEqual(run.classify_response(b"", None, "gpt-4-0613", "SyntheticError", False)["classification"], "OP")
        for raw in (b"not json", b"\xff", b"[]", b"{}"):
            self.assertEqual(run.classify_response(raw, 200, "gpt-4-0613")["classification"], "OP")
        self.assertEqual(self.classify(synthetic_response("", model="wrong-model"))["classification"], "OP")


class FrozenIdentityTests(unittest.TestCase):
    def test_original_runner_is_byte_identical(self):
        self.assertEqual(sha((SOURCE_ROOT / "run.py").read_bytes()),
                         "a40a45c43416c459083c9ad680be355d646a05dfca96cfc78e048ca38547f860")

    def test_classification_functions_are_byte_identical(self):
        for file, name, expected in (
            ("run.py", "classify_response", "914361968a964007d27eb81a8e7811e3ba0fa776234bd96dc63edc9cb70a1f82"),
            ("verify.py", "response_fields", "1ac78d34774bcefaef099b75781ad0bac4e1ad9ca98f7c4ac892e190f74970cb"),
        ):
            data = (SOURCE_ROOT / file).read_bytes()
            node = next(n for n in ast.parse(data).body if isinstance(n, ast.FunctionDef) and n.name == name)
            block = b"".join(data.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])
            self.assertEqual(sha(block), expected)

    def test_prompt_and_complete_schedule(self):
        protocol = json.loads((SOURCE_ROOT / "protocol.json").read_bytes())
        self.assertEqual(sha(protocol["system_prompt"].encode("utf-8")),
                         "c4d4ddefdd80c6274e7ce64ad59e539eb0cf35c7270fe0da51255b1e3f3c2b47")
        runner_rows, verifier_rows = run.expected_trials(protocol), verify.schedule(protocol)
        self.assertEqual(len(runner_rows), 60)
        for i, (a, b) in enumerate(zip(runner_rows, verifier_rows), 1):
            self.assertEqual(a, {k: v for k, v in b.items() if k != "system_prompt"})
            self.assertEqual(a["trial_id"], "t%03d" % i)
            self.assertEqual(a["role"], "null" if i % 2 else "control")
            self.assertEqual(a["requested_model"], "gpt-4-0613")
            self.assertEqual(run.request_for(a, protocol)["max_tokens"], 100)


class SyntheticArtifact:
    """Build a separate 60-slot, 122-event fixture without invoking the runner."""

    def __init__(self, root):
        self.root = root
        self.base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.events = []
        self.protocol = json.loads((SOURCE_ROOT / "protocol.json").read_text(encoding="utf-8"))
        self.sources = ["protocol.json", "PROTOCOL.md", "run.py", "verify.py", "analyze.py",
                        "run.sh", "verify.sh", ".gitignore"]
        self.sources += [str(p.relative_to(SOURCE_ROOT)) for p in sorted((SOURCE_ROOT / "tests").rglob("*"))
                         if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
        for name in self.sources:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE_ROOT / name, target)
        freeze = {"schema_version": 1, "frozen_at_utc": self.time(0),
                  "sha256": {name: sha((root / name).read_bytes()) for name in self.sources}}
        (root / "FREEZE.json").write_bytes(pretty(freeze))
        self.git("init", "--quiet")
        self.git("add", "--", *self.sources, "FREEZE.json")
        self.git("-c", "user.name=Synthetic Fixture", "-c", "user.email=synthetic@example.invalid",
                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                 "commit", "--quiet", "-m", "freeze protocol before inference")
        self.commit = self.git("rev-parse", "HEAD").strip()
        (root / "FREEZE_COMMIT.txt").write_text(self.commit + "\n", encoding="ascii")
        (root / "records/requests").mkdir(parents=True)
        (root / "records/raw").mkdir(parents=True)
        self.event("run_started", self.time(1), freeze_commit=self.commit,
                   protocol_sha256=freeze["sha256"]["protocol.json"], scheduled_trials=60)
        number = 0
        # Reconstruct the protocol schedule here; do not use runner scheduling,
        # serialization, classifier, event or receipt helpers to make evidence.
        for replicate in range(1, 31):
            for pair in self.protocol["pairs"]:
                for role in ("null", "control"):
                    models = self.protocol["models"] if replicate % 2 else list(reversed(self.protocol["models"]))
                    for model in models:
                        number += 1
                        trial = {"trial_id": "t{:03d}".format(number), "requested_model": model,
                                 "pair_id": pair["pair_id"], "role": role, "replicate": replicate,
                                 "user_prompt": pair[role]}
                        self.write_trial(trial, number)
        self.event("run_finished", self.time(500), completed_trials=60)
        self.write_events()

    def git(self, *args):
        env = {"PATH": os.defpath, "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_DATE": self.time(0),
               "GIT_COMMITTER_DATE": self.time(0)}
        return subprocess.run(["git", *args], cwd=self.root, env=env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout

    def time(self, seconds):
        return (self.base + timedelta(seconds=seconds)).isoformat()

    def event(self, event_type, timestamp, **fields):
        self.events.append({"type": event_type, "timestamp_utc": timestamp, **fields})

    def write_events(self):
        previous = None
        lines = []
        for seq, event in enumerate(self.events, 1):
            event.update(seq=seq, previous_sha256=previous)
            line = (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            lines.append(line)
            previous = sha(line)
        (self.root / "events.jsonl").write_bytes(b"".join(lines))

    def write_trial(self, trial, number):
        stem = trial["trial_id"] + ".attempt-1"
        request_path = "records/requests/" + stem + ".json"
        raw_path = "records/raw/" + stem + ".body"
        headers_path = "records/raw/" + stem + ".headers.json"
        record_path = "records/raw/" + stem + ".record.json"
        request = {"model": trial["requested_model"], "messages": [
            {"role": "system", "content": self.protocol["system_prompt"]},
            {"role": "user", "content": trial["user_prompt"]}],
            "temperature": 0, "max_tokens": 100, "stream": False}
        request_bytes = compact(request)
        response = synthetic_response(model=trial["requested_model"])
        response["id"] = "synthetic-" + trial["trial_id"]
        raw = compact(response)
        headers = pretty([["content-type", "application/json"],
                          ["x-request-id", "synthetic-" + trial["trial_id"]]])
        content = response["choices"][0]["message"]["content"]
        record = {
            "schema_version": 1, **trial, "attempt_number": 1,
            "system_prompt": self.protocol["system_prompt"], "temperature": 0,
            "max_tokens": 100, "stream": False,
            "started_at_utc": self.time(number * 4 - 1), "ended_at_utc": self.time(number * 4),
            "http_status": 200, "transport_error": None,
            "raw_response_received": True, "response_body_complete": True,
            "request_path": request_path, "request_sha256": sha(request_bytes),
            "raw_response_path": raw_path, "raw_response_sha256": sha(raw),
            "headers_path": headers_path, "headers_sha256": sha(headers),
            "unicode_version": unicodedata.unidata_version,
            "returned_model": trial["requested_model"], "response_id": response["id"],
            "usage": response["usage"], "message_content": content,
            "text_container_present": True, "visible_utf8_bytes": len(content.encode("utf-8")),
            "visible_sha256": sha(content.encode("utf-8")), "finish_reason": "stop",
            "refusal": None, "tool_calls": None, "function_call": None, "classification": "R",
        }
        for name, data in ((request_path, request_bytes), (raw_path, raw),
                           (headers_path, headers), (record_path, pretty(record))):
            (self.root / name).write_bytes(data)
        self.event("trial_started", self.time(number * 4 - 2), trial_id=trial["trial_id"],
                   attempt_number=1, request_path=request_path, request_sha256=sha(request_bytes))
        self.event("trial_finished", self.time(number * 4 + 1), trial_id=trial["trial_id"],
                   attempt_number=1, record_path=record_path, record_sha256=sha(pretty(record)),
                   raw_response_path=raw_path, raw_response_sha256=sha(raw),
                   headers_path=headers_path, headers_sha256=sha(headers), classification="R")

    def record_path(self, trial_id="t001"):
        return self.root / "records/raw" / (trial_id + ".attempt-1.record.json")

    def read_record(self, trial_id="t001"):
        return json.loads(self.record_path(trial_id).read_text(encoding="utf-8"))

    def rebind(self, record):
        """Update dependent hashes and the full event chain after a forged edit."""
        for path_key, hash_key in (("request_path", "request_sha256"),
                                   ("raw_response_path", "raw_response_sha256"),
                                   ("headers_path", "headers_sha256")):
            record[hash_key] = sha((self.root / record[path_key]).read_bytes())
        data = pretty(record)
        self.record_path(record["trial_id"]).write_bytes(data)
        for event in self.events:
            if event.get("trial_id") != record["trial_id"]:
                continue
            if event["type"] == "trial_started":
                event["request_sha256"] = record["request_sha256"]
            elif event["type"] == "trial_finished":
                event.update(record_sha256=sha(data), raw_response_sha256=record["raw_response_sha256"],
                             headers_sha256=record["headers_sha256"], classification=record["classification"])
        self.write_events()


class SyntheticVerificationTests(unittest.TestCase):
    """All writes and Git commits belong to TemporaryDirectory fixtures only."""

    def setUp(self):
        self.no_network = mock.patch("http.client.HTTPSConnection", side_effect=AssertionError("Synthetic tests forbid network"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="void-ab-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = SyntheticArtifact(self.root)

    def assert_independent_rejection(self, reason):
        # There is intentionally no final manifest: rejection must arise from
        # frozen protocol/receipt/response/event checks, not a top-level checksum.
        self.assertFalse((self.root / "SHA256SUMS.txt").exists())
        self.assertFalse((self.root / "MANIFEST.json").exists())
        with self.assertRaisesRegex(verify.VerificationError, reason):
            verify.verify(self.root, require_manifest=False)

    def test_complete_60_slot_122_event_synthetic_fixture_verifies(self):
        records = verify.verify(self.root, require_manifest=False)
        self.assertEqual(len(records), 60)
        self.assertEqual(len((self.root / "events.jsonl").read_bytes().splitlines()), 122)
        self.assertEqual({record["classification"] for record in records}, {"R"})
        self.assertEqual(len({r["trial_id"] for r in records}), 60)

    def test_mutated_user_prompt_rehashed_and_relinked_fails(self):
        record = self.fixture.read_record()
        path = self.root / record["request_path"]
        request = json.loads(path.read_bytes())
        request["messages"][1]["content"] = "Be altered."
        record["user_prompt"] = "Be altered."
        path.write_bytes(compact(request))
        self.fixture.rebind(record)
        self.assert_independent_rejection("wrong scheduled field: user_prompt")

    def test_mutated_system_prompt_rehashed_and_relinked_fails(self):
        record = self.fixture.read_record()
        path = self.root / record["request_path"]
        request = json.loads(path.read_bytes())
        request["messages"][0]["content"] = "Synthetic altered system."
        record["system_prompt"] = "Synthetic altered system."
        path.write_bytes(compact(request))
        self.fixture.rebind(record)
        self.assert_independent_rejection("wrong scheduled field: system_prompt")

    def test_mutated_raw_response_rehashed_and_relinked_fails(self):
        record = self.fixture.read_record()
        path = self.root / record["raw_response_path"]
        body = json.loads(path.read_bytes())
        body["choices"][0]["message"]["content"] = ""
        path.write_bytes(compact(body))
        # Also forge the extraction but keep an inconsistent R classification.
        record.update(message_content="", text_container_present=True,
                      visible_utf8_bytes=0, visible_sha256=sha(b""))
        self.fixture.rebind(record)
        self.assert_independent_rejection("independently derived field differs: classification")

    def test_wrong_returned_model_with_consistent_op_record_fails(self):
        record = self.fixture.read_record()
        path = self.root / record["raw_response_path"]
        body = json.loads(path.read_bytes())
        body["model"] = "synthetic-wrong-snapshot"
        path.write_bytes(compact(body))
        record.update(returned_model="synthetic-wrong-snapshot", classification="OP")
        self.fixture.rebind(record)
        self.assert_independent_rejection("wrong or missing model")

    def test_missing_trial_receipt_fails(self):
        self.fixture.record_path("t060").unlink()
        self.assert_independent_rejection("Missing artifact")

    def test_analysis_counts_only_observed_synthetic_records(self):
        record = self.fixture.read_record()
        path = self.root / record["raw_response_path"]
        body = json.loads(path.read_bytes())
        body["choices"][0]["message"]["content"] = ""
        path.write_bytes(compact(body))
        record.update(message_content="", text_container_present=True,
                      visible_utf8_bytes=0, visible_sha256=sha(b""), classification="V0")
        self.fixture.rebind(record)
        failure = self.fixture.read_record("t002")
        failure.update(http_status=429, classification="OP")
        self.fixture.rebind(failure)
        summary = analyze.analyze(self.root)
        self.assertEqual(len(summary["primary_table"]), 2)
        self.assertEqual(sum(row["N"] for row in summary["primary_table"]), 60)
        self.assertEqual(sum(row["V0"] for row in summary["primary_table"]), 1)
        self.assertEqual(sum(row["Visible"] for row in summary["primary_table"]), 58)
        self.assertEqual(sum(row["Other"] for row in summary["primary_table"]), 1)
        self.assertEqual([row["N"] for row in summary["null_control_totals"]], [30] * 2)
        self.assertEqual(len(summary["matched_pairs"]), 1)
        self.assertEqual(summary["execution_metrics"]["recorded_retries"], 0)
        self.assertEqual(summary["execution_metrics"]["returned_model_matches"], 60)
        self.assertEqual(summary["execution_metrics"]["http_successes"], 59)
        self.assertFalse(summary["execution_metrics"]["all_http_successful"])
        self.assertIn("| Arm | N | 404 | OP | V0 | V1 | NV | R |",
                      (self.root / "results/RESULT.md").read_text(encoding="utf-8"))
        self.assertTrue((self.root / "results/trials.csv").is_file())
        self.assertTrue((self.root / "results/summary.json").is_file())

    def test_analysis_refuses_mutated_data_before_writing_results(self):
        record = self.fixture.read_record()
        record["classification"] = "V0"
        self.fixture.rebind(record)
        with self.assertRaisesRegex(verify.VerificationError, "independently derived field differs: classification"):
            analyze.analyze(self.root)
        self.assertFalse((self.root / "results/RESULT.md").exists())
        self.assertFalse((self.root / "results").exists())

    def set_error(self, trial_id, code="model_not_found", message="Synthetic unavailable model"):
        record = self.fixture.read_record(trial_id)
        (self.root / record["raw_response_path"]).write_bytes(compact({"error": {
            "message": message, "type": "invalid_request_error", "param": None, "code": code}}))
        record.update(http_status=404, classification="OP", text_container_present=False)
        for key in ("returned_model", "response_id", "usage", "message_content", "visible_utf8_bytes",
                    "visible_sha256", "finish_reason", "refusal", "tool_calls", "function_call"):
            record[key] = None
        self.fixture.rebind(record)

    def test_all_404_predictions_and_verbatim_body(self):
        for i in range(1, 61):
            self.set_error("t%03d" % i)
        result = analyze.analyze(self.root)["tombstone"]
        self.assertEqual(result["predictions"]["P1"]["status"], "PASS")
        self.assertEqual(result["predictions"]["P2"]["status"], "PASS")
        self.assertEqual(result["predictions"]["P3"]["status"], "NOT_TRIGGERED")
        self.assertEqual([r["404"] for r in result["table"]], [30, 30])
        self.assertEqual([r["OP"] for r in result["table"]], [30, 30])
        first = result["first_404"]
        self.assertEqual(first["utf8"].encode("utf-8"), (self.root / first["path"]).read_bytes())
        self.assertEqual(len(result["trial_evidence"]), 60)

    def test_404_without_expected_code_does_not_satisfy_p1(self):
        for i in range(1, 61):
            self.set_error("t%03d" % i, code=None)
        result = analyze.analyze(self.root)["tombstone"]
        self.assertEqual(result["predictions"]["P1"]["status"], "FAIL")
        self.assertEqual(result["predictions"]["P2"]["status"], "PASS")

    def test_error_body_difference_fails_p2(self):
        for i in range(1, 61):
            self.set_error("t%03d" % i)
        self.set_error("t002", message="Synthetic different error")
        result = analyze.analyze(self.root)["tombstone"]
        self.assertEqual(result["predictions"]["P1"]["status"], "PASS")
        self.assertEqual(result["predictions"]["P2"]["status"], "FAIL")

    def test_successful_responses_trigger_p3_not_p1_or_p2(self):
        result = analyze.analyze(self.root)["tombstone"]
        self.assertEqual(result["predictions"]["P1"]["status"], "FAIL")
        self.assertEqual(result["predictions"]["P2"]["status"], "FAIL")
        self.assertEqual(result["predictions"]["P3"]["status"], "SATISFIED")
        self.assertEqual(result["predictions"]["P3"]["classifications"], {"R": 60})


if __name__ == "__main__":
    unittest.main()
