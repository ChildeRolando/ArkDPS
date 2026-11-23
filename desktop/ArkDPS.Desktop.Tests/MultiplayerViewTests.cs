using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
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

    private static IEnumerable<DependencyObject> VisualChildren(DependencyObject root)
    {
        yield return root;
        for (int index = 0; index < VisualTreeHelper.GetChildrenCount(root); index++)
            foreach (var child in VisualChildren(VisualTreeHelper.GetChild(root, index))) yield return child;
    }

    private static JsonObject Player(int side, string uid, int amount) => new()
    {
        ["slot"] = side, ["player_uid"] = "account-" + side, ["player_name"] = "玩家昵称" + side,
        ["player_side"] = side, ["label"] = "不得沿用的阵营编号 " + side, ["status"] = "partial",
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
            ["available"] = true, ["scope"] = round, ["scope_kind"] = "defense", ["native_round"] = 1,
            ["players"] = players ?? new JsonArray(Player(1, "101", 111), Player(2, "202", 222)),
        });

    private static JsonElement ChangeContext(JsonElement source, string kind = "defense", int nativeRound = 1, bool available = true)
    {
        var value = JsonNode.Parse(source.GetRawText())!;
        value["scope_kind"] = kind;
        value["native_round"] = nativeRound;
        value["available"] = available;
        return JsonSerializer.SerializeToElement(value);
    }

    private static Button PlayerButton(MultiplayerView surface, int slot) => Children(surface).OfType<Button>()
        .Single(button => AutomationProperties.GetAutomationId(button) == "MultiplayerPlayer" + slot);

    private static void StableActorControls()
    {
        const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        var enter = typeof(StatisticPanel).GetMethod("HoldOrder", Private)!;
        var leave = typeof(StatisticPanel).GetMethod("ReleaseOrder", Private)!;
        foreach (string skin in new[] { "lone-trail", "people", "babel" })
        foreach (var size in new[] { new Size(1380, 760), new Size(440, 360) })
        {
            Theme.Apply(skin);
            var surface = new MultiplayerView();
            surface.Update(Snapshot());
            var panel = surface.Ranking;
            void Arrange()
            {
                surface.Measure(size);
                surface.Arrange(new Rect(size));
                surface.UpdateLayout();
            }
            Arrange();
            var visibility = panel.Selectors.Visibility;
            var height = panel.Selectors.DesiredSize.Height;
            for (int cycle = 0; cycle < 3; cycle++)
            {
                // Invoke the existing handlers without dispatching mouse input.
                enter.Invoke(panel, new object?[] { panel.Rows, null });
                leave.Invoke(panel, new object?[] { panel.Rows, null });
                Arrange();
                Check(skin + size.Width + " actor selector geometry is stable after leaving the bars",
                    panel.Selectors.Visibility == visibility && panel.Selectors.DesiredSize.Height == height);
                Check(skin + size.Width + " actor controls remain visibly read only",
                    panel.Selectors.Visibility == Visibility.Visible && !panel.Category.IsEnabled && !panel.Rate.IsEnabled);
                Check(skin + size.Width + " internal refresh preserves actor template and hides live actions",
                    panel.Rows.ItemTemplate == panel.FindResource("ActorRow") && panel.Actions.Visibility == Visibility.Collapsed &&
                    panel.PanelKind.Visibility == Visibility.Collapsed && panel.RateAction.Visibility == Visibility.Collapsed);
                surface.Update(Snapshot());
                Arrange();
                Check(skin + size.Width + " worker refresh does not toggle the actor selector row",
                    panel.Selectors.Visibility == visibility && panel.Selectors.DesiredSize.Height == height &&
                    panel.View.Get("entries").Items().Single().Text("owner_id") == "char_same@101");
            }
        }
    }

    private static void RetainedDefenseTransitions()
    {
        foreach (string skin in new[] { "lone-trail", "people", "babel" })
        {
            Theme.Apply(skin);
            var surface = new MultiplayerView();
            var local = Player(2, "202", 222);
            local["is_local"] = true;
            var live = Snapshot("defense-retained", new JsonArray(Player(1, "101", 111), local));
            var retainedNode = JsonNode.Parse(live.GetRawText())!.AsObject();
            retainedNode["previous_defense"] = true;
            retainedNode["status"] = "ended";
            retainedNode["note"] = "战斗准备中，显示已保存的上一轮防御。";
            var retained = JsonSerializer.SerializeToElement(retainedNode);
            surface.Update(live);
            surface.SelectPlayer("account-1");
            surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
            Check(skin + " setup manually selects a foreign actor", surface.DetailInspector.Owner == "char_same@101");

            // The worker may never have proved HELP. Entering retained review
            // still resets once even if the last displayed scope is unchanged.
            surface.Update(retained);
            Check(skin + " first retained transition selects evidenced self and clears its old pin",
                surface.SelectedPlayerUid == "account-2" && surface.DetailInspector.Owner is null && surface.Ranking.Rows.SelectedItem is null);
            Check(skin + " retained defense keeps values and labels the prior round explicitly",
                Text(surface).Contains("上轮防御") && Text(surface).Contains("worker-111") && Text(surface).Contains("worker-222") &&
                !Text(surface).Contains("本轮防御已结束"));
            surface.SelectPlayer("account-1");
            surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
            surface.Update(retained);
            Check(skin + " repeated retained updates preserve manual player and actor selection",
                surface.SelectedPlayerUid == "account-1" && surface.DetailInspector.Owner == "char_same@101");

            var assist = ChangeContext(retained, kind: "assist");
            surface.Update(assist);
            Check(skin + " active assist suppresses retained defense despite retained flag and old players",
                surface.SelectedPlayerUid is null && surface.DetailInspector.Owner is null && surface.Ranking.Rows.Items.Count == 0 &&
                Text(surface).Contains("协防") && !Text(surface).Contains("worker-111"));
            surface.Update(retained);
            Check(skin + " assist to prepare retained chain reenters on self without stale details",
                surface.SelectedPlayerUid == "account-2" && surface.DetailInspector.Owner is null &&
                surface.Ranking.View.Get("entries").Items().Single().Text("owner_id") == "char_same@202");

            surface.SelectPlayer("account-1");
            surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
            var nextLocal = Player(2, "202", 222);
            nextLocal["is_local"] = true;
            surface.Update(ChangeContext(Snapshot("defense-next", new JsonArray(Player(1, "101", 111), nextLocal)), nativeRound: 2));
            Check(skin + " fresh scope cannot reuse the prior retained actor pin",
                surface.SelectedPlayerUid == "account-2" && surface.DetailInspector.Owner is null && surface.Ranking.Rows.SelectedItem is null);

            var unresolved = JsonNode.Parse(Snapshot("defense-no-self").GetRawText())!.AsObject();
            unresolved["previous_defense"] = true;
            var noSelf = JsonSerializer.SerializeToElement(unresolved);
            surface.Update(noSelf);
            Check(skin + " retained defense without self proof waits for manual selection",
                surface.SelectedPlayerUid is null && surface.Ranking.Rows.Items.Count == 0 && !Text(surface).Contains(" · 我方"));
            Check(skin + " known foreign players remain manually selectable without self proof",
                PlayerButton(surface, 1).IsEnabled && PlayerButton(surface, 2).IsEnabled);
            surface.SelectPlayer("account-1");
            surface.Update(noSelf);
            Check(skin + " manual foreign choice is retained without claiming it is self",
                surface.SelectedPlayerUid == "account-1" && surface.Ranking.Rows.Items.Count == 1 && !Text(surface).Contains(" · 我方"));
            surface.Update(ChangeContext(noSelf, available: false));
            Check(skin + " retained flag cannot certify unavailable defense",
                surface.SelectedPlayerUid is null && surface.Ranking.Rows.Items.Count == 0 && !Text(surface).Contains("worker-111"));
        }
    }

    private static void AssistHeadings()
    {
        const string Label = "当前协防战场";
        var first = Player(1, "101", 111);
        var second = Player(2, "202", 222);
        var scene = first["view"]!.DeepClone().AsObject();
        scene["scope_label"] = Label;
        scene["title"] = Label + " · 已观察有效伤害 · 接敌 DPS";
        scene["entries"]!.AsArray().Add(second["view"]!["entries"]![0]!.DeepClone());
        scene["source"]!.AsObject()["char_same@202"] = second["view"]!["source"]!["char_same@202"]!.DeepClone();
        scene["share_reason"] = "原生累计未知，以下仅含当前协防战场的已观察结算";
        foreach (string skin in new[] { "lone-trail", "people", "babel" })
        foreach (string mode in new[] { "main", "side", "hud", "history" })
        {
            Theme.Apply(skin);
            var panel = new StatisticPanel { IsHistory = mode == "history" };
            if (mode == "side") panel.ConfigureSideRanking();
            if (mode == "hud") panel.ConfigureHud();
            panel.Update(JsonSerializer.SerializeToElement(scene));
            Check(skin + mode + " visibly identifies the assist battlefield once",
                panel.Heading.Text.StartsWith(Label + " · ", StringComparison.Ordinal) &&
                panel.Heading.Text.IndexOf(Label, Label.Length, StringComparison.Ordinal) < 0);
            Check(skin + mode + " keeps both worker actor identities and values",
                panel.Rows.Items.Count == 2 && panel.View.Get("entries").Items().Select(row => row.Text("owner_id"))
                    .SequenceEqual(new[] { "char_same@101", "char_same@202" }) &&
                ((RowItem)panel.Rows.Items[0]).Entry.Text("display_value") == "worker-111" &&
                ((RowItem)panel.Rows.Items[1]).Entry.Text("display_value") == "worker-222");
            panel.Update(JsonSerializer.SerializeToElement(first["view"]));
            Check(skin + mode + " clears the assist label on the next local view",
                !panel.Heading.Text.Contains(Label, StringComparison.Ordinal) && panel.Rows.Items.Count == 1);
            if (mode == "hud") Check(skin + " local HUD keeps its existing exact title",
                panel.Heading.Text == "有效伤害·接敌DPS");
        }
    }

    private static void SideRankingNameAndSummary()
    {
        var selected = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory,
            "Fixtures", "native-ce5-healing.selected.json")))!;
        foreach (string skin in new[] { "lone-trail", "people", "babel" })
        foreach (double width in new[] { 280d, 320d })
        {
            Theme.Apply(skin);
            var view = selected["view"]!.DeepClone().AsObject();
            view["config"]!["panel_id"] = "side-name-summary";
            var entry = view["entries"]![0]!;
            string name = width == 280 ? "闪灵·侧边长名称布局检查" : entry["name"]!.GetValue<string>();
            entry["name"] = name;
            entry["rate"] = 123.4;
            entry["rate_text"] = "123.4";
            view["footer_total"] = entry["value"]!.DeepClone();
            view["footer_total_display"] = entry["display_value"]!.DeepClone();
            view["footer_rate"] = 123.4;
            view["footer_rate_display"] = "123.4";
            view["footer_total_complete"] = true;
            view["footer_rate_complete"] = true;
            string totalText = entry["display_value"]!.GetValue<string>();
            var side = new StatisticPanel();
            side.ConfigureSideRanking();
            var main = new StatisticPanel();
            main.SetReferenceStyle(true);
            void Update()
            {
                var state = JsonSerializer.SerializeToElement(view);
                side.Update(state);
                main.Update(state);
                side.Measure(new Size(width, 460));
                side.Arrange(new Rect(0, 0, width, 460));
                side.UpdateLayout();
            }
            Update();
            var container = (ListBoxItem)side.Rows.ItemContainerGenerator.ContainerFromIndex(0)!;
            var row = VisualChildren(container).OfType<Grid>().Single(item => item.Height == 36);
            var caption = VisualChildren(container).OfType<TextBlock>().Single(item =>
                AutomationProperties.GetAutomationId(item) == "SideRankingOperatorName");
            var bar = VisualChildren(container).OfType<ContributionBar>().Single(item =>
                AutomationProperties.GetAutomationId(item) == "SideRankingContribution");
            var rowTotal = VisualChildren(container).OfType<TextBlock>().Single(item =>
                AutomationProperties.GetAutomationId(item) == "SideRankingTotal");
            var rowRate = VisualChildren(container).OfType<TextBlock>().Single(item =>
                AutomationProperties.GetAutomationId(item) == "SideRankingRate");
            double captionBottom = caption.TransformToAncestor(row).Transform(new Point()).Y + caption.ActualHeight;
            double barTop = bar.TransformToAncestor(row).Transform(new Point()).Y;
            Check(skin + width + " side name fits above the bar in the unchanged 36 DIP row",
                row.ActualHeight == 36 && caption.Text == name && caption.ActualHeight > 0 &&
                captionBottom <= barTop && caption.TextWrapping == TextWrapping.NoWrap &&
                caption.TextTrimming == TextTrimming.CharacterEllipsis);
            Check(skin + width + " side name preserves worker cumulative value and rate beside the original bar",
                rowTotal.Text == totalText && rowRate.Text == "123.4" && bar.Data.Number("value") == 10919.251953125 &&
                ((RowItem)side.Rows.Items[0]).Tooltip.Contains(name, StringComparison.Ordinal));
            double RightEdge(FrameworkElement element) =>
                element.TranslatePoint(new Point(element.ActualWidth, 0), side).X;
            Check(skin + width + " side summary aligns with the two numeric columns below the rows",
                Grid.GetRow(side.ReferenceFooter) > Grid.GetRow(side.Rows) &&
                Math.Abs(RightEdge(side.ReferenceFooterTotal) - RightEdge(rowTotal)) <= 1 &&
                Math.Abs(RightEdge(side.ReferenceFooterRate) - RightEdge(rowRate)) <= 1);
            foreach (string quality in new[] { "complete", "partial_total", "partial_rate", "unknown" })
            {
                view["footer_total_complete"] = quality is "complete" or "partial_rate";
                view["footer_rate_complete"] = quality is "complete" or "partial_total";
                bool unknown = quality == "unknown";
                view["footer_total"] = unknown ? null : entry["value"]!.DeepClone();
                view["footer_total_display"] = unknown ? "—" : totalText;
                view["footer_rate"] = unknown ? null : JsonValue.Create(123.4);
                view["footer_rate_display"] = unknown ? "—" : "123.4";
                Update();
                Check(skin + width + quality + " side summary shares the main worker figures without inventing unknown zeros",
                    side.ReferenceFooter.Visibility == Visibility.Visible &&
                    side.ReferenceFooterLabel.Text == (quality == "complete" ? "全队汇总" : "已知合计") &&
                    side.ReferenceFooterTotal.Text == main.ReferenceFooterTotal.Text &&
                    side.ReferenceFooterRate.Text == main.ReferenceFooterRate.Text &&
                    side.ReferenceFooterTotal.Text == (unknown ? "—" : totalText) &&
                    side.ReferenceFooterRate.Text == (unknown ? "—" : "123.4"));
            }
            view["config"]!["ranking"] = "recent";
            entry["value"] = 17.25;
            entry["display_value"] = "17.3";
            entry["cumulative_display_value"] = totalText;
            view["footer_total"] = 17.25;
            view["footer_total_display"] = "17.3";
            view["footer_total_complete"] = true;
            Update();
            Check(skin + width + " recent summary keeps the main recent aggregate while each side row keeps cumulative output",
                side.ReferenceFooterLabel.Text == "全队汇总 · 近期" &&
                side.ReferenceFooterTotal.Text == "17.3" && side.ReferenceFooterTotal.Text == main.ReferenceFooterTotal.Text &&
                rowTotal.Text == totalText);
            side.Update(default);
            Check(skin + width + " clearing the side view removes the old rows and summary",
                side.Rows.Items.Count == 0 && side.ReferenceFooter.Visibility == Visibility.Collapsed);
        }
    }

    internal static void Run()
    {
        if (Application.Current is null)
        {
            var app = new App();
            app.InitializeComponent(); // Resource dictionary only. No Run, Show, target access or input.
        }
        Theme.Apply("lone-trail");
        Check("AutoChess strategy operator loads its own normal and muted portraits",
            Portraits.Get("char_618_acrbot", false) is System.Windows.Media.Imaging.CroppedBitmap { PixelWidth: 48, PixelHeight: 48 } &&
            Portraits.Get("char_618_acrbot", true) is System.Windows.Media.Imaging.CroppedBitmap { PixelWidth: 48, PixelHeight: 48 });
        StableActorControls();
        SideRankingNameAndSummary();
        RetainedDefenseTransitions();
        var surface = new MultiplayerView();
        string empty = Text(surface);
        Check("empty explains unverified native phase and observation", empty.Contains("卫戍协议") && empty.Contains("事件观察") && empty.Contains("阶段未知"));
        Check("four comparable selectable slots always exist", Children(surface).OfType<Button>().Count(button =>
            AutomationProperties.GetAutomationId(button).StartsWith("MultiplayerPlayer", StringComparison.Ordinal)) == 4);
        Check("empty slots never manufacture zero", !empty.Contains("0 DPS") && !empty.Contains("总伤 0"));

        surface.Update(Snapshot());
        Check("player cards identify actual UID and nickname without side guessing", Text(PlayerButton(surface, 1)).Contains("玩家昵称1") &&
            Text(PlayerButton(surface, 1)).Contains("UID account-1") && !Text(surface).Contains("不得沿用的阵营编号"));
        Check("observed number retains Python display and label", Text(surface).Contains("已观察结算伤害") &&
            Text(surface).Contains("worker-111") && Text(surface).Contains("worker-222"));
        Check("selected player view is worker-owned", surface.Ranking.View.Get("entries").Items().Single().Text("owner_id") == "char_same@101");
        Check("read only player ranking keeps stable visible selectors without live actions", surface.Ranking.Selectors.Visibility == Visibility.Visible &&
            surface.Ranking.Actions.Visibility == Visibility.Collapsed && !surface.Ranking.Category.IsEnabled && surface.Ranking.Command is null);
        surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
        Check("operator pin preserves CID plus UID owner", surface.DetailInspector.Owner == "char_same@101" && Text(surface).Contains("战斗 UID 101"));
        Check("portrait identity does not replace the UID selection identity", ((RowItem)surface.Ranking.Rows.Items[0]).Portrait is not null &&
            Children(surface.DetailInspector).OfType<Image>().Any(image => image.Source is not null) && surface.DetailInspector.Owner == "char_same@101");
        Check("actor row exposes complete UID separately from the name", ((RowItem)surface.Ranking.Rows.Items[0]).ActorIdentity == "UID 101" &&
            surface.Ranking.Rows.ItemTemplate == surface.Ranking.FindResource("ActorRow"));
        surface.SelectPlayer("account-2");
        Check("choosing another player clears previous pin", surface.DetailInspector.Owner is null && surface.Ranking.Rows.SelectedItem is null);
        Check("same CID in another player keeps separate actor", surface.Ranking.View.Get("entries").Items().Single().Text("owner_id") == "char_same@202");
        surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
        surface.Update(Snapshot());
        Check("same scope updates retain correct actor pin by player UID", surface.SelectedPlayerUid == "account-2" && surface.DetailInspector.Owner == "char_same@202");
        var moved = Player(2, "202", 222);
        moved["slot"] = 4;
        moved["player_side"] = 1;
        surface.Update(Snapshot(players: new JsonArray(Player(1, "101", 111), moved)));
        Check("same real UID moving slots or changing temporary side retains its actor", surface.SelectedPlayerUid == "account-2" &&
            surface.DetailInspector.Owner == "char_same@202" && Text(PlayerButton(surface, 4)).Contains("UID account-2"));
        surface.Update(Snapshot("round-b"));
        Check("new scope resets selected player and actor even if IDs recur", surface.SelectedPlayerUid == "account-1" && surface.DetailInspector.Owner is null && surface.Ranking.Rows.SelectedItem is null);
        surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
        surface.Update(Snapshot("round-b", new JsonArray(Player(2, "202", 222))));
        Check("missing selected view clears stale actor details", surface.DetailInspector.Owner is null && surface.Ranking.Rows.Items.Count == 0 &&
            !Text(surface.DetailInspector).Contains("同型干员1"));
        surface.Update(default);
        Check("unavailable snapshot clears previous round", surface.DetailInspector.Owner is null && surface.Ranking.Rows.Items.Count == 0);
        var local = Player(2, "202", 222);
        local["is_local"] = true;
        surface.Update(Snapshot("round-c", new JsonArray(Player(1, "101", 111), local)));
        Check("new scope initially opens the known local player UID", surface.SelectedPlayerUid == "account-2" &&
            surface.Ranking.View.Get("entries").Items().Single().Text("owner_id") == "char_same@202");

        surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
        surface.Update(ChangeContext(Snapshot("round-c", new JsonArray(Player(1, "101", 111), (JsonObject)local.DeepClone())), nativeRound: 2));
        Check("native round changes clear pins independently of scope string", surface.DetailInspector.Owner is null && surface.Ranking.Rows.SelectedItem is null);
        foreach (var (phase, label) in new[] { ("assist", "协防"), ("boss", "首领"), ("prepare", "准备"), ("unknown", "阶段未知") })
        {
            surface.Update(Snapshot());
            surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
            surface.Update(ChangeContext(Snapshot(), kind: phase));
            Check(phase + " explains its phase instead of defense output", Text(surface).Contains(label) && !Text(surface).Contains("worker-111"));
            Check(phase + " clears old player values and actor detail", surface.SelectedPlayerUid is null &&
                surface.DetailInspector.Owner is null && surface.Ranking.Rows.Items.Count == 0);
        }
        surface.Update(Snapshot());
        surface.Ranking.Rows.SelectedItem = surface.Ranking.Rows.Items[0];
        surface.Update(ChangeContext(Snapshot(), available: false));
        Check("unavailable defense ignores old players still present in DTO", surface.SelectedPlayerUid is null &&
            surface.Ranking.Rows.Items.Count == 0 && surface.DetailInspector.Owner is null && !Text(surface).Contains("worker-111"));
        var legacy = JsonNode.Parse(Snapshot().GetRawText())!;
        legacy.AsObject().Remove("scope_kind");
        legacy["current_state"] = 4; // Raw state numbers cannot substitute for verified phase semantics.
        surface.Update(JsonSerializer.SerializeToElement(legacy));
        Check("legacy DTO is unknown despite available and raw BATTLE state", Text(surface).Contains("阶段未知") &&
            surface.Ranking.Rows.Items.Count == 0 && !Text(surface).Contains("worker-111"));
        var invalidScope = JsonNode.Parse(Snapshot().GetRawText())!;
        invalidScope["scope"] = new JsonObject { ["phase"] = 4 };
        surface.Update(JsonSerializer.SerializeToElement(invalidScope));
        Check("non-string scope cannot become trusted defense scope", Text(surface).Contains("阶段未知") && surface.Ranking.Rows.Items.Count == 0);
        var unidentified = Player(1, "101", 111);
        unidentified["player_uid"] = null;
        unidentified["player_name"] = "昵称不能替代身份";
        surface.Update(Snapshot("unknown-player", new JsonArray(unidentified)));
        Check("missing UID remains identity unknown rather than side or nickname", Text(PlayerButton(surface, 1)).Contains("玩家身份未知") &&
            !Text(surface).Contains("昵称不能替代身份") && !Text(surface).Contains("worker-111") && surface.SelectedPlayerUid is null);
        Check("unknown identity cannot select a player or inspect its actors", !PlayerButton(surface, 1).IsEnabled && surface.Ranking.Rows.Items.Count == 0);
        Check("unknown identity retains the native product button theme", PlayerButton(surface, 1).Style == surface.FindResource(typeof(Button)));
        var duplicated = Player(2, "202", 222);
        duplicated["player_uid"] = "account-1";
        surface.Update(Snapshot("duplicate-player", new JsonArray(Player(1, "101", 111), duplicated)));
        Check("ambiguous duplicate player UID cannot cross-select actor data", surface.SelectedPlayerUid is null &&
            surface.Ranking.Rows.Items.Count == 0 && !Text(surface).Contains("worker-111") && !Text(surface).Contains("worker-222"));

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
        AssistHeadings();
        Check("headless checks created no windows", Application.Current!.Windows.Count == 0);
        Console.WriteLine($"PASS_MULTIPLAYER_VIEW {passed} assertions (synthetic worker projections; no windows/input/targets)");
    }
}
