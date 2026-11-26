import subprocess
from pathlib import Path


def test_hp_readplan_tiers_preserve_shared_callback_validation():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/event_probe_hp_readplan_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_HP_READPLAN_SOURCE_TIERS' in result.stdout


def test_graph_batch_source_uses_actual_field_order_and_keeps_guard_failures():
    import os
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/native_pc_captured_publication_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30,
                            env=dict(os.environ, ARKDPS_SOURCE_GRAPH_BATCH='1'))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_CAPTURED_PUBLICATION' in result.stdout


def test_raw_graph_comparison_declarations_preserve_original_bits_and_utf16():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/native_pc_graph_comparison_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_RAW_GRAPH_COMPARISONS' in result.stdout


def test_verified_graph_proof_source_preserves_guards_and_rejects_forged_success():
    import os
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/native_pc_captured_publication_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30,
                            env=dict(os.environ, ARKDPS_SOURCE_GRAPH_CHECK='1'))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_CAPTURED_PUBLICATION' in result.stdout
