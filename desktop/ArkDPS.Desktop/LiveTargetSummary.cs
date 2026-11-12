using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;

namespace ArkDPS.Desktop;

public sealed class LiveTargetSummary : Border
{
    private static readonly Brush Metal = Theme.Brush("#E2E6E9");
    private static readonly Brush DarkInk = Theme.Brush("#15212C");
    private static readonly Brush Etching = Theme.Brush("#A9B3BC");
    private readonly List<TextBlock> shellLabels = new();
    private readonly List<Border> dividers = new();
    private readonly TextBlock name = new();
    private readonly TextBlock identity = new();
    private readonly TextBlock badge = new();
    private readonly Image portrait = new();
    private readonly TextBlock health;
    private readonly TextBlock defense;
    private readonly TextBlock resistance;
    private readonly TextBlock stage;
    private readonly TextBlock duration;
    private readonly TextBlock enteredCount;
    private readonly TextBlock enteredHp;
    private readonly TextBlock remainingHp;
    private readonly TextBlock damage;
    private readonly TextBlock teamDps;
    private readonly TextBlock healing;
    private readonly TextBlock shield;
    private readonly OrbitDiagram diagram = new();
    private string lastSkin = "";
    private string battle = "";
    private string targetUid = "";
    private string targetRoster = "";

    internal event Action? OpenEnemies;

