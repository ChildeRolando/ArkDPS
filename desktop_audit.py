"""Store the desktop journal's cumulative enemy state prefix once per battle."""

ENEMY_STATE_DELTA = 'desktop_enemy_state_delta_v1'
ENEMY_STATE_RECORDS = frozenset(('published', 'lifecycle_summary', 'coverage_closed'))


def _statistics(kind, data):
    return data if kind == 'coverage_closed' else data.get('statistics')


def _replace_events(kind, data, events):
    statistics = _statistics(kind, data)
    statistics = dict(statistics, enemy_state=dict(statistics['enemy_state'], events=events))
    return statistics if kind == 'coverage_closed' else dict(data, statistics=statistics)


class DesktopAuditWriter:
    def __init__(self, emit):
        self.emit = emit
        self.key = None
        self.length = 0
        self.first = self.last = None

    def write(self, kind, data):
        if kind not in ENEMY_STATE_RECORDS:
            self.emit(dict(kind=kind, data=data))
            return
        statistics = _statistics(kind, data)
        state = (statistics or {}).get('enemy_state')
        if state is None:
            self.emit(dict(kind=kind, data=data))
            return
        key = statistics['session'], state['start_frame']
        events = state['events']
        # Statistics appends validated events without revising the prefix.
        # A smaller or different prefix starts a new series.
        offset = self.length
        if (key != self.key or len(events) < offset
                or offset and (events[0] != self.first or events[offset - 1] != self.last)):
            offset = 0
        self.emit(dict(kind=ENEMY_STATE_DELTA, data=dict(
            kind=kind, offset=offset, value=_replace_events(kind, data, events[offset:]))))
        self.key, self.length = key, len(events)
        self.first, self.last = (events[0], events[-1]) if events else (None, None)


class DesktopAuditReader:
    def __init__(self):
        self.key = None
        self.events = []

    def restore(self, record):
        if record['kind'] != ENEMY_STATE_DELTA:
            return record
        try:
            kind, offset, value = (record['data'][field] for field in ('kind', 'offset', 'value'))
            if kind not in ENEMY_STATE_RECORDS or type(offset) is not int or offset < 0:
                raise ValueError('invalid enemy state delta')
            statistics = _statistics(kind, value)
            state = statistics['enemy_state']
            key = statistics['session'], state['start_frame']
            tail = state['events']
            if not isinstance(tail, list):
                raise ValueError('invalid enemy state delta events')
            if offset and (key != self.key or offset != len(self.events)):
                raise ValueError('missing enemy state delta prefix')
        except (AttributeError, KeyError, TypeError) as exc:
            raise ValueError('invalid enemy state delta') from exc
        self.events = self.events + tail if offset else tail
        self.key = key
        return dict(kind=kind, data=_replace_events(kind, value, self.events))
