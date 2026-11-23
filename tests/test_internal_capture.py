from copy import deepcopy
import json
from pathlib import Path

import pytest

from battle_history import BattleHistory
from capture_battles import replay
from event_capture import CaptureRetry
from event_probe import NATIVE_PC_CONTRACT
from native_pc.internal_capture import CID, SOURCE, _builtin_fixture, run_internal_capture


ROOT = Path(__file__).resolve().parents[1]


class MemoryProbe:
    """Deterministic reference feed with real capture, stats, and archive consumers."""

    contract = NATIVE_PC_CONTRACT
    error = None
    probe_modes = {}
    cleanup_errors = {}

    def __init__(self, _host):
        self.status = 'stopped'
        self.records = []
        self.current = self.terminal = None

    def start(self):
        self.status = 'ready'

    def stop(self):
        self.status = 'stopped'

    def advance(self, mark, events, terminal_mark=None):
        self.current, self.terminal = deepcopy(mark), deepcopy(terminal_mark)
        self.records.extend(deepcopy(events))
        return True

    def mark(self):
        if self.status != 'ready':
            raise RuntimeError('synthetic host is stopped')
        return deepcopy(self.current)

    def read_through(self, sequence):
        return dict(self.mark(), events=deepcopy([event for event in self.records if event['sequence'] <= sequence]))

    def commit_events(self, sequence):
        self.records = [event for event in self.records if event['sequence'] > sequence]
        return True

    def read_terminal_mark(self):
        return {'terminal_boundary': deepcopy(self.terminal)} if self.terminal else {}


@pytest.fixture
def native_host():
    path = ROOT / 'runtime/native-pc/internal-event-host/ArkDPS.NativeEventHost.exe'
    if not path.is_file():
        pytest.skip('optional controlled C++ test host has not been built')
    return path


