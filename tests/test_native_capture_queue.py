"""Exercise SPSC publication with an owned producer and consumer."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_bounded_capture_queue(tmp_path):
    compiler = shutil.which('g++')
    if compiler is None:
        pytest.skip('g++ is required for the native queue fixture')
    source = Path(__file__).with_name('native_capture_queue_test.cpp')
    binary = tmp_path / 'capture-queue.exe'
    subprocess.run([compiler, '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                    '-static-libgcc', '-static-libstdc++', str(source), '-o', str(binary)],
                   check=True, capture_output=True, text=True, timeout=60)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=30)
