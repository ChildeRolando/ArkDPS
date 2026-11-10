"""Provision the isolated GitHub Windows desktop used by native geometry tests.

Never run on a user desktop. Tests intentionally retain their geometry asserts.
"""
import ctypes
from ctypes import wintypes
import json
import os
import sys


class DisplayMode(ctypes.Structure):
    _fields_ = [('device', wintypes.WCHAR * 32), ('spec', wintypes.WORD),
               ('driver', wintypes.WORD), ('size', wintypes.WORD),
               ('extra', wintypes.WORD), ('fields', wintypes.DWORD),
               ('position_x', wintypes.LONG), ('position_y', wintypes.LONG),
               ('orientation', wintypes.DWORD), ('fixed_output', wintypes.DWORD),
               ('color', wintypes.SHORT), ('duplex', wintypes.SHORT),
               ('y_resolution', wintypes.SHORT), ('tt_option', wintypes.SHORT),
               ('collate', wintypes.SHORT), ('form', wintypes.WCHAR * 32),
               ('log_pixels', wintypes.WORD), ('bits', wintypes.DWORD),
               ('width', wintypes.DWORD), ('height', wintypes.DWORD),
               ('flags', wintypes.DWORD), ('frequency', wintypes.DWORD),
               ('icm_method', wintypes.DWORD), ('icm_intent', wintypes.DWORD),
               ('media', wintypes.DWORD), ('dither', wintypes.DWORD),
               ('reserved1', wintypes.DWORD), ('reserved2', wintypes.DWORD),
               ('panning_width', wintypes.DWORD), ('panning_height', wintypes.DWORD)]


def prepare(api=None):
    if (sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_OS') != 'Windows'):
        raise RuntimeError('display preparation is restricted to GitHub Windows runners')
    api = api or ctypes.WinDLL('user32', use_last_error=True)
    mode = DisplayMode(size=ctypes.sizeof(DisplayMode))
    if not api.EnumDisplaySettingsW(None, 0xffffffff, ctypes.byref(mode)):
        raise OSError('cannot read the CI display mode')
    before = [mode.width, mode.height]
    mode.width, mode.height = 1920, 1080
    mode.fields = 0x80000 | 0x100000  # DM_PELSWIDTH | DM_PELSHEIGHT
    for flag in (2, 0):  # CDS_TEST, then apply without persisting registry settings.
        result = api.ChangeDisplaySettingsW(ctypes.byref(mode), flag)
        if result != 0:
            raise OSError(f'CI display change failed: flag={flag}, result={result}')
    if not api.EnumDisplaySettingsW(None, 0xffffffff, ctypes.byref(mode)):
        raise OSError('cannot verify the CI display mode')
    after = [mode.width, mode.height]
    if after != [1920, 1080]:
        raise RuntimeError(f'CI desktop was not resized: {after}')
    return dict(before=before, after=after)


if __name__ == '__main__':
    print(json.dumps(prepare()))
