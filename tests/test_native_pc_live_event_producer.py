import json

import pytest

from native_pc.live_event_producer import CooperativeEventWriter


INSTANCE = (42, 132245567890001234)
BUILD = 'win-x64:owned-source-build'
STAGE = 'owned/test/normal'
ROOTS = dict(controller='0x10000', native='0x20000', logger='0x30000', stats='0x40000', level='0x50000')


def writer(path, **overrides):
    return CooperativeEventWriter(path, **(dict(producer='自有事件源', producer_version=2,
                                                 process_instance=INSTANCE, source_build=BUILD) | overrides))


def mark(sequence=0, frame=0, **overrides):
    return dict(sequence=sequence, frame=frame, epoch=sequence, active=0, game_time=frame / 4,
                native_frame=frame, native_fixed_time=str(frame << 30), native_identity=dict(ROOTS),
                native_stage_id=STAGE, native_process_pid=INSTANCE[0], native_generation=1) | overrides


def event(sequence, frame, **overrides):
    return dict(sequence=sequence, frame=frame, game_time=frame / 4, kind='healing', amount=2.5) | overrides


def records(path):
    data = path.read_bytes()
    assert data.endswith(b'\n') and not data.startswith(b'\xef\xbb\xbf')
    return [json.loads(line) for line in data.splitlines()]


def test_feed_is_flushed_and_keeps_caller_evidence_unchanged(tmp_path):
    path = tmp_path / 'battle.jsonl'
    opening = mark(quality_contract_version=1, shield_unknown=True, detail='原始标记')
    update = mark(2, 4, skill_ready=False, skill_unknown=True)
    events = [event(1, 4, proof={'raw': '证据'}), event(2, 4)]
    terminal = mark(2, 8)
    with writer(path) as sink:
        assert records(path) == [dict(format='arkdps-pc-events', version=1, producer='自有事件源',
                                      producer_version=2, process_instance=list(INSTANCE), source_build=BUILD)]
        sink.publish(opening, [])
        sink.publish(update, events)
        assert records(path)[1:] == [dict(mark=opening, events=[]), dict(mark=update, events=events)]
        events.clear()
        update['skill_ready'] = True
        sink.heartbeat()
        heartbeat = records(path)[-1]
        assert heartbeat == dict(mark=mark(2, 4, skill_ready=False, skill_unknown=True), events=[])
        assert 'native_session' not in heartbeat['mark'] and 'source_build' not in heartbeat['mark']
        sink.publish(terminal, [], terminal=True)
        assert records(path)[-1] == dict(mark=terminal, events=[], terminal_mark=terminal)
        with pytest.raises(RuntimeError, match='finished'):
            sink.heartbeat()
        with pytest.raises(RuntimeError, match='finished'):
            sink.publish(terminal, [])
    with pytest.raises(RuntimeError, match='not open'):
        sink.heartbeat()


def test_writer_requires_a_new_file_and_never_synthesizes_terminal(tmp_path):
    path = tmp_path / 'battle.jsonl'
    with writer(path) as sink:
        with pytest.raises(RuntimeError, match='before the first frame'):
            sink.heartbeat()
        sink.publish(mark(), [])
    existing = path.read_bytes()
    with pytest.raises(FileExistsError):
        with writer(path):
            pytest.fail('overwrote an existing feed')
    assert path.read_bytes() == existing
    assert records(path)[-1] == dict(mark=mark(), events=[])
    path.unlink()
    with pytest.raises(RuntimeError, match='cannot be reopened'):
        with sink:
            pytest.fail('reopened a one-battle writer')
    assert not path.exists()


