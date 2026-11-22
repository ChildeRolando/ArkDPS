"""Current MuMu retains its existing cross-battle baseline discard semantics."""
import json
from pathlib import Path
import subprocess

from event_capture import LiveEventCapture
from event_probe import MUMU_CONTRACT
from test_event_capture import Collector


def test_actual_mumu_mark_read_ack_retains_then_discards_previous_battle_prefix():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run(['node',str(root/'tests/event_probe_retained_queue_fixture.js'),
                           str(root/'event_probe_android2791.js')],
                          capture_output=True,text=True,timeout=30,check=True)
    stages=json.loads(result.stdout)
    assert stages['first']['batch']['events'][0]['frame']==6000
    assert stages['first']['mark']['frame']==1 and stages['first']['mark']['sequence']==2
    assert stages['first']['ack']['acknowledged']==2

    class Probe:
        contract=MUMU_CONTRACT
        stage=stages['first']
        def mark(self):return self.stage['mark']
        def read_through(self,through):
            assert through==self.stage['mark']['sequence']
            return self.stage['batch']
        def commit_events(self,through):
            assert through==self.stage['mark']['sequence']

    collector,probe=Collector(),Probe()
    capture=LiveEventCapture(collector,probe)
    capture.sample()
    assert capture.adapter.event_stream.sequence==2 and not capture.closed
    probe.stage=stages['second']
    collector.frame,collector.game_time,collector.damage=2,1,120
    row=capture.sample()['rows']['char_a']
    assert row['event_coverage_complete'] and row['observed_damage']==20
    assert capture.adapter.event_stream.sequence==3
