"""Failure checkpoints retain evidence, never promote its numeric quality."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from event_probe import StreamGap, settlement_failure_samples


def test_native_checkpoint_is_first_only_bounded_detached_and_uses_no_native_reads():
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run(['node', 'tests/event_probe_failure_samples_fixture.js'],
                         cwd=root, capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stdout + run.stderr
    quality = json.loads(run.stdout)
    samples = settlement_failure_samples(quality)
    assert len(samples) == 16 and samples[0]['hp_after'] == 11
    assert quality['enemy_damage_unknown'] is False
    for mutate in (
        lambda q: q.update(damage_settlement_failure_samples_version=True),
        lambda q: q['damage_settlement_failure_samples'].append(samples[0]),
        lambda q: q['damage_settlement_failure_samples'][0].update(extra='unowned'),
        lambda q: q['damage_settlement_failure_samples'][0].update(native_signed_delta=float('nan')),
        lambda q: q['damage_settlement_failure_samples'][0].update(source_character_uid=True),
    ):
        broken = deepcopy(quality)
        mutate(broken)
        with pytest.raises(StreamGap):
            settlement_failure_samples(broken)
    assert settlement_failure_samples({}) is None
