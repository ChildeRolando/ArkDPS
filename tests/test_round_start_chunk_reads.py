"""A large round-start prefix is read without releasing any producer events."""
from copy import deepcopy

import pytest

from event_capture import CaptureRetry, read_event_batch
from event_observer import FridaEventObserver, NativePcFridaObserver
from event_probe import MUMU_CONTRACT, NATIVE_PC_CONTRACT, ObserverReadRetry, StreamGap
from native_pc.native_hook_source import NativeHookEventProbe
from test_android_autochess_acquisition import AndroidAutoChessBattle


ROOTS = dict(controller='0x11000', native='0x12000', logger='0x13000',
             stats='0x14000', level='0x15000')
SCOPE = ':'.join(ROOTS.values()) + ':autochess:3'
START_SEQUENCE = 500


def binding(frame, *, when=None):
    when = frame if when is None else when
    return dict(native_process_pid=512, native_generation=3, native_stage_id='act_autochess/test',
                native_identity=dict(ROOTS), native_frame=frame, native_fixed_time=str(when << 32),
                native_session='guarded-session', source_build='verified-build',
                native_autochess_context=dict(
                    protocol_version=1, status='available', verified_same_frame=True,
                    frame=frame, game_time=when, scope_key=SCOPE, match_key='0x16000:0x17000',
                    phase=4, round=3, collection_mode='all', self_player_index=3,
                    players=[dict(index=i, uid=f'00{i}', nick_name=f'P{i}', nick_number=f'00{i}')
                             for i in range(1, 5)],
                    memberships=[]))


class OwnedObserver:
    contract = NATIVE_PC_CONTRACT

    def __init__(self, fault=None):
        self.fault = fault
        self.reads = []
        self.commits = []
        self.cursor = 499
        self.first = dict(binding(30), events=[], autochess_round_start_events=[],
                          autochess_round_start_events_complete=False)
        origin = binding(0)
        for key in ('native_frame', 'native_session', 'source_build'):
            origin.pop(key)
        self.first['autochess_round_start'] = dict(
            origin, frame=0, game_time=0, sequence=START_SEQUENCE, epoch=1,
            active=0, version=1, source_method='BattleController.ClearRunTimeData',
            live_character_collection_empty=True, quality={})

    def read_baseline(self, through):
        return self.first

    def read_through(self, through):
        raise AssertionError('a baseline cannot use the ordinary drain')

    def read_round_start_range(self, after, through):
        self.reads.append((after, through))
        packet = dict(binding(30 + len(self.reads)),
                      events=[{'sequence': seq} for seq in range(after + 1, through + 1)])
        if len(self.reads) == 2 and self.fault is not None:
            return self.fault(packet)
        return packet

    def commit_events(self, sequence):
        self.commits.append(sequence)
        self.cursor = sequence
        raise AssertionError('a range read cannot release producer records')


@pytest.mark.parametrize('count', [2049, 4097, 100000])
def test_reassembles_only_the_requested_unacknowledged_prefix(count):
    observer = OwnedObserver()
    first = deepcopy(observer.first)
    end = START_SEQUENCE + count
    batch = read_event_batch(observer, end, baseline=True)
    assert observer.first == first and batch is not observer.first
    assert batch['native_frame'] == 30 and batch['native_fixed_time'] == first['native_fixed_time']
    assert batch['events'] == [] and batch['autochess_round_start_events_complete'] is True
    assert batch['autochess_round_start'] == first['autochess_round_start']
    assert [event['sequence'] for event in batch['autochess_round_start_events']] == list(range(501, end + 1))
    assert observer.reads == [(start, min(start + 2048, end))
                              for start in range(START_SEQUENCE, end, 2048)]
    assert observer.commits == [] and observer.cursor == 499


@pytest.mark.parametrize('count,change', [(2048, None), (100001, None), (2049, 'missing'),
                                          (2049, 'already_complete'), (2049, 'malformed')])
def test_does_not_expand_an_unadvertised_or_out_of_bound_prefix(count, change):
    observer = OwnedObserver()
    if change == 'missing':
        observer.first.pop('autochess_round_start')
    elif change == 'already_complete':
        observer.first['autochess_round_start_events_complete'] = True
    elif change == 'malformed':
        observer.first['autochess_round_start']['frame'] = False
    batch = read_event_batch(observer, START_SEQUENCE + count, baseline=True)
    assert batch == dict(observer.first, probe_contract=NATIVE_PC_CONTRACT.describe())
    assert not observer.reads and not observer.commits