@pytest.mark.parametrize(('overrides', 'reason'), [
    ({'producer': 'embedded\nnewline'}, 'producer name'),
    ({'producer_version': True}, 'producer version'),
    ({'process_instance': [42, 0]}, 'process instance'),
    ({'process_instance': [True, INSTANCE[1]]}, 'process instance'),
    ({'source_build': ''}, 'source build'),
])
def test_invalid_header_fails_before_creating_file(tmp_path, overrides, reason):
    path = tmp_path / 'battle.jsonl'
    with pytest.raises(ValueError, match=reason):
        writer(path, **overrides)
    assert not path.exists()


@pytest.mark.parametrize(('record', 'events', 'error'), [
    (mark(0, 0, active=1), [], 'no active callbacks'),
    (mark(0, 1, native_frame=2), [], 'exact native clock'),
    (mark(0, 1, native_fixed_time='０'), [], 'exact native clock'),
    (mark(0, 1, native_process_pid=43), [], 'native_process_pid'),
    (mark(0, 1, native_process_created_filetime=100), [], 'native_process_created_filetime'),
    (mark(0, 1, source_build='other'), [], 'source_build'),
    (mark(0, 1, native_identity=dict(ROOTS, controller='0x40001')), [], 'battle identity'),
    (mark(0, 1, native_stage_id=''), [], 'process or stage'),
    (mark(0, 1, native_session='other'), [], 'native session'),
    (mark(0, 1, game_time=float('inf')), [], 'watermark clock'),
    (mark(0, 1, detail={1: 'non-string key'}), [], 'keys must be strings'),
    (mark(0, 1, detail=float('nan')), [], 'JSON compliant'),
    (mark(0, 1, detail=1 << 65), [], 'outside the host'),
    (mark(2, 1), [event(0, 1), event(1, 1), event(2, 1)], 'first event sequence'),
    (mark(2, 1), [event(1, 1)], 'do not reach'),
    (mark(4, 2), [event(2, 1), event(4, 2)], 'not continuous'),
    (mark(4, 2), [event(2, 2), event(3, 1), event(4, 2)], 'out of order'),
    (mark(1, 1), [event(1, 1, game_time=2)], 'out of order'),
    (mark(1, 1), [event(1, 1, native_generation=True)], 'native_generation'),
    (mark(1, 1), [event(1, 1, native_stage_id='other')], 'native_stage_id'),
    (mark(1, 1), [event(1, 1, native_identity=dict(ROOTS, controller='0x60000'))], 'battle identity'),
    (mark(1, 1), [event(1, 1, native_session='other')], 'native session'),
])
def test_invalid_first_update_leaves_only_the_header(tmp_path, record, events, error):
    path = tmp_path / 'battle.jsonl'
    with writer(path) as sink:
        original = path.read_bytes()
        with pytest.raises(ValueError, match=error):
            sink.publish(record, events)
        assert path.read_bytes() == original
        sink.publish(mark(), [])
    assert len(records(path)) == 2


@pytest.mark.parametrize('prefix', [[], [event(68, 8), event(69, 8), event(70, 10)]])
def test_first_frame_accepts_a_late_join_or_an_explicit_continuous_prefix(tmp_path, prefix):
    path = tmp_path / 'battle.jsonl'
    with writer(path) as sink:
        sink.publish(mark(70, 10), prefix)
        sink.publish(mark(71, 11), [event(71, 11)])
    assert records(path)[1:] == [dict(mark=mark(70, 10), events=prefix),
                                  dict(mark=mark(71, 11), events=[event(71, 11)])]


