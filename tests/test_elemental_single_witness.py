from stats_core import Statistics


def test_modern_bar_has_one_store_and_settlement_consumes_it_once():
    stats = Statistics('scope', 0)
    stats.settlement_enabled = True
    stats.damage_settlement_version = 7
    stats.record_settlement_bar('7', 'enemy_test', 'native', 'local', 'owner', 1)
    assert 'last_elemental' not in vars(stats)
    assert stats.last_elemental == {'native': 'owner'}
    stats._discard_elemental_instance('native', legacy=True)
    assert '7' in stats.settlement_bars
    stats.record_damage_settlement(1, 'unknown', None, None, None,
        'enemy_test', '7', 'native', 50, 0, True, 5, 60, None, None, killed=True)
    assert not stats.settlement_bars
    assert stats.settlement_supplements == {'owner': 60}
    stats.record_damage_settlement(1, 'unknown', None, None, None,
        'enemy_test', '7', 'native', 50, 0, True, 5, 60, None, None, killed=True)
    assert stats.settlement_supplements == {'owner': 60}
    assert stats.settlement_unassigned['hits'] == 1
