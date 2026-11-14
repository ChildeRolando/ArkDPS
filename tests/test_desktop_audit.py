"""Round-trip desktop journal deltas without changing acceptance journals."""
import json

import pytest

from capture_battles import replay
from capture_cli import json_value
from desktop_audit import DesktopAuditReader, DesktopAuditWriter, ENEMY_STATE_DELTA
from test_lifecycle_events import connected


def test_desktop_enemy_state_delta_restores_prefix_across_records_and_resets():
    records = []
    writer = DesktopAuditWriter(records.append)
    expected = []

    def write(kind, data):
        expected.append(dict(kind=kind, data=data))
        writer.write(kind, data)

    def statistics(session, frame, *sequences, complete=True):
        return dict(session=session, rows={}, enemy_state=dict(
            start_frame=frame, events=[dict(sequence=value) for value in sequences],
            complete=complete))

    write('published', dict(session='first', statistics=statistics('first', 4, 5)))
    write('lifecycle_observation', {'sample': {'guard': {'start': 4}}})
    write('published', dict(session='first', statistics=statistics('first', 4, 5, 7)))
    write('lifecycle_summary', dict(statistics=statistics('first', 4, 5, 7, complete=False)))
    write('coverage_closed', statistics('first', 4, 5, 7, complete=False))
    write('published', dict(session='second', statistics=statistics('second', 4, 51)))
    write('published', dict(session='second', statistics=statistics('second', 6, 60)))
    write('published', dict(session='second', statistics=statistics('second', 6, 61)))
    write('published', dict(session='second', statistics=statistics('second', 6)))

    assert [record['data']['offset'] for record in records
            if record['kind'] == ENEMY_STATE_DELTA] == [0, 1, 2, 2, 0, 0, 0, 0]
    reader = DesktopAuditReader()
    assert [reader.restore(record) for record in records] == expected
    with pytest.raises(ValueError, match='missing enemy state delta prefix'):
        DesktopAuditReader().restore(records[2])


def test_desktop_enemy_state_delta_does_not_commit_after_failed_write():
    records = []

    def write(record):
        if not records:
            records.append(None)
            raise OSError('journal write failed')
        records.append(record)

    writer = DesktopAuditWriter(write)
    sample = dict(statistics=dict(session='one', enemy_state=dict(
        start_frame=4, events=[dict(sequence=5)])))
    with pytest.raises(OSError, match='journal write failed'):
        writer.write('published', sample)
    writer.write('published', sample)
    assert records[-1]['data']['offset'] == 0


def test_desktop_delta_replay_checks_enemy_state_in_restored_statistics(tmp_path):
    session, collector, _, captured = connected()
    observer = session.observer
    drain = observer.drain
    observer.drain = lambda: dict(drain(), enemy_state_ready=True,
                                  enemy_entry_ready=True, enemy_phase_ready=True)
    try:
        session.read()
        collector.frame, collector.game_time = 2, 1.0
        observer.sequence = observer.epoch = 1
        observer.events = [dict(sequence=1, frame=2, game_time=1., kind='enemy_enter',
                                source=None, target='enemy_a', target_instance='12',
                                amount=0, hp_after=60_000,
                                enemy_entry_attributes={'max_hp': 60_000})]
        session.read()
    finally:
        session.close()
    records = []
    write = DesktopAuditWriter(records.append).write
    for kind, data in captured:
        write(kind, data)
    path = tmp_path / 'capture.jsonl'

    def save():
        path.write_text('\n'.join(json.dumps(record, default=json_value) for record in records),
                        encoding='utf-8')

    save()
    actual = replay(path)['last_state']['statistics']['enemy_state']
    assert actual['events'][0]['hp_after'] == 60_000
    publication = next(record['data']['value'] for record in records if
                       record['kind'] == ENEMY_STATE_DELTA and
                       record['data']['kind'] == 'published' and
                       record['data']['value']['frame'] == 2)
    publication['statistics']['enemy_state']['events'][0]['hp_after'] = 10
    save()
    with pytest.raises(ValueError, match='published statistics differ'):
        replay(path)
