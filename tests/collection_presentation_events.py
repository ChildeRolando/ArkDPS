"""Inspect logical contents without changing the real collection queue shape."""


def presentation_events(events):
    """Expand an internal atomic update for existing payload assertions only.

    Queue admission and reducer atomicity are tested against the actual envelope
    in test_atomic_collection_presentation, never this inspection helper.
    """
    for kind, value in events:
        if kind == 'collection_presentation':
            yield from value.events()
        else:
            yield kind, value


def presentation_offsets(events):
    """Map raw queue slice boundaries to logical inspection boundaries."""
    offsets = [0]
    for event in events:
        offsets.append(offsets[-1] + sum(1 for _ in presentation_events((event,))))
    return offsets
