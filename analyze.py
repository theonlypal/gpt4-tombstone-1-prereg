#!/usr/bin/env python3
"""Generate descriptive results exclusively from independently verified receipts."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

import verify


def counts(records):
    """Count observed classifications without inserting any expected result."""
    observed = Counter(record["classification"] for record in records)
    n = len(records)
    v0 = observed.get("V0", 0)
    visible = observed.get("R", 0)
    return {
        "N": n,
        "V0": v0,
        "Visible": visible,
        "Other": n - v0 - visible,
        "V0 Rate": v0 / n if n else None,
        "classifications": dict(sorted(observed.items())),
    }


def percent(value):
    return "N/A" if value is None else "{:.1%}".format(value)


def tombstone_report(root, records):
    classes = ("OP", "V0", "V1", "NV", "R", "VU", "RF", "SB", "TC")
    rows, evidence, errors = [], [], {}
    first_404 = None
    for record in records:
        raw = (root / record["raw_response_path"]).read_bytes()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        errors[record["trial_id"]] = error
        headers = json.loads((root / record["headers_path"]).read_bytes())
        ids = [v for k, v in headers if k.lower() == "x-request-id"]
        evidence.append({"trial_id": record["trial_id"], "arm": record["user_prompt"],
                         "http_status": record["http_status"], "classification": record["classification"],
                         "error": error, "request_ids": ids,
                         "started_at_utc": record["started_at_utc"],
                         "ended_at_utc": record["ended_at_utc"],
                         "request_sha256": record["request_sha256"],
                         "raw_response_sha256": record["raw_response_sha256"]})
        if first_404 is None and record["http_status"] == 404:
            first_404 = {"trial_id": record["trial_id"], "path": record["raw_response_path"],
                         "sha256": record["raw_response_sha256"], "utf8": None}
            try:
                first_404["utf8"] = raw.decode("utf-8")
            except UnicodeError:
                pass
    for role in ("null", "control"):
        arm = [r for r in records if r["role"] == role]
        counts_by_class = Counter(r["classification"] for r in arm)
        rows.append({"Arm": arm[0]["user_prompt"], "N": len(arm),
                     "404": sum(r["http_status"] == 404 for r in arm),
                     **{c: counts_by_class[c] for c in classes}})
    p1_count = sum(r["http_status"] == 404 and r["classification"] == "OP" and
                   isinstance(errors[r["trial_id"]], dict) and
                   errors[r["trial_id"]].get("code") == "model_not_found" for r in records)
    error_distributions = [Counter(json.dumps(errors[r["trial_id"]], sort_keys=True, ensure_ascii=False)
                                   for r in records if r["role"] == role)
                           for role in ("null", "control")]
    successes = [r for r in records if isinstance(r["http_status"], int) and 200 <= r["http_status"] < 300]
    predictions = {
        "P1": {"status": "PASS" if p1_count == 60 else "FAIL", "satisfying_slots": p1_count, "N": 60},
        "P2": {"status": "PASS" if all(row["404"] == row["N"] == 30 for row in rows)
               and error_distributions[0] == error_distributions[1] else "FAIL",
               "same_error_object_distribution": error_distributions[0] == error_distributions[1]},
        "P3": {"status": "SATISFIED" if successes else "NOT_TRIGGERED",
               "http_2xx_count": len(successes),
               "classifications": dict(sorted(Counter(r["classification"] for r in successes).items()))},
    }
    event_rows = [json.loads(line) for line in (root / "events.jsonl").read_bytes().splitlines()]
    report = {"table": rows, "predictions": predictions, "trial_evidence": evidence,
              "first_404": first_404, "run_start_utc": event_rows[0]["timestamp_utc"],
              "run_end_utc": event_rows[-1]["timestamp_utc"],
              "first_request_utc": records[0]["started_at_utc"],
              "last_response_utc": records[-1]["ended_at_utc"],
              "error_distributions": [dict(sorted(d.items())) for d in error_distributions]}
    lines = ["# GPT4-TOMBSTONE-1 results", "", "| Arm | N | 404 | OP | V0 | V1 | NV | R |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append("| {Arm} | {N} | {http404} | {OP} | {V0} | {V1} | {NV} | {R} |".format(http404=row["404"], **row))
    lines += ["", "Additional classes (retained in every denominator):", "",
              "| Arm | VU | RF | SB | TC |", "|---|---:|---:|---:|---:|"]
    for row in rows:
        lines.append("| {Arm} | {VU} | {RF} | {SB} | {TC} |".format(**row))
    lines += ["", "## Predictions", "", "```json", json.dumps(predictions, indent=2, sort_keys=True),
              "```", "", "P1 requires HTTP 404, error.code=model_not_found and OP on every slot. "
              "P2 requires all slots to return 404 and the complete parsed error-object distributions "
              "to match across arms. P3 is a reporting commitment, not a behavioral prediction. "
              "A successful response with a wrong or missing model identity fails verification.", "",
              "## UTC timestamps", "", "Run start: " + report["run_start_utc"], "",
              "First request: " + report["first_request_utc"], "",
              "Last response: " + report["last_response_utc"], "", "Run end: " + report["run_end_utc"],
              "", "## First HTTP 404 body", ""]
    if first_404:
        lines += ["Trial: " + first_404["trial_id"], "", "Raw file: `" + first_404["path"] + "`.",
                  "", "SHA-256: `" + first_404["sha256"] + "`.", ""]
        if first_404["utf8"] is None:
            lines += ["The raw body is not valid UTF-8; the original file is authoritative.", ""]
        else:
            lines += ["The following block reproduces the UTF-8 body; the raw file preserves its exact terminal bytes.",
                      "", "````text", first_404["utf8"], "````", ""]
    else:
        lines += ["No HTTP 404 response was observed.", ""]
    lines += ["## Request IDs", "", "| Trial | Arm | HTTP | Class | x-request-id |",
              "|---|---|---:|---|---|"]
    for row in evidence:
        ids = ", ".join(row["request_ids"]) or "absent"
        lines.append("| {trial_id} | {arm} | {http_status} | {classification} | {ids} |".format(**row, ids=ids))
    lines += ["", "## Scope", "", "HTTP 404 is an operational outcome, not a Void. "
              "Availability is measured for this account and execution window; no internal model mechanism "
              "is inferred. Error bodies, request IDs, all classes and all attempted slots are retained.", ""]
    return report, lines


def analyze(root):
    root = Path(root).resolve()
    # Verification precedes every aggregation and every write. In particular,
    # neither a receipt's classification nor a previously saved result is trusted.
    records = verify.verify(root, require_manifest=False)
    protocol = verify.verify_freeze(root)
    primary = []
    role_totals = []
    pair_totals = []
    for model in protocol["models"]:
        model_records = [r for r in records if r["requested_model"] == model]
        for pair in protocol["pairs"]:
            for role in ("null", "control"):
                subset = [r for r in model_records
                          if r["pair_id"] == pair["pair_id"] and r["role"] == role]
                primary.append({"Model": model, "Prompt": pair[role], **counts(subset)})
            nulls = [r for r in model_records
                     if r["pair_id"] == pair["pair_id"] and r["role"] == "null"]
            controls = [r for r in model_records
                        if r["pair_id"] == pair["pair_id"] and r["role"] == "control"]
            pair_totals.append({
                "model": model, "pair_id": pair["pair_id"],
                "null_prompt": pair["null"], "control_prompt": pair["control"],
                "null": counts(nulls), "control": counts(controls),
            })
        for role in ("null", "control"):
            subset = [r for r in model_records if r["role"] == role]
            role_totals.append({"model": model, "role": role, **counts(subset)})

    http_success = sum(isinstance(r.get("http_status"), int)
                       and 200 <= r["http_status"] < 300 for r in records)
    returned_present = sum(isinstance(r.get("returned_model"), str)
                           and bool(r["returned_model"]) for r in records)
    returned_matches = sum(r.get("returned_model") == r["requested_model"] for r in records)
    finish_reasons = Counter(
        str(r["finish_reason"]) if r.get("finish_reason") is not None else "<absent>"
        for r in records
    )
    metrics = {
        "recorded_attempts": len(records),
        "http_successes": http_success,
        "all_http_successful": http_success == len(records),
        "recorded_retries": sum(max(0, r["attempt_number"] - 1) for r in records),
        "returned_model_ids_present": returned_present,
        "returned_model_matches": returned_matches,
        "transport_errors": sum(bool(r.get("transport_error")) for r in records),
        "finish_reasons": dict(sorted(finish_reasons.items())),
        "classifications": counts(records)["classifications"],
    }
    summary = {
        "schema_version": 1,
        "source": "Only independently verified records from this artifact.",
        "scope": protocol["scope"],
        "endpoint": protocol["endpoint"],
        "models": protocol["models"],
        "system_prompt": protocol["system_prompt"],
        "settings": protocol["settings"],
        "omitted_parameters": protocol["omitted_parameters"],
        "context": protocol["context"],
        "primary_table": primary,
        "null_control_totals": role_totals,
        "matched_pairs": pair_totals,
        "execution_metrics": metrics,
    }

    lines = [
        "# Observed Void A/B results", "",
        "These counts use only the complete run's independently verified receipts. "
        "V0 denotes successful, present-string, zero-byte content with normal stop, "
        "after operational failures, refusals, safety blocks and tool responses are excluded. "
        "Visible means R; Other includes every remaining classification.", "",
        "| Model | Prompt | N | V0 | Visible | Other | V0 Rate |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in primary:
        lines.append("| {Model} | {Prompt} | {N} | {V0} | {Visible} | {Other} | {rate} |".format(
            **row, rate=percent(row["V0 Rate"])))
    lines.extend(["", "## Null and control totals", ""])
    for row in role_totals:
        lines.extend(["{model} {role_upper} V0: {V0}/{N}".format(
            **row, role_upper=row["role"].upper()), ""])
    lines.extend(["", "## Each matched pair", "",
                  "| Model | Null prompt | Null V0 / N | Control prompt | Control V0 / N |",
                  "|---|---|---:|---|---:|"])
    for pair in pair_totals:
        lines.append("| {model} | {null_prompt} | {nv0} / {nn} | {control_prompt} | {cv0} / {cn} |".format(
            **pair, nv0=pair["null"]["V0"], nn=pair["null"]["N"],
            cv0=pair["control"]["V0"], cn=pair["control"]["N"]))
    lines.extend([
        "", "## Execution evidence", "",
        "Recorded attempts: {recorded_attempts}; HTTP successes: {http_successes}/{recorded_attempts}; "
        "recorded retries: {recorded_retries}; transport errors: {transport_errors}.".format(**metrics),
        "",
        "Returned model IDs exactly matched the requested snapshot in {returned_model_matches}/"
        "{recorded_attempts} records; {returned_model_ids_present}/{recorded_attempts} records "
        "contained a returned model ID.".format(**metrics), "",
        "Finish reasons: " + json.dumps(metrics["finish_reasons"], sort_keys=True) + ".", "",
        "Classifications: " + json.dumps(metrics["classifications"], sort_keys=True) + ".", "",
        "## Exact scope", "",
        "Models: " + ", ".join("`" + m + "`" for m in protocol["models"]) + ".", "",
        "Endpoint: `" + protocol["endpoint"] + "`.", "",
        "Settings: `" + json.dumps(protocol["settings"], sort_keys=True) + "`.", "",
        "System prompt:", "", "```text", protocol["system_prompt"], "```", "",
        protocol["context"], "",
        "Omitted parameters: " + ", ".join("`" + p + "`" for p in protocol["omitted_parameters"]) + ".", "",
        "All scheduled operational outcomes remain in their denominators; there is no imputation.", "",
        protocol["scope"], "",
    ])

    summary["tombstone"], lines = tombstone_report(root, records)
    results = root / "results"
    results.mkdir(exist_ok=True)
    columns = [
        "trial_id", "attempt_number", "requested_model", "returned_model", "pair_id", "role",
        "replicate", "user_prompt", "http_status", "classification", "finish_reason",
        "message_content", "text_container_present", "visible_utf8_bytes", "visible_sha256",
        "raw_response_received", "response_body_complete", "transport_error",
    ]
    with (results / "trials.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    (results / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (results / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    summary = analyze(args.root)
    print("Analysis generated from {} independently verified records.".format(
        summary["execution_metrics"]["recorded_attempts"]))


if __name__ == "__main__":
    main()
