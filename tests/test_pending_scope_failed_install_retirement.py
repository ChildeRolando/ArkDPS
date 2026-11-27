"""A rejected new installation must not lose the independently retired old battle."""
from dataclasses import replace

import pytest
from battle_lifecycle import BattleSessions, MappingRetry
from test_battle_lifecycle import begin


@pytest.mark.parametrize('departure', ['process_changed', 'battle_absent', 'stop'])
def test_failed_new_install_keeps_old_archive_and_replay(departure):
    collector, runtime, first = begin()
    records = [first]
    runtime.record = records.append
    collector.advance(time=1., frame=1, damage=5.)
    collector.roster = replace(collector.roster, entries=())
    with pytest.raises(MappingRetry):
        runtime.poll(collector)
    if departure == 'process_changed':
        runtime.process_changed()
    elif departure == 'battle_absent':
        collector.lifecycle['battle'] = None
        runtime.poll(collector)
    else:
        runtime.disconnected('capture_stopped')
    archives = runtime.sessions.pop_archives()
    assert len(archives) == 1
    assert archives[0]['statistics']['rows']['a']['damage'] == 100.
    replay = BattleSessions(runtime.feed.id)
    for observation in records:
        replay.consume(observation)
    assert replay.pop_archives() == archives
