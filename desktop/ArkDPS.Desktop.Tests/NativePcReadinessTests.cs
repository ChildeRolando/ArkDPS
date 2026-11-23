using System;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using ArkDPS.Desktop;

internal static class NativePcReadinessTests
{
    private static int passed;
    private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;
    private static void Check(string name, bool condition)
    {
        if (!condition) throw new InvalidOperationException(name);
        passed++;
    }

    private static JsonObject State(string phase = "starting", string? code = null, long? armed = null,
        bool? thread = true, string platform = "native_pc", bool events = true, string observer = "attaching",
        string? error = null) => new()
    {
        ["effective_connection_config"] = new JsonObject { ["platform"] = platform },
        ["layout"] = new JsonObject { ["event_observation"] = events },
        ["status"] = "waiting", ["status_text"] = "等待战斗数据", ["error"] = error,
        ["observer_status"] = observer,
        ["native_pc_bootstrap"] = new JsonObject
        {
            ["status"] = phase, ["code"] = code, ["armed_filetime"] = armed, ["thread_alive"] = thread,
        },
    };

    private static JsonElement Json(JsonObject state) => JsonSerializer.SerializeToElement(state);
    private static NativePcReadiness Read(JsonObject state) => NativePcReadiness.From(Json(state));

    internal static void Run()
    {
        var preparing = State();
        Check("starting is not watch readiness", Read(preparing).Label == NativePcReadiness.Preparing);
        var armed = State("waiting_for_process", armed: 134398900000000000);
        Check("only a running established watch authorizes the launch prompt", Read(armed).Label == NativePcReadiness.Armed);
        var restart = State("waiting_for_process", "restart_required", 134398900000000000);
        Check("old process requires restart while retaining ArkDPS", Read(restart).Label == NativePcReadiness.Restart);
        Check("armed field without the watching phase cannot claim watch ready", Read(State("starting", armed: 134398900000000000)).Label == NativePcReadiness.Preparing);
        foreach (var incomplete in new[]
        {
            State("waiting_for_process"), State("waiting_for_process", armed: 0),
            State("waiting_for_process", armed: 134398900000000000, thread: null),
            State("waiting_for_process", armed: 134398900000000000, thread: false),
            State("ready", armed: 134398900000000000),
        })
            Check("missing runtime proof never creates ready", Read(incomplete).Label == NativePcReadiness.Preparing);
        var cancelled = State("waiting_for_process", armed: 134398900000000000);
        cancelled["native_pc_bootstrap"]!["cancelled"] = true;
        Check("cancelled watch never invites game launch", Read(cancelled).Label == NativePcReadiness.Preparing);
        var failure = State("waiting_for_endpoint", "endpoint_in_use");
        failure["native_pc_bootstrap"]!["error"] = "监听端口被其他程序占用";
        Check("actual bootstrap failure retains its reason", Read(failure) is { IsError: true, Label: "监听端口被其他程序占用" });
        Check("Gadget loading is not an armed launch prompt", Read(State("loading", armed: 134398900000000000)).Phase == "loading");
        Check("owned pending loader stays a progress state", Read(State("loading", "loader_pending", 134398900000000000)) is { Phase: "loading", IsError: false });
        Check("authentication is not observer readiness", Read(State("authenticating", armed: 134398900000000000)).Phase == "authenticating");
        var authenticated = State("ready", armed: 134398900000000000);
        authenticated["native_pc_bootstrap"]!["endpoint_owned"] = true;
        authenticated["native_pc_bootstrap"]!["expected_resident"] = true;
        authenticated["native_pc_bootstrap"]!["lease"] = new JsonObject { ["status"] = "ready" };
        Check("authenticated endpoint still explains observer preparation", Read(authenticated) is { Phase: "connected", Label: "PC 事件组件已连接，正在准备采集" });
        authenticated["observer_status"] = "waiting_game_init";
        Check("authenticated initialization remains distinct from battle wait", Read(authenticated).Label.Contains("等待游戏初始化"));
        authenticated["observer_status"] = "ready";
        Check("ready observer restores ordinary battle status", !Read(authenticated).Active);
        Check("MuMu retains its own status semantics", !Read(State(platform: "mumu")).Active);
        Check("disabled events retain snapshot status semantics", !Read(State(events: false)).Active && !Read(State(observer: "disabled")).Active);
        Check("worker error takes precedence over previously ready observer", Read(State(observer: "ready", error: "采集进程读取失败")) is { IsError: true, Label: "采集进程读取失败" });

        JsonObject NativeState(string phase = "starting", bool watch = false)
        {
            var native = State(phase, armed: watch ? 134398900000000000 : null);
            native["effective_connection_config"]!["native_pc_event_backend"] = "native";
            native["native_pc_bootstrap"]!["provider"] = "arkdps-native-hooks";
            native["native_pc_bootstrap"]!["frida_required"] = false;
            native["native_pc_bootstrap"]!["cancelled"] = false;
            return native;
        }
        Check("native startup is not Frida readiness", Read(NativeState()).Label == NativePcReadiness.NativePreparing);
        var nativeArmed = NativeState("waiting_for_process", true);
        Check("native watch has its own explicit launch prompt", Read(nativeArmed).Label == NativePcReadiness.NativeArmed);
        var nativeLoading = NativeState("loading", true);
        Check("native early load has no Frida authentication claim", Read(nativeLoading) is { Phase: "loading", Label: "正在预载自研 PC 组件，请稍候" });
        var nativeRestart = NativeState("waiting_for_process", true);
        nativeRestart["native_pc_bootstrap"]!["code"] = "restart_required";
        Check("native restart instruction retains its real watch", Read(nativeRestart).Phase == "restart_required");
        foreach (var field in new[] { "armed_filetime", "thread_alive", "cancelled" })
        {
            var missing = (JsonObject)nativeArmed.DeepClone();
            ((JsonObject)missing["native_pc_bootstrap"]!).Remove(field);
            Check("native watch missing proof cannot invite launch: " + field, Read(missing).Label == NativePcReadiness.NativePreparing);
        }
        var nativeReady = NativeState("ready", true);
        nativeReady["native_pc_bootstrap"]!["expected_resident"] = true;
        nativeReady["native_pc_bootstrap"]!["ready_generation"] = 1;
        nativeReady["native_pc_bootstrap"]!["lease"] = new JsonObject { ["pid"] = 42, ["created"] = 134398900000000001, ["source_build"] = "verified-build" };
        Check("native prepared does not claim JS observer ready", Read(nativeReady) is { Phase: "connected", Label: "自研 PC 组件已预载，正在准备采集" });
        nativeReady["observer_status"] = "waiting_game_init";
        Check("native VM wait is explicit", Read(nativeReady).Label == "自研 PC 组件已预载，等待游戏初始化");
        nativeReady["native_pc_bootstrap"]!["ready_generation"] = 0;
        Check("native ready phase without a real generation is not prepared", Read(nativeReady).Label == NativePcReadiness.NativePreparing);
        nativeReady["native_pc_bootstrap"]!["ready_generation"] = true;
        Check("Boolean cannot impersonate native ready generation", Read(nativeReady).Label == NativePcReadiness.NativePreparing);
        nativeReady["observer_status"] = "ready";
        Check("native observer READY is an independent proof", !Read(nativeReady).Active);
        var nativeFailed = NativeState("cleanup_pending", true);
        nativeFailed["native_pc_bootstrap"]!["error"] = "自研资源待清理";
        Check("native ownership failure is not hidden", Read(nativeFailed) is { IsError: true, Label: "自研资源待清理" });
        var cooperative = NativeState();
        ((JsonObject)cooperative["native_pc_bootstrap"]!).Remove("provider");
        Check("cooperative native backend never inherits Frida bootstrap labels", !Read(cooperative).Active);

        if (Application.Current is null)
        {
            var app = new App();
            app.InitializeComponent();
        }
        var window = new MainWindow();
        try
        {
            var render = typeof(MainWindow).GetMethod("UpdateLiveStatus", PrivateInstance)!;
            void Render(JsonObject state)
            {
                window.State = Json(state);
                render.Invoke(window, new object[] { state["status"]!.GetValue<string>(), state["status_text"]!.GetValue<string>() });
                window.SideDataStatus.GetBindingExpression(TextBlock.TextProperty)!.UpdateTarget();
            }
            foreach (var (state, text) in new[]
            {
                (preparing, NativePcReadiness.Preparing), (armed, NativePcReadiness.Armed),
                (restart, NativePcReadiness.Restart),
            })
            {
                Render(state);
                typeof(MainWindow).GetMethod("RefreshSideRanking", PrivateInstance)!.Invoke(window, null);
                Check("main window renders authoritative watch guidance", window.Status.Text == text);
                Check("docked footer shares exact watch guidance", window.SideDataStatus.Text == text);
                Check("empty docked ranking gives launch guidance before battle wait", window.SideFriendly.RankingPanel.Empty.Text == text);
                Check("Babel masthead cannot mistake unready startup for battle wait", !window.BabelMastheadStageCode.Text.Contains("等待进入战斗"));
            }
            Render(authenticated);
            Check("fully ready observer still waits for battle normally", window.Status.Text == "PC 版 · 等待进入战斗");
            Render(State(platform: "mumu", observer: "ready"));
            Check("MuMu main status is unchanged", window.Status.Text.Contains("观察中") && window.Status.Text.Contains("等待进入战斗"));
            Render(State(events: false));
            Check("snapshot only native mode retains its normal lobby status", window.Status.Text == "PC 版 · 等待进入战斗");
            Render(failure);
            typeof(MainWindow).GetMethod("SetSideAttachmentStatus", PrivateInstance)!.Invoke(window, new object[] { "PC 版 · 独立侧边窗口" });
            Check("docked footer shows real error above placement hints", window.SideDataStatus.Text == "监听端口被其他程序占用" && window.SideAttachmentStatus.Visibility == Visibility.Collapsed);
            Check("readiness checks created neither HWND nor worker nor tray", new WindowInteropHelper(window).Handle == IntPtr.Zero &&
                typeof(MainWindow).GetField("bridge", PrivateInstance)!.GetValue(window) is null &&
                typeof(MainWindow).GetField("tray", PrivateInstance)!.GetValue(window) is null);
        }
        finally
        {
            typeof(MainWindow).GetField("quitting", PrivateInstance)!.SetValue(window, true);
            window.Close();
            ((IDisposable)typeof(MainWindow).GetField("recoveryCancellation", PrivateInstance)!.GetValue(window)!).Dispose();
        }
        Check("headless readiness checks leave no windows", Application.Current!.Windows.Count == 0);
        Console.WriteLine($"PASS_NATIVE_PC_READINESS {passed} assertions (injected worker states; no Show/input/target access)");
    }
}
