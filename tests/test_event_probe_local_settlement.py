"""Actor-local proof gaps retain explicit producer records, not global false coverage."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT, StreamGap
from panel_model import EventAvailability, PanelConfig, render_panel, rows_from_statistics
from roster import CharacterEntry, RosterSnapshot
from test_event_probe_parity import BoundedObserver

ROOT = Path(__file__).resolve().parents[1]
CIDS = ("char_147_shining", "char_2012_typhon")
UIDS = (1010, 1011)


@pytest.fixture(scope="module")
def producer():
    result = subprocess.run(["node", str(ROOT / "tests/event_probe_local_settlement_fixture.js")],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


class Collector:
    def __init__(self, platform):
        self.platform = platform
        self.session, self.frame, self.game_time, self.damage = "owned-local-proof", 0, 0, 0
    def read(self):
        return dict(session=self.session, native_session=self.session, state=2,
                    frame=self.frame, game_time=self.game_time,
                    battle_metadata=dict(platform=self.platform, source_build="synthetic-build"),
                    rows=[dict(id=cid, damage=self.damage, healing=0, physical=self.damage,
                               arts=0, true=0, elemental=0) for cid in CIDS])
    def read_roster(self):
        return RosterSnapshot(self.frame, tuple(CharacterEntry(cid, uid, False, False, None, None, 0)
                                               for cid, uid in zip(CIDS, UIDS)),
                              frozenset(UIDS), True, 0)


def capture(report, platform):
    contract = NATIVE_PC_CONTRACT if platform == "native_pc" else MUMU_CONTRACT
    collector = Collector(platform)
    observer = BoundedObserver(collector, contract, report["batch"])
    live = LiveEventCapture(collector, observer)
    live.sample()
    collector.frame = report["mark"]["frame"]
    collector.game_time = report["mark"]["game_time"]
    collector.damage = 40
    observer.records = deepcopy(report["batch"]["events"])
    observer.sequence = report["mark"]["sequence"]
    return live.sample(), live, observer


@pytest.mark.parametrize("platform", ["mumu", "native_pc"])
@pytest.mark.parametrize("fault", ["bad_hp", "nested_same_target", "duplicate_target", "duplicate_output",
                                    "missing_unit", "positive_zero_without_absorption"])
def test_actual_two_owner_batch_keeps_the_other_owner_complete(producer, platform, fault):
    report = producer["native_pc" if platform == "native_pc" else "android"]["local"][fault]
    assert report["batch"]["enemy_damage_unknown"] is False
    rows, live, observer = capture(report, platform)
    a, b = (rows["rows"][cid]["settled_damage"] for cid in CIDS)
    assert a["complete"] is False and a["unverified_hits"] == 1
    assert a["known_hp_damage"] == 0 and a["observed_hits"] == 1
    assert b["complete"] is True and b["unverified_hits"] == 0
    assert b["known_hp_damage"] == b["effective_damage"] == 40
    assert b["mitigation_complete"] is True and b["mitigation_matched_hits"] == 1
    view = render_panel(PanelConfig(effective_only=True), rows_from_statistics(rows),
                        event=EventAvailability(status="recording"))
    entry = next(x for x in view.entries if x.owner_id == CIDS[1])
    assert entry.detail["effective_damage"] == 40
    # A lost ACK replays the unchanged prefix; it cannot add a second hit.
    observer.records = deepcopy(report["batch"]["events"])
    replay = live.sample()
    assert replay["rows"][CIDS[0]]["settled_damage"]["observed_hits"] == 1
    assert replay["rows"][CIDS[1]]["settled_damage"]["observed_hits"] == 1


@pytest.mark.parametrize("platform", ["mumu", "native_pc"])
@pytest.mark.parametrize("fault", ["reused_modifier", "source_uid", "unlocalizable_owner", "source_character_uid", "source_id", "source_pointer",
                                    "target_uid", "target_id", "target_pointer", "damage_type",
                                    "cross_frame", "cross_time", "unreadable_source"])
def test_real_identity_or_clock_failure_still_degrades_global_settlement_coverage(producer, platform, fault):
    report = producer["native_pc" if platform == "native_pc" else "android"]["global"][fault]
    assert report["batch"]["enemy_damage_unknown"] is True
    if fault == "cross_time":
        with pytest.raises(StreamGap, match="clock|watermark"):
            capture(report, platform)
        return
    rows, _, _ = capture(report, platform)
    b = rows["rows"][CIDS[1]]["settled_damage"]
    assert b["known_hp_damage"] == 40
    assert b["complete"] is False


@pytest.mark.parametrize("platform", ["android", "native_pc"])
def test_equal_amounts_are_distinct_scopes_and_overflow_stays_fatal(producer, platform):
    actual = producer[platform]
    assert len(actual["same_amount_twice"]["actor_a_hits"]) == 2
    assert len(actual["same_local_gap_twice"]["actor_a_hits"]) == 2
    assert actual["overflow"]["mark"]["error"].startswith("event buffer overflow")
    assert actual["overflow"]["batch"]["error"].startswith("event buffer overflow")


@pytest.mark.parametrize("platform", ["mumu", "native_pc"])
def test_transport_sequence_loss_remains_global_even_for_actor_local_quality(producer, platform):
    report = deepcopy(producer["native_pc" if platform == "native_pc" else "android"]["local"]["bad_hp"])
    assert report["batch"]["enemy_damage_unknown"] is False
    # Explicit transport fault: drop a real record, without changing any
    # producer declaration or filling a replacement sequence.
    report["batch"]["events"].pop(0)
    with pytest.raises(StreamGap, match="sequence"):
        capture(report, platform)
