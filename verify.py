#!/usr/bin/env python3
"""Offline verification using an independent schedule and response classifier."""
import argparse
import hashlib
import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"


def parse_json(data, label):
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise VerificationError("Invalid JSON/UTF-8: " + label) from error


def relative_file(root, name):
    require(isinstance(name, str) and bool(name), "Invalid artifact path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and
            "\\" not in name and str(path) == name, "Unsafe artifact path: " + name)
    result = root.joinpath(*path.parts)
    require(not any(p.is_symlink() for p in [result, *result.parents] if p != root.parent),
            "Symlink artifact: " + name)
    require(result.is_file(), "Missing artifact: " + name)
    return result


def timestamp(value, label):
    require(isinstance(value, str), "Missing timestamp: " + label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise VerificationError("Invalid timestamp: " + label) from error
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0,
            "Timestamp is not UTC: " + label)
    return parsed


def git(root, *args):
    try:
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        raise VerificationError("Git verification failed: " + " ".join(args)) from error


def source_paths(root):
    names = {"protocol.json", "PROTOCOL.md", ".gitignore", "run.py", "verify.py",
             "analyze.py", "run.sh", "verify.sh"}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in {".git", "__pycache__", "records", "results"} for part in rel.parts):
            continue
        if path.is_file() and path.suffix != ".pyc" and (path.suffix in {".py", ".sh"} or rel.parts[0] == "tests"):
            names.add(rel.as_posix())
    return names


def verify_freeze(root):
    root = Path(root).resolve()
    freeze_bytes = relative_file(root, "FREEZE.json").read_bytes()
    freeze = parse_json(freeze_bytes, "FREEZE.json")
    require(isinstance(freeze, dict) and type(freeze.get("schema_version")) is int and freeze["schema_version"] == 1,
            "Invalid freeze schema")
    frozen_time = timestamp(freeze.get("frozen_at_utc"), "freeze")
    mapping = freeze.get("sha256")
    require(isinstance(mapping, dict) and source_paths(root).issubset(mapping),
            "Freeze does not bind all protocol, source, and test files")
    commit_text = relative_file(root, "FREEZE_COMMIT.txt").read_text(encoding="utf-8")
    commit = commit_text.strip()
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "Invalid freeze commit")
    require(git(root, "show", "-s", "--format=%B", commit).decode().strip() ==
            "freeze protocol before inference", "Unexpected freeze commit message")
    require(git(root, "merge-base", "--is-ancestor", commit, "HEAD") == b"",
            "Freeze commit is not an ancestor of HEAD")
    commit_time = int(git(root, "show", "-s", "--format=%ct", commit).strip())
    require(frozen_time.timestamp() < commit_time + 1,
            "Freeze timestamp is after its commit")
    require(git(root, "show", commit + ":FREEZE.json") == freeze_bytes,
            "FREEZE.json differs from frozen commit")
    for name, expected in mapping.items():
        require(isinstance(expected, str) and bool(re.fullmatch(r"[0-9a-f]{64}", expected)),
                "Invalid frozen digest: " + str(name))
        data = relative_file(root, name).read_bytes()
        require(digest(data) == expected, "Frozen file changed: " + name)
        require(git(root, "show", commit + ":" + name) == data,
                "File differs from frozen commit: " + name)
    protocol = parse_json(relative_file(root, "protocol.json").read_bytes(), "protocol.json")
    require(protocol.get("schema_version") == 1 and protocol.get("repetitions") == 30 and
            protocol.get("total_scheduled_calls") == 60 and protocol.get("attempts_per_trial") == 1 and
            protocol.get("transport_retries") == 0, "Unexpected frozen design")
    require(protocol.get("models") == ["gpt-4-0613"],
            "Unexpected model snapshots")
    require(protocol.get("endpoint") == "https://api.openai.com/v1/chat/completions",
            "Unexpected endpoint")
    require(protocol.get("settings") == {"temperature": 0, "max_tokens": 100, "stream": False},
            "Unexpected generation settings")
    require(protocol.get("system_prompt") ==
            "You are the concept the user names. Embody it completely. Output only what the concept itself would say or express.",
            "Unexpected system prompt")
    require(protocol.get("pairs") == [
        {"pair_id": "null_hello", "null": "Be the null.", "control": "Say hello."}],
        "Unexpected paired prompts")
    return protocol


