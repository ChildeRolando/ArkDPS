using System;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Interop;
using ArkDPS.Desktop;

internal static class RefreshBudgetTests
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private static int passed;
    private static void Check(string name, bool value)
    {
        if (!value) throw new InvalidOperationException(name);
        passed++;
    }

    private static JsonObject Snapshot(string status = "live", int amount = 111) => new()
    {
        ["protocol"] = 1, ["battle_id"] = "injected-refresh-battle", ["game_time"] = 12,
        ["status"] = status,
        ["status_text"] = status == "stale" ? "数据已过期 · 等待下一次可靠采样" : "正在统计",
        ["error"] = "", ["warnings"] = new JsonArray(), ["history"] = new JsonArray(),
        ["observer_status"] = "ready", ["event_status"] = "recording",
        ["effective_connection_config"] = new JsonObject { ["platform"] = "mumu" },
        ["layout"] = new JsonObject { ["theme"] = Theme.Key, ["number_units"] = "western", ["event_observation"] = true },
        ["panels"] = new JsonArray(new JsonObject
        {
            ["config"] = new JsonObject { ["panel_id"] = "injected-refresh-panel", ["category"] = "damage", ["ranking"] = "total", ["rate_basis"] = "recent_5s" },
            ["title"] = "已观察伤害", ["source"] = new JsonObject { ["owner-a"] = new JsonObject { ["damage"] = amount } },
            ["entries"] = new JsonArray(new JsonObject { ["owner_id"] = "owner-a", ["name"] = "fixture actor", ["value"] = amount, ["display_value"] = amount.ToString() }),
        }),
        ["enemy"] = new JsonObject { ["status"] = "ready", ["rows"] = new JsonArray(), ["series"] = new JsonArray() },
    };

    internal static void Run()
    {
        if (Application.Current is null) { var app = new App(); app.InitializeComponent(); }
        var window = new MainWindow();
        var type = typeof(MainWindow);
        type.GetField("initialized", Private)!.SetValue(window, true); // Skip placement/startup; controls only.
        var stale = type.GetMethod("MarkDataStale", Private)!;
        var unavailable = type.GetMethod("ShowWorkerUnavailable", Private)!;
        var responseStale = type.GetField("responseStale", Private)!;
        var workerUnavailable = type.GetField("workerUnavailable", Private)!;
        try
        {
            foreach (string skin in new[] { "lone-trail", "people", "babel" })
            {
                Theme.Apply(skin);
                workerUnavailable.SetValue(window, false);
                window.ApplyState(JsonSerializer.SerializeToElement(Snapshot()));
                var panel = window.Panels["injected-refresh-panel"];
                stale.Invoke(window, new object[] { "stale", "数据已过期 · 等待 worker 响应" });
                Check(skin + " delayed reply is stale without retiring its worker",
                    (bool)responseStale.GetValue(window)! && !(bool)workerUnavailable.GetValue(window)!);
                Check(skin + " delayed reply retains the exact accepted row",
                    panel.Rows.Items.Count == 1 && ((RowItem)panel.Rows.Items[0]).Entry.Number("value") == 111);
                Check(skin + " delayed reply does not initiate worker recovery",
                    type.GetField("recoveryTask", Private)!.GetValue(window) is null && window.Status.Text.Contains("等待 worker 响应"));

                window.ApplyState(JsonSerializer.SerializeToElement(Snapshot("stale")));
                Check(skin + " responsive IPC does not certify an old capture as fresh",
                    !(bool)responseStale.GetValue(window)! && window.State.Text("status") == "stale" && window.Status.Text.Contains("数据已过期"));
                window.ApplyState(JsonSerializer.SerializeToElement(Snapshot(amount: 222)));
                Check(skin + " next accepted fresh reply recovers in the same panel",
                    !window.Status.Text.Contains("数据已过期") && panel.Rows.Items.Count == 1 &&
                    ((RowItem)panel.Rows.Items[0]).Entry.Number("value") == 222 && ReferenceEquals(panel, window.Panels["injected-refresh-panel"]));

                unavailable.Invoke(window, new object[] { "injected broken stdio", "自动重连次数已用完" });
                Check(skin + " reported transport loss enters a distinct unavailable state",
                    (bool)workerUnavailable.GetValue(window)! && window.Status.Text.Contains("自动重连次数已用完") &&
                    ((RowItem)panel.Rows.Items[0]).Entry.Number("value") == 222);
            }
            workerUnavailable.SetValue(window, false);
            var partial = Snapshot();
            partial["enemy"] = new JsonObject
            {
                ["status"] = "ready", ["scope"] = "native_pc_enemy_events", ["ready"] = true,
                ["observed_subset_only"] = true, ["enemy_state_complete"] = false,
                ["rows"] = new JsonArray(), ["series"] = new JsonArray(),
                ["totals"] = new JsonObject { ["entered_total_hp"] = 100, ["total_hp_complete"] = false },
                ["last_point"] = new JsonObject { ["our_damage_known"] = false, ["our_damage_complete"] = false },
            };
            window.ApplyState(JsonSerializer.SerializeToElement(partial));
            Check("partial committed enemy facts are identified without assigning missing damage a zero",
                window.EnemyStatusText.Text == "部分敌情 · 已观测" && window.EnemyTotalHp.Text == "≥ 100" &&
                window.EnemyOurDamage.Text == "—");
            partial["enemy"] = new JsonObject
            {
                ["status"] = "ready", ["hp_scope"] = "local_account_unverified", ["rows_scope"] = "battlefield",
                ["damage_scope"] = "local_account", ["enemy_state_complete"] = true,
                ["rows"] = new JsonArray(), ["series"] = new JsonArray(), ["totals"] = new JsonObject(),
                ["last_point"] = new JsonObject { ["our_effective_damage"] = 20,
                    ["our_damage_known"] = true, ["our_damage_complete"] = true },
            };
            window.ApplyState(JsonSerializer.SerializeToElement(partial));
            Check("defense shows local damage and leaves unverified enemy HP scope blank",
                window.EnemyTotalHp.Text == "—" && window.EnemyOurDamage.Text == "20" &&
                window.EnemyOurDamageLabel.Text == "本家对敌扣血" && window.EnemyStatusHint.Text.Contains("敌人列表为全场"));
            Check("refresh checks create no HWND, worker or tray",
                new WindowInteropHelper(window).Handle == IntPtr.Zero && type.GetField("bridge", Private)!.GetValue(window) is null &&
                type.GetField("tray", Private)!.GetValue(window) is null);
        }
        finally
        {
            type.GetField("quitting", Private)!.SetValue(window, true);
            window.Close();
        }
        Console.WriteLine($"PASS_REFRESH_BUDGET {passed} assertions (injected controls; no Show/input/target access)");
    }
}