def test_after_publish_battle_and_completed_frames_are_immutable(tmp_path):
    path = tmp_path / 'battle.jsonl'
    claimed = '%d:%d:' % INSTANCE + 'a' * 64
    with writer(path) as sink:
        sink.publish(mark(5, 6, native_session=claimed), [event(5, 6)])
        original = path.read_bytes()
        bad_updates = [
            (mark(4, 7), [], 'went backwards'),
            (mark(5, 7, epoch=4), [], 'went backwards'),
            (mark(5, 5), [], 'went backwards'),
            (mark(5, 7, game_time=1.25, native_fixed_time=str(5 << 30)), [], 'went backwards'),
            (mark(5, 6, game_time=1.75, native_fixed_time=str(7 << 30)), [], 'clock of a completed frame'),
            (mark(6, 6), [event(6, 6)], 'already completed'),
            (mark(6, 8), [event(6, 7)], 'does not match the completed frame'),
            (mark(6, 8), [], 'do not reach'),
            (mark(7, 8), [event(7, 8)], 'not continuous'),
            (mark(5, 7, native_generation=2), [], 'cannot change its battle'),
            (mark(5, 7, native_stage_id='other'), [], 'cannot change its battle'),
            (mark(5, 7, native_identity=dict(ROOTS, controller='0x60000')), [], 'cannot change its battle'),
            (mark(5, 7, native_session=claimed[:-1] + 'b'), [], 'different native session'),
        ]
        for record, events, reason in bad_updates:
            with pytest.raises(ValueError, match=reason):
                sink.publish(record, events)
            assert path.read_bytes() == original
        sink.publish(mark(6, 8), [event(6, 8)])
        sink.publish(mark(6, 8), [], terminal=True)
    assert records(path)[-1]['terminal_mark'] == mark(6, 8)


def test_same_frame_can_only_repeat_canonical_watermark_or_seal_it(tmp_path):
    path = tmp_path / 'battle.jsonl'
    original = mark(0, 4, quality_contract_version=1, skill_ready=False, context={'sample': 1, 'other': True})
    with writer(path) as sink:
        sink.publish(original, [])
        prefix = path.read_bytes()
        for changes in ({'epoch': 1}, {'skill_ready': True}, {'quality_contract_version': True},
                        {'context': {'sample': True, 'other': True}}):
            with pytest.raises(ValueError, match='cannot change the watermark'):
                sink.publish(original | changes, [], terminal=True)
            assert path.read_bytes() == prefix
        reordered = dict(reversed(tuple(original.items()))) | {'context': {'other': True, 'sample': 1}}
        sink.publish(reordered, [], terminal=True)
    assert records(path)[-1] == dict(mark=original, events=[], terminal_mark=original)


def test_encoded_size_queue_and_depth_limits_are_checked_before_append(tmp_path):
    path = tmp_path / 'battle.jsonl'
    nested = None
    for _ in range(61):
        nested = [nested]
    with writer(path) as sink:
        original = path.read_bytes()
        with pytest.raises(ValueError, match='native host size limit'):
            sink.publish(mark(detail='界' * 90000), [])
        with pytest.raises(ValueError, match='nesting limit'):
            sink.publish(mark(detail=nested), [])
        with pytest.raises(ValueError, match='4096 event objects'):
            sink.publish(mark(4097), [event(index, 0) for index in range(1, 4098)])
        sink.max_line_bytes = 1024
        with pytest.raises(ValueError, match='record exceeds the line limit'):
            sink.publish(mark(1), [event(1, 0, evidence='界' * 500)])
        assert path.read_bytes() == original
        sink.publish(mark(), [])
        sink.max_file_bytes = path.stat().st_size + 1
        with pytest.raises(ValueError, match='exceeds the file limit'):
            sink.heartbeat()
    assert records(path)[-1] == dict(mark=mark(), events=[])


def test_partial_io_poisoning_never_appends_after_failure(tmp_path):
    path = tmp_path / 'battle.jsonl'
    with writer(path) as sink:
        raw = sink._stream

        class PartialWrite:
            def write(self, data):
                return raw.write(data[: len(data) // 2])

            def close(self):
                raw.close()

        sink._stream = PartialWrite()
        with pytest.raises(OSError, match='incomplete event feed write'):
            sink.publish(mark(), [])
        original = path.read_bytes()
        assert not original.endswith(b'\n')
        with pytest.raises(RuntimeError, match='not open'):
            sink.publish(mark(), [])
        assert path.read_bytes() == original
