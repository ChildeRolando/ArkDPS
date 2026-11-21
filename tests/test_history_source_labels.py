"""Historical source labels use stored route metadata, never the current source."""
import json

import pytest

from battle_history import BattleRecord
from battle_share import json_bytes, record_display
from connection import ConnectionConfig
from desktop_bridge import DesktopState
from test_battle_history import record


@pytest.mark.parametrize('platform,label', [
    ('native_pc', '官服 PC'), ('android', 'Android（ADB）'),
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
