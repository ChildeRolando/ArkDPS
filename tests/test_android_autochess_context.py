"""Real shared JS producer with separately reflected, non-PC Android layouts."""
import json
from pathlib import Path
import subprocess

from event_observer import FridaEventObserver
from event_probe import MUMU_AUTOCHESS_CONTRACT, android_autochess_event_window, contract_for


def test_android_autochess_typed_metadata_scope_and_mode_contract():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ['node', str(root / 'tests/android_autochess_context_fixture.js')],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    result = json.loads(completed.stdout)
    assert result['status'] == 'verified'
    assert len(result['checks']) == 14
    assert 'optional_android_view_mode_is_typed_fresh_and_never_identity' in result['checks']
    assert 'local_preference_keeps_the_assisted_accounts_on_android' in result['checks']


def test_android_probe_and_guarded_collector_share_the_installed_layout():
    root = Path(__file__).resolve().parents[1]
    observer = FridaEventObserver(None, 'owned fixture')
    source = observer._probe_source()
    prefix, _, _ = source.partition('\n')
    assignment = 'globalThis.arkdpsAndroidBattleLayout = '
    assert prefix.startswith(assignment) and prefix.endswith(';')
    serialized = prefix[len(assignment):-1]
    layout = json.loads(serialized)
    assert observer._probe_source_manifest['android_battle_layout'] == layout
    # The pinned Android 2.7.91 package overrides all three older defaults.
    assert (layout['logger'], layout['level'], layout['state']) == (0xe0, 0x160, 0x228)
    completed = subprocess.run(
        ['node', str(root / 'tests/android_autochess_context_fixture.js'),
         '--effective-layout', serialized],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    result = json.loads(completed.stdout)
    assert result['status'] == 'verified' and result['effective_layout'] == layout
    sample = result['sample']
    context, sequence = android_autochess_event_window(sample, result['before'], result['after'])
    assert context['phase'] == 4 and context['collection_mode'] == 'local'
    assert len(context['players']) == 4 and len(context['memberships']) == 4
    sample.update(autochess_context=context, native_counter_scope='events_only')
    contract = contract_for(observer, sample)
    assert contract is MUMU_AUTOCHESS_CONTRACT
    contract.validate_batch(sample, sequence, result['drain'])
