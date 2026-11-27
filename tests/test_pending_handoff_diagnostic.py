"""Diagnostic branch isolation; real raw-contract integration remains in pending_scope_start."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import battle_lifecycle as lifecycle
from event_probe import (StreamGap, BarrierBusy, annotate_pending_handoff_failure,
    sanitize_pending_handoff_failure, sanitize_stream_gap_failure, stream_gap_failure_context)


def owned_case(monkeypatch, reset=False):
    """Only bypass upstream validators to isolate this validator's exact branches."""
    def fence():
        return dict(sequence=3, epoch=4, frame=20, game_time=2., native_generation=5,
            active=0, active_hooks=[], stopped=False, error=None, retry=False,
            native_battle_state=2, native_identity={'root':'PRIVATE_ADDRESS'}, native_process_pid=12,
            damage_settlement_scope=1, enemy_damage_unknown=False, damage_unknown=False,
            damage_settlement_unlocalized_unknown=False, damage_settlement_source_issues=[])
    previous=dict(native_session='PRIVATE_SESSION',battle_identity=('PRIVATE_ID',),frame=20,game_time=2.)
    current=dict(native_session=previous['native_session'],battle_identity=previous['battle_identity'],frame=30,game_time=3.)
    old=SimpleNamespace(match='PRIVATE_MATCH',collection_mode='all',scope='PRIVATE_OLD')
    new=SimpleNamespace(match=old.match,collection_mode='all',scope='PRIVATE_NEW')
    anchor=dict(sequence=3,after=fence());latest=dict(before=fence(),after=fence(),sequence=3,drain={})
    successor=dict(before=fence(),after=fence())
    case=SimpleNamespace(anchor=anchor,latest=latest,successor=successor,previous=previous,current=current,
        old=old,new=new,reset=reset,round_end=True,quality=True)
    if reset:
        for f in (successor['before'],successor['after']):f['native_generation']=6
    monkeypatch.setattr(lifecycle,'_verified_pending_scope_anchor',lambda value,**kw:(previous,None,None))
    monkeypatch.setattr(lifecycle,'verified_pending_scope_start',lambda *a,**kw:None)
    monkeypatch.setattr(lifecycle,'verified_pending_scope_successor',lambda *a:(current,None))
    monkeypatch.setattr(lifecycle,'autochess_scene',lambda sample:case.old if sample is previous else case.new)
    monkeypatch.setattr(lifecycle,'_native_pc_guarded_clock_reset',lambda *a:case.reset)
    monkeypatch.setattr(lifecycle,'_autochess_successor_ends_round',lambda *a:case.round_end)
    monkeypatch.setattr(lifecycle,'_same_autochess_boundary_quality',lambda *a:case.quality)
    return case


TAIL_CASES=[('sequence_type','sequence',True),('sequence_changed','sequence',4),
    ('epoch_type','epoch',True),('epoch_changed','epoch',5),('active_type','active',True),
    ('active_nonzero','active',1),('active_hooks','active_hooks',['PRIVATE_HOOK']),('stopped','stopped',True),
    ('error','error','PRIVATE_ERROR'),('retry','retry',True),('terminal','terminal_boundary',{'PRIVATE':'ADDRESS'}),
    ('native_state_type','native_battle_state',True),('native_state','native_battle_state',3),
    ('quiet_boundary','native_quiet_boundary_version',1),('deferred_callbacks','native_deferred_callbacks',[])]


@pytest.mark.parametrize('condition,key,value',TAIL_CASES)
@pytest.mark.parametrize('edge',['before','after'])
def test_tail_first_condition_and_owned_values(monkeypatch,condition,key,value,edge):
    case=owned_case(monkeypatch);case.successor[edge][key]=value
    with pytest.raises(StreamGap,match='unread tail or nonquiet source')as caught:
        lifecycle._verified_pending_empty_handoff(case.anchor,case.latest,case.successor)
    detail=stream_gap_failure_context(caught.value,'acquire')
    assert detail['handoff']['condition']==condition
    assert detail['handoff']['fence']=='successor_'+edge
    assert detail['reason']=='pending_handoff_tail'
    assert sanitize_stream_gap_failure(detail)==detail
    assert 'PRIVATE' not in json.dumps(detail)


OTHER_CASES=[('old_scene_missing','old',None),('new_scene_missing','new',None),
    ('match_changed','new.match','other'),('collection_changed','new.collection_mode','local'),
    ('scope_unchanged','new.scope','PRIVATE_OLD'),('frame_changed','successor.after.frame',21),
    ('game_time_changed','successor.after.game_time',2.1),('native_generation_type','latest.after.native_generation',None),
    ('successor_round','round_end',False),('session_changed','current.native_session','other'),
    ('battle_identity_changed','current.battle_identity',('other',)),('frame_not_advanced','current.frame',20),
    ('time_not_advanced','current.game_time',2.),('source_generation_changed','successor.before.native_generation',6),
    ('source_identity_changed','successor.before.native_identity',{}),
    ('settlement_scope_type','successor.before.damage_settlement_scope',True),
    ('settlement_scope_changed','successor.before.damage_settlement_scope',2),('boundary_quality','quality',False)]