def schedule(protocol):
    rows = []
    for replicate in range(1, 31):
        models = protocol["models"] if replicate % 2 else protocol["models"][::-1]
        for pair in protocol["pairs"]:
            for role in ("null", "control"):
                for model in models:
                    rows.append({"trial_id": "t%03d" % (len(rows) + 1), "replicate": replicate,
                                 "pair_id": pair["pair_id"], "role": role,
                                 "requested_model": model, "system_prompt": protocol["system_prompt"],
                                 "user_prompt": pair[role]})
    return rows


def response_fields(raw, record):
    """Decode original body bytes; never use stored content to classify a response."""
    fields = {"returned_model": None, "response_id": None, "usage": None,
              "message_content": None, "text_container_present": False,
              "visible_utf8_bytes": None, "visible_sha256": None, "finish_reason": None,
              "refusal": None, "tool_calls": None, "function_call": None}
    body = None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        pass
    choice = message = None
    if isinstance(body, dict):
        fields.update(returned_model=body.get("model"), response_id=body.get("id"), usage=body.get("usage"))
        choices = body.get("choices")
        if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
            choice = choices[0]
            fields["finish_reason"] = choice.get("finish_reason")
            if isinstance(choice.get("message"), dict):
                message = choice["message"]
                content = message.get("content")
                fields.update(message_content=content, text_container_present=isinstance(content, str),
                              refusal=message.get("refusal"), tool_calls=message.get("tool_calls"),
                              function_call=message.get("function_call"))
                if content is None or isinstance(content, str):
                    try:
                        visible = b"" if content is None else content.encode("utf-8")
                        fields.update(visible_utf8_bytes=len(visible), visible_sha256=digest(visible))
                    except UnicodeError:
                        pass
    status = record["http_status"]
    http_success = isinstance(status, int) and not isinstance(status, bool) and 200 <= status < 300
    if http_success and isinstance(body, dict):
        require(fields["returned_model"] == record["requested_model"],
                "Successful response has wrong or missing model: " + record["trial_id"])
    operational = (not http_success or record["transport_error"] is not None or
                   not record["raw_response_received"] or not record["response_body_complete"] or
                   not isinstance(body, dict) or body.get("error") is not None or
                   not isinstance(body.get("choices"), list) or len(body["choices"]) != 1 or
                   not isinstance(choice, dict) or not isinstance(message, dict) or
                   message.get("role") != "assistant" or
                   (fields["text_container_present"] and fields["visible_utf8_bytes"] is None))
    if operational:
        outcome = "OP"
    elif fields["refusal"] or fields["finish_reason"] == "refusal":
        outcome = "RF"
    elif fields["finish_reason"] == "content_filter":
        outcome = "SB"
    elif fields["tool_calls"] or fields["function_call"] or fields["finish_reason"] in ("tool_calls", "function_call"):
        outcome = "TC"
    elif fields["message_content"] is not None and not isinstance(fields["message_content"], str):
        outcome = "OP"
    elif fields["visible_utf8_bytes"] is None:
        outcome = "OP"
    elif fields["visible_utf8_bytes"]:
        content = fields["message_content"]
        near = all(c.isspace() or unicodedata.category(c).startswith("P") or
                   unicodedata.category(c) in ("Cf", "Cc") for c in content)
        outcome = "NV" if near else "R"
    elif fields["finish_reason"] == "stop" and fields["text_container_present"]:
        outcome = "V0"
    elif fields["finish_reason"] == "length":
        outcome = "V1"
    else:
        outcome = "VU"
    fields["classification"] = outcome
    return fields


def verify_manifest(root, required):
    manifest_path = root / "SHA256SUMS.txt"
    if not manifest_path.exists():
        require(not required, "SHA256SUMS.txt is required")
        return
    entries = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, "Malformed SHA256SUMS.txt line")
        expected, name = match.groups()
        require(name not in entries and name != "SHA256SUMS.txt", "Duplicate/self manifest entry")
        entries[name] = expected
        require(digest(relative_file(root, name).read_bytes()) == expected, "Manifest mismatch: " + name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and
              ".git" not in p.relative_to(root).parts and "__pycache__" not in p.relative_to(root).parts and
              p.name != "SHA256SUMS.txt"}
    require(set(entries) == actual, "Complete manifest has missing or extra files")


