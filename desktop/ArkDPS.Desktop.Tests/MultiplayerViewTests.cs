using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using ArkDPS.Desktop;

internal static class MultiplayerViewTests
{
    private static int passed;
    private static void Check(string name, bool condition)
    {
        if (!condition) throw new InvalidOperationException(name);
        passed++;
    }

    private static IEnumerable<DependencyObject> Children(DependencyObject root)
    {
        yield return root;
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach (var item in Children(child)) yield return item;
    }

    private static string Text(DependencyObject root) =>
        string.Join("|", Children(root).OfType<TextBlock>().Select(item => item.Text));

    private static JsonObject Player(int side, string uid, int amount) => new()
    {
        ["player_side"] = side, ["label"] = "第 " + side + " 家", ["status"] = "partial",
        ["coverage_complete"] = false, ["damage"] = null, ["observed_damage"] = amount,
        ["dps"] = null, ["observed_dps"] = 15.5, ["recent_dps"] = null, ["observed_recent_dps"] = 1.5,
        ["display"] = new JsonObject { ["damage"] = "worker-" + amount, ["dps"] = "15.5", ["recent_dps"] = "1.5", ["coverage"] = "已观察 · 证据不完整" },
        ["view"] = new JsonObject
        {
            ["config"] = new JsonObject { ["panel_id"] = "multiplayer-" + side, ["category"] = "damage", ["ranking"] = "total", ["rate_basis"] = "engagement", ["effective_only"] = true },
            ["title"] = "已观察有效伤害 · 接敌 DPS", ["share_scope"] = "known", ["empty_text"] = "等待事件",
            ["source"] = new JsonObject { ["char_same@" + uid] = new JsonObject { ["name"] = "同型干员" + side, ["actor_uid"] = uid, ["portrait_id"] = "char_367_swllow", ["damage"] = amount } },
            ["entries"] = new JsonArray(new JsonObject
            {
                ["owner_id"] = "char_same@" + uid, ["name"] = "同型干员" + side, ["value"] = amount,
                ["portrait_id"] = "char_367_swllow",
                ["actor_uid"] = uid,
                ["display_value"] = "worker-" + amount, ["exact_value"] = amount.ToString(), ["rate_text"] = "15.5", ["share_display"] = "100%",
                ["detail"] = new JsonObject { ["effective_damage"] = amount },
            }),
        },
    };

    private static JsonElement Snapshot(string round = "round-a", JsonArray? players = null) =>
        JsonSerializer.SerializeToElement(new JsonObject
        {
            ["available"] = true, ["round_id"] = round,
            ["players"] = players ?? new JsonArray(Player(1, "101", 111), Player(2, "202", 222)),
        });

    internal static void Run()
    {
        if (Application.Current is null)
        {
            var app = new App();
            app.InitializeComponent(); // Resource dictionary only. No Run, Show, target access or input.
        }
        Theme.Apply("lone-trail");
        var surface = new MultiplayerView();
        string empty = Text(surface);
        Check("empty explains mode and observation", empty.Contains("卫戍协议") && empty.Contains("事件观察") && empty.Contains("未知"));
        Check("four comparable selectable slots always exist", Children(surface).OfType<Button>().Count(button =>
            AutomationProperties.GetAutomationId(button).StartsWith("MultiplayerPlayer", StringComparison.Ordinal)) == 4);
        Check("empty slots never manufacture zero", !empty.Contains("0 DPS") && !empty.Contains("总伤 0"));

        surface.Update(Snapshot());
        Check("observed number retains Python display and label", Text(surface).Contains("已观察结算伤害") &&
            Text(surface).Contains("worker-111") && Text(surface).Contains("worker-222"));
        Check("selected player view is worker-owned", surface.Ranking.View.Get("entries").Items().Single().Text("owner_id") == "char_same@101");
        Check("read only player ranking has no live actions", surface.Ranking.Selectors.Visibility == Visibility.Collapsed &&
            surface.Ranking.Actions.Visibility == Visibility.Collapsed && !surface.Ranking.Category.IsEnabled && surface.Ranking.Command is null);
        surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
        Check("operator pin preserves CID plus UID owner", surface.DetailInspector.Owner == "char_same@101" && Text(surface).Contains("战斗 UID 101"));
        Check("portrait identity does not replace the UID selection identity", ((RowItem)surface.Ranking.Rows.Items[0]).Portrait is not null &&
            Children(surface.DetailInspector).OfType<Image>().Any(image => image.Source is not null) && surface.DetailInspector.Owner == "char_same@101");
        Check("actor row exposes complete UID separately from the name", ((RowItem)surface.Ranking.Rows.Items[0]).ActorIdentity == "UID 101" &&
            surface.Ranking.Rows.ItemTemplate == surface.Ranking.FindResource("ActorRow"));
        surface.SelectPlayer(2);
        Check("choosing another player clears previous pin", surface.DetailInspector.Owner is null && surface.Ranking.Rows.SelectedItem is null);
        Check("same CID in another player keeps separate actor", surface.Ranking.View.Get("entries").Items().Single().Text("owner_id") == "char_same@202");
        surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
        surface.Update(Snapshot());
        Check("same round updates retain correct actor pin", surface.SelectedSide == 2 && surface.DetailInspector.Owner == "char_same@202");
        surface.Update(Snapshot("round-b"));
        Check("new round resets selected player and actor even if IDs recur", surface.SelectedSide == 1 && surface.DetailInspector.Owner is null && surface.Ranking.Rows.SelectedItem is null);
        surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
        surface.Update(Snapshot("round-b", new JsonArray(Player(2, "202", 222))));
        Check("missing selected view clears stale actor details", surface.DetailInspector.Owner is null && surface.Ranking.Rows.Items.Count == 0 &&
            !Text(surface.DetailInspector).Contains("同型干员1"));
        surface.Update(default);
        Check("unavailable snapshot clears previous round", surface.DetailInspector.Owner is null && surface.Ranking.Rows.Items.Count == 0);
        var local = Player(2, "202", 222);
        local["is_local"] = true;
        surface.Update(Snapshot("round-c", new JsonArray(Player(1, "101", 111), local)));
        Check("new round initially opens the known local player", surface.SelectedSide == 2 &&
            surface.Ranking.View.Get("entries").Items().Single().Text("owner_id") == "char_same@202");

        foreach (string skin in new[] { "lone-trail", "people", "babel" })
        {
            Theme.Apply(skin);
            surface.Update(Snapshot());
            foreach (var size in new[] { new Size(1380, 760), new Size(440, 360) })
            {
                surface.Measure(size);
                surface.Arrange(new Rect(size));
                surface.UpdateLayout();
                Check(skin + " comparison fits horizontally at " + size.Width,
                    surface.ExtentWidth <= surface.ViewportWidth + 1);
                surface.ScrollToEnd();
                surface.UpdateLayout();
                Check(skin + " compact layout offers vertical scroll", size.Width > 500 || surface.ScrollableHeight > 0);
            }
        }
        Check("headless checks created no windows", Application.Current!.Windows.Count == 0);
        Console.WriteLine($"PASS_MULTIPLAYER_VIEW {passed} assertions (synthetic worker projections; no windows/input/targets)");
    }
}
