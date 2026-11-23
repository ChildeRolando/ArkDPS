using System.Text.Json;

namespace ArkDPS.Desktop;

// Presentation of the worker's actual process watch and authenticated endpoint.
// This does not establish a watch, compare process births or infer readiness from time.
internal readonly record struct NativePcReadiness(string Phase, string Label, bool IsError = false)
{
    internal const string Preparing = "正在准备监听，等监听就绪后再启动游戏";
    internal const string Armed = "监听已就绪，请启动游戏";
    internal const string Restart = "保持 ArkDPS 开启，正常重启游戏";
    internal bool Active => !string.IsNullOrEmpty(Label);
    internal bool NeedsAttention => Active && Phase != "connected";

    internal static NativePcReadiness From(JsonElement state)
    {
        var config = state.Get("effective_connection_config");
        if (config.ValueKind != JsonValueKind.Object) config = state.Get("connection_config");
        if (config.Text("platform") != "native_pc" || config.Text("native_pc_event_backend") == "native")
            return default;
        string observer = state.Text("observer_status");
        if (observer == "disabled" || state.Get("layout").Get("event_observation").ValueKind == JsonValueKind.False)
            return default;
        // A real worker failure must stay visible even if its last bootstrap was ready.
        if (state.Text("error") is { Length: > 0 } error)
            return new("failed", error, true);
        // Observer readiness is independent proof that the runtime is already connected.
        // Battle/lobby status can resume its ordinary meaning at this point.
        if (observer == "ready" || observer.Length == 0 && state.Text("event_status") == "recording")
            return default;

        var bootstrap = state.Get("native_pc_bootstrap");
        string phase = bootstrap.Text("status"), code = bootstrap.Text("code");
        bool alive = bootstrap.Bool("thread_alive") && !bootstrap.Bool("cancelled");
        var armed = bootstrap.Get("armed_filetime");
        bool watchEstablished = alive && armed.ValueKind == JsonValueKind.Number && armed.TryGetInt64(out long birthFence) && birthFence > 0;
        if (watchEstablished && code == "restart_required") return new("restart_required", Restart);
        if (phase == "waiting_for_process" && watchEstablished && code.Length == 0) return new("armed", Armed);
        if (phase == "loading" && code is "" or "loader_pending")
            return new("loading", "正在连接新启动的 PC 游戏，请稍候");
        if (phase == "authenticating" && code.Length == 0)
            return new("authenticating", "正在认证 PC 事件组件，请稍候");
        if (phase == "failed" || phase == "process_failed" || code.Length > 0)
        {
            string reason = bootstrap.Text("error");
            return new("failed", reason.Length > 0 ? reason : "监听准备未完成，请查看连接诊断", true);
        }
        bool authenticated = phase == "ready" && watchEstablished && bootstrap.Bool("endpoint_owned") &&
            bootstrap.Bool("expected_resident") && bootstrap.Get("lease").Text("status") == "ready";
        if (authenticated)
            return new("connected", observer == "waiting_game_init"
                ? "PC 事件组件已连接，等待游戏初始化"
                : "PC 事件组件已连接，正在准备采集");
        return new("preparing", Preparing);
    }
}
