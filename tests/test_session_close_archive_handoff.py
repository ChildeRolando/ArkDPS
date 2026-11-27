"""Stopping collection cannot invent an unobserved battle-end archive."""
from types import SimpleNamespace

from test_collection_session import session


def collecting_with_history():
    current, collector, calls = session(live=False)
    saved = []

    def save(record):
        saved.append(record.to_dict())
        return SimpleNamespace(written=True, diagnostics=[])

    current.history = SimpleNamespace(save=save)
    current.connect()
    current.locate()
    current.read()
    return current, saved, calls


def test_close_without_native_end_keeps_frozen_totals_without_archiving_finish():
    current, saved, calls = collecting_with_history()
    current.close()
    assert saved == []
    assert current.runtime.sessions.state == 'frozen'
    assert current.runtime.sessions.adapter.statistics.end_time is None
    assert current.runtime.sessions.adapter.result()['rows']['char_a']['damage'] == 100
    assert not current.runtime.sessions.pop_archives()
    assert not current.runtime.pending_history
    current.close()
    assert saved == []
    assert calls.count('collector_close') == 1
