import math
import struct
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from metrics import observation_age
from stats_core import Statistics
from panel_model import Category, RateBasis, rows_from_statistics
from collector import decode_character, SampleUnavailable, gs


def sample(t, damage, session='a', observed_at=0):
    return dict(session=session,game_time=t,rows=[dict(id='char_a',damage=damage)],
                sum_damage=damage,global_total=damage,observed_at=observed_at)


def native_stats(start=0, session='a'):
    stats = Statistics(session, start)
    stats.register_source('char_a', 'char_a')
    return stats


def native_snapshot(stats, when, damage, extra=None):
    counters = {'char_a': dict(damage=damage, healing=0)}
    if extra:
        for source, amount in extra.items():
            stats.register_source(source, source)
            counters[source] = dict(damage=amount, healing=0)
    stats.snapshot(when, counters)
    return stats.result()['rows']


def test_pause_uses_game_clock_not_wall_time():
    stats = native_stats(10)
    a = native_snapshot(stats, 10, 100)['char_a']
    b = native_snapshot(stats, 10, 100)['char_a']
    assert a['damage'] == b['damage'] == 100
    assert len(stats.sources['char_a']['history']) == 1
    assert b['recent_damage'] is None
    # The product additionally requires proved presence for full-cycle DPS.
    assert rows_from_statistics(stats.result())[0].rate(Category.DAMAGE, RateBasis.FULL_CYCLE) is None


def test_start_mid_battle_does_not_invent_past_window():
    stats = native_stats(30)
    for n in range(10):
        rows = native_snapshot(stats, 30+n*.5, 1000+n*50)
        assert rows['char_a']['recent_damage'] == (None if n == 0 else 100)
        assert rows['char_a']['recent_damage_window'] == (None if n == 0 else (30, 30+n*.5))
    assert native_snapshot(stats, 35, 1500)['char_a']['recent_damage'] == 100


def test_speed_does_not_double_dps():
    stats = native_stats()
    for n in range(11):
        rows = native_snapshot(stats, n*.5, n*50)
    assert rows['char_a']['recent_damage'] == 100
    # No wall-clock input enters the production Statistics.snapshot contract.
    assert rows_from_statistics(stats.result())[0].rate(Category.DAMAGE, RateBasis.RECENT_5S) == 100


def test_new_session_clears_rolling_history():
    old = native_stats()
    for n in range(7): native_snapshot(old, n, n*100)
    current = native_stats(1, 'b')
    rows = native_snapshot(current, 1, 50)
    assert current.session != old.session and rows['char_a']['damage'] == 50
    assert rows['char_a']['recent_damage'] is None


def test_time_rewind_and_counter_reset_clear_history():
    for when, damage in ((1, 50), (7, 10)):
        old = native_stats()
        for n in range(7): native_snapshot(old, n, n*100)
        # A changed epoch is established by the lifecycle owner. The canonical
        # ledger rejects reusing an old epoch instead of silently rewriting it.
        with pytest.raises(ValueError): native_snapshot(old, when, damage)
        current = native_stats(when, 'new-epoch')
        assert native_snapshot(current, when, damage)['char_a']['recent_damage'] is None


def test_missing_window_boundary_is_unknown():
    stats = native_stats()
    native_snapshot(stats, 0, 0)
    native_snapshot(stats, 6, 600)
    assert native_snapshot(stats, 8, 800)['char_a']['recent_damage'] is None


def test_new_operator_not_double_counted():
    stats = native_stats()
    for n in range(7):
        rows = native_snapshot(stats, n, n*100, {'char_b': 50*(n-2)} if n>=3 else None)
    assert len(rows) == 2
    assert rows['char_a']['recent_damage'] == 100
    # A later source keeps its observed rate but lacks a full-window certificate.
    assert rows['char_b']['recent_damage'] == 50
    assert rows['char_b']['recent_damage_window'] is None
    assert rows['char_b']['damage'] == 200


def blocks(values):
    name='char_a'
    key=bytearray(256);struct.pack_into('<i',key,0x10,len(name))
    key[0x14:0x14+len(name)*2]=name.encode('utf-16-le')
    summary=bytearray(gs.CharAdvancedStatsFields.READ_SIZE)
    struct.pack_into('<f',summary,gs.CharAdvancedStatsFields.OUTPUT_DAMAGE_TOTAL,-999)
    head=bytearray(32);struct.pack_into('<i',head,gs.ListInternal.SIZE,6)
    return key,summary,head,struct.pack('<6f',*values)