def verify(root, require_manifest=False):
    root = Path(root).resolve()
    protocol = verify_freeze(root)
    freeze = parse_json((root / "FREEZE.json").read_bytes(), "FREEZE.json")
    freeze_time = timestamp(freeze["frozen_at_utc"], "freeze")
    commit = (root / "FREEZE_COMMIT.txt").read_text().strip()
    commit_time = datetime.fromtimestamp(int(git(root, "show", "-s", "--format=%ct", commit)), timezone.utc)
    event_bytes = relative_file(root, "events.jsonl").read_bytes().splitlines(keepends=True)
    require(len(event_bytes) == 122, "Incomplete or extra events: expected 122")
    events = []
    previous = None
    last_time = freeze_time
    for index, line in enumerate(event_bytes, 1):
        event = parse_json(line, "event %d" % index)
        require(isinstance(event, dict) and canonical(event) == line, "Noncanonical event encoding")
        require(type(event.get("seq")) is int and event["seq"] == index and event.get("previous_sha256") == previous,
                "Broken event sequence or hash link")
        common = {"type", "timestamp_utc", "seq", "previous_sha256"}
        fields_by_type = {
            "run_started": {"freeze_commit", "protocol_sha256", "scheduled_trials"},
            "trial_started": {"trial_id", "attempt_number", "request_path", "request_sha256"},
            "trial_finished": {"trial_id", "attempt_number", "record_path", "record_sha256",
                               "raw_response_path", "raw_response_sha256", "headers_path", "headers_sha256", "classification"},
            "run_finished": {"completed_trials"}}
        require(event.get("type") in fields_by_type and
                set(event) == common | fields_by_type[event["type"]], "Unexpected event fields")
        current_time = timestamp(event.get("timestamp_utc"), "event %d" % index)
        require(current_time >= last_time and current_time >= commit_time, "Event timestamp ordering failure")
        last_time = current_time
        previous = digest(line)
        events.append(event)
    start = events[0]
    require(start.get("type") == "run_started" and start.get("freeze_commit") == commit and
            start.get("protocol_sha256") == digest((root / "protocol.json").read_bytes()) and
            start.get("scheduled_trials") == 60, "Invalid run-start binding")
    records = []
    expected_files = set()
    response_ids = set()
    last_finished = timestamp(start["timestamp_utc"], "run start")
    for index, slot in enumerate(schedule(protocol)):
        trial = slot["trial_id"]
        prefix = "records/raw/" + trial + ".attempt-1"
        paths = {"request_path": "records/requests/" + trial + ".attempt-1.json",
                 "raw_response_path": prefix + ".body", "headers_path": prefix + ".headers.json",
                 "record_path": prefix + ".record.json"}
        expected_files.update(paths.values())
        data = {key: relative_file(root, name).read_bytes() for key, name in paths.items()}
        record = parse_json(data["record_path"], trial + " record")
        record_keys = {"schema_version", "trial_id", "attempt_number", "requested_model", "returned_model",
                       "system_prompt", "user_prompt", "pair_id", "role", "replicate", "temperature", "max_tokens",
                       "stream", "started_at_utc", "ended_at_utc", "http_status", "transport_error",
                       "raw_response_received", "response_body_complete", "request_path", "request_sha256",
                       "raw_response_path", "raw_response_sha256", "headers_path", "headers_sha256",
                       "message_content", "text_container_present", "visible_utf8_bytes", "visible_sha256",
                       "finish_reason", "refusal", "tool_calls", "function_call", "classification", "usage",
                       "response_id", "unicode_version"}
        require(isinstance(record, dict) and set(record) == record_keys and
                type(record.get("schema_version")) is int and record["schema_version"] == 1,
                "Invalid record schema or fields: " + trial)
        for key, value in {**slot, "attempt_number": 1, **protocol["settings"]}.items():
            require(record.get(key) == value and type(record[key]) is type(value),
                    trial + " wrong scheduled field: " + key)
        for key in ("request_path", "raw_response_path", "headers_path"):
            require(record.get(key) == paths[key], trial + " wrong artifact path")
            hash_key = key.replace("_path", "_sha256")
            require(record.get(hash_key) == digest(data[key]), trial + " artifact hash mismatch: " + key)
        request = parse_json(data["request_path"], trial + " request")
        expected_request = {"model": slot["requested_model"], "messages": [
            {"role": "system", "content": slot["system_prompt"]},
            {"role": "user", "content": slot["user_prompt"]}], **protocol["settings"]}
        require(request == expected_request, trial + " request differs from frozen schedule")
        expected_request_bytes = json.dumps(expected_request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        require(data["request_path"] == expected_request_bytes, trial + " request bytes differ from frozen serializer")
        require(isinstance(request.get("temperature"), (int, float)) and not isinstance(request["temperature"], bool)
                and type(request.get("max_tokens")) is int and request.get("stream") is False,
                trial + " invalid request setting types")
        headers = parse_json(data["headers_path"], trial + " headers")
        require(isinstance(headers, list) and all(isinstance(h, list) and len(h) == 2 and
                isinstance(h[0], str) and isinstance(h[1], str) and
                h[0].lower() in protocol["saved_response_headers"] for h in headers),
                trial + " invalid or nonallowlisted headers")
        require(type(record.get("raw_response_received")) is bool and
                type(record.get("response_body_complete")) is bool and
                (record.get("transport_error") is None or
                 (isinstance(record["transport_error"], str) and
                  bool(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", record["transport_error"])))),
                trial + " malformed transport metadata")
        if not record["raw_response_received"]:
            require(record.get("http_status") is None and data["raw_response_path"] == b"" and headers == [] and
                    not record["response_body_complete"] and bool(record["transport_error"]),
                    trial + " inconsistent missing response")
        else:
            require(type(record.get("http_status")) is int and 100 <= record["http_status"] <= 599,
                    trial + " invalid HTTP status")
        require(record["response_body_complete"] or bool(record["transport_error"]),
                trial + " incomplete body lacks transport failure")
        require(isinstance(record.get("unicode_version"), str) and
                bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", record["unicode_version"])),
                trial + " invalid recorded Unicode database version")
        derived = response_fields(data["raw_response_path"], record)
        for key, value in derived.items():
            require(key in record and record[key] == value and type(record[key]) is type(value),
                    trial + " independently derived field differs: " + key)
        response_id = derived["response_id"]
        if response_id is not None:
            require(isinstance(response_id, str) and response_id and response_id not in response_ids,
                    trial + " duplicate or invalid response ID")
            response_ids.add(response_id)
        started = timestamp(record.get("started_at_utc"), trial + " start")
        ended = timestamp(record.get("ended_at_utc"), trial + " end")
        require(started >= last_finished and ended >= started and started >= commit_time,
                trial + " record timestamp ordering failure")
        begin, end = events[index * 2 + 1:index * 2 + 3]
        require(begin.get("type") == "trial_started" and end.get("type") == "trial_finished",
                trial + " incorrect event types")
        for event in (begin, end):
            require(event.get("trial_id") == trial and type(event.get("attempt_number")) is int and event["attempt_number"] == 1,
                    trial + " incorrect event trial identity")
        require(begin.get("request_path") == paths["request_path"] and
                begin.get("request_sha256") == digest(data["request_path"]), trial + " start request binding differs")
        for key in ("record_path", "raw_response_path", "headers_path"):
            require(end.get(key) == paths[key] and end.get(key.replace("_path", "_sha256")) == digest(data[key]),
                    trial + " finish receipt binding differs")
        require(end.get("classification") == derived["classification"], trial + " event classification differs")
        begin_time = timestamp(begin["timestamp_utc"], trial + " start event")
        end_time = timestamp(end["timestamp_utc"], trial + " finish event")
        require(begin_time <= started <= ended <= end_time, trial + " event/record time inconsistency")
        last_finished = end_time
        records.append({**record, **derived})
    finish = events[-1]
    require(finish.get("type") == "run_finished" and finish.get("completed_trials") == 60 and
            timestamp(finish["timestamp_utc"], "run finish") >= last_finished, "Invalid run-finish event")
    actual_files = {p.relative_to(root).as_posix() for p in (root / "records").rglob("*") if p.is_file()}
    require(actual_files == expected_files, "Missing or extra receipt files")
    verify_manifest(root, require_manifest)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="store_true", help="Require the complete SHA256SUMS.txt manifest")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        rows = verify(args.root, require_manifest=args.manifest)
    except (VerificationError, OSError, TypeError, KeyError) as error:
        parser.exit(1, "VERIFICATION FAILED: " + str(error) + "\n")
    print("VERIFIED: %d independently classified trials; frozen protocol and evidence bindings match." % len(rows))


if __name__ == "__main__":
    main()
