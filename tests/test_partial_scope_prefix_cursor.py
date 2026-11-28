"""A valid new-scope batch cannot choose which accepted records to skip."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from capture_battles import observation_from_json
from event_probe import StreamGap
from test_pending_scope_start import live_scope_case


def test_partial_prefix_cannot_move_its_start_past_the_host_accepted_cursor(monkeypatch):
    case = live_scope_case(monkeypatch)
    source = case.fixture
    case.advance(1.5, phase=5)
    assert source.poll().kind == 'sample'
    generation = source.runtime.sessions.generation
    accepted = source.runtime.sessions.adapter.event_stream.sequence
    earlier, = source.runtime.sessions.pop_archives()
    assert earlier['last_sample']['game_time'] == 1
    case.advance(2, phase=5, next_round=True, amount=75)
    case.advance(2, amount=25)
    retained = deepcopy(source.queue)
    observation = source.runtime._acquire(source.collector, source.observer)
    raw = asdict(observation)
    prefix = raw['events']['partial_scope_prefix']
    assert prefix['after'] == accepted
    # Keep a correctly bound, consecutive page for the final 25, but remove the
    # earlier 75 by lying about the host cursor. Raw page validity is insufficient.
    prefix['after'] += 1
    page, = prefix['pages']
    page['after'] += 1
    page['batch']['events'] = page['batch']['events'][1:]
    raw['events']['drain']['events'] = raw['events']['drain']['events'][1:]
    with pytest.raises(StreamGap, match='accepted cursor'):
        source.runtime.sessions.consume(observation_from_json(raw))
    assert source.runtime.sessions.generation == generation
    assert source.runtime.sessions.adapter.event_stream.sequence == accepted
    assert max(source.commits) == accepted and source.queue == retained
    assert not source.runtime.sessions.pop_archives()
