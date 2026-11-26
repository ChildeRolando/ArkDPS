"""Owned transport prefixes: publication is neither a read nor an ACK."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_native_publication_journal():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is required for the JavaScript journal fixture')
    fixture = Path(__file__).with_name('native_pc_publication_journal_fixture.js')
    result = subprocess.run([node, str(fixture)], capture_output=True, text=True, check=True, timeout=20)
    report = json.loads(result.stdout)
    assert report['checks'] >= 35
    assert report['live_reads'] == report['live_calls'] == 0


def test_native_compact_publish_receipt_preserves_host_and_ack_contract():
    fixture = Path(__file__).with_name('native_publication_compact_receipt_fixture.js')
    result = subprocess.run(['node', str(fixture)], cwd=fixture.parents[1],
                            capture_output=True, text=True, check=True, timeout=20)
    report = json.loads(result.stdout)
    assert report['full']['copies'] == 1 and report['compact']['copies'] == 0
    assert report['identicalHostMarkRead'] and report['unservedAckRejected']
    assert report['callerAndReplyMutationIsolated']
