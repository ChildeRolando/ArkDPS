"""Historical async publications do not predict a new independent sampler serial."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from event_probe import NativePcBinding,ObserverReadRetry,BarrierBusy,verified_autochess_event_window
from native_pc.probe_native_pc_battle import NativePcBattleProbe
from native_pc_collector import NativePcCollector

ROOTS=dict(zip(('controller','native','logger','stats','level'),('0x10000','0x20000','0x30000','0x40000','0x50000')))

def world():
    process=object.__new__(NativePcBattleProbe)
    process.pid=42;process.process_instance=(42,100);process.process_alive=lambda:True
    process._identity=tuple(int(v,16)for v in ROOTS.values())
    process._stage_id='Activities/AUTOCHESS/test';process._serial=5
    process._last_clock=(100,100*(1<<32)//30)
    collector=NativePcCollector(None,collect_actions=False)
    collector.process=process;collector.diagnostics.version_actual='fixture-build'
    return collector,NativePcBinding(collector)

def mark(frame=100,generation=77):
    fixed=frame*(1<<32)//30
    return dict(sequence=10,epoch=20,active=0,active_hooks=[],damage_settlement_scope=1,
        frame=frame,game_time=fixed/(1<<32),native_frame=frame,native_fixed_time=str(fixed),
        native_generation=generation,native_process_pid=42,native_stage_id='Activities/AUTOCHESS/test',native_identity=deepcopy(ROOTS))

def sample(collector,bound):
    stamp=dict(frame=bound['frame'],native_fixed_time=bound['native_fixed_time'],native_identity=deepcopy(ROOTS),stage_id=bound['native_stage_id'])
    digest=bound['native_session'].rsplit(':',1)[-1]
    return dict(frame=bound['frame'],game_time=bound['game_time'],session='host',native_session=bound['native_session'],
        battle_identity=(digest,),battle_metadata=dict(platform='native_pc',source_build='fixture-build',stage_id=bound['native_stage_id']),
        native_window_guard=dict(version=1,source='native_pc_guarded_read',process_instance_before=[42,100],process_instance_after=[42,100],
            before=deepcopy(stamp),after=deepcopy(stamp),battle_serial=collector.process._serial,source_build='fixture-build'))

def committed():
    collector,binding=world();bound=binding.bind('mark',mark())
    assert not binding.generation_sessions and not binding.session_generations
    assert binding.remember_verified_mark(sample(collector,bound),bound)
    assert binding.generation_sessions=={77:bound['native_session']}
    return collector,binding,bound


def test_known_generation_lagging_mark_reuses_serial_but_not_clock():
    collector,binding,bound=committed()
    old=binding.bind('mark',mark(99))
    assert old['native_session']==bound['native_session']
    assert old['frame']==99 and old['native_fixed_time']==mark(99)['native_fixed_time']
    assert collector.process._serial==5
    assert binding.bind('mark',mark())['native_session']==bound['native_session']


def test_unknown_initial_historical_mark_does_not_commit_or_poison():
    collector,binding=world()
    old=binding.bind('mark',mark(99))
    assert not binding.generation_sessions and not binding.session_generations
    assert binding._pending_generation_binding['serial']==collector.process._serial
    assert binding._terminal_cache is None
    fresh=binding.bind('mark',mark())
    assert binding.remember_verified_mark(sample(collector,fresh),fresh)


def test_real_reset_rejects_old_generation_and_accepts_new_after_independent_confirmation():
    collector,binding,bound=committed()
    opening=binding.bind('mark',mark(0,78))
    assert opening['native_session']!=bound['native_session']
    assert 78 not in binding.generation_sessions
    collector.process._serial=6;collector.process._last_clock=(0,0)
    with pytest.raises(ObserverReadRetry):binding.bind('mark',mark(100,77))
    closing=binding.bind('mark',mark(0,78))
    assert closing['native_session']==opening['native_session']
    assert binding.remember_verified_mark(sample(collector,closing),closing)
    assert set(binding.generation_sessions)=={77,78}


@pytest.mark.parametrize('fault',['roots','stage','birth','build','pid','new_generation_same_session'])
def test_bound_generation_does_not_bypass_identity_conflicts(fault):
    collector,binding,_=committed();raw=mark()
    if fault=='roots':raw['native_identity']['stats']='0x60000'
    elif fault=='stage':raw['native_stage_id']='other'
    elif fault=='birth':collector.process.process_instance=(42,101)
    elif fault=='build':collector.diagnostics.version_actual='other'
    elif fault=='pid':raw['native_process_pid']=43
    else:raw['native_generation']=78
    with pytest.raises((RuntimeError,ObserverReadRetry)):binding.bind('mark',raw)
    assert set(binding.generation_sessions)=={77}


def test_unaccepted_mark_cannot_populate_terminal_or_confirmed_maps():
    collector,binding=world();raw=binding.bind('mark',mark())
    wrong=sample(collector,raw);wrong['frame']+=1
    assert not binding.remember_verified_mark(wrong,raw)
    assert not binding.generation_sessions and binding._terminal_cache is None


def test_failed_terminal_mark_does_not_replace_pending_proposal():
    collector,binding=world()
    with pytest.raises(RuntimeError):binding.bind('mark',dict(mark(),terminal_boundary={}))
    assert binding._pending_generation_binding is None and not binding.generation_sessions
    binding.bind('mark',mark());old=deepcopy(binding._pending_generation_binding)
    with pytest.raises(RuntimeError):binding.bind('mark',dict(mark(101),terminal_boundary={}))
    assert binding._pending_generation_binding==old


def test_independent_reset_retires_only_unconfirmed_candidate_then_can_recover():
    collector,binding=world();binding.bind('mark',mark(99))
    collector.process._serial=6;collector.process._last_clock=(0,0)
    with pytest.raises(ObserverReadRetry):binding.bind('mark',mark(0))
    assert binding._pending_generation_binding is None and not binding.generation_sessions
    fresh=binding.bind('mark',mark(0))
    assert binding.remember_verified_mark(sample(collector,fresh),fresh)
    assert binding._generation_bindings[77]['serial']==6


def test_optional_terminal_copy_failure_does_not_erase_independent_generation_commit():
    class Uncopyable:
        def __deepcopy__(self,memo):raise RuntimeError('optional terminal cache unavailable')
    collector,binding=world();bound=binding.bind('mark',mark())
    bound['native_autochess_context']=Uncopyable()
    assert not binding.remember_verified_mark(sample(collector,bound),bound)
    assert binding._terminal_cache is None
    assert binding.generation_sessions[77]==bound['native_session']


@pytest.mark.parametrize('fault',['reverse','forward','birth','stage','build'])
def test_generation_commit_never_overwrites_conflicting_scope_or_bijection(fault):
    collector,binding=world();bound=binding.bind('mark',mark())
    proposal=deepcopy(binding._pending_generation_binding)
    if fault=='reverse':binding.session_generations[bound['native_session']]=999
    elif fault=='forward':binding.generation_sessions[77]='other'
    elif fault=='birth':binding._pending_generation_binding['process_instance']=(42,101)
    elif fault=='stage':binding._pending_generation_binding['stage']='other'
    else:binding._pending_generation_binding['source_build']='other'
    before=(deepcopy(binding.generation_sessions),deepcopy(binding.session_generations))
    binding.remember_verified_mark(sample(collector,bound),bound)
    assert (binding.generation_sessions,binding.session_generations)==before
    assert not binding._generation_bindings


def test_initial_new_roots_retry_still_advances_independent_acquisition():
    from event_capture import CaptureRetry
    from test_pc_serial_clock_reset_end import CollectorClockBattle
    source=CollectorClockBattle();process=source.collector.process
    actual=tuple(int(v,16)for v in source.identity_roots.values())
    process._identity=(*actual[:-1],0x60000);process._stage_id=source.stage
    process._serial=4;process._last_clock=(100,100*(1<<32)//30)
    binding=NativePcBinding(source.collector)
    raw=source.observer.mark
    source.observer.mark=lambda:binding.bind('mark',raw())
    source.observer.remember_verified_mark=binding.remember_verified_mark
    source.advance(2)
    with pytest.raises(CaptureRetry):source.poll()
    assert process._identity==actual and process._serial==5
    assert process._last_clock[0]==60
    assert not binding.generation_sessions
    assert source.poll().kind=='sample'
    assert binding.generation_sessions

def test_unknown_opening_always_lags_sampler_but_valid_closing_window_progresses():
    from test_pc_serial_clock_reset_end import CollectorClockBattle
    source=CollectorClockBattle();process=source.collector.process
    process._identity=tuple(int(v,16)for v in source.identity_roots.values())
    process._stage_id=source.stage;process._serial=5
    process._last_clock=(60,2*(1<<32))
    binding=NativePcBinding(source.collector);raw=source.observer.mark;calls=[]
    def publication():
        value=raw();calls.append(1)
        if len(calls)%2:
            frame=process._last_clock[0]-1;fixed=frame*(1<<32)//30
            value.update(frame=frame,native_frame=frame,native_fixed_time=str(fixed),game_time=fixed/(1<<32))
            value['native_autochess_context'].update(frame=frame,game_time=value['game_time'])
        return binding.bind('mark',value)
    source.observer.mark=publication
    source.observer.remember_verified_mark=binding.remember_verified_mark
    for when in (3,4,5):
        source.advance(when)
        assert source.poll().kind=='sample'
        assert process._serial==5
    assert source.runtime.sessions.latest_sample['frame']==150
    assert binding.generation_sessions
