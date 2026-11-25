"""Exercise the real producer's reset hook without a host mark at frame zero."""

from pathlib import Path
import subprocess


def test_autochess_round_origin_preserves_first_raw_prefix_on_both_backends():
    fixture = Path(__file__).with_name('event_probe_autochess_round_start_fixture.js')
    subprocess.run(['node', str(fixture)], check=True, timeout=30)
