import json

import pytest

from native_pc import arkframework_replay
from native_pc.arkframework_replay import RegenerationRecorder, read_regeneration_snapshot


@pytest.fixture
def timeline():
    # Field names and first regeneration match the ordinary PC CE-5 recording.
    return {
        "schema": 1, "revision": 318, "max_frame": 1300, "source": "game",
        "meta": {"level_id": "Obt/Weekly/level_weekly_melee_5", "stage_id": "wk_melee_5", "seed": 275629443,
                 "created_unix_ms": 1795269299761, "source": "game"},
        "level_id": "Obt/Weekly/level_weekly_melee_5", "stage": "wk_melee_5", "seed": 275629443,
        "current_id": "n1", "root_id": "n1", "active_path": ["n1"],
        "jumping": False, "advancing": False, "nodes": [{"id": "n1", "parent_id": ""}],
        "damage_hits": [
            {"node_id": "n1", "type": 4, "value": 500},
            {"node_id": "n1", "type": 6, "frame": 1270, "value": 2.0,
             "actor": "char_1045_svash2", "target": "char_1045_svash2",
             "actor_uid": 120, "target_uid": 120},
        ],
    }


def save(tmp_path, timeline):
    path = tmp_path / "battle_timeline.json"
    path.write_text(json.dumps(timeline), encoding="utf-8-sig")
    return path


def test_regeneration_preserves_instances_and_matching_hits(tmp_path, timeline):
    timeline["damage_hits"].append(timeline["damage_hits"][-1].copy())
    response = read_regeneration_snapshot(save(tmp_path, timeline))
    assert response["coverage"] == "recorded_hits"
    assert response["battle"] == {"level_id": "Obt/Weekly/level_weekly_melee_5", "stage_id": "wk_melee_5",
                                  "created_unix_ms": 1795269299761, "seed": 275629443}
    expected = {"type": "regeneration", "frame": 1270, "node_id": "n1", "actor_uid": 120,
                "target_uid": 120, "actor_id": "char_1045_svash2",
                "target_id": "char_1045_svash2", "actual_amount": 2.0}
    assert response["events"] == [dict(expected, hit_index=1), dict(expected, hit_index=2)]
    assert response["max_frame"] == 1300


def test_rewrite_replaces_old_events_and_same_stage_restart_has_new_identity(tmp_path, timeline):
    path = save(tmp_path, timeline)
    first = read_regeneration_snapshot(path)
    assert first == read_regeneration_snapshot(path)
    timeline["damage_hits"].pop()
    timeline["revision"] += 1
    revised = read_regeneration_snapshot(save(tmp_path, timeline))
    assert revised["battle"] == first["battle"]
    assert revised["events"] == []
    timeline["meta"]["created_unix_ms"] += 1000
    timeline["revision"] = 0
    assert read_regeneration_snapshot(save(tmp_path, timeline))["battle"] != first["battle"]


@pytest.mark.parametrize(("changes", "coverage"), [
    ({"source": "file"}, "non_game_timeline"),
    ({"jumping": True}, "timeline_jumping"),
    ({"advancing": True}, "timeline_jumping"),
    ({"current_id": "n2", "nodes": [{"id": "n1", "parent_id": ""}, {"id": "n2", "parent_id": "n1"}],
      "active_path": ["n1", "n2"]}, "branched_timeline"),
    ({"nodes": [{"id": "n1", "parent_id": ""}, {"id": "n2", "parent_id": "n1"}]},
     "branched_timeline"),
])
def test_simulated_or_branched_timelines_do_not_supply_original_events(
        tmp_path, timeline, changes, coverage):
    timeline.update(changes)
    response = read_regeneration_snapshot(save(tmp_path, timeline))
    assert response["coverage"] == coverage
    assert response["events"] == []


@pytest.mark.parametrize("changes", [
    {"frame": 1301}, {"value": float("nan")}, {"value": -2}, {"value": True},
    {"type": "6"}, {"target_uid": 3.5}, {"actor": None}, {"node_id": "n2"},
])
def test_malformed_records_cannot_return_partial_or_silent_empty_coverage(
        tmp_path, timeline, changes):
    timeline["damage_hits"].append({**timeline["damage_hits"][1], **changes})
    with pytest.raises(ValueError):
        read_regeneration_snapshot(save(tmp_path, timeline))


def test_zero_amount_and_unknown_source_uid_are_preserved(tmp_path, timeline):
    timeline["damage_hits"][-1].update(actor="__no_source__", actor_uid=0, value=0)
    event, = read_regeneration_snapshot(save(tmp_path, timeline))["events"]
    assert (event["actor_id"], event["actor_uid"], event["actual_amount"]) == ("__no_source__", 0, 0)


