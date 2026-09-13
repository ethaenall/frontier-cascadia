from scripts.trials import summarize


def trial(label="crossing", alerts=0, **kwargs):
    row = dict(split="evaluation", source_mode="LIVE", operator_confirmed=True,
               completed=True, issues=[], label=label, config_id="fixture-only",
               event_ids=[str(i) for i in range(alerts)])
    return dict(row, **kwargs)


def test_empty_report_does_not_invent_accuracy():
    assert "NOT RUN" in summarize([])["status"]
    assert not summarize([])["configurations"]


def test_misses_and_false_alert_counts_are_per_configuration():
    report = summarize([trial(), trial(alerts=1), trial("door_only", 2), trial("empty"), trial(config_id="other")])
    assert report["configurations"]["fixture-only"]["crossing"]["missed_crossing_trials"] == 1
    assert report["configurations"]["fixture-only"]["crossing"]["trials"] == 2
    assert report["configurations"]["fixture-only"]["door_only"]["false_alert_trials_for_crossing_target"] == 1
    assert report["configurations"]["fixture-only"]["door_only"]["alarm_events"] == 2
    assert report["configurations"]["other"]["crossing"]["trials"] == 1


def test_tuning_synthetic_unconfirmed_and_faulted_trials_excluded():
    rows = [trial(split="tuning"), trial(source_mode="TEST"), trial(source_mode="REPLAY"),
            trial(operator_confirmed=False), trial(issues=["disconnect"]), trial(completed=False)]
    report = summarize(rows)
    assert report["excluded_or_tuning_trials"] == len(rows)
    assert not report["configurations"]