def test_healing_excluded_from_damage_and_no_raw_fallback():
    row=decode_character(*blocks([0,10,20,30,500,40]),{})
    assert row['damage']==100
    assert row['healing']==500
    assert row['raw_output']==-999


@pytest.mark.parametrize('invalid',[float('nan'),float('inf'),-10])
def test_invalid_damage_is_rejected_not_zero_filled(invalid):
    with pytest.raises(SampleUnavailable):
        decode_character(*blocks([0,invalid,0,0,0,0]),{})


def test_zero_time_has_no_division_by_zero():
    stats = native_stats()
    native_snapshot(stats, 0, 0)
    assert rows_from_statistics(stats.result())[0].rate(Category.DAMAGE, RateBasis.FULL_CYCLE) is None


def test_delayed_queue_cannot_refresh_old_observation():
    old=dict(captured_monotonic=100)
    assert observation_age(old,now=130)==30
    assert observation_age(old,now=131)==31
    assert math.isinf(observation_age({}))


@pytest.mark.parametrize('force',[False,True])
def test_explicit_reconnect_bypasses_still_allocated_hint(tmp_path,force):
    import json
    from types import SimpleNamespace
    from collector import Collector
    c=Collector.__new__(Collector)
    c.log=lambda _:None
    c.cache_path=tmp_path/'hint.json'
    c.cache_path.write_text(json.dumps(dict(pid=1,birth=['boot',2],bc=123)))
    scans=[]
    mc=SimpleNamespace(pid=1,process_instance=('boot',2),package='com.hypergryph.arknights',
        shell=lambda *a,**kw:'versionName=2.7.91',channel=lambda:object(),
        is_ptr=lambda p:bool(p),read_klass_name=lambda p:'BattleController',read_ptr=lambda p:789)
    c.core=SimpleNamespace(mc=mc,bc_addr=0,connect=lambda:None,_resolve_battle_clock=lambda:True)
    def scan():
        scans.append(True)
        c.core.bc_addr=456
        return True
    c._valid_battle=lambda bc:bc==123
    c._locate_active_battle=scan
    c.connect(force=force)
    assert c.core.bc_addr==(456 if force else 123)
    assert bool(scans)==force


@pytest.mark.parametrize('native,managed,owner',[(0,123,123),(789,456,123),(789,123,456)])
def test_stale_battle_ownership_is_rejected(native,managed,owner):
    from collector import check_battle_ownership, BattleChanged
    with pytest.raises(BattleChanged):
        check_battle_ownership(123,native,managed,owner)


@pytest.mark.parametrize('valid,expected',[((),None),((123,456),None),((456,),456)])
def test_discovery_requires_one_owned_battle(monkeypatch,valid,expected):
    from types import SimpleNamespace
    import collector
    c=collector.Collector.__new__(collector.Collector)
    c.core=SimpleNamespace(mc=object(),_chan=object(),bc_addr=0)
    c.log=lambda _:None
    c._valid_battle=lambda bc:bc in valid
    locator=SimpleNamespace(set_status_callback=lambda _:None,
        _scan_class_objects=lambda _: {'BattleController':[123,456]},
        _battle_controller_candidates=lambda _: [123,456])
    monkeypatch.setattr(collector,'DeployTrackerReader',lambda _:locator)
    if expected is None:
        with pytest.raises(SampleUnavailable):c._locate_active_battle()
        assert c.core.bc_addr==0
    else:
        c._locate_active_battle()
        assert c.core.bc_addr==expected


def test_live_redeploy_and_new_battle_evidence():
    import json
    evidence=Path(__file__).resolve().parents[1]/'acceptance'
    old=json.loads((evidence/'redeploy-observation.json').read_text(encoding='utf-8'))['sample']
    new=json.loads((evidence/'new-battle-observation.json').read_text(encoding='utf-8'))['sample']
    assert [r['damage'] for r in old['rows'] if r['id']=='char_502_nblade']==[2560]
    assert not [r for r in new['rows'] if r['id']=='char_502_nblade']
    assert new['session']!=old['session']
    stats = Statistics(new['session'], new['game_time'])
    for row in new['rows']: stats.register_source(row['id'], row['id'])
    stats.snapshot(new['game_time'], {row['id']: dict(damage=row['damage'], healing=row.get('healing', 0)) for row in new['rows']})
    result = stats.result()
    assert sum(row['damage'] for row in result['rows'].values()) == 373
    assert all(len(source['history']) == 1 for source in stats.sources.values())
    assert all(row['recent_damage'] is None for row in result['rows'].values())
