"""Contract selection is host-owned; record values never negotiate weaker proof."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from event_probe import (LEGACY_CONTRACT, MUMU_CONTRACT, NATIVE_PC_CONTRACT, ProbeContract,
                         NativePcBinding, NativePcProbeContract, ObserverReadRetry, StreamGap,
                         contract_for, register_contract)


def native_sample():
    return {'session': 'host-battle', 'native_session': 'verified-battle', 'frame': 2, 'game_time': 1,
            'battle_metadata': {'platform': 'native_pc', 'source_build': 'verified-build'}}


def test_payload_cannot_downgrade_native_binding_or_quality():
    sample = native_sample()
    chosen = contract_for(sample=sample, descriptor=LEGACY_CONTRACT.describe())
    assert chosen is NATIVE_PC_CONTRACT
    with pytest.raises(StreamGap, match='different or unverified'):
        chosen.validate_binding(sample, {})
    quality = chosen.normalize_quality({})
    assert all(quality[k] is True for k in ('damage_unknown', 'engagement_unknown',
                                           'shield_unknown', 'skill_unknown', 'mitigation_unknown'))
    assert quality['mitigation_ready'] is False


@pytest.mark.parametrize('contract', [LEGACY_CONTRACT, MUMU_CONTRACT, NATIVE_PC_CONTRACT])
@pytest.mark.parametrize('version', [None, True, 0, 1.0, '1', 2, {}])
def test_unknown_quality_version_cannot_certify_any_metrics(contract, version):
    with pytest.raises(StreamGap, match='quality contract'):
        contract.normalize_quality({'quality_contract_version': version, 'mitigation_ready': True,
                                    'mitigation_unknown': False})


def test_version_one_is_strict_for_both_sources_and_preserves_raw_event_data():
    event = {'sequence': 1, 'kind': 'healing', 'amount': 80,
             'proof': {'direct_healing': {'modifier_actual_delta': 23.5}}}
    payload = {'quality_contract_version': 1, 'events': [event], 'skill_unknown': 'false',
               'shield_unknown': 0, 'damage_unknown': False, 'mitigation_ready': 1}
    original = deepcopy(payload)
    for contract in (LEGACY_CONTRACT, MUMU_CONTRACT, NATIVE_PC_CONTRACT):
        quality = contract.normalize_quality(payload)
        assert quality['skill_unknown'] is True and quality['shield_unknown'] is True
        assert quality['damage_unknown'] is False and quality['mitigation_ready'] is False
        assert quality['mitigation_unknown'] is True
        assert quality['events'] is payload['events']
    assert payload == original


def test_only_legacy_unversioned_recordings_keep_old_omission_semantics():
    assert LEGACY_CONTRACT.normalize_quality({})['shield_unknown'] is False
    assert LEGACY_CONTRACT.normalize_quality({})['mitigation_ready'] is True
    assert MUMU_CONTRACT.normalize_quality({})['shield_unknown'] is True
    assert MUMU_CONTRACT.normalize_quality({})['mitigation_ready'] is False


def test_host_probe_contract_overrides_producer_selected_descriptor():
    probe = SimpleNamespace(contract=MUMU_CONTRACT)
    assert contract_for(probe, {}, LEGACY_CONTRACT.describe()) is MUMU_CONTRACT
    with pytest.raises(StreamGap, match='unsupported event probe contract'):
        contract_for(descriptor={'name': 'invented-contract'})


@pytest.mark.parametrize('contract', [NATIVE_PC_CONTRACT])
def test_bounded_prefix_and_replay_checks_are_selected_by_contract(contract):
    sample = native_sample()
    event = {'sequence': 1, 'frame': 2, 'game_time': 1, 'kind': 'damage', 'amount': 20}
    stream = SimpleNamespace(sequence=1, baseline_sequence=0, received={1: event})
    batch = {'native_session': 'verified-battle', 'source_build': 'verified-build', 'events': [event]}
    contract.validate_batch(sample, 1, batch, stream=stream)
    broken = deepcopy(batch)
    broken['events'][0]['amount'] = 21
    with pytest.raises(StreamGap, match='replay conflicts'):
        contract.validate_batch(sample, 1, broken, stream=stream)
    assert contract.validate_ack_mark(sample, dict(batch, sequence=1), 1)
    assert not contract.validate_ack_mark(sample, dict(batch, sequence=0), 1)


def test_native_binding_retains_exact_raw_clock_and_generation_guards():
    seen = []
    collector = SimpleNamespace(observer_binding=lambda **values: (
        seen.append(values) or {'native_session': 'verified-battle', 'source_build': 'verified-build'}))
    binding = NativePcBinding(collector)
    raw = {'native_generation': 1, 'native_frame': 30, 'frame': 30, 'native_fixed_time': str(1 << 32),
           'game_time': 1, 'native_identity': {'controller': 'opaque'}, 'native_stage_id': 'stage',
           'native_process_pid': 42}
    assert binding.bind('mark', raw)['native_session'] == 'verified-battle'
    assert seen[0]['native_fixed_time'] == str(1 << 32)
    with pytest.raises(RuntimeError, match='新事件世代'):
        binding.bind('mark', dict(raw, native_generation=2))
    with pytest.raises(ObserverReadRetry):
        binding.bind('mark', {'retry': True})


def test_ready_installation_diagnostics_are_retained_by_type_without_asserting_hit_coverage():
    from event_observer import FridaEventObserver
    observer = FridaEventObserver(SimpleNamespace(), None)
    observer._message({'type': 'send', 'payload': {
        'ready': True, 'mitigation_ready': True, 'mitigation_error': None,
        'mitigation_no_event_ready': 1, 'mitigation_factory_ready': False,
        'mitigation_factory_error': 'factory signature unavailable',
        'mitigation_diagnostics': {'formula_scopes': 0, 'emitted': 3, 'unpaired_damage': float('nan'),
                                   'factory_candidates': True, 'apply_value_mismatch': float('inf'),
                                   'formula_candidates': 10 ** 400, 'no_event_ready': True,
                                   'unknown_private_field': 'secret', 'samples': [{'source': 'untrusted'}]},
    }}, None)
    assert observer.ready.is_set()
    assert observer.probe_modes == {
        'mitigation_ready': True, 'mitigation_error': None, 'mitigation_factory_ready': False,
        'mitigation_factory_error': 'factory signature unavailable',
        'mitigation_diagnostics': {'formula_scopes': 0, 'emitted': 3, 'no_event_ready': True},
    }
    # Installation readiness alone is never a replacement for batch evidence.
    assert MUMU_CONTRACT.normalize_quality(observer.probe_modes)['mitigation_unknown'] is True


def test_explicit_bounded_probe_cannot_fall_back_to_destructive_drain_or_unacknowledged_reads():
    from event_capture import acknowledge_event_batch, read_event_batch
    probe = SimpleNamespace(contract=NATIVE_PC_CONTRACT,
                            drain=lambda: pytest.fail('A current probe used destructive drain'))
    with pytest.raises(StreamGap, match='bounded reads'):
        read_event_batch(probe, 1)
    with pytest.raises(StreamGap, match='commit acknowledgements'):
        acknowledge_event_batch(probe, 1)


def test_host_can_extend_binding_profiles_without_allowing_payload_registration_or_builtin_override():
    custom = ProbeContract('test-public-custom-provider', strict_quality=True)
    with pytest.raises(StreamGap, match='unregistered host'):
        contract_for(SimpleNamespace(contract=custom))
    register_contract(custom)
    assert contract_for(SimpleNamespace(contract=custom)) is custom
    assert contract_for(descriptor=custom.describe()) is custom
    assert register_contract(custom) is custom
    with pytest.raises(ValueError, match='already registered'):
        register_contract(ProbeContract(NATIVE_PC_CONTRACT.name))


def test_mumu_contract_preserves_old_queue_replay_and_ack_strategies():
    assert not MUMU_CONTRACT.raw_replay and not MUMU_CONTRACT.strict_batches
    assert not MUMU_CONTRACT.ack_mark_required and not MUMU_CONTRACT.bounded_reads_required
    assert MUMU_CONTRACT.strict_quality


def test_registered_native_contract_can_add_binding_proof_without_disabling_native_floor():
    class AdditionalProof(NativePcProbeContract):
        def validate_binding(self,sample,*payloads):
            super().validate_binding(sample,*payloads)
            if any(payload.get('additional_proof') is not True for payload in payloads):
                raise StreamGap('missing additional provider proof')

    custom=register_contract(AdditionalProof('test-extended-native-proof',True,True,True,True,True))
    chosen=contract_for(SimpleNamespace(contract=custom),native_sample())
    assert chosen is custom
    with pytest.raises(StreamGap,match='additional provider proof'):
        chosen.validate_binding(native_sample(),{'native_session':'verified-battle','source_build':'verified-build'})


@pytest.mark.parametrize('raw_replay,strict_batches', [(True,False),(False,True)])
def test_public_registration_and_selection_reject_incompatible_replay_strategies(
        monkeypatch,raw_replay,strict_batches):
    import event_probe
    invalid=ProbeContract('test-incompatible-replay',raw_replay=raw_replay,strict_batches=strict_batches)
    with pytest.raises(ValueError,match='enabled together'):
        register_contract(invalid)
    # Even a profile restored from an older host registration is checked at
    # selection, before a capture could skip or compare accepted records.
    monkeypatch.setitem(event_probe._CONTRACTS,invalid.name,invalid)
    with pytest.raises(StreamGap,match='enabled together'):
        contract_for(SimpleNamespace(contract=invalid))
    with pytest.raises(StreamGap,match='enabled together'):
        contract_for(descriptor=invalid.describe())


@pytest.mark.parametrize('raw_replay', [False,True])
def test_supported_public_profiles_accept_identical_replay_and_reject_conflicting_replay(raw_replay):
    from event_capture import LiveEventCapture
    from test_event_capture import QualityCollector,QualityObserver,event,quality_declarations
    profile=register_contract(ProbeContract('test-replay-pair-'+str(raw_replay),raw_replay=raw_replay,
                                           strict_batches=raw_replay,strict_quality=True))
    collector=QualityCollector('mumu')
    observer=QualityObserver(collector,quality_declarations())
    observer.contract=profile
    capture=LiveEventCapture(collector,observer)
    capture.sample()
    collector.frame,collector.game_time,collector.damage=2,1,20
    record=dict(event(1,frame=2,game_time=1),target='enemy_a',target_instance='enemy-1',
                hp_before=20,hp_after=0,real_delta=-20,killed=True,shield_absorbed=0,damage_type=1)
    observer.sequence=observer.epoch=1
    observer.events=[record]
    assert capture.sample()['rows']['char_a']['observed_damage']==20
    collector.frame,collector.game_time=3,2
    observer.events=[deepcopy(record)]  # Lost ACK: exact replay remains valid.
    assert capture.sample()['rows']['char_a']['observed_damage']==20
    assert not capture.closed and capture.adapter.event_stream.sequence==1
    collector.frame,collector.game_time=4,3
    observer.events=[dict(record,amount=21)]
    with pytest.raises(StreamGap,match='replay conflicts'):
        capture.sample()
    assert capture.closed
