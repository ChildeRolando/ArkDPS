from test_battle_share import history_panel
"""Historical source labels use stored route metadata, never the current source."""
import json

import pytest

from autochess_context import NATIVE_AUTOCHESS_CHARACTER_COUNTERS
from battle_history import BattleRecord
from battle_share import json_bytes, record_display
from connection import ConnectionConfig
from desktop_bridge import DesktopState, PROTOCOL_VERSION
from panel_model import PanelConfig, Ranking, RateBasis
from test_battle_history import record


@pytest.mark.parametrize('platform,label', [
    ('native_pc', 'PC'), ('android', 'Android（ADB）'),
    (None, '来源未记录'), ('unknown', '来源未记录'),
])
def test_display_source_uses_explicit_route_and_preserves_raw_record(platform, label):
    original = record(practice=True, **({'platform': platform} if platform else {}))
    raw = original.to_dict()
    display = record_display(original)
    assert display['source_label'] == label
    assert display['labels'][1] == '演习'
    assert display['labels'][2] == '结果未知'
    assert display['labels'][-1] == label
    assert len(display['labels']) == 6
    assert display.get('platform') == (platform if platform in ('native_pc', 'android') else None)
    assert original.to_dict() == raw
    exported = json.loads(json_bytes(original))
    exported.pop('display')
    assert exported == raw


def test_legacy_android_record_shape_remains_unrecorded_after_source_switch(tmp_path):
    # Match the source-relevant shape of a loaded legacy Android 2.7.91 archive,
    # without checking a full live record into the repository. Neither Android
    # evidence nor native_session identifies a particular emulator route.
    data = record(stage_id='Obt/Main/level_main_00-01', practice=True,
                  evidence='native.LevelData.levelId:Android2.7.91').to_dict()
    data['metadata'].pop('platform', None)
    data['last_sample']['native_session'] = 'unscoped-native-session'
    data['last_sample']['battle_metadata'] = dict(data['metadata'])
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    original = BattleRecord.from_dict(data)
    assert 'Android2.7.91' in original.metadata['evidence']
    assert 'platform' not in original.metadata
    assert 'native_session' in original.last_sample

    state = DesktopState(tmp_path)
    archive_path = state.history.path_for(original.battle_id)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(raw)
    state.refresh_history()
    for platform in ('native_pc', 'mumu'):
        state.effective_connection = ConnectionConfig(platform=platform)
        snapshot = state.snapshot()
        for item in (snapshot['history'][0], snapshot['history_selected']):
            assert item['source_label'] == '来源未记录'
            assert 'platform' not in item
            assert item['labels'][1] == '演习'
            assert item['labels'][-1] == '来源未记录'
        assert '来源未记录' in snapshot['history'][0]['search']
        assert archive_path.read_bytes() == raw


@pytest.mark.parametrize('platform,counter_scope', [('native_pc', None), ('android', None),
                                                       ('native_pc', NATIVE_AUTOCHESS_CHARACTER_COUNTERS)])
def test_autochess_history_missing_player_scope_keeps_raw_record(tmp_path, platform, counter_scope):
    unverified = platform == 'native_pc'
    archived = record(platform=platform, stage_id='Activities/ACT3AUTOCHESS/level_act3autochess_m03',
                      outcome='victory', practice=True).to_dict()
    archived['last_sample'].update(native_counter_scope=counter_scope, rows=[
        dict(id='char_328_cammou', damage=482, healing=0, physical=482),
        dict(id='duplicate', damage=800, healing=0),
        dict(id='duplicate', damage=900, healing=0),
        dict(id='invalid:cid', damage=1000, healing=0),
        dict(id='invalid_counters', damage=-1000, healing=False),
    ])
    original = BattleRecord.from_dict(archived)
    raw = json.dumps(archived, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    state = DesktopState(tmp_path)
    archive_path = state.history.path_for(original.battle_id)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(raw)
    state.refresh_history()

    snapshot = state.snapshot()
    index, detail = snapshot['history'][0], snapshot['history_selected']
    standalone = history_panel(original, PanelConfig())
    expected = ('char_328_cammou', 482) if unverified and not counter_scope else ('a', 230)
    assert [(row['owner_id'], row['value']) for row in detail['view']['entries']] == [expected]
    assert [(row['owner_id'], row['value']) for row in standalone['entries']] == [expected]
    assert original.unverified_autochess_scope is unverified
    if unverified:
        assert index['scope_warning'] == detail['scope_warning'] == '卫戍快照 · 玩家归属未知'
        assert '卫戍快照 · 玩家归属未知' in index['search']
        assert index['duration'] == detail['duration'] == '采样至 12.0 秒'
        assert detail['outcome'] == '结果未知'
        assert detail['labels'][1] == '演习状态未知'
        assert detail['view']['share_known'] is False
        assert detail['multiplayer'] is None
        assert standalone['entries'][0]['name'].casefold() in index['search']
        if not counter_scope:
            for basis in RateBasis:
                assert not history_panel(original, PanelConfig(ranking=Ranking.DPS, rate_basis=basis))['entries']
    else:
        assert 'scope_warning' not in index and 'scope_warning' not in detail
        assert detail['outcome'] == '胜利'
        assert detail['labels'][1] == '演习'

    export_path = tmp_path / 'export.json'
    state.command(dict(version=PROTOCOL_VERSION, op='export', battle_id=original.battle_id,
                       format='json', path=str(export_path)))
    exported = json.loads(export_path.read_bytes())
    display = exported.pop('display')
    assert ('scope_warning' in display) is unverified
    assert exported == archived
    assert archive_path.read_bytes() == raw
