"""Optional four-player detail is persisted, with no effect on accepted facts."""
from copy import deepcopy
from dataclasses import replace
import io
import json
from queue import Queue
from types import SimpleNamespace

import pytest

from battle_share import json_bytes
from desktop_bridge import DesktopState, PROTOCOL_VERSION, serve
from layout_store import GuiGeometry, HudPlacement, Layout, LayoutStore
from panel_model import PanelConfig
from test_desktop_native_autochess_projection import populated, saved_native_record
from test_desktop_native_defense_retention import run_chain


def toggle(state, enabled):
    return state.command(dict(version=PROTOCOL_VERSION, op='multiplayer_enabled', enabled=enabled))


def test_first_launch_has_no_four_player_detail_or_written_preference(tmp_path):
    state = DesktopState(tmp_path)
    assert Layout.default().multiplayer_enabled is False
    assert state.layout.multiplayer_enabled is False
    assert state.snapshot()['layout']['multiplayer_enabled'] is False
    assert state.snapshot()['multiplayer'] is None
    assert not state.store.path.exists()


@pytest.mark.parametrize('version', [1, 2, 3])
def test_legacy_missing_preference_migrates_off_and_preserves_other_choices(tmp_path, version):
    panel = PanelConfig(panel_id='saved', effective_only=True)
    raw = Layout(panels=(panel,), huds=(HudPlacement('saved', 10, 20, 400, 300),),
                 gui=GuiGeometry(50, 60, 1000, 800), event_observation=False,
                 number_units='chinese', side_mode=True).to_dict()
    raw.update(version=version)
    raw.pop('multiplayer_enabled')
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    result = LayoutStore(path).load()
    assert result.ok and result.layout.multiplayer_enabled is False
    assert result.layout.panels == (panel,)
    if version >= 2:
        assert result.layout.event_observation is False
        assert result.layout.gui == GuiGeometry(50, 60, 1000, 800)
        assert result.layout.huds == (HudPlacement('saved', 10, 20, 400, 300),)
        assert result.layout.number_units == 'chinese' and result.layout.side_mode is True
    LayoutStore(path).save(result.layout)
    assert json.loads(path.read_text(encoding='utf-8'))['multiplayer_enabled'] is False
    assert DesktopState(tmp_path).layout.multiplayer_enabled is False


@pytest.mark.parametrize('invalid', [None, 0, 1, 'false', 'true', [], {}])
def test_invalid_saved_preference_falls_back_off_without_discarding_layout(tmp_path, invalid):
    expected = Layout(panels=(PanelConfig(panel_id='saved', effective_only=True),),
                      event_observation=False, number_units='chinese', side_mode=True)
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps(dict(expected.to_dict(), multiplayer_enabled=invalid)), encoding='utf-8')
    loaded = LayoutStore(path).load()
    assert loaded.ok and loaded.warning and loaded.backup is None
    assert loaded.layout == expected and loaded.layout.multiplayer_enabled is False


@pytest.mark.parametrize('enabled', [True, False])
def test_command_persists_real_boolean_and_reloads_without_a_source_change(tmp_path, enabled):
    state = DesktopState(tmp_path)
    revision, connection, observer = state._source_request_revision, state.effective_connection, state.observer_status
    toggle(state, True)
    toggle(state, enabled)
    assert state.layout.multiplayer_enabled is enabled
    assert json.loads(state.store.path.read_text(encoding='utf-8'))['multiplayer_enabled'] is enabled
    assert DesktopState(tmp_path).layout.multiplayer_enabled is enabled
    assert (state._source_request_revision, state.effective_connection, state.observer_status) == (
        revision, connection, observer)


@pytest.mark.parametrize('invalid', [None, 0, 1, 'true', 'false', [], {}])
def test_command_rejects_non_boolean_without_changing_memory_or_disk(tmp_path, invalid):
    state = DesktopState(tmp_path)
    toggle(state, True)
    before, raw = state.layout, state.store.path.read_bytes()
    with pytest.raises(ValueError):
        toggle(state, invalid)
    assert state.layout == before and state.store.path.read_bytes() == raw


def test_failed_preference_write_keeps_live_setting_and_existing_file(tmp_path, monkeypatch):
    state = DesktopState(tmp_path)
    toggle(state, True)
    before = state.store.path.read_bytes()

    def failed(_layout):
        raise OSError('owned disk failure')

    monkeypatch.setattr(state.store, 'save', failed)
    with pytest.raises(OSError, match='owned disk failure'):
        toggle(state, False)
    assert state.layout.multiplayer_enabled is True
    assert state.store.path.read_bytes() == before