def test_replaced_mid_read_and_truncated_files_are_retryable(tmp_path, timeline, monkeypatch):
    path = save(tmp_path, timeline)
    different_file = tmp_path / "other"
    different_file.write_bytes(b"x")
    other_stat = different_file.stat()
    with monkeypatch.context() as context:
        context.setattr(type(path), "stat", lambda self: other_stat)
        with pytest.raises(ValueError, match="rewrote the timeline"):
            read_regeneration_snapshot(path)
    path.write_bytes(b'{"schema":1,')
    with pytest.raises(ValueError):
        read_regeneration_snapshot(path)
    assert read_regeneration_snapshot(save(tmp_path, timeline))["coverage"] == "recorded_hits"


def test_recorder_replaces_export_on_rewrite_and_preserves_separate_replay_history(tmp_path, timeline):
    path = save(tmp_path, timeline)
    recorder = RegenerationRecorder(path, tmp_path / "out")
    first = recorder.poll()
    assert json.loads(first.read_bytes()) == read_regeneration_snapshot(path)
    timeline["nodes"].append({"id": "n2", "parent_id": "n1"})
    timeline["revision"] += 1
    save(tmp_path, timeline)
    assert recorder.poll() == first
    discarded = json.loads(first.read_bytes())
    assert discarded["coverage"] == "branched_timeline"
    assert discarded["events"] == []

    timeline["nodes"].pop()
    timeline["meta"]["created_unix_ms"] += 5
    timeline["revision"] = 1
    save(tmp_path, timeline)
    second = recorder.poll()
    assert second != first
    assert json.loads(second.read_bytes())["coverage"] == "recorded_hits"
    assert json.loads(first.read_bytes()) == discarded
    assert set((tmp_path / "out").iterdir()) == {first, second}


def test_recorder_skips_unchanged_input_before_decoding(tmp_path, timeline, monkeypatch):
    recorder = RegenerationRecorder(save(tmp_path, timeline), tmp_path / "out")
    assert recorder.poll() is not None
    monkeypatch.setattr(arkframework_replay, "read_regeneration_snapshot",
                        lambda path: pytest.fail("Reread unchanged timeline"))
    assert recorder.poll() is None


def test_atomic_recorder_retries_failed_read_and_failed_write(tmp_path, timeline, monkeypatch):
    path = save(tmp_path, timeline)
    recorder = RegenerationRecorder(path, tmp_path / "out")
    output = recorder.poll()
    original = output.read_bytes()
    path.write_bytes(b'{"schema":')
    for _ in range(2):
        with pytest.raises(ValueError):
            recorder.poll()
    assert output.read_bytes() == original

    timeline["revision"] += 1
    timeline["damage_hits"].clear()
    save(tmp_path, timeline)
    with monkeypatch.context() as context:
        def fail_replace(src, dst):
            raise PermissionError("replacement failed")
        context.setattr(arkframework_replay.os, "replace", fail_replace)
        with pytest.raises(PermissionError):
            recorder.poll()
    assert list((tmp_path / "out").iterdir()) == [output]
    assert output.read_bytes() == original
    assert recorder.poll() == output
    assert json.loads(output.read_bytes())["events"] == []


def test_poll_interval_includes_failures_and_forced_final_poll(tmp_path, timeline):
    recorder = RegenerationRecorder(tmp_path / "battle_timeline.json", tmp_path / "out")
    with pytest.raises(FileNotFoundError):
        recorder.poll_if_due(2.0)
    save(tmp_path, timeline)
    assert recorder.poll_if_due(2.99) is None
    assert recorder.poll_if_due(3.0) is not None
    timeline["damage_hits"].clear()
    save(tmp_path, timeline)
    assert recorder.poll_if_due(3.5) is None
    assert recorder.poll_if_due(3.5, force=True) is not None


def test_reporting_deduplicates_errors_until_next_saved_snapshot(tmp_path, timeline):
    messages = []
    def audit(kind, fields):
        messages.append((kind, fields))
    path = tmp_path / "battle_timeline.json"
    recorder = RegenerationRecorder(path, tmp_path / "out")
    assert recorder.poll_reporting(0, audit) is None
    assert recorder.poll_reporting(1, audit) is None
    first_error, = messages
    assert first_error[0] == "arkframework_timeline_unavailable"
    assert first_error[1]["reason"]
    path.write_bytes(b"{")
    assert recorder.poll_reporting(2, audit) is None
    assert messages[-1][1]["reason"] != first_error[1]["reason"]
    assert len(messages) == 2
    save(tmp_path, timeline)
    saved = recorder.poll_reporting(2, audit, force=True)
    assert messages[-1] == ("arkframework_regeneration_saved", {"file": saved.name})
    assert recorder.poll_reporting(3, audit) is None
    assert len(messages) == 3
    path.write_bytes(b"{")
    assert recorder.poll_reporting(4, audit) is None
    assert messages[-1] == messages[1]
    assert len(messages) == 4
