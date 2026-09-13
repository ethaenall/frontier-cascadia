"""Operator-ground-truthed LIVE trial capture. No automatic sensing claims."""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
LABELS = ("empty", "crossing", "door_only", "approach_turn", "nearby")


def utc():
    return datetime.now(timezone.utc).isoformat()


def summarize(trials):
    groups = defaultdict(list)
    excluded = 0
    for trial in trials:
        if (trial.get("split") != "evaluation" or trial.get("source_mode") != "LIVE"
                or not trial.get("operator_confirmed") or trial.get("issues")
                or trial.get("label") not in LABELS or not trial.get("completed")):
            excluded += 1
            continue
        groups[trial["config_id"]].append(trial)
    report = {"claim": "Crossing-target counts, not a validated crossing classifier or accuracy guarantee.",
              "excluded_or_tuning_trials": excluded, "configurations": {}}
    for config_id, rows in groups.items():
        by_label = {}
        for label in LABELS:
            subset = [row for row in rows if row["label"] == label]
            alerted = sum(bool(row.get("event_ids")) for row in subset)
            item = {"trials": len(subset), "alerted_trials": alerted,
                    "alarm_events": sum(len(set(row.get("event_ids", []))) for row in subset)}
            if label == "crossing":
                item["missed_crossing_trials"] = len(subset) - alerted
            else:
                item["false_alert_trials_for_crossing_target"] = alerted
            by_label[label] = item
        report["configurations"][config_id] = by_label
    if not groups:
        report["status"] = "NOT RUN: no eligible confirmed LIVE evaluation trials."
    return report


async def capture(args):
    url = urlsplit(args.url)
    if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.username or url.password:
        raise ValueError("Trial capture is laptop-loopback-only; no credentials sent to an external URL.")
    token = args.token_file.read_text().strip()
    headers = {"Authorization": "Bearer " + token}
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=2, trust_env=False) as client:
        response = await client.get("/api/state")
        response.raise_for_status()
        initial = response.json()
        if (initial["source_mode"] != "LIVE" or initial["alarm_state"] != "ARMED"
                or initial["health"]["status"] != "healthy" or not initial["calibration"]["ready"]):
            raise ValueError("Start requires LIVE, ARMED, healthy, calibrated. Calibrate/arm in the dashboard first.")
        if (not initial["recording"].get("enabled") or initial["recording"].get("error")
                or not initial["recording"].get("session_path")):
            raise ValueError("Start requires working raw-data recording for trial evidence.")
        print(f"START {args.split} {args.label}: {args.duration:g}s. Perform ONLY the named action once near the middle.", flush=True)
        start_utc, start = utc(), time.monotonic()
        known = {row["event_id"] for row in initial["events"]}
        events, issues, observations = {}, set(), []
        while time.monotonic() - start < args.duration:
            try:
                response = await client.get("/api/state")
                response.raise_for_status()
                state = response.json()
                if state["session_id"] != initial["session_id"]: issues.add("backend session changed")
                if state["source_mode"] != "LIVE": issues.add("source is not LIVE")
                if state["health"]["status"] != "healthy": issues.add("unhealthy stream during trial")
                if not state["calibration"]["ready"]: issues.add("baseline invalid during trial")
                if state["alarm_state"] not in {"ARMED", "ALARM"}: issues.add("detector not armed during trial")
                if state["features"]["threshold"] != initial["features"]["threshold"]: issues.add("threshold changed")
                if state["recording"].get("error"): issues.add("raw recording failed")
                for alarm in state["events"]:
                    if alarm["event_id"] not in known and alarm.get("occurred_at", "") >= start_utc:
                        events[alarm["event_id"]] = alarm
                observations.append({"at": utc(), "health": state["health"]["status"],
                                     "alarm_state": state["alarm_state"], "score": state["features"]["activity_score"],
                                     "valid_packets": state["health"]["valid_packets"]})
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                issues.add("API observation failed")
            await asyncio.sleep(0.25)
        end_utc = utc()
    performed = input("STOP. Did you perform exactly the labelled action (empty = stayed still)? Type yes to confirm: ").strip().lower() == "yes"
    trial = {"trial_id": str(uuid.uuid4()), "schema_version": 1,
             "source_mode": "LIVE", "session_id": initial["session_id"], "label": args.label,
             "split": args.split, "config_id": args.config_id, "started_at": start_utc,
             "ended_at": end_utc, "requested_duration_s": args.duration, "completed": True,
             "operator_confirmed": performed, "issues": sorted(issues),
             "threshold": initial["features"]["threshold"], "recording": initial["recording"],
             "event_ids": sorted(events), "events": list(events.values()), "observations": observations}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as file:
        file.write(json.dumps(trial, allow_nan=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    print(json.dumps({"saved": str(args.output), "alarm_events": len(events), "confirmed": performed, "issues": sorted(issues)}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--label", choices=LABELS, required=True)
    rec.add_argument("--split", choices=("tuning", "evaluation"), required=True)
    rec.add_argument("--config-id", required=True)
    rec.add_argument("--duration", type=float, default=20)
    rec.add_argument("--url", default="http://127.0.0.1:8765")
    rec.add_argument("--token-file", type=Path, default=ROOT/".local/control-token")
    rec.add_argument("--output", type=Path, default=ROOT/"recordings/trials.jsonl")
    report = sub.add_parser("report")
    report.add_argument("--input", type=Path, default=ROOT/"recordings/trials.jsonl")
    args = parser.parse_args()
    if args.command == "record":
        if not 5 <= args.duration <= 120:
            parser.error("Duration must be 5–120 seconds")
        try:
            asyncio.run(capture(args))
        except (ValueError, OSError, httpx.HTTPError) as error:
            # Never print an HTTP exception which may contain request metadata.
            print(str(error) if isinstance(error, ValueError) else "Trial failed: check local app, authentication and recording paths.")
            raise SystemExit(1)
    else:
        rows = []
        if args.input.exists():
            for line in args.input.read_text().splitlines():
                if line.strip(): rows.append(json.loads(line))
        print(json.dumps(summarize(rows), indent=2))


if __name__ == "__main__":
    main()