def test_default_off_does_not_construct_live_or_history_four_player_detail(tmp_path, monkeypatch):
    record, path = saved_native_record(tmp_path, end_phase=True)
    sample = populated().runtime.display_sample()
    original, saved_bytes, exported = deepcopy(sample), path.read_bytes(), json_bytes(record)
    state = DesktopState(tmp_path)
    state.accept('sample', sample)

    def forbidden(*_args, **_kwargs):
        pytest.fail('disabled four-player detail was constructed')

    monkeypatch.setattr(state, 'multiplayer_view', forbidden)
    reply = json.loads(json.dumps(state.snapshot(), ensure_ascii=False, allow_nan=False))
    assert reply['multiplayer'] is None
    assert reply['history_selected']['multiplayer'] is None
    assert reply['panels'][0]['entries'][0]['value'] == 300
    assert reply['history_selected']['view']['entries'][0]['value'] == 300
    assert state.last is sample and sample == original
    assert record.to_dict() == state.selected.to_dict()
    assert path.read_bytes() == saved_bytes and json_bytes(record) == exported


def test_switch_on_off_on_reuses_live_and_cached_history_without_mutating_exports(tmp_path):
    record, path = saved_native_record(tmp_path, end_phase=True)
    sample = populated().runtime.display_sample()
    state = DesktopState(tmp_path)
    state.accept('sample', sample)
    original, recorded, original_bytes = deepcopy(sample), record.to_dict(), path.read_bytes()
    off = state.snapshot()
    toggle(state, True)
    on = state.snapshot()
    assert len(on['multiplayer']['players']) == 4
    assert len(on['history_selected']['multiplayer']['players']) == 4
    for key in ('panels', 'totals', 'enemy', 'live_analysis', 'boss_live_analysis', 'boss_effective_live_analysis'):
        assert on[key] == off[key]
    assert on['history_selected']['view'] == off['history_selected']['view']
    toggle(state, False)
    disabled = state.snapshot()
    assert disabled['multiplayer'] is None and disabled['history_selected']['multiplayer'] is None
    assert disabled['panels'] == on['panels']
    toggle(state, True)
    resumed = state.snapshot()
    assert resumed['multiplayer'] == on['multiplayer']
    assert resumed['history_selected'] == on['history_selected']
    assert state.last is sample and sample == original
    assert record.to_dict() == state.selected.to_dict() == recorded
    assert path.read_bytes() == original_bytes
    toggle(state, False)
    destination = tmp_path / 'export.json'
    state.command(dict(version=PROTOCOL_VERSION, op='export', battle_id=record.battle_id,
                       format='json', path=str(destination)))
    assert destination.read_bytes() == json_bytes(record)
    assert destination.read_bytes() == json_bytes(state.selected)


def test_off_hides_detail_but_keeps_assist_scene_and_retained_self_panels(tmp_path, monkeypatch):
    state, _, _, _, views, accepted = run_chain(tmp_path, monkeypatch,
        ['defense_baseline', 'defense_hits', 'assist_busy', 'assist_baseline', 'assist_hits', 'prepare'],
        multiplayer_enabled=False)
    assert all(view['multiplayer'] is None for view in views.values())
    assert views['assist_busy']['panels'][0]['entries'] == []
    assert '当前协防战场' in views['assist_busy']['status_text']
    assert sorted(row['value'] for row in views['assist_hits']['panels'][0]['entries']) == [1000, 2000, 3000, 4000]
    assert [row['value'] for row in views['prepare']['panels'][0]['entries']] == [300]
    assert '上一场防御' in views['prepare']['panels'][0]['title']
    before = views['prepare']['panels']
    toggle(state, True)
    restored = state.snapshot()
    assert restored['panels'] == before
    assert restored['multiplayer']['previous_defense'] is True
    assert [p['observed_damage'] for p in restored['multiplayer']['players']] == [100, 200, 300, 400]
    assert not [raw for action, raw, _ in accepted if action == 'assist_busy']


def test_actual_worker_command_toggles_wire_detail_without_requesting_reconnect(tmp_path):
    state = DesktopState(tmp_path)
    sample = populated().runtime.display_sample()
    state.accept('sample', sample)
    original = deepcopy(sample)

    def reconnect(*_args, **_kwargs):
        pytest.fail('presentation preference requested a new collection source')

    worker = SimpleNamespace(events=Queue(), effective=(state.effective_connection, state.layout.event_observation),
                             request=reconnect, close=lambda: None)
    commands = [dict(version=PROTOCOL_VERSION, id=1, op='hello'),
                dict(version=PROTOCOL_VERSION, id=2, op='multiplayer_enabled', enabled=True),
                dict(version=PROTOCOL_VERSION, id=3, op='multiplayer_enabled', enabled=False)]
    output = io.StringIO()
    serve(io.StringIO(''.join(json.dumps(command) + '\n' for command in commands)), output, state, worker)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(replies) == 3 and all(reply['ok'] for reply in replies)
    assert replies[0]['state']['multiplayer'] is None
    assert len(replies[1]['state']['multiplayer']['players']) == 4
    assert replies[2]['state']['multiplayer'] is None
    assert replies[0]['state']['panels'] == replies[1]['state']['panels'] == replies[2]['state']['panels']
    assert state.last is sample and sample == original
