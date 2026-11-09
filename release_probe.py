"""Opt-in measurements on the real GUI; never manufactures game observations."""
import ctypes
from ctypes import wintypes
import json
import math
import platform
import subprocess
import sys
import time


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def summarize(points):
    # Frozen frames/menus and the first baseline are not active combat samples.
    active = []
    for previous, point in zip(points, points[1:]):
        if (point['session'] == previous['session'] and
                point['game_time'] > previous['game_time'] and
                point['frame'] > previous['frame']):
            active.append(dict(point, interval_ms=1000 * (
                point['captured_monotonic'] - previous['captured_monotonic'])))
    groups = {}
    for p in active:
        key = f"speed_level={p['speed_level']},panels={p['panels']},huds={p['huds']}"
        groups.setdefault(key, []).append(p)
    return {key: dict(samples=len(rows), **{
        field: dict(p50=percentile([p[field] for p in rows], .5),
                    p95=percentile([p[field] for p in rows], .95))
        for field in ('interval_ms', 'read_ms', 'render_complete_age_ms')})
        for key, rows in groups.items()}


def process_resources():
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('peak_ws', 'ws', 'peak_paged', 'paged',
            'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    process = kernel.GetCurrentProcess()
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(process, *(ctypes.byref(t) for t in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    cpu = sum((t.dwHighDateTime << 32) | t.dwLowDateTime for t in times[2:]) / 1e7
    counters = Counters(cb=ctypes.sizeof(Counters))
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), ctypes.sizeof(counters)):
        raise ctypes.WinError(ctypes.get_last_error())
    return dict(monotonic=time.monotonic(), cpu_seconds=cpu,
                private_bytes=counters.private, working_set_bytes=counters.ws)


class LiveProbe:
    def __init__(self, app, output, seconds, panels, expected=None):
        from collector import ROOT
        from release_identity import load_manifest
        self.app, self.output = app, output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.points, self.resources, self.errors = [], [], []
        self.started = time.monotonic()
        if getattr(sys, 'frozen', False):
            self.head = load_manifest(ROOT, verify=True)['head']
        else:
            self.head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        if expected and expected != self.head:
            raise RuntimeError('live acceptance candidate mismatch')
        while len(app.panels) < panels:
            app.duplicate_panel(app.panels[0])
        while len(app.panels) > panels:
            app.delete_panel(app.panels[-1])
        app.create_hud(app.panels[0])
        original_render = app.render

        def render(sample):
            original_render(sample)
            point = dict(session=sample['session'], frame=sample['frame'],
                game_time=sample['game_time'], captured_monotonic=sample['captured_monotonic'],
                speed_level=sample.get('speed'), read_ms=sample.get('read_ms', 0),
                panels=len(app.panels), huds=len(app.huds), event_status=sample.get('event_status'))
            def completed():
                if app.quitting:
                    return
                app.root.update_idletasks()
                point['render_complete_age_ms'] = 1000 * (
                    time.monotonic() - point['captured_monotonic'])
                self.points.append(point)
            app.root.after_idle(completed)
        app.render = render
        original_error = app.root.report_callback_exception
        def callback_error(*exc):
            self.errors.append(repr(exc))
            original_error(*exc)
        app.root.report_callback_exception = callback_error
        self.sample_resources()
        app.root.after(round(seconds * 1000), app.quit)

    def sample_resources(self):
        if self.app.quitting:
            return
        try:
            self.resources.append(process_resources())
        except Exception as exc:
            self.errors.append(str(exc))
        self.app.root.after(1000, self.sample_resources)

    def finish(self):
        elapsed = time.monotonic() - self.started
        cpu_percent = None
        if len(self.resources) > 1:
            first, last = self.resources[0], self.resources[-1]
            # One logical CPU = 100%, not normalized by machine core count.
            cpu_percent = 100 * (last['cpu_seconds'] - first['cpu_seconds']) / (
                last['monotonic'] - first['monotonic'])
        groups = summarize(self.points)
        report = dict(head=self.head, kind='live-native-artifact-measurement',
            frozen=bool(getattr(sys, 'frozen', False)), platform=platform.platform(),
            elapsed_seconds=elapsed, published_samples=len(self.points), groups=groups,
            cpu_percent_one_core=cpu_percent, resources=self.resources, errors=self.errors,
            points=self.points, shutdown=self.app.shutdown_report,
            connection=self.app.connection_diagnostic, event_status=self.app.event_status,
            event_error=self.app.event_error,
            status='RECORDED' if groups and not self.errors and
                self.app.shutdown_report and self.app.shutdown_report['ok'] else 'INCOMPLETE',
            interpretation='Tk idle/update_idletasks completion; not display scanout. '
                'speed_level is the native raw field, not inferred from wall time. '
                'RECORDED is not an independent release PASS.')
        (self.output / 'measurement.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
