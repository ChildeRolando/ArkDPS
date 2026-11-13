"""Stage display resolution across live, history and export surfaces."""
import json
from pathlib import Path

import pytest

import stage_names
from battle_history import BattleHistory
from battle_share import json_bytes, record_labels, record_stage
from desktop_bridge import DesktopState
from test_battle_history import record
from sample_data import sample

MAIN = 'Obt/Main/level_main_01-07'
ROGUE = 'Obt/Roguelike/RO6/level_rogue6_1-3'
ROGUE_SECOND = 'Obt/Roguelike/RO6/level_rogue6_1-4'
ACTIVITY = 'Activities/ACT3D0/level_act3d0_08'
CATALOG = Path(stage_names.__file__).resolve().parent / 'vendor/ark-source/game_data/catalogs/stage_names.json'


def test_bundled_catalog_matches_the_pinned_bundle_version():
    catalog = stage_names.catalog()
    assert not catalog.warning
    assert len(catalog.stages) > 3000
    manifest = json.loads((CATALOG.parents[1] / 'manifest.json').read_text(encoding='utf-8'))
    assert catalog.data_version == manifest['data_version']


def test_main_activity_and_roguelike_ids_resolve_to_readable_names():
    main = stage_names.resolve(MAIN)
    assert main.known and main.label == '1-7' and main.code == '1-7'
    assert MAIN in main.detail

    activity = stage_names.resolve(ACTIVITY)
    assert activity.known and activity.label == 'OF-8 汐斯塔狂想曲'

    assert stage_names.resolve(ROGUE).label == '急不可耐'
    assert stage_names.resolve(ROGUE_SECOND).label == '灌水贤者'
    for value in (main, activity, stage_names.resolve(ROGUE)):
        assert '/' not in value.label
        assert value.raw in value.detail


@pytest.mark.parametrize('raw,label', [
    ('Obt/Unknown/level_rogue6_9-9', 'rogue6_9-9'),
    ('Activities/ACT9D0/level_act9d0_99', 'act9d0_99'),
    ('level_only_id', 'only_id'),
    (r'Obt\Unknown\level_x_1', 'x_1'),
    ('Obt/Unknown/', 'Unknown'),
    ('OF-8', 'OF-8'),
])
def test_unknown_ids_use_a_readable_short_code_and_keep_the_raw_id(raw, label):
    display = stage_names.resolve(raw)
    assert not display.known and display.label == label
    assert '未收录于关卡表' in display.detail and raw in display.detail
    assert '/' not in display.label


def test_missing_or_mismatched_catalog_is_reported_instead_of_silent_paths(tmp_path):
    missing = stage_names.StageCatalog(tmp_path / 'stage_names.json', tmp_path / 'manifest.json')
    assert '未找到' in missing.warning and 'stage_names.json' in missing.warning
    assert missing.resolve(MAIN).label == 'main_01-07'
    assert '版本未知' in stage_names.StageCatalog(CATALOG, tmp_path / 'manifest.json').warning

    broken = tmp_path / 'broken.json'
    broken.write_text('{', encoding='utf-8')
    assert '无法解析' in stage_names.StageCatalog(broken, tmp_path / 'manifest.json').warning

    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'data_version': '2026-11-01-ea7475c2dad5'}), encoding='utf-8')
    payload = json.loads(CATALOG.read_text(encoding='utf-8'))
    payload['bundle_data_version'] = '2099-01-01-000000000000'
    stale = tmp_path / 'stale.json'
    stale.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    catalog = stage_names.StageCatalog(stale, manifest)
    assert '版本不匹配' in catalog.warning and '重新生成' in catalog.warning
    assert catalog.resolve(MAIN).label == '1-7'  # data still usable, problem surfaced


def test_old_archive_keeps_raw_stage_and_displays_resolved_name_without_rewrite(tmp_path):
    history = BattleHistory(tmp_path)
    original = record(stage_id=ROGUE)
    history.save(original)
    archived = json.loads(history.path_for(original.battle_id).read_text(encoding='utf-8'))
    assert archived['metadata']['stage_id'] == ROGUE

    restarted = BattleHistory(tmp_path).list_recent().records[0]
    assert record_labels(restarted)[0] == '急不可耐'
    assert '原始关卡 ID：' + ROGUE in record_stage(restarted).detail
    assert restarted.to_dict()['metadata']['stage_id'] == ROGUE


