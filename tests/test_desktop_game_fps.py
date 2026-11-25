"""FPS crosses the existing worker boundary independently of battle publication."""
from dataclasses import replace
import io
import json
import sys
from types import SimpleNamespace

import pytest

from desktop_bridge import DesktopState, serve


class Monitor:
    def __init__(self, *, emit=None):
        self.emit = emit
        self.configured = []
        self.closed = 0
        self.sample = dict(status='ok', source='dxgi_present', fps=1, frame_count=1,
                           duration_ms=1000, sample_seq=1, window_end_utc='2026-11-25T01:00:01+00:00')

    def configure(self, connection, diagnostic):
        self.configured.append((connection, diagnostic))

    def snapshot(self):
        return dict(self.sample)

    def close(self):
        self.closed += 1


def test_fps_remains_visible_without_battle_or_successful_statistics(tmp_path):
    state = DesktopState(tmp_path)
    monitor = state.game_fps_monitor = Monitor()
    state.effective_connection = replace(state.connection, platform='native_pc', native_game_root='C:/Game')
    state.diagnostic = {'platform': 'native_pc', 'process_pid': 123, 'process_created_filetime': 456}
    state.accept('sampling_rejection', {'code': 'mapping_unavailable', 'reason': '等待归属'})
    state.accept('error', 'observer timed out')
    snapshot = state.snapshot()
    assert snapshot['battle_id'] is None
    assert snapshot['game_fps']['fps'] == 1
    assert snapshot['game_fps']['duration_ms'] == 1000
    assert monitor.configured == [(state.effective_connection.to_dict(), state.diagnostic)]
    monitor.sample.update(fps=0, frame_count=0, sample_seq=2)
    assert state.snapshot()['game_fps']['fps'] == 0


def test_offline_fps_is_unknown_and_never_synthetic_zero(tmp_path):
    state = DesktopState(tmp_path)
    assert state.snapshot()['game_fps'] == dict(
        status='unavailable', source=None, fps=None, error_code='not_running')


def test_fps_logs_publish_without_snapshot_and_with_debug_mode_disabled(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'game_fps', SimpleNamespace(GameFpsMonitor=Monitor))
    state = DesktopState(tmp_path)
    assert not state.layout.debug_mode
    state.start_game_fps()
    monitor = state.game_fps_monitor
    state.start_game_fps()  # One observer and one journal per worker.
    assert state.game_fps_monitor is monitor
    monitor.emit(monitor.sample)
    monitor.emit(dict(monitor.sample, fps=60, frame_count=60, sample_seq=2))
    state.close_game_fps()
    state.close_game_fps()
    assert monitor.closed == 1
    files = list((tmp_path / 'performance' / 'fps').glob('*.jsonl'))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text('utf-8').splitlines()]
    assert records[0]['kind'] == 'log_header'
    samples = [record['data'] for record in records if record['kind'] == 'game_fps']
    assert [record['fps'] for record in samples] == [1, 60]
    assert [record['sample_seq'] for record in samples] == [1, 2]


def test_quit_stops_render_observer_before_bye_and_can_retry_cleanup(tmp_path):
    state = DesktopState(tmp_path)
    monitor = state.game_fps_monitor = Monitor()

    class Output(io.StringIO):
        def write(self, value):
            if '"bye"' in value:
                assert monitor.closed == 1
            return super().write(value)

    output = Output()
    serve(io.StringIO('{"version":1,"op":"hello"}\n{"version":1,"op":"quit"}\n'), output, state)
    assert json.loads(output.getvalue().splitlines()[-1])['kind'] == 'bye'

    class FailsOnce(Monitor):
        def close(self):
            self.closed += 1
            if self.closed == 1:
                raise RuntimeError('owned trace still stopping')

    retained = state.game_fps_monitor = FailsOnce()
    with pytest.raises(RuntimeError, match='still stopping'):
        state.close_game_fps()
    assert state.game_fps_monitor is retained
    state.close_game_fps()
    assert retained.closed == 2 and state.game_fps_monitor is None


def test_fps_log_error_is_exposed_without_replacing_measurement(tmp_path):
    state = DesktopState(tmp_path)
    state.game_fps_monitor = Monitor()
    state.game_fps_log_error = 'disk full'
    assert state.snapshot()['game_fps']['log_error'] == 'disk full'
    assert state.snapshot()['game_fps']['fps'] == 1
