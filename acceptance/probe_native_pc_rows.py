"""Compatibility entry point for :mod:`native_pc.probe_native_pc_rows`."""

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_pc import probe_native_pc_rows as _implementation
from native_pc.probe_native_pc_rows import *

# Normal imports share the implementation module, including monkeypatch targets.
sys.modules[__name__] = _implementation
