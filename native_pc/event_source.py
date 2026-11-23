"""Select and own the startup runtime for a single PC event source."""

from contextlib import nullcontext
from pathlib import Path


def _native_pc_frida_runtime(game_root, runtime_dir, *, startup_trace=None):
    from native_pc.frida_runtime import NativePcFridaRuntime
    return NativePcFridaRuntime(game_root, runtime_dir=runtime_dir, startup_trace=startup_trace)


class PcEventSourceCleanupError(RuntimeError):
    pass


class PcEventSource:
    """Own the startup runtime. CollectionSession owns its attached observer."""

    def __init__(self, runtime_dir, *, observer_factory=None):
        self.runtime_dir = runtime_dir
        self.observer_factory = observer_factory
        self.runtime = None
        self.game_root = None

    def select(self, *, enabled, game_root=None, explicit_retry=False, startup_trace=None,
               admission=None, cancelled=lambda: False, on_start_error=None):
        use_default = enabled and game_root and self.observer_factory is None
        key = Path(game_root).expanduser().resolve() if use_default else None
        if self.runtime is not None and (not use_default or key != self.game_root):
            self.close()
        if not use_default:
            return self.observer_factory if enabled else None
        if self.runtime is None:
            options = {} if startup_trace is None else dict(startup_trace=startup_trace)
            candidate = _native_pc_frida_runtime(game_root, self.runtime_dir, **options)
            with (admission if admission is not None else nullcontext()):
                # Publish ownership before starting, under the caller's shutdown gate.
                self.runtime, self.game_root = candidate, key
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
            self.runtime, self.game_root = None, None
