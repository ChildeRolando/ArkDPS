"""Exercise the compiled event host without starting the game."""

import json
from pathlib import Path
import subprocess
import sys

from build import DEFAULT_OUTPUT


def watermark(sequence, **fields):
    return dict(dict(sequence=sequence, epoch=sequence, active=0, native_session='battle-a', source_build='build-a',
                     frame=30, game_time=3), **fields)


def advance(mark, events, terminal=None):
    return dict(op='advance', mark=mark, events=events, terminal_mark=terminal)


def query(op, **fields):
    return dict(op=op, **fields)


def run_host(binary, requests):
    requests = [dict(dict(version=1, id=index), **request)
                for index, request in enumerate(requests, start=1)]
    wire = b''.join((json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8') for request in requests)
    process = subprocess.run([str(binary)], input=wire, capture_output=True, check=True, timeout=20)
    replies = [json.loads(line) for line in process.stdout.decode('utf-8').splitlines()]
    assert not process.stderr, process.stderr
    assert [reply.get('id') for reply in replies] == [request['id'] for request in requests], replies
    assert all(type(reply.get('version')) is int and reply['version'] == 1 for reply in replies)
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
    assert replies[0]['result'] == {'protocol': 1, 'provider': 'internal-test-host', 'source_version': 1}
    assert [replies[index]['result'] for index in (1, 2)] == [{}, {}]
    assert 'no watermark' in replies[3]['error']
    assert replies[4]['result'] is True and replies[5]['result'] is True
    assert replies[6]['result'] == dict(second, events=[])
    assert replies[7]['result'] == dict(second, events=[event])
    assert 'exceeds' in replies[8]['error']
    assert replies[9]['result'] == {'acknowledged': None}
    assert replies[10]['result'] == replies[11]['result'] == {'acknowledged': 6}
    assert replies[12]['result'] is True
    assert replies[13]['result'] == dict(second, events=[])
    assert replies[14]['result'] == second
    assert 'conflicting' in replies[15]['error']
    assert 'stream identity' in replies[16]['error']
    assert 'finished stream' in replies[17]['error']
    assert replies[18]['result'] == second and replies[19]['result'] == second
    assert 'invalid game time' in replies[20]['error']
    assert replies[21]['result'] is True


def check_capacity(binary):
    full = [dict(sequence=sequence, frame=30, game_time=3) for sequence in range(1, 4097)]
    last = [dict(sequence=4097, frame=30, game_time=3)]
    replies = run_host(binary, [
        advance(watermark(4096), full), advance(watermark(4097), last), query('mark'),
        query('commit', sequence=1), query('read', sequence=1), query('commit', sequence=1),
        advance(watermark(4097), last), query('read', sequence=3),
        query('commit', sequence=1), query('stop'),
    ])
    assert replies[0]['result'] is True
    assert 'capacity' in replies[1]['error']
    assert replies[2]['result'] == watermark(4096)
    assert replies[3]['result'] == {'acknowledged': None}
    assert replies[4]['result'] == dict(watermark(4096), events=full[:1])
    assert replies[5]['result'] == {'acknowledged': 1} and replies[6]['result'] is True
    assert replies[7]['result'] == dict(watermark(4097), events=full[1:3])
    assert replies[8]['result'] == {'acknowledged': 1} and replies[9]['result'] is True


def check_replay_and_validation(binary):
    mark = watermark(0)
    event = dict(sequence=1, frame=40, game_time=4, amount=120)
    current = dict(mark, sequence=1, epoch=1, frame=40, game_time=4)
    next_mark = dict(current, sequence=2, epoch=2, frame=50, game_time=5)
    replies = run_host(binary, [
        query('hello', version=2), advance(mark, []),
        advance(next_mark, [dict(event, sequence=2)]),
        advance(current, [dict(event, game_time=3)]),
        advance(current, [event], dict(current, sequence=0)),
        advance(current, [event]), query('read', sequence=1), query('read', sequence=1),
        query('commit', sequence=1), query('commit', id=9, sequence=1),
        query('commit', id=9, sequence=0), advance(current, [dict(event, amount=120.0)]),
        advance(dict(current, epoch=0), []), advance(current, [], dict(current, active=1)),
        query('terminal'), query('mark'), query('stop'),
    ])
    assert 'version' in replies[0]['error']
    assert replies[1]['result'] is True
    assert 'sequence gap' in replies[2]['error']
    assert 'different game time' in replies[3]['error']
    assert 'terminal does not match' in replies[4]['error']
    assert replies[5]['result'] is True
    assert replies[6]['result'] == replies[7]['result'] == dict(current, events=[event])
    assert replies[8] == replies[9]
    assert 'conflicting request replay' in replies[10]['error']
    assert 'conflicting event replay' in replies[11]['error']
    assert 'generation went backwards' in replies[12]['error']
    assert 'callback activity' in replies[13]['error']
    assert replies[14]['result'] == {} and replies[15]['result'] == current
    assert replies[16]['result'] is True


if __name__ == '__main__':
    executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_OUTPUT
    check_protocol(executable)
    check_capacity(executable)
    check_replay_and_validation(executable)
    print('Native event host protocol and queue smoke passed')
