using System;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Interop;
using ArkDPS.Desktop;

internal static class MultiplayerSettingsTests
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private static int passed;
    private static void Check(string name, bool value)
    {
        if (!value) throw new InvalidOperationException(name);
        passed++;
    }

    private static JsonObject Player(int slot) => new()
    {
        ["slot"] = slot, ["player_uid"] = "91000000" + slot, ["is_local"] = slot == 1,
        ["player_name"] = "fixture player " + slot, ["status"] = "partial", ["observed_damage"] = slot * 100,
        ["view"] = new JsonObject
        {
            ["config"] = new JsonObject { ["panel_id"] = "mp-" + slot, ["category"] = "damage", ["ranking"] = "total", ["rate_basis"] = "engagement" },
            ["title"] = "已观察有效伤害", ["source"] = new JsonObject
            {
                ["91000000" + slot + ":char_same#" + slot] = new JsonObject { ["actor_uid"] = slot, ["player_uid"] = "91000000" + slot, ["damage"] = slot * 100 },
            },
            ["entries"] = new JsonArray(new JsonObject { ["owner_id"] = "91000000" + slot + ":char_same#" + slot,
                ["actor_uid"] = slot, ["value"] = slot * 100, ["display_value"] = "worker-" + slot * 100 }),
        },
    };

    private static JsonElement Snapshot(bool? enabled, string theme, bool? active = null, bool scopeUnverified = false,
        string platform = "native_pc", bool? supported = true)
    {
        var selected = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory,
            "Fixtures", "native-ce5-healing.selected.json")))!.AsObject();
        var live = selected["view"]!.DeepClone();
        live["config"]!["panel_id"] = "optional-live-panel";
        var layout = new JsonObject { ["theme"] = theme, ["number_units"] = "western", ["event_observation"] = true };
        if (enabled.HasValue) layout["multiplayer_enabled"] = enabled.Value;
        return JsonSerializer.SerializeToElement(new JsonObject
        {
            ["protocol"] = 1, ["layout"] = layout, ["battle_id"] = "optional-live-battle", ["game_time"] = 216,
            ["multiplayer_active"] = active, ["multiplayer_mode_pending"] = active.HasValue && active != enabled,
            ["multiplayer_supported"] = supported,
            ["multiplayer_scope_unverified"] = scopeUnverified,
            ["status"] = "live", ["status_text"] = "正在统计", ["warnings"] = new JsonArray(), ["error"] = "",
            ["effective_connection_config"] = new JsonObject { ["platform"] = platform },
            ["observer_status"] = "ready", ["event_status"] = "recording",
            ["panels"] = new JsonArray(live), ["history"] = new JsonArray(), ["history_selected"] = selected,
            ["multiplayer"] = new JsonObject { ["scope_kind"] = "defense", ["scope"] = "optional-defense", ["native_round"] = 1,
                ["available"] = true, ["players"] = new JsonArray(Player(1), Player(2)) },
            ["enemy"] = new JsonObject { ["status"] = "ready", ["rows"] = new JsonArray(), ["series"] = new JsonArray() },
        });
    }

    internal static void Run()
    {
        if (Application.Current is null) { var app = new App(); app.InitializeComponent(); }
        var window = new MainWindow();
        var type = typeof(MainWindow);
        type.GetField("initialized", Private)!.SetValue(window, true); // Skip placement/startup.
        try
        {
            Check("optional four-player UI is off before any worker reply",
                window.MultiplayerEnabledSetting.IsChecked == false && window.MultiplayerNav.Visibility == Visibility.Collapsed);
            foreach (string skin in new[] { "lone-trail", "people", "babel" })
            {
                window.ApplyState(Snapshot(null, skin));
                Check(skin + " old layout with no flag defaults off despite an old multiplayer payload",
                    window.MultiplayerEnabledSetting.IsChecked == false && window.MultiplayerNav.Visibility == Visibility.Collapsed &&
                    window.MultiplayerPage.Ranking.Rows.Items.Count == 0);
                window.Navigate("settings").GetAwaiter().GetResult();
                window.Navigate("multiplayer").GetAwaiter().GetResult();
                Check(skin + " disabled route is rejected without leaving settings",
                    type.GetField("route", Private)!.GetValue(window) as string == "settings" && window.SettingsPage.Visibility == Visibility.Visible);

                var panel = window.Panels["optional-live-panel"];
                string live = panel.View.GetRawText(), history = window.HistoryPanel.View.GetRawText();
                panel.Rows.SelectedItem = panel.Rows.Items[0];
                window.HistoryPanel.Rows.SelectedItem = window.HistoryPanel.Rows.Items[0];
                string? owner = window.LiveInspector.Owner, historyOwner = window.HistoryInspector.Owner;
                window.ApplyState(Snapshot(true, skin));
                Check(skin + " confirmed worker preference enables nav and synchronizes checkbox",
                    window.MultiplayerEnabledSetting.IsChecked == true && window.MultiplayerNav.Visibility == Visibility.Visible &&
                    window.MultiplayerNavLabel.Text == "卫戍队友DPS" &&
                    window.MultiplayerEnabledSetting.Content?.ToString() == "统计卫戍队友DPS");
                window.Navigate("multiplayer").GetAwaiter().GetResult();
                Check(skin + " enabled route shows four-player page with evidenced local choice",
                    window.MultiplayerPage.Visibility == Visibility.Visible && window.MultiplayerPage.SelectedPlayerUid == "910000001" &&
                    window.PageTitle.Text == "卫戍队友DPS");
                window.ContentRoot.Measure(new Size(1320, 900));
                window.ContentRoot.Arrange(new Rect(0, 0, 1320, 900));
                type.GetMethod("ArrangePanels", Private)!.Invoke(window, null);
                window.ContentRoot.UpdateLayout();
                var navContent = (FrameworkElement)window.MultiplayerNav.Content;
                double captionRoom = window.MultiplayerNav.ActualWidth - window.MultiplayerNav.Padding.Left -
                    window.MultiplayerNav.Padding.Right - window.MultiplayerNav.BorderThickness.Left -
                    window.MultiplayerNav.BorderThickness.Right;
                Check(skin + " full teammate DPS navigation caption fits its existing expanded layout",
                    window.MultiplayerNavLabel.Visibility == Visibility.Visible && captionRoom > 0 &&
                    navContent.DesiredSize.Width <= captionRoom);
                window.MultiplayerPage.SelectPlayer("910000002");
                window.MultiplayerPage.Ranking.Rows.SelectedItem = window.MultiplayerPage.Ranking.Rows.Items[0];
                Check(skin + " setup pins the manually selected foreign actor", window.MultiplayerPage.DetailInspector.Owner == "910000002:char_same#2");
                window.ApplyState(Snapshot(false, skin));
                Check(skin + " disabling while on four-player route returns to live and clears cached actors",
                    type.GetField("route", Private)!.GetValue(window) as string == "live" && window.MultiplayerNav.Visibility == Visibility.Collapsed &&
                    window.MultiplayerPage.Visibility == Visibility.Collapsed && window.MultiplayerPage.SelectedPlayerUid is null &&
                    window.MultiplayerPage.DetailInspector.Owner is null && window.MultiplayerPage.Ranking.Rows.Items.Count == 0);
                Check(skin + " turning off four-player UI preserves ordinary live and history views and pins",
                    ReferenceEquals(panel, window.Panels["optional-live-panel"]) && panel.View.GetRawText() == live &&
                    window.HistoryPanel.View.GetRawText() == history && window.LiveInspector.Owner == owner && window.HistoryInspector.Owner == historyOwner);
                window.Navigate("history").GetAwaiter().GetResult();
                window.Navigate("settings").GetAwaiter().GetResult();
                Check(skin + " route switching preserves the off preference and selected history",
                    window.MultiplayerEnabledSetting.IsChecked == false && window.HistoryPanel.View.GetRawText() == history);
                window.ApplyState(Snapshot(true, skin));
                Check(skin + " restored on preference starts clean instead of reviving the old foreign pin",
                    window.MultiplayerEnabledSetting.IsChecked == true && window.MultiplayerPage.SelectedPlayerUid == "910000001" &&
                    window.MultiplayerPage.DetailInspector.Owner is null);

                window.Navigate("multiplayer").GetAwaiter().GetResult();
                window.ApplyState(Snapshot(false, skin, active: true));
                Check(skin + " pending off preserves the current four-player page until the next combat",
                    window.MultiplayerEnabledSetting.IsChecked == false && window.MultiplayerNav.Visibility == Visibility.Visible &&
                    window.MultiplayerPage.Visibility == Visibility.Visible && window.MultiplayerSettingStatus.Visibility == Visibility.Visible &&
                    window.MultiplayerSettingStatus.Text.Contains("只统计本家"));
                window.ApplyState(Snapshot(false, skin, active: false));
                Check(skin + " the new combat applies pending off",
                    window.MultiplayerNav.Visibility == Visibility.Collapsed && window.LivePage.Visibility == Visibility.Visible &&
                    window.MultiplayerSettingStatus.Visibility == Visibility.Collapsed);
                window.ApplyState(Snapshot(true, skin, active: false));
                Check(skin + " pending on keeps the local round and explains the next combat",
                    window.MultiplayerEnabledSetting.IsChecked == true && window.MultiplayerNav.Visibility == Visibility.Collapsed &&
                    window.MultiplayerSettingStatus.Visibility == Visibility.Visible && window.MultiplayerSettingStatus.Text.Contains("统计卫戍队友DPS"));

                window.ApplyState(Snapshot(false, skin, scopeUnverified: true));
                Check(skin + " a legacy AutoChess sample identifies the unverified collection scope",
                    window.MultiplayerNav.Visibility == Visibility.Collapsed &&
                    window.MultiplayerSettingStatus.Visibility == Visibility.Visible &&
                    window.MultiplayerSettingStatus.Text.Contains("未确认"));

                window.ApplyState(Snapshot(true, skin, active: true));
                window.Navigate("multiplayer").GetAwaiter().GetResult();
                foreach (bool claimedSupport in new[] { false, true })
                {
                    window.ApplyState(Snapshot(true, skin, active: true, scopeUnverified: true,
                        platform: "mumu", supported: claimedSupport));
                    Check(skin + " MuMu hides even a previously all-mode defense and preserves the saved PC checkbox",
                        window.MultiplayerEnabledSetting.IsChecked == true && !window.MultiplayerEnabledSetting.IsEnabled &&
                        window.MultiplayerNav.Visibility == Visibility.Collapsed && window.LivePage.Visibility == Visibility.Visible &&
                        window.MultiplayerPage.DetailInspector.Owner is null && window.MultiplayerPage.Ranking.Rows.Items.Count == 0);
                    Check(skin + " unsupported setting has visible and accessible platform feedback instead of pending mode text",
                        window.MultiplayerSettingStatus.Visibility == Visibility.Visible &&
                        window.MultiplayerSettingStatus.Text == "卫戍队友DPS仅支持官服 PC" &&
                        window.MultiplayerEnabledSetting.ToolTip?.ToString() == "卫戍队友DPS仅支持官服 PC" &&
                        System.Windows.Controls.ToolTipService.GetShowOnDisabled(window.MultiplayerEnabledSetting));
                    window.Navigate("multiplayer").GetAwaiter().GetResult();
                    window.MultiplayerEnabledSetting.IsChecked = false;
                    type.GetMethod("MultiplayerEnabledChanged", Private)!.Invoke(window,
                        new object[] { window.MultiplayerEnabledSetting, new RoutedEventArgs() });
                    Check(skin + " unsupported route and stale click cannot change the stored PC preference or ordinary views",
                        type.GetField("route", Private)!.GetValue(window) as string == "live" &&
                        window.MultiplayerEnabledSetting.IsChecked == true && window.State.Get("layout").Bool("multiplayer_enabled") &&
                        panel.View.GetRawText() == live && window.HistoryPanel.View.GetRawText() == history &&
                        type.GetField("recoveryTask", Private)!.GetValue(window) is null);
                }
                window.ApplyState(Snapshot(true, skin, active: true, supported: null));
                Check(skin + " missing platform capability fails closed despite an old all-mode payload",
                    !window.MultiplayerEnabledSetting.IsEnabled && window.MultiplayerNav.Visibility == Visibility.Collapsed);
                window.ApplyState(Snapshot(true, skin, active: true));
                Check(skin + " returning to PC restores the existing preference without a foreign actor pin",
                    window.MultiplayerEnabledSetting.IsEnabled && window.MultiplayerEnabledSetting.IsChecked == true &&
                    window.MultiplayerNav.Visibility == Visibility.Visible && window.MultiplayerPage.SelectedPlayerUid == "910000001" &&
                    window.MultiplayerPage.DetailInspector.Owner is null && window.MultiplayerSettingStatus.Visibility == Visibility.Collapsed);

                window.ApplyState(Snapshot(false, skin));
                window.MultiplayerEnabledSetting.IsChecked = true;
                type.GetMethod("MultiplayerEnabledChanged", Private)!.Invoke(window,
                    new object[] { window.MultiplayerEnabledSetting, new RoutedEventArgs() });
                Check(skin + " unavailable transport cannot optimistically persist an unchecked worker preference",
                    window.MultiplayerEnabledSetting.IsChecked == false && !window.State.Get("layout").Bool("multiplayer_enabled") &&
                    type.GetField("recoveryTask", Private)!.GetValue(window) is null);
            }
            Check("optional UI tests create no HWND, worker or tray", new WindowInteropHelper(window).Handle == IntPtr.Zero &&
                type.GetField("bridge", Private)!.GetValue(window) is null && type.GetField("tray", Private)!.GetValue(window) is null);
        }
        finally
        {
            type.GetField("quitting", Private)!.SetValue(window, true);
            window.Close();
        }
        Console.WriteLine($"PASS_MULTIPLAYER_SETTINGS {passed} assertions (injected worker states/controls; no Show/input/targets)");
    }
}