RESET_CASES=[('reset_anchor_epoch','anchor.after.epoch',5),
    ('reset_generation_type','successor.before.native_generation',None),
    ('reset_generation_changed','successor.before.native_generation',7),
    ('reset_identity_changed','successor.before.native_identity',{}),
    ('reset_pid_changed','successor.before.native_process_pid',99),
    ('enemy_damage_unknown','anchor.after.enemy_damage_unknown',True),
    ('damage_unknown','latest.after.damage_unknown',True),
    ('unlocalized_unknown','successor.before.damage_settlement_unlocalized_unknown',True),
    ('source_issues','successor.after.damage_settlement_source_issues',[{'private':'secret'}])]


def change(case,path,value):
    parts=path.split('.');target=case
    for key in parts[:-1]:target=target[key]if isinstance(target,dict)else getattr(target,key)
    if isinstance(target,dict):target[parts[-1]]=value
    else:setattr(target,parts[-1],value)


@pytest.mark.parametrize('reset,cases',[(False,OTHER_CASES),(True,RESET_CASES)])
def test_other_rejections_have_finite_first_conditions(monkeypatch,reset,cases):
    for condition,path,value in cases:
        with monkeypatch.context()as local:
            case=owned_case(local,reset=reset);change(case,path,value)
            with pytest.raises(StreamGap)as caught:
                lifecycle._verified_pending_empty_handoff(case.anchor,case.latest,case.successor)
            detail=stream_gap_failure_context(caught.value,'acquire')
            assert detail['handoff']['condition']==condition
            assert 'PRIVATE' not in json.dumps(detail)


def test_first_condition_keeps_original_short_circuit_order(monkeypatch):
    case=owned_case(monkeypatch);case.latest['after']['sequence']=9
    case.successor['before'].update(sequence=7,active=9)
    with pytest.raises(StreamGap)as caught:lifecycle._verified_pending_empty_handoff(case.anchor,case.latest,case.successor)
    assert caught.value.handoff_failure['condition']=='sequence_changed'
    assert caught.value.handoff_failure['fence']=='latest_after'


def test_missing_source_values_are_unknown_and_no_busy_is_upgraded():
    error=annotate_pending_handoff_failure(StreamGap('unclassified'),'unknown','none',{},None,{})
    detail=stream_gap_failure_context(error,'acquire')
    assert all(v is None for point in detail['handoff']['points'].values()for v in point.values())
    busy=annotate_pending_handoff_failure(BarrierBusy('typed busy'),'unknown','none',{},None,{})
    assert isinstance(busy,BarrierBusy)and not hasattr(busy,'handoff_failure')
    assert stream_gap_failure_context(busy,'acquire')is None


def test_old_schema_and_invalid_optional_detail_keep_base_reason_and_frames():
    base=dict(version=1,reason='pending_handoff_tail',stage='acquire',frames=[])
    assert sanitize_stream_gap_failure(base)==base
    good=annotate_pending_handoff_failure(StreamGap('unclassified'),'unknown','none',{},None,{}).handoff_failure
    for bad in [None,{},dict(good,condition='PRIVATE'),dict(good,version=True),dict(good,private='SECRET')]:
        assert sanitize_stream_gap_failure(dict(base,handoff=bad))==base
    for key,value in [('sequence',True),('epoch',-1),('frame',2**64),('has_error',1)]:
        bad=deepcopy(good);bad['points']['latest_after'][key]=value
        assert sanitize_pending_handoff_failure(bad)is None
        assert sanitize_stream_gap_failure(dict(base,handoff=bad))==base


@pytest.mark.parametrize('debug',[False,True])
def test_normal_and_debug_journal_share_same_handoff_sanitizer(tmp_path,debug):
    from desktop_audit import DesktopCaptureJournal
    from capture_cli import json_value
    detail=stream_gap_failure_context(annotate_pending_handoff_failure(StreamGap('unclassified'),
        'unknown','none',{},None,{}),'acquire')
    with DesktopCaptureJournal(tmp_path,encoder=json_value,warn=pytest.fail,debug_mode=debug,
        header=lambda *a,**kw:dict(kind='log_header',data={}))as journal:
        journal.write('capture_error',dict(error_type='StreamGap',stream_gap_failure=detail))
        bad=deepcopy(detail);bad['handoff']['private']='SECRET'
        journal.write('capture_error',dict(error_type='StreamGap',stream_gap_failure=bad))
    rows=[json.loads(l)for l in next(tmp_path.glob('wpf-*.jsonl')).read_text(encoding='utf-8').splitlines()]
    assert rows[1]['data']['stream_gap_failure']==detail
    assert rows[2]['data']['stream_gap_failure']=={k:v for k,v in detail.items()if k!='handoff'}
    assert 'SECRET'not in json.dumps(rows)
