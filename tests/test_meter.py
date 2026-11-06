import math
import struct
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from metrics import Meter, observation_age
from collector import decode_character, SampleUnavailable, gs


def sample(t, damage, session='a', observed_at=0):
    return dict(session=session,game_time=t,rows=[dict(id='char_a',damage=damage)],
                sum_damage=damage,global_total=damage,observed_at=observed_at)


def test_pause_uses_game_clock_not_wall_time():
    m=Meter()
    a=m.update(sample(10,100,observed_at=1))
    b=m.update(sample(10,100,observed_at=101))
    assert a['rows'][0]['dps']==b['rows'][0]['dps']==10
    assert len(m.history)==1
    assert b['rows'][0]['recent_dps'] is None


def test_start_mid_battle_does_not_invent_past_window():
    m=Meter()
    for n in range(10):
        out=m.update(sample(30+n*.5,1000+n*50))
        assert out['rows'][0]['recent_dps'] is None
    out=m.update(sample(35,1500))
    assert out['rows'][0]['recent_dps']==100


def test_speed_does_not_double_dps():
    m=Meter()
    for n in range(11):
        out=m.update(sample(n*.5,n*50,observed_at=n*.25))
    assert out['rows'][0]['recent_dps']==100
    assert out['rows'][0]['dps']==100


def test_new_session_clears_rolling_history():
    m=Meter()
    for n in range(7):m.update(sample(n,n*100))
    out=m.update(sample(1,50,'b'))
    assert out['rows'][0]['dps']==50
    assert out['rows'][0]['recent_dps'] is None


def test_time_rewind_and_counter_reset_clear_history():
    for new in (sample(1,50),sample(7,10)):
        m=Meter()
        for n in range(7):m.update(sample(n,n*100))
        assert m.update(new)['rows'][0]['recent_dps'] is None


def test_missing_window_boundary_is_unknown():
    m=Meter()
    m.update(sample(0,0))
    m.update(sample(4,400))
    assert m.update(sample(6,600))['rows'][0]['recent_dps'] is None


def test_new_operator_not_double_counted():
    m=Meter()
    for n in range(7):
        x=sample(n,n*100)
        if n>=3:
            x['rows'].append(dict(id='char_b',damage=50*(n-2)))
            x['sum_damage']+=50*(n-2)
        out=m.update(x)
    assert len(out['rows'])==2
    assert out['rows'][1]['recent_dps']==40


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
    assert Meter().update(sample(0,0))['rows'][0]['dps'] is None


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
    m=Meter()
    m.update(old)
    result=m.update(new)
    assert result['sum_damage']==373
    assert len(m.history)==1
    assert all(r['recent_dps'] is None for r in result['rows'])
