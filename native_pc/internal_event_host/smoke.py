"""Exercise the compiled event host without starting the game."""

import json
from pathlib import Path
import subprocess
import sys

from build import DEFAULT_OUTPUT


def watermark(sequence, **fields):
    return dict(sequence=sequence, native_session='battle-a', source_build='build-a', **fields)


def advance(mark, events, terminal=None):
    return dict(op='advance', mark=mark, events=events, terminal_mark=terminal)


def query(op, **fields):
    return dict(op=op, **fields)


def run_host(binary, requests):
    wire = b''.join((json.dumps(dict(request, id=index), ensure_ascii=False) + '\n').encode('utf-8')
                    for index, request in enumerate(requests))
    process = subprocess.run([str(binary)], input=wire, capture_output=True, check=True, timeout=20)
    replies = [json.loads(line) for line in process.stdout.decode('utf-8').splitlines()]
    assert not process.stderr, process.stderr
    assert [reply.get('id') for reply in replies] == list(range(len(requests))), replies
    return replies


def check_protocol(binary):
    first = watermark(5, frame=20, game_time=2)
    second = watermark(6, frame=30, game_time=3)
    event = dict(sequence=6, frame=30, game_time=3, kind='healing', source='闪灵', amount=120)
    conflicting = dict(event, amount=121)
    wrong_stream = dict(second, sequence=7, native_session='battle-b')
    replies = run_host(binary, [
        query('hello'), query('mark'), query('terminal'), query('read', sequence=0),
        advance(first, []), advance(second, [event, event]),
        query('read', sequence=5), query('read', sequence=6), query('read', sequence=7),
        query('commit', sequence=7), query('commit', sequence=6), query('commit', sequence=6),
        advance(second, [event], second), query('read', sequence=6), query('terminal'),
        advance(second, [conflicting]), advance(wrong_stream, []),
        advance(watermark(8), [dict(event, sequence=8)]), query('mark'), query('terminal'),
        advance(watermark(6, frame=30, game_time=-1), []), query('stop'),
    ])
    assert replies[0]['result'] == {'protocol': 1}
    assert [replies[index]['result'] for index in (1, 2)] == [{}, {}]
    assert 'no watermark' in replies[3]['error']
    assert replies[4]['result'] is True and replies[5]['result'] is True
    assert replies[6]['result'] == dict(second, events=[])
    assert replies[7]['result'] == dict(second, events=[event])
    assert 'exceeds' in replies[8]['error']
    assert replies[9]['result'] is False
    assert all(replies[index]['result'] is True for index in (10, 11, 12))
    assert replies[13]['result'] == dict(second, events=[])
    assert replies[14]['result'] == second
    assert 'conflicting' in replies[15]['error']
    assert 'stream identity' in replies[16]['error']
    assert 'sequence gap' in replies[17]['error']
    assert replies[18]['result'] == second and replies[19]['result'] == second
    assert 'invalid game time' in replies[20]['error']
    assert replies[21]['result'] is True


def check_capacity(binary):
    full = [{'sequence': sequence} for sequence in range(1, 4097)]
    last = [{'sequence': 4097}]
    replies = run_host(binary, [
        advance(watermark(4096), full), advance(watermark(4097), last), query('mark'),
        query('commit', sequence=1), advance(watermark(4097), last),
        query('read', sequence=3), query('commit', sequence=1), query('stop'),
    ])
    assert replies[0]['result'] is True
    assert 'capacity' in replies[1]['error']
    assert replies[2]['result'] == watermark(4096)
    assert replies[3]['result'] is True and replies[4]['result'] is True
    assert replies[5]['result'] == dict(watermark(4097), events=[{'sequence': 2}, {'sequence': 3}])
    assert replies[6]['result'] is True and replies[7]['result'] is True


if __name__ == '__main__':
    executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_OUTPUT
    check_protocol(executable)
    check_capacity(executable)
    print('Native event host protocol and queue smoke passed')
