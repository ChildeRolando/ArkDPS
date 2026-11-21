"""Reject an unverified PC mode hint in display projections, not raw archives."""
import json

import pytest

from battle_history import BattleRecord
from battle_share import json_bytes, record_display
from connection import ConnectionConfig
from desktop_bridge import DesktopState
from test_battle_history import record


@pytest.mark.parametrize('practice', [True, False, None])
@pytest.mark.parametrize('evidence,sources', [
    ('native_pc_guarded_memory+native_pc_squad_loader+native_pc_game_flow', []),
    ('native_pc_game_flow', ['native_pc_guarded_memory+native_pc_squad_loader']),
    ('native_pc_guarded_memory', ['native_pc_squad_loader', 'native_pc_game_flow']),
])
def test_old_pc_loader_hint_only_changes_display_practice(practice, evidence, sources):
    original = record(platform='native_pc', practice=practice, outcome='victory',
                      stage_id='Obt/Main/level_main_01-07', evidence=evidence,
                      evidence_sources=sources)
    raw = original.to_dict()
    expected = dict(raw['metadata'], practice=None)
    assert original.display_metadata()['metadata'] == expected
    display = record_display(original)
    assert display['labels'][1] == '演习状态未知'
    assert display['labels'][2] == '胜利'
    assert display['stage_raw'] == 'Obt/Main/level_main_01-07'
    assert display['source_label'] == '官服 PC'
    assert original.metadata == raw['metadata']
    assert original.to_dict() == raw
    exported = json.loads(json_bytes(original))
    exported.pop('display')
    assert exported == raw


@pytest.mark.parametrize('evidence', [
    'native_pc_squad_loader_extra', 'prefix_native_pc_squad_loader',
    'native_pc_squad_loader:v2', 'native_pc_squad_loader ',
    'native_pc_guarded_memory+native_pc_game_flow',
])
def test_only_an_exact_evidence_component_triggers_display_guard(evidence):
    original = record(platform='native_pc', practice=True, evidence=evidence,
                      evidence_sources=[evidence])
    assert original.display_metadata()['metadata']['practice'] is True
    assert record_display(original)['labels'][1] == '演习'


@pytest.mark.parametrize('platform', ['android', None])
@pytest.mark.parametrize('practice,label', [(True, '演习'), (False, '正式'), (None, '演习状态未知')])
def test_established_android_modes_and_unscoped_records_remain_unchanged(platform, practice, label):
    original = record(practice=practice, evidence='native_pc_squad_loader',
                      **({'platform': platform} if platform else {}))
    assert original.display_metadata()['metadata'] == original.metadata
    assert record_display(original)['labels'][1] == label


def test_loaded_old_pc_archive_is_unknown_in_both_history_surfaces_without_rewrite(tmp_path):
    original = record(platform='native_pc', practice=True, outcome='abandoned',
                      stage_id='Obt/Main/level_main_01-07',
                      evidence='native_pc_guarded_memory+native_pc_squad_loader',
                      evidence_sources=['native_pc_guarded_memory+native_pc_squad_loader',
                                        'native_pc_game_flow'])
    raw = json.dumps(original.to_dict(), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    state = DesktopState(tmp_path)
    path = state.history.path_for(original.battle_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    state.refresh_history()
    for platform in ('mumu', 'native_pc'):
        state.effective_connection = ConnectionConfig(platform=platform)
        snapshot = state.snapshot()
        for item in (snapshot['history'][0], snapshot['history_selected']):
            assert item['labels'][1] == '演习状态未知'
            assert item['labels'][2] == '退出'
            assert item['stage_raw'] == 'Obt/Main/level_main_01-07'
        assert path.read_bytes() == raw
    assert BattleRecord.from_dict(json.loads(path.read_bytes())).metadata['practice'] is True
