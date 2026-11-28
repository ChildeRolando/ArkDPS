"""Retained current-scope facts are partial evidence, never a historical snapshot."""
from copy import deepcopy

import pytest

from event_capture import LiveEventCapture, read_partial_scope_prefix, source_attribution_batch
from event_probe import probe_cursor_lifetime, verified_partial_scope_prefix
from event_stream import StreamGap
from multiplayer_battle import MultiplayerBattleStatistics
from test_autochess_scope_end import OwnedScopeEnd
from test_pc_serial_clock_reset_end import CollectorClockBattle


def acquired(monkeypatch, platform='native_pc'):
    import test_autochess_scope_end
    from event_probe import reset_probe_cursor
    class SourceWithLifetime(CollectorClockBattle):
        def __init__(self):
            super().__init__()
            reset_probe_cursor(self.observer)
    if platform == 'native_pc':
        monkeypatch.setattr(test_autochess_scope_end, 'CrossingPcBattle', SourceWithLifetime)
    if platform == 'native_pc':
        case = OwnedScopeEnd(platform, phase=4, mode='all')
        source, advance = case.fixture, case.advance
    else:
        from test_android_autochess_acquisition import AndroidAutoChessBattle
        source = AndroidAutoChessBattle()
        source.mode = 'all'
        source.poll()
        source.advance(1, amounts=((3, 300),))
        source.poll()
        def advance(when, *, phase=None, amount=None):
            source.advance(when, phase=phase, amounts=() if amount is None else ((3, amount),))
        # This fixture itself implements the real independent Android read pair.
        source.collector = source
        source.queue = source.events
        reset_probe_cursor(source.observer)
    batch = source.batch if platform == 'native_pc' else source.read_through
    source.observer.read_round_start_range = lambda after, through: dict(
        batch(through), events=deepcopy([event for event in source.queue
                                              if after < event['sequence'] <= through]))
    accepted = source.runtime.sessions.adapter.event_stream.sequence
    advance(1.7, phase=5, amount=75)
    advance(2)
    opening = source.observer.mark()
    lifecycle = source.collector.read_lifecycle()
    sample, roster, evidence = source.runtime.feed._read_pair(
        source.collector, lifecycle, source.observer, (opening, None))
    assert sample['frame'] == 60
    evidence = read_partial_scope_prefix(source.observer, accepted, evidence, sample,
                                        cursor_lifetime=probe_cursor_lifetime(source.observer))
    return source, sample, roster, evidence


@pytest.mark.parametrize('platform', ['native_pc', 'mumu'])
def test_retained_partial_prefix_preserves_first_hit_in_both_shared_owners(monkeypatch, platform):
    source, sample, roster, evidence = acquired(monkeypatch, platform)
    retained = deepcopy(evidence)
    checked = verified_partial_scope_prefix(sample, evidence, previous_sequence=1)
    assert (checked['after'], checked['frame'], checked['game_time']) == (1, 51, 1.7)
    cap = LiveEventCapture(source.collector, source.observer)
    cap.adapter.update(sample, roster, **source_attribution_batch(
        evidence['drain'], 1, evidence['sequence'], sample=sample, mark=evidence['after']))
    cap.ingest(sample, roster, evidence['sequence'], evidence['drain'],
               mark=evidence['after'], partial_prefix=evidence)
    assert cap.adapter.event_stream.sequence == 2
    assert cap.adapter.event_stream.baseline_frame == 51
    assert cap.last_sample['frame'] == 60
    assert cap.adapter.statistics.event_coverage_broken
    enemy_damage = cap.adapter.statistics.result()['enemy_damage']
    target_uid = evidence['drain']['events'][0]['target_uid']
    assert enemy_damage['by_uid'][str(target_uid)]['amount'] == 75
    assert enemy_damage['complete'] is False
    mp = MultiplayerBattleStatistics()
    mp.consume(sample, roster, evidence['sequence'], evidence['drain'],
               mark=evidence['after'], partial_prefix=evidence)
    result = mp.result()
    assert result['players'][2]['observed_damage'] == 75
    assert result['players'][2]['damage'] is None
    assert result['coverage_start'] == 1.7
    assert evidence == retained and source.commits == [0, 1]


@pytest.mark.parametrize('fault', ['scope', 'sequence', 'clock', 'page', 'source', 'drain',
                                  'mode', 'frame_pair', 'binding', 'old_tail'])
