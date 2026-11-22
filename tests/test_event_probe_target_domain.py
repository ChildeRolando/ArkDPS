"""2207 self-damage is unrelated to enemy-HP proof; unknown identity stays global."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT
from panel_model import EventAvailability, PanelConfig, render_panel, rows_from_statistics
from roster import CharacterEntry, RosterSnapshot
from test_event_probe_parity import BoundedObserver, CanonicalCollector

ROOT = Path(__file__).resolve().parents[1]
A, B = "char_1041_angel2", "char_2012_typhon"
UID_A, UID_B = 2147483989, 1011
NON_ENEMY = ("self_uid0", "self_uid10", "ally_uid0", "ally_uid12", "token_uid0", "token_uid12",
             "self_output_uid0", "ally_output", "self_output_only", "ally_healing",
             "token_unit_uid0", "token_bobject_uid0")
GLOBAL = ("unknown_id", "empty_id", "unreadable_target", "unreadable_class", "char_id_enemy_class",
          "enemy_uid0", "ally_uid0_to12", "enemy_uid_changed", "enemy_to_ally", "ally_to_enemy",
          "ally_enemy_ally", "enemy_ally_enemy", "token_enemy_class", "self_end_clock_unknown")


@pytest.fixture(scope="module")
def matrix():
    frozen = ROOT / "tests/fixtures/event_probe_business/2207-friendly-scope-first-issue.jsonl"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == "e8edb1495a3c67cd88979562e0a2e33c9c989355f5fb5ed5b01aa654fdcbe71e"
    original = json.loads(frozen.read_text(encoding="utf-8"))["data"]["events"]["drain"]
    assert original["enemy_damage_unknown"] is True  # The real failed producer evidence is not repaired.
    result = subprocess.run(["node", str(ROOT / "tests/event_probe_target_domain_fixture.js")],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


class Collector:
    def __init__(self, platform):
        self.platform, self.session = platform, "owned-domain-proof"
        self.frame, self.game_time = 0, 0
        self.a_damage = self.a_healing = self.b_damage = 0
    def read(self):
        return dict(session=self.session, native_session=self.session, state=2,
                    frame=self.frame, game_time=self.game_time,
                    battle_metadata=dict(platform=self.platform, source_build="synthetic-build"),
                    rows=[dict(id=cid, damage=damage, healing=healing, physical=damage if cid == B else 0,
                               arts=0, true=damage if cid == A else 0, elemental=0)
                          for cid, damage, healing in ((A, self.a_damage, self.a_healing), (B, self.b_damage, 0))])
    def read_roster(self):
        return RosterSnapshot(self.frame, tuple(CharacterEntry(cid, uid, False, False, None, None, 0)
                                               for cid, uid in ((A, UID_A), (B, UID_B))),
                              frozenset((UID_A, UID_B)), True, 0)


def consume(report, platform):
    collector = Collector(platform)
    observer = BoundedObserver(collector, NATIVE_PC_CONTRACT if platform == "native_pc" else MUMU_CONTRACT,
                              report["batch"])
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time = report["mark"]["frame"], report["mark"]["game_time"]
    collector.a_damage = report["inputs"]["actor_a_counter"]
    collector.a_healing = report["inputs"]["actor_a_healing"]
    collector.b_damage = 40
    observer.records, observer.sequence = deepcopy(report["batch"]["events"]), report["mark"]["sequence"]
    return capture.sample(), capture, observer


@pytest.mark.parametrize("platform", ["mumu", "native_pc"])
@pytest.mark.parametrize("case", NON_ENEMY)
def test_known_non_enemy_hits_preserve_raw_event_and_do_not_degrade_same_frame_enemy(matrix, platform, case):
    report = matrix["native_pc" if platform == "native_pc" else "android"][case]
    assert report["batch"]["enemy_damage_unknown"] is False
    assert report["after_unrelated"]["enemy_damage_unknown"] is False
    assert not any(e["kind"] == "damage_settlement" and e.get("source") == A for e in report["batch"]["events"])
    raw = report["inputs"]["original_damage"]
    if case != "self_output_only":
        event, = [e for e in report["batch"]["events"] if e.get("source") == A and e["kind"] in ("damage", "healing")]
        assert event["amount"] == raw["amount"]
        if case != "ally_healing":
            assert event["damage_type"] == 3
            assert event["real_delta"] == raw["real_delta"]
            assert event["hp_before"] == raw["hp_before"] and event["hp_after"] == raw["hp_after"]
    result, capture, observer = consume(report, platform)
    assert result["rows"][A]["damage"] == 0
    lane = result["rows"][B]["settled_damage"]
    assert lane["complete"] and lane["unverified_hits"] == 0
    assert lane["known_hp_damage"] == 40 and lane["mitigation_complete"]
    view = render_panel(PanelConfig(effective_only=True), rows_from_statistics(result),
                        event=EventAvailability(status="recording"))
    assert next(x for x in view.entries if x.owner_id == B).detail["effective_damage"] == 40
    observer.records = deepcopy(report["batch"]["events"])
    assert capture.sample()["rows"][B]["settled_damage"]["observed_hits"] == 1


@pytest.mark.parametrize("platform", ["mumu", "native_pc"])
@pytest.mark.parametrize("case", ("enemy", "neutral_trap"))
def test_enemy_and_potential_trap_stay_in_certificate_domain(matrix, platform, case):
    report = matrix["native_pc" if platform == "native_pc" else "android"][case]
    hit, = [e for e in report["batch"]["events"] if e["kind"] == "damage_settlement" and e.get("source") == A]
    assert hit["settlement_complete"] is True and hit["target_uid"] == "12"
    assert hit["amount"] == report["inputs"]["original_damage"]["amount"]
    assert report["batch"]["enemy_damage_unknown"] is False
    result, _, _ = consume(report, platform)
    assert result["rows"][A]["settled_damage"]["known_hp_damage"] == hit["amount"]
    # This preserves the trap UID; stage HP-pool admission remains the existing host decision.


@pytest.mark.parametrize("platform", ["mumu", "native_pc"])
@pytest.mark.parametrize("case", GLOBAL)
def test_unknown_identity_or_domain_crossing_is_not_declared_unrelated(matrix, platform, case):
    report = matrix["native_pc" if platform == "native_pc" else "android"][case]
    assert report["batch"]["enemy_damage_unknown"] is True
    assert report["batch"]["damage_settlement_first_issue"]
    result, _, _ = consume(report, platform)
    lane = result["rows"][B]["settled_damage"]
    assert lane["known_hp_damage"] == 40 and not lane["complete"]


@pytest.fixture(scope="module")
def disabled_family_quality():
    result = subprocess.run(["node", str(ROOT / "tests/event_probe_quality_fixture.js")],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("platform", ["mumu", "native_pc"])
@pytest.mark.parametrize("scenario", ("paired_hit", "per_hit_missing", "per_hit_mismatch"))
def test_disabled_enemy_certificate_family_does_not_degrade_legacy_damage_or_formula(
        disabled_family_quality, platform, scenario):
    backend = "native_pc" if platform == "native_pc" else "android"
    report = disabled_family_quality[backend]["scenarios"][scenario]
    batch, mark = report["batch"], report["mark"]
    for message in (mark, batch):
        assert message["enemy_damage_ready"] is False
        assert message["enemy_damage_unknown"] is False
        assert message["damage_settlement_first_issue"] is None
        assert message["mitigation_ready"] is True and message["mitigation_unknown"] is False
    damage, = [event for event in batch["events"] if event["kind"] == "damage"]
    assert damage["amount"] == 40
    assert not any(event["kind"] == "damage_settlement" for event in batch["events"])
    collector = CanonicalCollector(platform)
    collector.cid, collector.uid = damage["source"], damage["source_character_uid"]
    contract = NATIVE_PC_CONTRACT if platform == "native_pc" else MUMU_CONTRACT
    observer = BoundedObserver(collector, contract, batch)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time, collector.damage = mark["frame"], mark["game_time"], 40
    observer.records, observer.sequence = deepcopy(batch["events"]), mark["sequence"]
    result = capture.sample()
    row = result["rows"][collector.cid]
    assert row["damage"] == row["observed_damage"] == 40
    if scenario == "paired_hit":
        assert damage["mitigation_before"] == 80 and damage["mitigation_after"] == 40
        assert row["mitigation_rate"] == 50 and row["mitigation_matched_hits"] == 1
    else:
        assert "mitigation_before" not in damage and "mitigation_after" not in damage
        assert row["mitigation_rate"] is None and row["mitigation_matched_hits"] == 0
    view = render_panel(PanelConfig(), rows_from_statistics(result),
                        event=EventAvailability(status="recording"))
    entry = next(entry for entry in view.entries if entry.owner_id == collector.cid)
    assert entry.value == entry.detail["damage"] == 40
    assert row["settled_damage"]["complete"] is False  # Unsupported family is not a complete zero.