def _fixture_file(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def _known_player_amounts(sample):
    return [(player['slot'], player['observed_damage']) for player in sample['multiplayer']['players']]


def _assert_history(output, directory):
    assert output['status'] == 'ok'
    assert output['source'] == SOURCE and output['game_connected'] is False
    assert output['samples'] == 3 and output['archives'] == 1
    assert output['last_sample']['event_status'] == 'finished'
    record, = BattleHistory(directory / 'history').list_recent().records
    assert output['battle_ids'] == [record.battle_id] == [output['last_sample']['battle_id']]
    assert record.metadata['evidence'] == SOURCE
    with Path(output['journal']).open(encoding='utf-8') as journal:
        source = json.loads(journal.readline())
    assert source['kind'] == 'controlled_source' and source['data']['fixture_sha256'] == output['fixture_sha256']
    assert replay(output['journal'])['archives'] == 1
    return record


@pytest.mark.parametrize('scenario', ['normal', 'autochess'])
def test_controlled_capture_publishes_and_archives_once(scenario, tmp_path, monkeypatch):
    monkeypatch.setattr('native_pc.internal_event_source.InternalEventProbe', MemoryProbe)
    published = []
    result = run_internal_capture(tmp_path / 'unused-host', scenario, tmp_path, publish=published.append)
    record = _assert_history(result, tmp_path)
    assert [sample['frame'] for sample in published] == [0, 30, 60]
    if scenario == 'normal':
        row = record.statistics['rows'][CID]
        assert row['damage'] == row['effective_damage'] == row['skill_damage'] == 20
        assert row['settled_damage']['observed_hits'] == 1
        assert row['healing'] == 6 and row['shield_absorbed'] == 3
        assert row['mitigation_rate'] is None
    else:
        assert _known_player_amounts(result['last_sample']) == [(1, 100), (2, 200), (3, 300), (4, 400)]
        assert record.statistics['rows'] == {}
        assert [p['damage'] for p in record.last_sample['multiplayer']['players']] == [None] * 4


@pytest.mark.parametrize('scenario', ['normal', 'autochess'])
def test_native_host_matches_same_reference_input(scenario, native_host, tmp_path, monkeypatch):
    native = run_internal_capture(native_host, scenario, tmp_path / 'native')
    monkeypatch.setattr('native_pc.internal_event_source.InternalEventProbe', MemoryProbe)
    reference = run_internal_capture(native_host, scenario, tmp_path / 'reference')
    actual = _assert_history(native, tmp_path / 'native')
    expected = _assert_history(reference, tmp_path / 'reference')
    assert native['fixture_sha256'] == reference['fixture_sha256']
    assert native['last_sample']['event_watermark'] == reference['last_sample']['event_watermark']
    assert actual.statistics['rows'] == expected.statistics['rows']
    if scenario == 'autochess':
        assert _known_player_amounts(actual.last_sample) == _known_player_amounts(expected.last_sample)


def test_runtime_carries_reliable_native_counters_after_host_failure(tmp_path, monkeypatch):
    class FailedFrameProbe(MemoryProbe):
        def advance(self, mark, events, terminal_mark=None):
            if mark['frame'] == 30:
                raise RuntimeError('controlled source closed output')
            return super().advance(mark, events, terminal_mark)

    monkeypatch.setattr('native_pc.internal_event_source.InternalEventProbe', FailedFrameProbe)
    result = run_internal_capture(tmp_path / 'unused-host', 'normal', tmp_path)
    assert result['status'] == 'degraded'
    assert result['source_failure'] == dict(reason='controlled source closed output', error_type='RuntimeError', frame=30)
    assert result['samples'] == 3 and result['archives'] == 1
    assert result['last_sample']['event_status'] == 'gap'
    record, = BattleHistory(tmp_path / 'history').list_recent().records
    assert record.event_status == 'gap'
    row = record.statistics['rows'][CID]
    assert row['damage'] == 20 and row['healing'] == 6
    assert row['effective_damage'] is None and row['event_coverage_complete'] is False
    assert row['event_coverage_end'] == 0  # A known initial zero is only valid for this retained prefix.
    assert replay(result['journal'])['archives'] == 1


def test_native_host_rejects_gapped_event_batch_without_discarding_counters(native_host, tmp_path):
    fixture = _builtin_fixture('normal')
    fixture['frames'][1]['events'].pop(0)
    result = run_internal_capture(native_host, _fixture_file(tmp_path / 'gap.json', fixture), tmp_path)
    assert result['status'] == 'degraded'
    assert 'gap' in result['source_failure']['reason']
    record, = BattleHistory(tmp_path / 'history').list_recent().records
    assert record.event_status == 'gap'
    assert record.statistics['rows'][CID]['damage'] == 20


def test_native_host_cannot_replace_independent_autochess_scope(native_host, tmp_path):
    fixture = _builtin_fixture('autochess')
    fixture['frames'][0]['snapshot']['controlled_autochess_scope']['round'] += 1
    published = []
    with pytest.raises(CaptureRetry, match='controlled AutoChess event scope'):
        run_internal_capture(native_host, _fixture_file(tmp_path / 'foreign-round.json', fixture),
                             tmp_path, publish=published.append)
    assert not published
    assert not BattleHistory(tmp_path / 'history').list_recent().records


def test_native_host_keeps_assist_uid_scoped_and_freezes_verified_terminal(native_host, tmp_path):
    fixture = _builtin_fixture('autochess')
    baseline, defense, original_terminal, absent = fixture['frames']
    mark = original_terminal['mark']
    help_scope = dict(mark['native_autochess_context'], phase=5, round=2,
                      scope_key=mark['native_autochess_context']['scope_key'] + '0')
    help_scope['memberships'] = [dict(member, player_side=1) if member['player_index'] == 3 else member
                                 for member in help_scope['memberships']]

    def step(at, state, sequence, deltas=()):
        frame = at * 30
        snapshot = dict(original_terminal['snapshot'], game_time=at, frame=frame, state=state)
        snapshot['controlled_autochess_scope'] = {
            key: help_scope[key] for key in ('scope_key', 'phase', 'round')
        }
        lifecycle = dict(original_terminal['lifecycle'], state=state, metadata=dict(
            original_terminal['lifecycle']['metadata'], outcome='victory' if state == 3 else None))
        context = dict(help_scope, game_time=at, frame=frame)
        evidence = dict(mark, game_time=at, frame=frame, sequence=sequence, epoch=sequence,
                        native_autochess_context=context)
        scope = {key: context[key] for key in ('protocol_version', 'status', 'verified_same_frame',
                                               'frame', 'game_time', 'scope_key', 'phase', 'round')}
        hits = [dict(defense['events'][2], sequence=number, frame=frame, game_time=at,
                     amount=damage, nominal_amount=damage, source_character_uid=uid,
                     native_autochess_scope=scope) for number, uid, damage in deltas]
        return dict(lifecycle=lifecycle, snapshot=snapshot, roster=[], mark=evidence, events=hits)

    assist_baseline = step(2, 2, 4)
    assist = step(3, 2, 6, [(5, 301, 50), (6, 999, 900)])
    terminal = step(4, 3, 7, [(7, 301, 25)])
    fixture['frames'] = [baseline, defense, assist_baseline, assist, terminal, dict(
        absent, mark=terminal['mark'], terminal_mark=terminal['mark'])]
    rows = []
    result = run_internal_capture(native_host, _fixture_file(tmp_path / 'assist.json', fixture), tmp_path,
                                  publish=rows.append)
    assert result['status'] == 'ok' and result['archives'] == 2
    assert rows[1]['multiplayer']['scope_kind'] == 'defense'
    assert _known_player_amounts(rows[1]) == [(1, 100), (2, 200), (3, 300), (4, 400)]
    assert rows[2]['multiplayer']['scope_kind'] == 'assist'
    assert rows[2]['multiplayer']['native_round'] == 2
    assert _known_player_amounts(rows[2]) == [(1, None), (2, None), (3, None), (4, None)]
    assert _known_player_amounts(rows[3]) == [(1, None), (2, None), (3, 50), (4, None)]
    assert rows[3]['multiplayer']['unknown_attribution'][0]['reason'] == 'source_uid_unmapped'
    assert _known_player_amounts(result['last_sample']) == [(1, None), (2, None), (3, 75), (4, None)]
    assert result['last_sample']['multiplayer']['status'] == 'ended'
    records = BattleHistory(tmp_path / 'history').list_recent().records
    assert {record.last_sample['multiplayer']['scope_kind'] for record in records} == {'defense', 'assist'}
    assert replay(result['journal'])['archives'] == 2
