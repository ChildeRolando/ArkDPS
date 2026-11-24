"""Select and own the startup runtime for a single PC event source."""

from contextlib import nullcontext
from pathlib import Path


def _native_pc_frida_runtime(game_root, runtime_dir, *, startup_trace=None):
    from native_pc.frida_runtime import NativePcFridaRuntime
    return NativePcFridaRuntime(game_root, runtime_dir=runtime_dir, startup_trace=startup_trace)


def _native_pc_hook_runtime(game_root, runtime_dir, *, collector_root, startup_trace=None):
    from native_pc.native_hook_runtime import NativeHookRuntime
    return NativeHookRuntime(game_root, runtime_dir=runtime_dir, collector_root=collector_root,
                             startup_trace=startup_trace)


class PcEventSourceCleanupError(RuntimeError):
    pass


class PcEventSource:
    """Own the startup runtime. CollectionSession owns its attached observer."""

    def __init__(self, runtime_dir, *, observer_factory=None, collector_root=None):
        self.runtime_dir = runtime_dir
        self.observer_factory = observer_factory
        self.collector_root = collector_root if collector_root is not None else Path(__file__).resolve().parents[1]
        self.runtime = None
        self.game_root = None
        self.backend = None

    def select(self, *, enabled, game_root=None, backend='frida', feed_path=None, explicit_retry=False, startup_trace=None,
               admission=None, cancelled=lambda: False, on_start_error=None):
        use_default = enabled and backend == 'frida' and game_root and self.observer_factory is None
        use_native = enabled and backend == 'native' and not feed_path and game_root and self.observer_factory is None
        use_runtime = use_default or use_native
        key = Path(game_root).expanduser().resolve() if use_runtime else None
        if self.runtime is not None and (not use_runtime or key != self.game_root or backend != self.backend):
            self.close()
        if enabled and self.observer_factory is not None:
            return self.observer_factory
        if enabled and backend == 'native' and not use_native:
            if not feed_path:
                def native_hooks(collector, remote):
                    if (Path(self.collector_root).parent / 'metadata' / 'release.json').is_file():
                        from native_pc.internal_entry import native_hook_package_profile
                        if native_hook_package_profile(self.collector_root) is None:
                            raise RuntimeError('此便携包缺少自研 PC 采集组件，不从便携包加载。')
                    raise RuntimeError('自研实时采集需要游戏目录，并须在客户端启动前准备独立原生运行时。')

                return native_hooks
            opened = False

            def live_observer(collector, _remote):
                nonlocal opened
                if opened:
                    raise RuntimeError('自研事件输入按战局独立，请选择新战局的 JSONL 并重连。')
                from native_pc.internal_entry import live_native_host
                from native_pc.live_event_source import LiveNativeEventProbe
                opened = True
                return LiveNativeEventProbe(collector, Path(feed_path), live_native_host(self.collector_root))

            return live_observer
        if not use_runtime:
            return self.observer_factory if enabled else None
        if self.runtime is None:
            options = {} if startup_trace is None else dict(startup_trace=startup_trace)
            candidate = (_native_pc_hook_runtime(game_root, self.runtime_dir,
                         collector_root=self.collector_root, **options) if use_native
                         else _native_pc_frida_runtime(game_root, self.runtime_dir, **options))
            with (admission if admission is not None else nullcontext()):
                # Publish ownership before starting, under the caller's shutdown gate.
                self.runtime, self.game_root, self.backend = candidate, key, backend
                if cancelled():
                    self.close()
                    raise InterruptedError('采集器在启动运行时创建期间关闭')
                try:
                    self.runtime.start()
                except Exception as exc:
                    if on_start_error is None:
                        raise
                    on_start_error(exc)
        elif explicit_retry:
            self.runtime.retry()
        return self.runtime.observer_factory

    def close(self):
        if self.runtime is not None:
            try:
                self.runtime.close()
            except Exception as exc:
                raise PcEventSourceCleanupError(f'PC 启动加载器清理失败：{exc}') from exc
            self.runtime, self.game_root, self.backend = None, None, None
