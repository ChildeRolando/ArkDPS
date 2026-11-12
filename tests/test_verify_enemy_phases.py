from acceptance.verify_enemy_phases import summarize


def callback(sequence, method, before, after, *, reason=0):
    return {
        'kind': 'callback', 'sequence': sequence, 'frame': sequence * 30,
        'game_time': sequence, 'method': method, 'uid': 42, 'eid': 'boss',
        'before': {'hp': before}, 'after': {
            'hp': after, 'max_hp': 60000, 'finish_reason': reason,
        },
    }


def test_reborn_phase_uses_actual_starting_hp_even_when_already_recharged():
    trace = [
        callback(1, 'Enemy.Born', 60000, 60000),
        callback(2, 'Enemy.DoFakeDeath', 0, 0),
        callback(3, 'Enemy.OnReborn', 0, 0),
        callback(4, 'Unit.DoReborn', 60000, 60000),
        callback(5, 'Enemy.DoReborn', 60000, 60000),
        callback(6, 'Entity.ApplyModifier', 10000, 12000),
        callback(7, 'Enemy.OnFinish', 0, 0, reason=2),
    ]
    boss, = summarize(trace, eid='boss')['enemies']
    assert boss['observed_total_hp'] == 120000
    assert [phase['frame'] for phase in boss['phases']] == [30, 150]
    assert boss['complete']


def test_exit_does_not_confirm_all_phases():
    trace = [
        callback(1, 'Enemy.Born', 60000, 60000),
        callback(2, 'Enemy.OnFinish', 60000, 0, reason=1),
    ]
    boss, = summarize(trace, eid='boss')['enemies']
    assert boss['observed_total_hp'] == 60000
    assert not boss['complete']
