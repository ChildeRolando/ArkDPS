using System;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using ArkDPS.Desktop;

internal static class SamplingRejectionTests
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private static int passed;
    private const string Reason = "玩家归属证据尚未确认";
    private const string Label = "当前样本未通过归属校验：" + Reason;

    private static void Check(string name, bool value)
    {
        if (!value) throw new InvalidOperationException(name);
        passed++;
    }

    internal static void Run()
    {
        if (Application.Current is null)
        {
            var app = new App();
            app.InitializeComponent();
        }
        var window = new MainWindow();
        var update = typeof(MainWindow).GetMethod("UpdateLiveStatus", Private)!;
        var side = typeof(MainWindow).GetMethod("RefreshSideRanking", Private)!;
        var updatePanel = typeof(MainWindow).GetMethod("UpdateLivePanel", Private)!;
        var panel = new StatisticPanel();
        const string EmptyText = "等待可靠战斗数据 · 未知不记零";
        var emptyView = JsonSerializer.SerializeToElement(new
        {
            config = new { panel_id = "owned-sampling-rejection", category = "damage", ranking = "total", rate_basis = "full_cycle" },
            entries = Array.Empty<object>(), empty_text = EmptyText
        });
        var notice = new HudStatusNotice();
        try
        {
            foreach (string platform in new[] { "native_pc", "mumu" })
            foreach (string theme in new[] { "lone-trail", "people", "babel" })
            {
                Theme.Apply(theme);
                var state = new JsonObject
                {
                    ["effective_connection_config"] = new JsonObject { ["platform"] = platform },
                    ["layout"] = new JsonObject { ["event_observation"] = true },
                    ["status"] = "sampling_rejected", ["status_text"] = Label,
                    ["observer_status"] = "ready", ["error"] = "",
                    ["sampling_rejection"] = new JsonObject { ["code"] = "mapping_unavailable", ["reason"] = Reason }
                };
                void Render()
                {
                    window.State = JsonSerializer.SerializeToElement(state);
                    update.Invoke(window, new object[] { state["status"]!.GetValue<string>(), state["status_text"]!.GetValue<string>() });
                    window.SideDataStatus.GetBindingExpression(TextBlock.TextProperty)!.UpdateTarget();
                }
                Render();
                side.Invoke(window, null);
                updatePanel.Invoke(window, new object[] { "owned-sampling-rejection", panel, emptyView, false });
                Check(theme + platform + " main preserves the nonfatal reason", window.Status.Text == Label);
                Check(theme + platform + " side footer shares the exact reason", window.SideDataStatus.Text == Label);
                Check(theme + platform + " empty side retains unknown with its reason", window.SideFriendly.RankingPanel.Empty.Text == Label);
                Check(theme + platform + " ordinary empty panel shows the same reason", panel.Empty.Text == Label);
                Check(theme + platform + " Babel cannot invent a battle wait", window.BabelMastheadStageCode.Text == Label);
                notice.Update(new HudDataStatus("sampling_rejected", window.Status.Text, false, false));
                Check(theme + platform + " HUD displays nonfatal rejection", notice.Visibility == Visibility.Visible && notice.Message == Label);

                state["status"] = "stale";
                state["status_text"] = "数据已过期 · " + Label + " · 保留最后有效记录";
                Render();
                notice.Update(new HudDataStatus("stale", window.Status.Text, false, false));
                Check(theme + platform + " retained data remains explicitly expired", window.Status.Text.Contains("数据已过期") && notice.Message.Contains(Reason));

                state["status"] = "disconnected";
                state["status_text"] = "采集连接读取失败";
                state["error"] = "transport denied";
                Render();
                notice.Update(new HudDataStatus("disconnected", window.Status.Text, false, false));
                Check(theme + platform + " transport failure wins over retained rejection", !window.Status.Text.Contains(Reason) && notice.Message.Contains(platform == "native_pc" ? "transport denied" : "读取失败"));

                state["status"] = "waiting";
                state["status_text"] = "等待战斗数据";
                state["error"] = "";
                state["sampling_rejection"] = null;
                Render();
                side.Invoke(window, null);
                updatePanel.Invoke(window, new object[] { "owned-sampling-rejection", panel, emptyView, false });
                Check(theme + platform + " side rerender removes the old rejection", !window.SideFriendly.RankingPanel.Empty.Text.Contains(Reason));
                Check(theme + platform + " same ordinary view restores its own empty text", panel.Empty.Text == EmptyText);
                notice.Update(new HudDataStatus("waiting", window.Status.Text, false, false));
                Check(theme + platform + " real waiting clears the rejection", !window.Status.Text.Contains(Reason) && window.Status.Text.Contains("等待进入战斗") && notice.Visibility == Visibility.Collapsed);
            }
            Check("rejection checks create no HWND, worker or tray", new WindowInteropHelper(window).Handle == IntPtr.Zero &&
                typeof(MainWindow).GetField("bridge", Private)!.GetValue(window) is null &&
                typeof(MainWindow).GetField("tray", Private)!.GetValue(window) is null);
        }
        finally
        {
            Theme.Apply("lone-trail");
            typeof(MainWindow).GetField("quitting", Private)!.SetValue(window, true);
            window.Close();
            ((IDisposable)typeof(MainWindow).GetField("recoveryCancellation", Private)!.GetValue(window)!).Dispose();
        }
        Check("rejection checks leave no windows", Application.Current!.Windows.Count == 0);
        Console.WriteLine($"PASS_SAMPLING_REJECTION {passed} assertions (injected states/controls only; no Show/input/targets)");
    }
}
