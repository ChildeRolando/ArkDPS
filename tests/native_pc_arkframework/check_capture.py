"""Exercise the compiled plugin inside the SDK test process using controlled inputs."""
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def run(host, plugin, root, scenario):
    root.mkdir()
    with subprocess.Popen([host, plugin, str(root), scenario]) as process:
        try:
            code = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            raise
        if code:
            raise RuntimeError(f"SDK test process failed in {scenario}")
    (path,) = (root / "exports" / "ArkDPS").glob("events-*.jsonl")
    with path.open(encoding="utf-8") as journal:
        records = [json.loads(line) for line in journal]
    header = records[0]
    assert header["kind"] == "header" and header["service"] == "ark.battle.events.v2"
    assert header["test_host"] is True and header["pid"] == process.pid
    assert header["process_created_filetime"] > 0 and header["schema_version"] == 1
    return records


def main():
    host, plugin = (Path(arg).resolve(strict=True) for arg in sys.argv[1:])
    with TemporaryDirectory(prefix="arkdps-sdk-test-") as temp:
        root = Path(temp)
        rows = run(host, plugin, root / "中文-完整", "valid")
        batches = [row for row in rows if row["kind"] == "batch"]
        events = [event for batch in batches for event in batch["events"]]
        assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5, 6, 9]
        start, hit, heal, no_hp_change, elemental, end, next_battle = events
        assert [event["kind"] for event in events] == [1, 2, 2, 2, 3, 4, 5]
        assert start["battle"] == end["battle"] == 21 and next_battle["battle"] == 22
        assert hit["frame"] == heal["frame"] == 30 and hit["game_time"] == heal["game_time"] == 1
        assert hit["frame_event_index"] == 0 and heal["frame_event_index"] == 1
        assert hit["stage_id"] == "stage-test" and hit["actor_id"] == 'token_"A"'
        assert hit["owner_id"] == "char_host" and hit["original_source_id"] == "char_original"
        assert (hit["target_id"], hit["damage_type"], hit["calculated_damage"], hit["actual_damage"]) == (
            "enemy_target", 1, 125.5, 99.25)
        assert (heal["actor_id"], heal["target_id"], heal["damage_type"],
                heal["calculated_damage"], heal["actual_damage"]) == ("char_medic", "char_host", 4, 80, 23.5)
        assert heal["direct_healing"] == {"amount": 80, "actual_hp_restored": 23.5}
        assert no_hp_change["direct_healing"] == {"amount": 80, "actual_hp_restored": 0}
        assert all("direct_healing" not in event for event in (start, hit, elemental, end, next_battle))
        assert elemental["frame"] == end["frame"] == 60
        assert any(batch["ended"] and end in batch["events"] for batch in batches)
        gap, = [batch for batch in batches if batch["gap"]]
        assert gap["after"] == 6 and gap["oldest_sequence"] == gap["newest_sequence"] == 9
        assert gap["overrun"] and gap["partial"] and next_battle["partial"]
        assert rows[-1]["kind"] == "stop" and rows[-1]["sequence"] == 9

        invalid = run(host, plugin, root / "重复序号", "invalid")
        assert invalid[-1]["kind"] == "error"
        assert [item["sequence"] for row in invalid if row["kind"] == "batch"
                for item in row["events"]] == [1]

        backlog = run(host, plugin, root / "多页", "backlog")
        pages = [row for row in backlog if row["kind"] == "batch"]
        assert len(pages) >= 2 and all(not page["gap"] for page in pages)
        assert [item["sequence"] for page in pages for item in page["events"]] == list(range(1, 601))
        assert backlog[-1]["kind"] == "stop" and backlog[-1]["sequence"] == 600


if __name__ == "__main__":
    main()
