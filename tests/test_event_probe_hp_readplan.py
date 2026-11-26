import subprocess
from pathlib import Path


def test_hp_pointer_baseline_preserves_partial_branches_and_class_overlap():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/event_probe_hp_readplan_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_HP_POINTER_SOURCE_BASELINE' in result.stdout


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


import pytest


@pytest.mark.parametrize('legacy_values', ['0', '1'])
def test_scope_primitive_source_keeps_signed_values_and_role_metadata_guards(legacy_values):
    import os
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', 'tests/native_pc_captured_publication_fixture.js'], cwd=root,
                            capture_output=True, text=True, timeout=30,
                            env=dict(os.environ, ARKDPS_SOURCE_GRAPH_PRIMITIVES='1',
                                     ARKDPS_SOURCE_GRAPH_PRIMITIVES_LEGACY=legacy_values))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS_CAPTURED_PUBLICATION' in result.stdout
