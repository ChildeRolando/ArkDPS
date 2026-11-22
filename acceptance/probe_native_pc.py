"""Compatibility entry point for :mod:`native_pc.probe_native_pc`."""

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_pc import probe_native_pc as _implementation
from native_pc.probe_native_pc import *


def verify_build(root):
    return _implementation.verify_build(root, known_files=KNOWN_FILES)


def probe(game_root, api=None, *, handle_only=False):
    return _implementation.probe(game_root, api, handle_only=handle_only,
                                 build_check=verify_build)


if __name__ == '__main__':
    raise SystemExit(_implementation.main())

# Normal imports share the implementation module, including monkeypatch targets.
sys.modules[__name__] = _implementation