def test_live_bridge_history_and_search_use_the_same_resolution(tmp_path):
    state = DesktopState(tmp_path)
    state.history.save(record(7, stage_id=ROGUE))
    state.refresh_history()
    state.accept('sample', dict(sample(), battle_metadata={'stage_id': MAIN}))
    snapshot = state.snapshot()

    assert snapshot['stage'] == '1-7'
    assert snapshot['stage_code'] == '1-7'
    assert snapshot['stage_name'] == '暴君'
    assert snapshot['stage_raw'] == MAIN
    assert MAIN in snapshot['stage_detail']
    assert snapshot['stage_known'] is True
    item, = snapshot['history']
    assert item['labels'][0] == '急不可耐' and item['stage'] == '急不可耐'
    assert ROGUE in item['stage_detail']
    # Both the readable name and the legacy native id remain searchable.
    assert '急不可耐' in item['search'] and ROGUE.casefold() in item['search']


@pytest.mark.parametrize('raw, code, name, label', [
    (ACTIVITY, 'OF-8', '汐斯塔狂想曲', 'OF-8 汐斯塔狂想曲'),
    (ROGUE, 'ISW-NO', '急不可耐', '急不可耐'),
    ('Obt/Unknown/level_new', None, None, 'new'),
    (None, None, None, None),
])
def test_live_stage_code_and_name_keep_fallback_in_display_label(tmp_path, raw, code, name, label):
    state = DesktopState(tmp_path)
    state.accept('sample', dict(sample(), battle_metadata={'stage_id': raw}))
    snapshot = state.snapshot()
    assert (snapshot['stage_code'], snapshot['stage_name'], snapshot['stage']) == (code, name, label)


def test_json_export_keeps_the_resolved_name_and_the_raw_id():
    # PNG rendering lives in the WPF client (Screenshot.cs) and paints the same
    # resolved label; the Python worker only serialises the export payload.
    original = record(stage_id=ACTIVITY)
    label = record_labels(original)[0]
    payload = json_bytes(original)
    exported = json.loads(payload)
    assert exported['metadata']['stage_id'] == ACTIVITY
    assert exported['display']['stage'] == dict(
        stage_id=ACTIVITY, stage_name=label, stage_code='OF-8', stage_zone='主舞台',
        stage_known=True, catalog_version=stage_names.catalog().data_version)


def test_alternate_stage_ids_resolve_through_documented_aliases():
    assert stage_names.resolve('ro6_e_1_3').label == '急不可耐'
    assert stage_names.resolve('act6bossrush_tm01').label == 'TN-1 睥睨夜幕'


def test_builder_prefers_normal_entries_and_records_aliases():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'build_stage_names', Path(stage_names.__file__).resolve().parent / 'tools/build_stage_names.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    stage_table = {'stages': {
        'main_01-07': dict(levelId='Obt/Main/level_main_01-07', code='1-7', name='暴君',
                           stageType='MAIN', zoneId='main_1'),
        'main_01-07#f#': dict(levelId='Obt/Main/level_main_01-07', code='1-7', name='暴君',
                              stageType='MAIN', zoneId='main_1'),
        'act6bossrush_tm01': dict(levelId='Activities/x/level_x_01', code='TN-1', name='测试',
                                  stageType='ACTIVITY', zoneId='act1'),
        'act6bossrush_01': dict(levelId='Activities/x/level_x_01', code='TN-1', name='测试',
                                stageType='ACTIVITY', zoneId='act1'),
    }}
    roguelike_table = {
        'topics': {'rogue_6': {'name': '沉沦者的黑流树海'}},
        'details': {'rogue_6': {'stages': {
            'ro6_n_1_3': dict(levelId='Obt/Roguelike/RO6/level_rogue6_1-3', code='ISW-NO', name='急不可耐'),
            'ro6_e_1_3': dict(levelId='Obt/Roguelike/RO6/level_rogue6_1-3', code='ISW-NO', name='急不可耐'),
        }}},
    }
    catalog = builder.build_catalog(stage_table, roguelike_table,
                                    {'zones': {'main_1': {'zoneNameFirst': '第一章'}}},
                                    bundle_data_version='v1')
    assert catalog['stages']['Obt/Main/level_main_01-07'] == ['main_01-07', 'MAIN', '1-7', '暴君', '第一章']
    assert catalog['stages']['Obt/Roguelike/RO6/level_rogue6_1-3'][0] == 'ro6_n_1_3'
    assert catalog['stages']['Obt/Roguelike/RO6/level_rogue6_1-3'][4] == '沉沦者的黑流树海'
    assert catalog['aliases'] == {'act6bossrush_tm01': 'Activities/x/level_x_01',
                                  'ro6_e_1_3': 'Obt/Roguelike/RO6/level_rogue6_1-3'}
    # Deterministic output: rebuilding the same tables yields identical bytes.
    assert builder.render(catalog) == builder.render(
        builder.build_catalog(stage_table, roguelike_table,
                              {'zones': {'main_1': {'zoneNameFirst': '第一章'}}},
                              bundle_data_version='v1'))