def test_android_raw_envelope_replays_through_the_real_lifecycle_without_pc_binding_fields():
    battle = AndroidAutoChessBattle()
    raw_mark = battle.observer.mark

    def mark():
        return dict(raw_mark(), native_generation=1, active_hooks=[], skill_ready=True,
                    source_attribution_version=1, source_character_uid_ready=True,
                    quality_contract_version=1, damage_settlement_quality_version=1,
                    unknown_sources=[], engagement_unknown_sources=[])

    zero = mark()
    point = {key: zero[key] for key in ('frame', 'game_time', 'sequence', 'epoch', 'active',
        'native_process_pid', 'native_generation', 'native_stage_id', 'native_identity',
        'native_fixed_time', 'native_autochess_context')}
    point.update(version=1, source_method='BattleController.ClearRunTimeData',
                 live_character_collection_empty=True, quality=zero)
    reads = []

    def baseline(through):
        assert through == 2049
        return dict(mark(), events=[], autochess_round_start=point,
                    autochess_round_start_events=[], autochess_round_start_events_complete=False)

    def read_range(after, through):
        reads.append((after, through))
        return dict(mark(), events=battle.events[after:through])

    battle.observer.mark = mark
    battle.observer.read_baseline = baseline
    battle.observer.read_round_start_range = read_range
    battle.advance(0.5, amounts=[(3, 1)] * 2049)
    battle.advance(1)
    first = battle.poll()
    assert first.kind == 'sample'
    assert 'native_session' not in first.events['drain'] and 'source_build' not in first.events['drain']
    assert reads == [(0, 2048), (2048, 2049)]
    assert battle.commits == [2049]
    player = battle.runtime.display_sample()['multiplayer']['players'][2]
    assert player['observed_damage'] == 2049
    assert player['actor_ledgers'][0]['ledger']['observed_non_skill_damage'] == 2049


@pytest.mark.parametrize('field', ('native_session', 'source_build'))
def test_pc_requires_bound_transport_fields_before_first_range(field):
    observer = OwnedObserver()
    observer.first.pop(field)
    with pytest.raises(StreamGap, match='first read'):
        read_event_batch(observer, START_SEQUENCE + 2049, baseline=True)
    assert not observer.reads and not observer.commits


@pytest.mark.parametrize('field', ('native_session', 'source_build'))
def test_android_checks_transport_fields_when_supplied(field):
    observer = OwnedObserver(lambda packet: dict(packet, **{field: None}))
    observer.contract = MUMU_CONTRACT
    with pytest.raises(StreamGap, match='source or scope'):
        read_event_batch(observer, START_SEQUENCE + 2049, baseline=True)
    assert len(observer.reads) == 2 and not observer.commits


def retry_request(_packet):
    raise ObserverReadRetry('owned timeout')


def broken_request(_packet):
    raise RuntimeError('owned read failure')


def invalid_transport_response(_packet):
    raise TypeError('owned invalid read response')


@pytest.mark.parametrize('fault,error', [
    (lambda packet: dict(packet, error='unavailable'), StreamGap),
    (lambda packet: dict(packet, stopped=True), StreamGap),
    (lambda packet: dict(packet, retry=True), CaptureRetry),
    (retry_request, CaptureRetry), (broken_request, StreamGap),
    (invalid_transport_response, StreamGap),
    (lambda packet: None, StreamGap),
    (lambda packet: dict(packet, events=[]), StreamGap),
    (lambda packet: dict(packet, events=[None] * len(packet['events'])), StreamGap),
    (lambda packet: dict(packet, native_process_pid=513), StreamGap),
    (lambda packet: dict(packet, native_generation=True), StreamGap),
    (lambda packet: dict(packet, native_stage_id='other-stage'), StreamGap),
    (lambda packet: dict(packet, native_identity=dict(ROOTS, stats='0x88000')), StreamGap),
    (lambda packet: dict(packet, native_session='next-session'), StreamGap),
    (lambda packet: dict(packet, source_build='different-build'), StreamGap),
], ids=['error', 'stopped', 'retry', 'transport-timeout', 'read-failure', 'transport-type', 'bad-packet',
        'short-range', 'bad-record', 'pid', 'generation', 'stage', 'roots', 'session', 'build'])
def test_second_range_failure_never_returns_the_first_part(fault, error):
    observer = OwnedObserver(fault)
    first = deepcopy(observer.first)
    with pytest.raises(error):
        read_event_batch(observer, START_SEQUENCE + 5000, baseline=True)
    assert len(observer.reads) == 2
    assert observer.first == first and observer.commits == [] and observer.cursor == 499


@pytest.mark.parametrize('field,value', [
    ('scope_key', SCOPE.rsplit(':', 1)[0] + ':4'),
    ('phase', 5), ('round', 4), ('collection_mode', 'local'),
    ('self_player_index', 2), ('match_key', '0x18000:0x19000'),
    ('players', [dict(index=3, uid='42', nick_name='X', nick_number='0042')]),
])
def test_ranges_must_remain_in_the_initial_scope_and_accounts(field, value):
    def change_context(packet):
        packet['native_autochess_context'][field] = value
        return packet

    observer = OwnedObserver(change_context)
    with pytest.raises(StreamGap, match='source or scope'):
        read_event_batch(observer, START_SEQUENCE + 5000, baseline=True)
    assert len(observer.reads) == 2 and not observer.commits


@pytest.mark.parametrize('observer_type,method', [
    (FridaEventObserver, '_read_event_range'),
    (NativePcFridaObserver, '_read_event_range'),
    (NativeHookEventProbe, '_read_range'),
])
def test_public_range_wrapper_does_not_touch_cursors_or_access_committed_data(observer_type, method):
    observer = object.__new__(observer_type)
    observer._committed_sequence, observer._acknowledged_sequence = 11, 10
    calls = []
    reply = {}

    def read(after, through):
        calls.append((after, through))
        return reply

    setattr(observer, method, read)
    assert observer.read_round_start_range(11, 25) is reply
    with pytest.raises(RuntimeError, match='already been committed'):
        observer.read_round_start_range(10, 25)
    assert calls == [(11, 25)]
    assert (observer._committed_sequence, observer._acknowledged_sequence) == (11, 10)
