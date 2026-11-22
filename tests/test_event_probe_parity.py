"""Backend contract parity through the production capture and statistics chain.

The canonical combat is synthetic. Producer-quality tests separately execute the
production JavaScript; no missing declaration is supplied by this harness.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from event_capture import LiveEventCapture
from event_observer import ObserverReadRetry
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT
from event_stream import StreamGap
from panel_model import EventAvailability, PanelConfig, render_panel, rows_from_statistics
from roster import CharacterEntry, RosterSnapshot


ROOT = Path(__file__).resolve().parents[1]
BACKENDS = (("mumu", MUMU_CONTRACT), ("native_pc", NATIVE_PC_CONTRACT))
FAMILIES = ("damage_unknown", "engagement_unknown", "shield_unknown", "skill_unknown")


def declared_quality():
    """Explicit evidence for the synthetic trace, not a producer-output repair."""
    return dict(quality_contract_version=1, partial_damage_version=7,
                source_attribution_version=1, damage_unknown=False,
                engagement_unknown=False, shield_unknown=False, skill_unknown=False,
                mitigation_ready=True, mitigation_unknown=False,
                damage_settlement_ready=True, damage_settlement_unknown=False,
                enemy_damage_ready=True, enemy_damage_unknown=False)


class CanonicalCollector:
    def __init__(self, platform):
        self.platform = platform
        self.frame = 0
        self.game_time = 0
        self.damage = self.healing = 0
        self.session = "canonical-battle"
        self.cid, self.uid = "char_a", 1

    def read(self):
        return dict(session=self.session, native_session=self.session, state=2,
                    frame=self.frame, game_time=self.game_time,
                    global_total=self.damage, global_heal=self.healing,
                    presence_duration_supported=True,
                    battle_metadata=dict(platform=self.platform, source_build="synthetic-build"),
                    rows=[dict(id=self.cid, damage=self.damage, healing=self.healing,
                               physical=self.damage, arts=0, true=0, elemental=0)])

    def read_roster(self):
        entry = CharacterEntry(cid=self.cid, unique_id=self.uid, is_token=False,
                               is_predefined=False, host_key=None, host_uid=None,
                               player_side=0)
        return RosterSnapshot(frame=self.frame, entries=(entry,), local_player_side=0,
                              present_known=True,
                              present_unique_ids=frozenset({self.uid} if self.frame else ()))


class BoundedObserver:
    """An owned journal with a lost acknowledgement, not always-success RPCs."""
    def __init__(self, collector, contract, quality=None):
        self.collector, self.contract = collector, contract
        self.quality = deepcopy(declared_quality() if quality is None else quality)
        self.records = []
        self.sequence = 0
        self.acknowledged = 0
        self.ack_calls = []
        self.fail_next_ack = False
        self.disconnected = False

    def binding(self):
        return dict(native_session=self.collector.session, source_build="synthetic-build")

    def mark(self):
        if self.disconnected:
            raise RuntimeError("synthetic transport disconnected")
        return dict(self.binding(), sequence=self.sequence, epoch=self.sequence, active=0,
                    frame=self.collector.frame, game_time=self.collector.game_time,
                    stopped=False, error=None)

    def read_through(self, sequence):
        return dict(self.quality, **self.binding(),
                    events=deepcopy([record for record in self.records
                                     if record["sequence"] <= sequence]))

    def commit_events(self, sequence):
        self.ack_calls.append(sequence)
        if self.fail_next_ack:
            self.fail_next_ack = False
            raise ObserverReadRetry("synthetic acknowledgement response lost")
        self.acknowledged = sequence
        self.records = [record for record in self.records if record["sequence"] > sequence]

    def append(self, events):
        for event in events:
            self.sequence += 1
            self.records.append(dict(event, sequence=self.sequence))


def event(kind, amount, time, **fields):
    return dict(source="char_a", source_character_uid=1, kind=kind, amount=amount,
                frame=time * 30, game_time=time, **fields)


def first_window(*, hp_proof=True):
    damage = event("damage", 20, 1, target="enemy_a", target_instance="enemy-1",
                   target_uid="11", target_type=0, damage_type=1, hp_before=8,
                   hp_after=0, real_delta=-8, killed=True, shield_absorbed=0,
                   mitigation_before=40, mitigation_after=20)
    if not hp_proof:
        damage.pop("hp_before")
    return [event("skill_start", 1, 1, skill_key="skill-1"), damage,
            event("damage_settlement", 8, 1, target="enemy_a", target_uid="11",
                  target_instance="enemy-1", damage_type=1, nominal_amount=20,
                  settlement_complete=hp_proof, shield_absorbed=0, killed=True,
                  mitigation_before=40, mitigation_after=20),
            event("healing", 80, 1, proof={"direct_healing": {
                "amount": 80, "modifier_actual_delta": 23.5,
                "hp_before": 50, "hp_after": 73.5}}),
            event("shield_absorbed", 3, 1)]


def second_window(*, end_before_damage=True):
    finish = event("skill_end", 1, 2, skill_key="skill-1",
                   proof={"method": "Character.OnSkillFinish"})
    damage = event("damage", 10, 2, target="enemy_b", target_instance="enemy-2",
                   target_uid="12", target_type=0, damage_type=1, hp_before=30,
                   hp_after=20, real_delta=-10, killed=False, shield_absorbed=0,
                   mitigation_before=10, mitigation_after=10)
    settlement = event("damage_settlement", 10, 2, target="enemy_b", target_uid="12",
                       target_instance="enemy-2", damage_type=1, nominal_amount=10,
                       settlement_complete=True, shield_absorbed=0, killed=False,
                       mitigation_before=10, mitigation_after=10)
    return [finish, damage, settlement] if end_before_damage else [damage, settlement, finish]


def connect(platform, contract, quality=None):
    collector = CanonicalCollector(platform)
    observer = BoundedObserver(collector, contract, quality)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    return collector, observer, capture


def advance(collector, observer, capture, time, records, damage, healing):
    collector.frame, collector.game_time = time * 30, time
    collector.damage, collector.healing = damage, healing
    observer.append(records)
    return capture.sample()["rows"]["char_a"]


def run_combat(platform, contract, *, quality=None, end_before_damage=True, hp_proof=True):
    collector, observer, capture = connect(platform, contract, quality)
    advance(collector, observer, capture, 1, first_window(hp_proof=hp_proof), 20, 80)
    row = advance(collector, observer, capture, 2,
                  second_window(end_before_damage=end_before_damage), 30, 80)
    return collector, observer, capture, row


@pytest.mark.parametrize("end_before_damage", [True, False])
def test_same_nominal_combat_has_identical_advanced_statistics(end_before_damage):
    rows = [run_combat(platform, contract, end_before_damage=end_before_damage)[3]
            for platform, contract in BACKENDS]
    fields = ("damage", "healing", "observed_damage", "observed_healing",
              "engagement_damage", "engagement_healing", "active_damage", "active_healing",
              "recent_damage", "recent_healing", "counter_snapshot_complete",
              "shield_absorbed", "overkill_damage", "effective_damage",
              "skill_damage", "non_skill_damage", "skill_damage_effective",
              "non_skill_damage_effective", "skill_active_seconds", "skill_window_count",
              "skill_coverage_complete", "skill_split_complete", "skill_split_effective_complete",
              "mitigation_rate", "mitigation_before", "mitigation_after",
              "event_coverage_complete", "settled_damage")
    assert {key: rows[0][key] for key in fields} == {key: rows[1][key] for key in fields}
    row = rows[0]
    assert row["damage"] == row["observed_damage"] == 30
    assert row["healing"] == row["observed_healing"] == 80  # actual HP gain was 23.5
    assert row["shield_absorbed"] == 3
    assert row["overkill_damage"] == 12 and row["effective_damage"] == 18
    assert row["skill_damage"] == (20 if end_before_damage else 30)
    assert row["non_skill_damage"] == (10 if end_before_damage else 0)
    assert row["skill_split_complete"] and row["skill_split_effective_complete"]
    assert row["skill_active_seconds"] == 1 and row["skill_window_count"] == 1
    assert row["mitigation_rate"] == pytest.approx(40)
    assert row["settled_damage"]["mitigation_complete"] is True
    assert row["settled_damage"]["observed_mitigation_rate"] == pytest.approx(40)


@pytest.mark.parametrize("platform,contract", BACKENDS)
def test_lost_ack_replays_once_and_proof_remains_attached(platform, contract):
    collector, observer, capture = connect(platform, contract)
    observer.fail_next_ack = True
    first = advance(collector, observer, capture, 1, first_window(), 20, 80)
    assert observer.acknowledged == 0 and observer.records
    collector.frame, collector.game_time = 60, 2
    replayed = capture.sample()["rows"]["char_a"]
    for key in ("observed_damage", "observed_healing", "shield_absorbed", "mitigation_matched_hits"):
        assert replayed[key] == first[key]
    assert observer.acknowledged == 5 and observer.ack_calls[-2:] == [5, 5]
    if contract.raw_replay:
        assert capture.adapter.event_stream.received[4]["proof"]["direct_healing"]["modifier_actual_delta"] == 23.5


@pytest.mark.parametrize("platform,contract", BACKENDS)
@pytest.mark.parametrize("flag", FAMILIES + ("mitigation_unknown",))
def test_versioned_missing_quality_never_certifies_zero_or_hides_other_families(platform, contract, flag):
    quality = declared_quality()
    quality.pop(flag)
    _, _, _, row = run_combat(platform, contract, quality=quality)
    assert row["damage"] == 30 and row["healing"] == 80
    assert row["event_coverage_complete"] is True
    # v7 settlement evidence has an independent certificate. A missing legacy
    # damage-family declaration must not erase separately proven settlements.
    assert row["overkill_damage"] == 12
    assert row["skill_split_effective_complete"] is (
        flag not in ("damage_unknown", "engagement_unknown", "skill_unknown"))
    assert row["shield_absorbed"] == (None if flag == "shield_unknown" else 3)
    assert row["skill_damage"] == (None if flag == "skill_unknown" else 20)
    assert row["observed_healing"] == (None if flag == "engagement_unknown" else 80)
    assert row["mitigation_rate"] == (None if flag == "mitigation_unknown" else pytest.approx(40))


@pytest.fixture(scope="module")
def production_pipeline():
    result = subprocess.run(["node", str(ROOT / "tests" / "event_probe_pipeline_fixture.js")],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("platform,contract", BACKENDS)
@pytest.mark.parametrize("scenario", ["target_return_before_apply_commit", "nonlethal_cap", "lethal_hit",
                                      "full_block_unit_before_shield", "full_block_unit_after_shield",
                                      "full_block_bad_uid", "zero_nominal_with_hp_damage",
                                      "bad_hp_difference", "changed_target_uid", "clock_changed",
                                      "duplicate_target_callback", "nested_same_target",
                                      "late_amplification_130_after_unit",
                                      "late_amplification_unrelated_after_unit", "late_attenuation_after_unit",
                                      "late_amplification_before_unit", "late_amplification_during_unit",
                                      "late_amplification_old_output", "final_nominal_below_hp",
                                      "late_amplification_bad_uid", "late_amplification_bad_hp"])
def test_real_final_apply_settlement_reaches_existing_v7_owner_detail(
        production_pipeline, platform, contract, scenario):
    backend = "native_pc" if platform == "native_pc" else "android"
    report = production_pipeline[backend][scenario]
    batch, mark = report["batch"], report["mark"]
    inputs = report["inputs"]  # owned fixture inputs, never production quality or proof replacements
    source_record = next(record for record in batch["events"] if record.get("source"))
    assert batch["enemy_damage_ready"] is True and batch["mitigation_ready"] is True
    collector = CanonicalCollector(platform)
    collector.cid, collector.uid = source_record["source"], source_record["source_character_uid"]
    observer = BoundedObserver(collector, contract, report["initial_batch"])
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time = mark["frame"], mark["game_time"]
    collector.damage = inputs["counter_damage"]  # owned cumulative counter, not an event delta
    observer.quality = deepcopy(batch)
    observer.records, observer.sequence = deepcopy(batch["events"]), mark["sequence"]
    if batch.get("error") or batch.get("stopped"):
        with pytest.raises(StreamGap):
            capture.sample()
        result = capture.snapshot()
    else:
        result = capture.sample()
    view = render_panel(PanelConfig(panel_id="source-pipeline", effective_only=True),
                        rows_from_statistics(result), event=EventAvailability("recording"))
    detail = view.details_by_owner[collector.cid]
    lane = detail["settled_damage"]
    assert detail["damage"] == pytest.approx(inputs["counter_damage"])
    if inputs["certified"]:
        actual = inputs["hp_damage"] + inputs["shield_damage"]
        nominal = inputs["counter_damage"]
        assert batch["enemy_damage_unknown"] is False
        assert lane["complete"] is True
        assert lane["known_hp_damage"] == pytest.approx(inputs["hp_damage"])
        assert lane["known_shield_damage"] == pytest.approx(inputs["shield_damage"])
        assert detail["effective_damage"] == pytest.approx(actual)
        assert detail["overkill_damage"] == (10 if scenario == "lethal_hit" else 0)
        assert lane["mitigation_complete"] is True
        assert lane["observed_mitigation_before"] == pytest.approx(inputs["formula_before"])
        assert lane["observed_mitigation_after"] == pytest.approx(nominal)
        assert lane["observed_mitigation_rate"] == pytest.approx(
            (1 - nominal / inputs["formula_before"]) * 100)
        assert detail["skill_damage"] == pytest.approx(nominal)
        assert detail["skill_damage_effective"] == pytest.approx(actual)
        assert detail["non_skill_damage"] == detail["non_skill_damage_effective"] == 0
        assert detail["skill_split_complete"] and detail["skill_split_effective_complete"]
        damage, = [record for record in batch["events"] if record["kind"] == "damage"]
        assert damage["amount"] == pytest.approx(inputs["final_nominal"])
        if inputs["entry_nominal"] != inputs["final_nominal"] and not inputs["shield_damage"]:
            assert batch["mitigation_diagnostics"]["emitted_after_adjustment"] == 1
    else:
        assert lane["complete"] is False
        assert detail["effective_damage"] is None
        assert detail["skill_damage_effective"] is None
        assert lane["mitigation_complete"] is False
        assert lane["observed_mitigation_rate"] is None


@pytest.mark.parametrize("platform,contract", BACKENDS)
@pytest.mark.parametrize("scenario", ["target_return_before_apply_commit", "late_amplification_130_after_unit"])
def test_real_active_skill_has_finite_panel_share_before_end_and_keeps_prefix_at_finish(
        production_pipeline, platform, contract, scenario):
    backend = "native_pc" if platform == "native_pc" else "android"
    report = production_pipeline[backend][scenario]
    assert not any(record["kind"] == "skill_end" for record in report["active_batch"]["events"])
    assert any(record["kind"] == "skill_end" for record in report["batch"]["events"])
    source = next(record for record in report["active_batch"]["events"] if record.get("source"))
    collector = CanonicalCollector(platform)
    collector.cid, collector.uid = source["source"], source["source_character_uid"]
    observer = BoundedObserver(collector, contract, report["initial_batch"])
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    details = []
    for batch, mark in ((report["active_batch"], report["active_mark"]),
                        (report["batch"], report["mark"])):
        collector.frame, collector.game_time = mark["frame"], mark["game_time"]
        collector.damage = report["inputs"]["counter_damage"]
        observer.quality = deepcopy(batch)
        observer.records, observer.sequence = deepcopy(batch["events"]), mark["sequence"]
        result = capture.sample()
        view = render_panel(PanelConfig(panel_id="active-skill", effective_only=True),
                            rows_from_statistics(result), event=EventAvailability("recording"))
        detail = view.details_by_owner[collector.cid]
        details.append(detail)
        assert detail["skill_split_complete"] and detail["skill_split_effective_complete"]
        assert detail["skill_damage"] == pytest.approx(report["inputs"]["counter_damage"])
        assert detail["skill_damage_effective"] == pytest.approx(report["inputs"]["hp_damage"])
        assert detail["non_skill_damage"] == detail["non_skill_damage_effective"] == 0
        assert detail["skill_damage"] / (detail["skill_damage"] + detail["non_skill_damage"]) == 1
        assert detail["skill_damage_effective"] / (detail["skill_damage_effective"]
                                                   + detail["non_skill_damage_effective"]) == 1
        assert detail["settled_damage"]["observed_hits"] == 1  # full-prefix replay is not another hit
    for key in ("damage", "effective_damage", "skill_damage", "non_skill_damage",
                "skill_damage_effective", "non_skill_damage_effective"):
        assert details[0][key] == details[1][key]


@pytest.mark.parametrize("platform,contract", BACKENDS)
def test_partial_hit_proof_keeps_components_but_not_full_certificate(platform, contract):
    _, _, _, row = run_combat(platform, contract, hp_proof=False)
    assert row["damage"] == 30 and row["observed_damage"] == 30
    assert row["healing"] == 80 and row["shield_absorbed"] == 3
    assert row["overkill_damage"] is None
    assert row["effective_damage"] == 18  # known components, never a complete total
    assert row["skill_split_complete"] is True
    assert row["skill_split_effective_complete"] is False
    assert row["settled_damage"]["complete"] is False
    assert row["settled_damage"]["unverified_hits"] == 1


@pytest.mark.parametrize("platform,contract", BACKENDS)
@pytest.mark.parametrize("fault", ["disconnect", "sequence_gap", "conflicting_replay"])
def test_transport_or_order_failure_keeps_snapshot_and_prefix_without_filling_unknown(platform, contract, fault):
    collector, observer, capture = connect(platform, contract)
    advance(collector, observer, capture, 1, first_window(), 20, 80)
    collector.frame, collector.game_time, collector.damage = 60, 2, 30
    if fault == "disconnect":
        observer.disconnected = True
    elif fault == "sequence_gap":
        observer.sequence += 1  # an event was never delivered
        observer.append(second_window())
    else:
        observer.records = [dict(first_window()[1], sequence=2, amount=21)]
    with pytest.raises(StreamGap):
        capture.sample()
    row = capture.snapshot()["rows"]["char_a"]
    assert capture.closed and row["damage"] == 30 and row["healing"] == 80
    assert row["event_coverage_complete"] is False
    assert row["shield_absorbed"] == 3
    assert row["overkill_damage"] is None
    assert row["effective_damage"] == 8  # preserved first-hit proof only
    assert row["settled_damage"]["complete"] is False
    assert row["settled_damage"]["engagement_damage"] is None
    assert row["settled_damage"]["unverified_share"] is None
    assert row["skill_split_complete"] is False


@pytest.mark.parametrize("platform,contract", BACKENDS)
@pytest.mark.parametrize("fault", ["missing_before", "missing_pair", "wrong_after"])
def test_ready_quality_cannot_certify_missing_same_hit_formula_values(platform, contract, fault):
    collector, observer, capture = connect(platform, contract)
    records = first_window()
    for record in records:
        if record["kind"] not in ("damage", "damage_settlement"):
            continue
        if fault in ("missing_before", "missing_pair"):
            record.pop("mitigation_before")
        if fault == "missing_pair":
            record.pop("mitigation_after")
        elif fault == "wrong_after":
            record["mitigation_after"] = 19
    advance(collector, observer, capture, 1, records, 20, 80)
    row = advance(collector, observer, capture, 2, second_window(), 30, 80)
    assert observer.quality["mitigation_ready"] is True
    assert observer.quality["mitigation_unknown"] is False
    assert row["mitigation_rate"] is None
    assert row["mitigation_matched_hits"] == 1
    assert row["observed_mitigation_rate"] == 0  # only the independently matched second hit
    lane = row["settled_damage"]
    assert lane["mitigation_complete"] is False and lane["mitigation_matched_hits"] == 1
    assert lane["mitigation_unverified_share"] == .5
    assert row["damage"] == 30 and row["healing"] == 80 and row["shield_absorbed"] == 3


@pytest.mark.parametrize("platform,contract", BACKENDS)
def test_interleaved_operators_keep_independent_skill_windows(platform, contract):
    class DualCollector(CanonicalCollector):
        damage_b = 0

        def read(self):
            sample = super().read()
            sample["rows"].append(dict(id="char_b", damage=self.damage_b, healing=0,
                                       physical=self.damage_b, arts=0, true=0, elemental=0))
            sample["global_total"] += self.damage_b
            return sample

        def read_roster(self):
            first = super().read_roster().entries[0]
            second = CharacterEntry(cid="char_b", unique_id=2, is_token=False,
                                    is_predefined=False, host_key=None, host_uid=None,
                                    player_side=0)
            return RosterSnapshot(frame=self.frame, entries=(first, second), local_player_side=0,
                                  present_known=True,
                                  present_unique_ids=frozenset({1, 2} if self.frame else ()))

    def skill(kind, owner, uid, time):
        return dict(event(kind, 1, time, skill_key="skill-" + owner,
                          proof={"method": "Character.OnSkillFinish" if kind == "skill_end"
                                 else "Character.OnSkillStart"}),
                    source=owner, source_character_uid=uid)

    def hit(owner, uid, time, amount):
        identity = dict(target="enemy_" + owner, target_uid=str(uid + 10),
                        target_instance="enemy-" + owner, damage_type=1,
                        shield_absorbed=0, killed=False,
                        mitigation_before=2 * amount, mitigation_after=amount)
        return [dict(event("damage", amount, time, **identity, target_type=0,
                           hp_before=100, hp_after=100 - amount, real_delta=-amount),
                     source=owner, source_character_uid=uid),
                dict(event("damage_settlement", amount, time, **identity,
                           nominal_amount=amount, settlement_complete=True),
                     source=owner, source_character_uid=uid)]

    collector = DualCollector(platform)
    observer = BoundedObserver(collector, contract)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    for time, damage_a, damage_b, records in (
        (1, 20, 10, [skill("skill_start", "char_a", 1, 1), *hit("char_a", 1, 1, 20),
                     skill("skill_start", "char_b", 2, 1), *hit("char_b", 2, 1, 10)]),
        (2, 25, 15, [skill("skill_end", "char_a", 1, 2), *hit("char_a", 1, 2, 5),
                     *hit("char_b", 2, 2, 5)]),
        (3, 25, 20, [skill("skill_end", "char_b", 2, 3), *hit("char_b", 2, 3, 5)]),
    ):
        collector.damage_b = damage_b
        advance(collector, observer, capture, time, records, damage_a, 0)
    rows = capture.adapter.result()["rows"]
    for owner, inside, outside, duration in (("char_a", 20, 5, 1), ("char_b", 15, 5, 2)):
        row = rows[owner]
        assert row["skill_damage"] == inside and row["non_skill_damage"] == outside
        assert row["skill_active_seconds"] == duration
        assert row["skill_window_count"] == 1 and row["skill_split_complete"] is True
        assert row["settled_damage"]["skill_split_effective_complete"] is True


@pytest.fixture(scope="module")
def production_quality():
    """Execute production JS in owned fixtures; never repair its declarations."""
    result = subprocess.run(["node", str(ROOT / "tests" / "event_probe_quality_fixture.js")],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_real_producer_quality_drives_both_canonical_combat_pipelines(production_quality):
    rows = []
    for platform, contract in BACKENDS:
        report = production_quality["native_pc" if platform == "native_pc" else "android"]
        quality = report["batch"]  # complete actual readEvents output, no missing-key defaults
        assert quality["quality_contract_version"] == 1
        for flag in FAMILIES + ("mitigation_unknown",):
            assert type(quality[flag]) is bool
            assert quality[flag] is False
        assert quality["mitigation_ready"] is True
        _, _, _, row = run_combat(platform, contract, quality=quality)
        rows.append(row)
        assert row["damage"] == 30 and row["observed_damage"] == 30
        assert row["healing"] == 80 and row["observed_healing"] == 80
        assert row["shield_absorbed"] == 3 and row["skill_damage"] == 20
        assert row["mitigation_rate"] == pytest.approx(40)
        assert row["mitigation_matched_hits"] == 2
    for key in ("damage", "healing", "observed_damage", "observed_healing", "shield_absorbed",
                "skill_damage", "non_skill_damage", "skill_split_complete", "mitigation_rate"):
        assert rows[0][key] == rows[1][key]


@pytest.mark.parametrize("platform,contract", BACKENDS)
@pytest.mark.parametrize("scenario", ["unavailable", "install_failure"])
def test_real_unavailable_or_rolled_back_installer_keeps_formula_unknown(production_quality, platform, contract, scenario):
    backend = "native_pc" if platform == "native_pc" else "android"
    quality = production_quality[backend]["scenarios"][scenario]["batch"]
    assert quality["mitigation_ready"] is False
    assert quality["mitigation_unknown"] is True
    collector, observer, capture = connect(platform, contract, quality)
    records = first_window()
    for record in records:
        record.pop("mitigation_before", None)
        record.pop("mitigation_after", None)
    row = advance(collector, observer, capture, 1, records, 20, 80)
    assert row["damage"] == 20 and row["healing"] == 80 and row["shield_absorbed"] == 3
    assert row["mitigation_rate"] is None and row["observed_mitigation_rate"] is None
    assert row["mitigation_matched_hits"] == 0
    assert row["settled_damage"]["mitigation_complete"] is False


@pytest.mark.parametrize("platform,contract", BACKENDS)
@pytest.mark.parametrize("scenario,diagnostic,paired", [
    ("per_hit_missing", "apply_missing_candidate", False),
    ("per_hit_mismatch", "apply_value_mismatch", False),
    ("paired_hit", "emitted", True),
])
def test_real_formula_callbacks_preserve_same_hit_pair_or_explicit_missing_evidence(
        production_quality, platform, contract, scenario, diagnostic, paired):
    backend = "native_pc" if platform == "native_pc" else "android"
    report = production_quality[backend]["scenarios"][scenario]
    batch, mark = report["batch"], report["mark"]
    assert batch["mitigation_ready"] is True and batch["mitigation_unknown"] is False
    assert batch["mitigation_diagnostics"][diagnostic] == 1
    damage, = [record for record in batch["events"] if record["kind"] == "damage"]
    if paired:
        assert damage["mitigation_before"] == 80
        assert damage["mitigation_after"] == damage["amount"] == 40
    else:
        assert "mitigation_before" not in damage and "mitigation_after" not in damage
    if scenario == "per_hit_mismatch":
        sample, = batch["mitigation_diagnostics"]["samples"]
        assert sample["formula_after"] == 39 and sample["modifier_amount"] == 40

    # The real production event records are delivered without replacing their
    # source, UID, amounts, proof, sequence or clock. Only the owned collector's
    # roster/counter snapshot is configured to match those fixture identities.
    collector = CanonicalCollector(platform)
    collector.cid, collector.uid = damage["source"], damage["source_character_uid"]
    observer = BoundedObserver(collector, contract, batch)
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time = mark["frame"], mark["game_time"]
    collector.damage = damage["amount"]
    observer.records = deepcopy(batch["events"])
    observer.sequence = mark["sequence"]
    row = capture.sample()["rows"][collector.cid]
    assert row["damage"] == row["observed_damage"] == 40
    if paired:
        assert row["mitigation_before"] == row["observed_mitigation_before"] == 80
        assert row["mitigation_after"] == row["observed_mitigation_after"] == 40
        assert row["mitigation_rate"] == row["observed_mitigation_rate"] == 50
        assert row["mitigation_matched_hits"] == 1 and row["mitigation_unknown_reason"] is None
    else:
        assert row["mitigation_rate"] is None and row["observed_mitigation_rate"] is None
        assert row["mitigation_matched_hits"] == 0 and row["mitigation_unknown_reason"]
    if contract.raw_replay:
        assert capture.adapter.event_stream.received[damage["sequence"]] == damage
    assert row["event_coverage_complete"] is True