@pytest.mark.parametrize('platform', ['native_pc', 'mumu'])
def test_retained_partial_prefix_rejects_wrong_or_missing_facts(monkeypatch, fault, platform):
    source, sample, roster, evidence = acquired(monkeypatch, platform)
    tx = evidence['partial_scope_prefix']
    event = tx['pages'][0]['batch']['events'][0]
    if fault == 'scope':
        event['native_autochess_scope']['phase'] = 4
    elif fault == 'sequence':
        event['sequence'] += 1
    elif fault == 'clock':
        event['frame'] = sample['frame'] + 1
    elif fault == 'page':
        tx['pages'] = []
    elif fault == 'source':
        tx['pages'][0]['batch']['native_process_pid'] += 1
    elif fault == 'drain':
        evidence['drain']['events'] = []
    elif fault == 'mode':
        event['native_autochess_scope']['collection_mode'] = 'local'
    elif fault == 'frame_pair':
        evidence['after']['native_fixed_time'] = '0'
    elif fault == 'binding':
        if platform == 'native_pc':
            tx['pages'][0]['batch']['source_build'] = 'another-build'
        else:
            evidence['after']['source_build'] = 'android:2.7.91'
            tx['pages'][0]['batch']['source_build'] = 'another-build'
    else:
        event['native_autochess_scope']['scope_key'] += '-old'
    with pytest.raises(StreamGap):
        verified_partial_scope_prefix(sample, evidence, previous_sequence=1)
    assert source.commits == [0, 1]


def test_retained_partial_is_not_a_zero_origin_even_if_first_fact_is_at_zero(monkeypatch):
    source, sample, roster, evidence = acquired(monkeypatch)
    for event in (evidence['drain']['events'][0],
                  evidence['partial_scope_prefix']['pages'][0]['batch']['events'][0]):
        event['frame'] = event['native_autochess_scope']['frame'] = 0
        event['game_time'] = event['native_autochess_scope']['game_time'] = 0
    cap = LiveEventCapture(source.collector, source.observer)
    cap.adapter.update(sample, roster, **source_attribution_batch(
        evidence['drain'], 1, evidence['sequence'], sample=sample, mark=evidence['after']))
    cap.ingest(sample, roster, 2, evidence['drain'], mark=evidence['after'], partial_prefix=evidence)
    assert cap.last_sample['frame'] == 60 and cap.last_sample['game_time'] == 2
    assert cap.adapter.statistics.event_coverage_broken
    assert cap.adapter.statistics.skill_unknown
    before = cap.adapter.event_stream.sequence
    with pytest.raises(StreamGap):
        cap.ingest(sample, roster, 2, evidence['drain'], mark=evidence['after'], partial_prefix=evidence)
    assert cap.adapter.event_stream.sequence == before


@pytest.mark.parametrize('fault', ['before', 'drain', 'sequence', 'sample'])
def test_partial_marker_malformed_shape_is_local_gap_without_ack(monkeypatch, fault):
    from event_observer import ObserverStatus
    source, sample, _, evidence = acquired(monkeypatch, 'mumu')
    prior = source.runtime.sessions.adapter
    if fault == 'sample':
        sample = None
    else:
        del evidence[fault]
    with pytest.raises(StreamGap):
        verified_partial_scope_prefix(sample, evidence, previous_sequence=1)
    assert source.runtime.sessions.adapter is prior
    assert source.runtime.sessions.adapter.event_stream.sequence == 1
    assert source.observer.status is ObserverStatus.READY
    assert source.commits == [0, 1]


def test_tampered_partial_prefix_normal_journal_has_finite_reason_without_payload(monkeypatch, tmp_path):
    import json
    from capture_cli import json_value
    from desktop_audit import DesktopCaptureJournal
    from event_probe import stream_gap_failure_context

    source, sample, _, evidence = acquired(monkeypatch)
    evidence['partial_scope_prefix']['pages'][0]['batch']['events'][0]['native_autochess_scope']['scope_key'] = 'PRIVATE_SCOPE'
    with pytest.raises(StreamGap) as caught:
        verified_partial_scope_prefix(sample, evidence, previous_sequence=1)
    detail = stream_gap_failure_context(caught.value, 'acquire')
    assert detail['reason'] == 'partial_prefix_event'
    assert any(frame['function'] == 'verified_partial_scope_prefix' for frame in detail['frames'])
    with DesktopCaptureJournal(tmp_path, encoder=json_value, warn=pytest.fail, debug_mode=False,
                               header=lambda *args, **kwargs: dict(kind='log_header', data={})) as journal:
        journal.write('capture_error', dict(error_type='StreamGap', stream_gap_failure=detail))
    rows = [json.loads(line) for line in next(tmp_path.glob('wpf-*.jsonl')).read_text(encoding='utf-8').splitlines()]
    assert rows[1]['data']['stream_gap_failure']['reason'] == 'partial_prefix_event'
    assert 'PRIVATE_SCOPE' not in json.dumps(rows)
    assert source.commits == [0, 1]


