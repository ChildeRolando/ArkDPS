"""Replay real probe envelopes from an owned Node fixture, never a game process."""

from copy import deepcopy
import json
import subprocess

import pytest

from acceptance.replay_pc_probe_batches import replay_probe_batches
from event_probe import NATIVE_PC_CONTRACT
from test_internal_capture import ROOT, native_host


@pytest.fixture(scope='module')
def ammo_stages():
    # The fixture runs production JavaScript on owned metadata and memory.
    # Its frozen numeric inputs are not live evidence for this replay.
    result = subprocess.run(
        ['node', str(ROOT / 'tests' / 'event_probe_ammo_pipeline_fixture.js')],
        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)['native_pc']['stages']


@pytest.fixture
def probe_journal(ammo_stages):
    feed = 'owned-pc-probe-ammo'
    pid = ammo_stages[0]['mark']['native_process_pid']
    binding = dict(native_session=f'{pid}:100000:{"a" * 64}', source_build='win-x64:owned-pc-probe-ammo')
    contract = NATIVE_PC_CONTRACT.describe()
    journal = [dict(kind='lifecycle_header', data=dict(feed=feed))]
    previous = 0
    for observation_sequence, stage in enumerate(ammo_stages, 1):
        mark = dict(deepcopy(stage['mark']), **binding)
        drain = dict(deepcopy(stage['batch']), **binding, probe_contract=contract)
        # The fixture reads the cumulative prefix. The product drains only
        # the addition after its last confirmed sequence; do not renumber it.
        drain['events'] = [event for event in drain['events'] if event['sequence'] > previous]
        through = mark['sequence']
        is_terminal = mark['terminal_boundary'] is not None
        sample = dict(session=binding['native_session'], native_session=binding['native_session'],
                      frame=mark['frame'], game_time=mark['game_time'], state=3 if is_terminal else 2,
                      battle_metadata=dict(platform='native_pc', source_build=binding['source_build'],
                                           stage_id=mark['native_stage_id']))
        evidence = dict(probe_contract=contract, before=deepcopy(mark), after=mark,
                        sequence=through, drain=drain)
        observation = dict(feed=feed, sequence=observation_sequence, generation=1,
                           kind='terminal' if is_terminal else 'sample', sample=sample, events=evidence)
        journal.append(dict(kind='lifecycle_observation', data=observation))
        previous = through
    return journal


def write_journal(tmp_path, journal):
    path = tmp_path / 'probe.jsonl'
    path.write_text(''.join(json.dumps(line, ensure_ascii=False, allow_nan=False) + '\n'
                            for line in journal), encoding='utf-8')
    return path


def test_native_host_replays_production_ammo_batches_and_terminal(native_host, tmp_path, probe_journal):
    result = replay_probe_batches(write_journal(tmp_path, probe_journal), native_host)
    assert result['batches'] == 5
    assert result['events'] == 19
    assert result['first_sequence'] == 1
    assert result['unverified_through'] == 0
    assert result['last_sequence'] == 19
    assert result['terminal_verified'] is True


def test_replay_rejects_terminal_for_different_battle_roots(native_host, tmp_path, probe_journal):
    evidence = probe_journal[-1]['data']['events']
    for name in ('before', 'after'):
        # Mutate both observation fences, not native_identity, so the genuine
        # source watermark is still steady but no longer proves this endpoint.
        evidence[name]['terminal_boundary']['controller'] = '0x200000'
    with pytest.raises(ValueError, match='(?i)terminal'):
        replay_probe_batches(write_journal(tmp_path, probe_journal), native_host)


def test_replay_rejects_missing_record_below_watermark(native_host, tmp_path, probe_journal):
    evidence = probe_journal[2]['data']['events']
    assert evidence['drain']['events'].pop(0)['sequence'] == 1
    with pytest.raises(ValueError, match='(?i)sequence|gap'):
        replay_probe_batches(write_journal(tmp_path, probe_journal), native_host)


def test_replay_rejects_disagreeing_recorded_quality(native_host, tmp_path, probe_journal):
    evidence = probe_journal[2]['data']['events']
    assert evidence['after']['skill_unknown'] is False
    evidence['drain']['skill_unknown'] = True
    with pytest.raises(ValueError, match='disagree on shared evidence'):
        replay_probe_batches(write_journal(tmp_path, probe_journal), native_host)


def test_replay_reports_unverified_prefix_and_no_terminal_for_retained_tail(native_host, tmp_path, probe_journal):
    # Start after watermarked sequence 9 and stop before the source's end.
    journal = [probe_journal[0], *probe_journal[3:-1]]
    result = replay_probe_batches(write_journal(tmp_path, journal), native_host)
    assert result['batches'] == 2
    assert result['events'] == 10
    assert result['first_sequence'] == 10
    assert result['last_sequence'] == 19
    assert result['unverified_through'] == 9
    assert result['terminal_verified'] is False
