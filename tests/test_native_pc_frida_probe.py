"""Exercise the Windows probe against owned IL2CPP callback and memory fixtures."""
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_windows_probe_preserves_healing_shield_skill_order_and_exact_native_binding():
    result = subprocess.run(['node', str(ROOT / 'tests' / 'native_pc_frida_probe_fixture.js'),
                             str(ROOT / 'event_probe_native_pc.js')],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report == {'events': 7, 'shield_amounts': [40, 25], 'direct_healing': 80,
                      'damage': 40, 'queue_ack': True, 'identity_rollback': True, 'windows_sret': [7, 8]}


def test_raw_healing_proof_preserves_nominal_statistics_and_native_replay_record():
    from event_capture import LiveEventCapture
    from test_event_capture import QualityCollector, QualityObserver, event, quality_declarations

    collector = QualityCollector('native_pc')
    observer = QualityObserver(collector, quality_declarations())
    capture = LiveEventCapture(collector, observer)
    capture.sample()
    collector.frame, collector.game_time, collector.healing = 2, 1, 80
    record = dict(event(1, frame=2, game_time=1, amount=80), kind='healing', proof={
        'method': 'Entity.ApplyModifier', 'type': 4, 'target': 0,
        'direct_healing': {'amount': 80, 'modifier_actual_delta': 23.5,
                           'hp_before': 50, 'hp_after': 73.5, 'target': 'char_target',
                           'target_instance': '0x1234', 'target_uid': '20'},
    })
    observer.sequence = observer.epoch = 1
    observer.events = [record]
    row = capture.sample()['rows']['char_a']
    assert row['healing'] == row['observed_healing'] == 80
    assert capture.adapter.event_stream.received[1] == record


def test_producer_initial_witness_and_raw_destroy_boundary_use_actual_callback_evidence():
    result = subprocess.run(['node', str(ROOT / 'tests/native_pc_boundary_fixture.js')],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'owned_boundary_cases': 12, 'live_game': False}


def test_terminal_only_queue_read_requires_real_destroy_fence_and_never_weakens_normal_read():
    result = subprocess.run(['node', str(ROOT / 'tests/native_pc_terminal_read_fixture.js')],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'owned_terminal_read_cases': 33, 'live_game': False}