@pytest.mark.parametrize('platform', ['native_pc', 'mumu'])
def test_partial_prefix_does_not_require_optional_match_or_mode_fields(monkeypatch, platform):
    source, sample, roster, evidence = acquired(monkeypatch, platform)
    for payload in (sample['autochess_context'], evidence['before']['native_autochess_context'],
                    evidence['after']['native_autochess_context'],
                    evidence['drain']['native_autochess_context'],
                    evidence['partial_scope_prefix']['pages'][0]['batch']['native_autochess_context']):
        if platform == 'native_pc':
            # Android's existing independent admission requires match_key;
            # the partial extension must neither add nor remove that rule.
            payload.pop('match_key', None)
        payload.pop('collection_mode', None)
    for event in (evidence['drain']['events'][0],
                  evidence['partial_scope_prefix']['pages'][0]['batch']['events'][0]):
        event['native_autochess_scope'].pop('collection_mode', None)
    assert verified_partial_scope_prefix(sample, evidence, previous_sequence=1)['events'][0]['amount'] == 75
    cap = LiveEventCapture(source.collector, source.observer)
    cap.adapter.update(sample, roster, **source_attribution_batch(
        evidence['drain'], 1, 2, sample=sample, mark=evidence['after']))
    cap.ingest(sample, roster, 2, evidence['drain'], mark=evidence['after'], partial_prefix=evidence)
    assert sum(row['amount'] for row in cap.adapter.statistics.result()['enemy_damage']['by_uid'].values()) == 75
    mp = MultiplayerBattleStatistics()
    mp.consume(sample, roster, 2, evidence['drain'], mark=evidence['after'], partial_prefix=evidence)
    assert mp.result()['players'][2]['observed_damage'] == 75
    assert mp.result()['players'][2]['damage'] is None
    assert source.commits == [0, 1]


@pytest.mark.parametrize('platform', ['native_pc', 'mumu'])
def test_partial_prefix_still_binds_actual_player_account_table(monkeypatch, platform):
    source, sample, _, evidence = acquired(monkeypatch, platform)
    evidence['partial_scope_prefix']['pages'][0]['batch']['native_autochess_context']['players'][0]['uid'] = '1234567'
    with pytest.raises(StreamGap, match='changed source or scope'):
        verified_partial_scope_prefix(sample, evidence, previous_sequence=1)
    assert source.commits == [0, 1]


@pytest.mark.parametrize('platform', ['native_pc', 'mumu'])
def test_partial_prefix_unknown_self_account_does_not_guess_local_ownership(monkeypatch, platform):
    source, sample, roster, evidence = acquired(monkeypatch, platform)
    sample['autochess_context'].update(self_player_index=0, self_player_uid=None)
    for payload in (evidence['before'], evidence['after'], evidence['drain'],
                    evidence['partial_scope_prefix']['pages'][0]['batch']):
        payload['native_autochess_context']['self_player_index'] = 0
    verified_partial_scope_prefix(sample, evidence, previous_sequence=1)
    mp = MultiplayerBattleStatistics()
    mp.consume(sample, roster, 2, evidence['drain'], mark=evidence['after'], partial_prefix=evidence)
    result = mp.result()
    assert result['self_player_uid'] is None
    assert all(player['is_local'] is None for player in result['players'])
    assert result['players'][2]['observed_damage'] == 75
    assert result['players'][2]['damage'] is None
    assert source.commits == [0, 1]


@pytest.mark.parametrize('field,expected,actual', [
    ('native_generation', 10, 11), ('native_session', 'owned-a', 'owned-b'),
    ('native_process_created_filetime', 100, 101), ('source_build', 'android:a', 'android:b')])
def test_mumu_optional_fields_cannot_contradict_when_both_declared(monkeypatch, field, expected, actual):
    source, sample, _, evidence = acquired(monkeypatch, 'mumu')
    evidence['after'][field] = expected
    evidence['partial_scope_prefix']['pages'][0]['batch'][field] = actual
    with pytest.raises(StreamGap, match='changed source or scope'):
        verified_partial_scope_prefix(sample, evidence, previous_sequence=1)
    assert source.commits == [0, 1]