    public LiveTargetSummary()
    {
        SetResourceReference(BorderBrushProperty, "Edge");
        BorderThickness = new Thickness(1);
        Padding = new Thickness(6);
        SnapsToDevicePixels = true;
        var content = new Grid();
        for (int index = 0; index < 4; index++)
            content.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        content.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        Child = content;

        var open = new Button { Content = "敌情 ›", Padding = new Thickness(6, 1, 6, 1),
            MinHeight = 0, MinWidth = 0, FontSize = 11, VerticalAlignment = VerticalAlignment.Center };
        AutomationProperties.SetName(open, "打开敌情");
        open.Click += (_, _) => OpenEnemies?.Invoke();
        var targetCommand = new DockPanel { LastChildFill = false };
        DockPanel.SetDock(open, Dock.Right);
        targetCommand.Children.Add(open);
        Place(content, targetCommand, 0);

        var target = new Grid();
        target.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(98) });
        target.ColumnDefinitions.Add(new ColumnDefinition());
        var targetSurface = new Border { Child = target, Padding = new Thickness(7),
            Margin = new Thickness(0, 3, 0, 4), BorderThickness = new Thickness(1) };
        targetSurface.SetResourceReference(BackgroundProperty, "Console");
        targetSurface.SetResourceReference(BorderBrushProperty, "ConsoleLine");
        Place(content, targetSurface, 1);

        var art = new Grid();
        portrait.Stretch = Stretch.Uniform;
        portrait.Margin = new Thickness(4);
        badge.FontSize = 23;
        badge.FontWeight = FontWeights.SemiBold;
        badge.HorizontalAlignment = HorizontalAlignment.Center;
        badge.VerticalAlignment = VerticalAlignment.Center;
        badge.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleMuted");
        art.Children.Add(badge);
        art.Children.Add(portrait);
        var portraitFrame = new Border { Child = art, Height = 91, VerticalAlignment = VerticalAlignment.Center,
            BorderThickness = new Thickness(1), Margin = new Thickness(0, 0, 7, 0) };
        portraitFrame.SetResourceReference(BackgroundProperty, "ConsoleCard");
        portraitFrame.SetResourceReference(BorderBrushProperty, "ConsoleLine");
        target.Children.Add(portraitFrame);

        var stats = new StackPanel();
        Grid.SetColumn(stats, 1);
        target.Children.Add(stats);
        name.FontSize = 13;
        name.FontWeight = FontWeights.Bold;
        name.TextTrimming = TextTrimming.CharacterEllipsis;
        name.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleInk");
        stats.Children.Add(name);
        identity.FontSize = 10;
        identity.FontFamily = new FontFamily("Cascadia Mono, Consolas");
        identity.TextTrimming = TextTrimming.CharacterEllipsis;
        identity.Margin = new Thickness(0, 0, 0, 3);
        identity.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleMuted");
        stats.Children.Add(identity);
        health = Reading(stats, "HP", dark: true);
        defense = Reading(stats, "DEF", dark: true);
        resistance = Reading(stats, "RES", dark: true);

        Place(content, Header("作战信息", "ANALYSIS SESSION"), 2);
        var mission = new StackPanel { Margin = new Thickness(3, 2, 3, 0) };
        Place(content, mission, 3);
        stage = Reading(mission, "关卡");
        duration = Reading(mission, "作战时长");
        enteredCount = Reading(mission, "已入场敌人");
        enteredHp = Reading(mission, "已入场总 HP");
        remainingHp = Reading(mission, "敌方剩余 HP");
        damage = Reading(mission, "总伤");
        teamDps = Reading(mission, "当前团队 DPS");
        healing = Reading(mission, "总治疗");
        shield = Reading(mission, "有效护盾");
        diagram.MinHeight = 74;
        diagram.Margin = new Thickness(2, 4, 2, 0);
        Place(content, diagram, 4);
        Update(default, "—", "—", "—", "");
    }

    internal void Update(JsonElement state, string entered, string total, string remaining, string status,
        JsonElement displayedEnemy = default)
    {
        ApplyPalette();
        var id = state.Text("battle_id");
        if (battle != id)
        {
            battle = id;
            targetUid = "";
            targetRoster = "";
        }

        var enemy = displayedEnemy.ValueKind == JsonValueKind.Object ? displayedEnemy : state.Get("enemy");
        var rows = enemy.Get("rows").Items().ToArray();
        var row = rows.FirstOrDefault(MatchesTarget);
        if (row.ValueKind != JsonValueKind.Object || row.Text("lifecycle") == "departed")
        {
            var active = rows.Where(candidate => candidate.Bool("alive")
                || candidate.Text("lifecycle") is "active" or "rebirthing")
                .OrderByDescending(candidate => candidate.Number("max_hp", 0)).FirstOrDefault();
            if (active.ValueKind == JsonValueKind.Object) row = active;
            else if (row.ValueKind != JsonValueKind.Object) row = rows.FirstOrDefault();
        }

        if (row.ValueKind == JsonValueKind.Object)
        {
            targetUid = EnemyItem.InstanceUid(row);
            targetRoster = EnemyItem.RosterIdentity(row);
            var target = new EnemyItem(row, targetUid, targetRoster);
            Show(name, target.Name);
            var code = row.Text("code");
            Show(identity, code.Length == 0 ? target.Eid : code + " · " + target.Eid);
            Show(badge, target.Portrait is null ? target.Badge : "");
            if (portrait.Source != target.Portrait) portrait.Source = target.Portrait;
            portrait.ToolTip = target.Tooltip;
            Show(health, target.HpText);
            Show(defense, Json.UiInt(row, "def"));
            Show(resistance, Json.UiInt(row, "res"));
        }
        else
        {
            Show(name, "等待敌方数据");
            Show(identity, status.Length > 0 ? status : "—");
            Show(badge, "—");
            portrait.Source = null;
            portrait.ToolTip = null;
            Show(health, "—");
            Show(defense, "—");
            Show(resistance, "—");
        }

        Show(stage, state.Text("stage") is { Length: > 0 } label ? label : "—");
        double seconds = state.Number("game_time", double.NaN);
        Show(duration, double.IsFinite(seconds) && seconds >= 0
            ? $"{Math.Floor(seconds / 60):00}:{seconds % 60:00.0}" : "—");
        Show(enteredCount, entered);
        Show(enteredHp, total);
        Show(remainingHp, remaining);
        var analysis = state.Get("live_analysis");
        Show(damage, Json.UiInt(analysis, "team_total_damage"));
        Show(teamDps, Json.Ui(analysis, "team_recent_dps"));
        var totals = state.Get("totals");
        Show(healing, Json.UiInt(totals, "healing"));
        Show(shield, Json.UiInt(totals, "shield_absorbed"));
    }

    private bool MatchesTarget(JsonElement row)
    {
        string uid = EnemyItem.InstanceUid(row);
        return targetUid.Length > 0 && uid.Length > 0
            ? uid == targetUid
            : targetRoster.Length > 0 && EnemyItem.RosterIdentity(row) == targetRoster;
    }

    private void ApplyPalette()
    {
        if (lastSkin == Theme.Key) return;
        lastSkin = Theme.Key;
        bool metal = lastSkin == "lone-trail";
        if (metal) Background = Metal;
        else SetResourceReference(BackgroundProperty, "Card");
        foreach (var label in shellLabels)
            if (metal) label.Foreground = DarkInk;
            else label.SetResourceReference(TextBlock.ForegroundProperty, "Ink");
        foreach (var divider in dividers)
            if (metal) divider.BorderBrush = Etching;
            else divider.SetResourceReference(BorderBrushProperty, "Line");
        diagram.Visibility = metal ? Visibility.Visible : Visibility.Collapsed;
    }

    private Border Header(string chinese, string english, Button? action = null)
    {
        var dock = new DockPanel();
        if (action is not null)
        {
            DockPanel.SetDock(action, Dock.Right);
            dock.Children.Add(action);
        }
        var marker = new Border { Width = 9, Height = 13, Margin = new Thickness(2, 0, 7, 0),
            LayoutTransform = new SkewTransform(-20, 0) };
        marker.SetResourceReference(BackgroundProperty, "Accent");
        dock.Children.Add(marker);
        var title = new TextBlock { Text = $"{chinese} / {english}", FontSize = 12,
            FontWeight = FontWeights.Bold, TextTrimming = TextTrimming.CharacterEllipsis,
            VerticalAlignment = VerticalAlignment.Center, MinHeight = 19 };
        shellLabels.Add(title);
        dock.Children.Add(title);
        var header = new Border { Child = dock, Padding = new Thickness(2, 2, 2, 1),
            BorderThickness = new Thickness(0, 0, 0, 1) };
        dividers.Add(header);
        return header;
    }

    private TextBlock Reading(Panel host, string caption, bool dark = false)
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        var label = new TextBlock { Text = caption, FontSize = 11, Margin = new Thickness(0, 0, 5, 0) };
        var value = new TextBlock { FontSize = 11, FontWeight = FontWeights.SemiBold,
            TextAlignment = TextAlignment.Right, TextTrimming = TextTrimming.CharacterEllipsis };
        if (dark)
        {
            label.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleMuted");
            value.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleInk");
        }
        else
        {
            shellLabels.Add(label);
            shellLabels.Add(value);
        }
        grid.Children.Add(label);
        Grid.SetColumn(value, 1);
        grid.Children.Add(value);
        var row = new Border { Child = grid, BorderThickness = new Thickness(0, 0, 0, 1),
            Padding = new Thickness(0, 1, 0, 1) };
        if (dark) row.SetResourceReference(BorderBrushProperty, "ConsoleLine");
        else dividers.Add(row);
        host.Children.Add(row);
        return value;
    }

    private static void Show(TextBlock element, string value)
    {
        if (element.Text == value) return;
        element.Text = value;
        element.ToolTip = value;
    }

    private static void Place(Grid grid, UIElement element, int row)
    {
        Grid.SetRow(element, row);
        grid.Children.Add(element);
    }

    private sealed class OrbitDiagram : FrameworkElement
    {
        private static readonly Pen Guide = new(Theme.Brush("#7D8995"), 0.75);
        private static readonly Pen Orbit = new(Theme.Brush("#607181"), 1);
        private static readonly Brush Planet = Theme.Brush("#AAB5C0");
        private static readonly Brush Accent = Theme.Brush("#FF982F");

        protected override void OnRender(DrawingContext dc)
        {
            base.OnRender(dc);
            if (ActualWidth <= 20 || ActualHeight <= 20) return;
            double width = ActualWidth;
            double height = Math.Min(ActualHeight, width * .68);
            var center = new Point(width * .48, height * .5);
            double radius = height * .16;
            for (int x = 1; x <= 5; x++)
            for (int y = 1; y <= 3; y++)
            {
                var point = new Point(width * x / 6, height * y / 4);
                dc.DrawLine(Guide, new Point(point.X - 3, point.Y), new Point(point.X + 3, point.Y));
                dc.DrawLine(Guide, new Point(point.X, point.Y - 3), new Point(point.X, point.Y + 3));
            }
            dc.DrawEllipse(Planet, Guide, center, radius, radius);
            foreach (double angle in new[] { -24d, 13d, -8d })
            {
                dc.PushTransform(new RotateTransform(angle, center.X, center.Y));
                dc.DrawEllipse(null, Orbit, center, width * .43, height * (.20 + Math.Abs(angle) / 125));
                dc.Pop();
            }
            dc.DrawRectangle(Accent, null, new Rect(width * .75, height * .18, 5, 5));
            dc.DrawRectangle(Accent, null, new Rect(width * .26, height * .75, 4, 4));
        }
    }
}
