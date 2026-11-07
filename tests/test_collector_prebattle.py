from types import SimpleNamespace

import pytest

from collector import Collector, UnsupportedVersion
from connection import EXPECTED_GAME_VERSION, VersionStatus


def stub(version):
    calls = []
    mc = SimpleNamespace(process_instance=(1, 2), pid=123, package='com.hypergryph.arknights',
                         shell=lambda *args, **kwargs: 'versionName=' + version,
                         channel=lambda: object())
    collector = Collector.__new__(Collector)
    collector.log = lambda *args: None
    collector.core = SimpleNamespace(mc=mc, connect=lambda: calls.append('transport'))
    collector.locate_battle = lambda **kwargs: calls.append('locate')
    return collector, calls


@pytest.mark.parametrize('locate', [False, True])
def test_prebattle_connection_checks_version_without_forcing_a_battle(locate):
    collector, calls = stub(EXPECTED_GAME_VERSION or '2.7.91')
    collector.connect(locate=locate)
    assert collector.diagnostics.version_status == VersionStatus.COMPATIBLE
    assert calls == (['transport', 'locate'] if locate else ['transport'])


def test_prebattle_observer_cannot_bypass_version_gate():
    collector, calls = stub('unsupported')
    with pytest.raises(UnsupportedVersion):
        collector.connect(locate=False)
    assert calls == ['transport']
