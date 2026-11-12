using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;

namespace ArkDPS.Desktop;

public sealed class LiveTargetSummary : Border
{
    private const int CardCount = 3;
    private readonly TargetCard[] cards;
    private readonly ScrollViewer cardScroll;
    private readonly Dictionary<string, Target> departedByType = new(StringComparer.Ordinal);
    private Target[] queue = Array.Empty<Target>();
    private string battle = "";
    private string status = "";

    public LiveTargetSummary()
    {
        SetResourceReference(BackgroundProperty, "Card");
        Padding = new Thickness(6);
        SnapsToDevicePixels = true;
        var stack = new StackPanel();
        cardScroll = new ScrollViewer
        {
            Content = stack, MaxHeight = 540, CanContentScroll = true, Focusable = true,
            VerticalScrollBarVisibility = ScrollBarVisibility.Hidden,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
        };
        AutomationProperties.SetName(cardScroll, "敌方卡片");
        cardScroll.SizeChanged += (_, args) =>
        {
            if (args.NewSize.Height < args.PreviousSize.Height || args.NewSize.Width < args.PreviousSize.Width)
                cardScroll.ScrollToTop();
        };
        Child = cardScroll;
        cards = Enumerable.Range(1, CardCount).Select(slot => new TargetCard(slot, Page, LockChanged)).ToArray();
        foreach (var card in cards) stack.Children.Add(card);
        Update("", default, "");
    }

    internal void Update(string battleId, JsonElement enemy, string enemyStatus)
    {
        status = enemyStatus;
        bool newBattle = battle != battleId;
        if (newBattle)
        {
            battle = battleId;
            departedByType.Clear();
            foreach (var card in cards) card.Reset();
        }

        var observed = enemy.Get("rows").Items().Select(Target.Read).OfType<Target>()
            .DistinctBy(target => target.Key).ToDictionary(target => target.Key, StringComparer.Ordinal);
        foreach (var target in observed.Values.Where(target => target.Departed))
        {
            if (!departedByType.TryGetValue(target.Eid, out var previous) ||
                target.EndFrame >= previous.EndFrame)
            {
                bool sameInstance = previous is not null && previous.Key == target.Key;
                var row = sameInstance ? KeepReadings(target.Row, previous!.Row) : target.Row;
                if (!sameInstance || !JsonElement.DeepEquals(row, previous!.Row))
                    departedByType[target.Eid] = target with { Row = row.Clone() };
            }
        }

        var live = observed.Values.Where(target => target.Live)
            .OrderByDescending(target => target.Row.Number("hp", double.NegativeInfinity))
            .ThenBy(target => target.Key, StringComparer.Ordinal).ToArray();
        var representatives = live.DistinctBy(target => target.Eid).ToArray();
        var representativeKeys = representatives.Select(target => target.Key).ToHashSet(StringComparer.Ordinal);
        queue = representatives.Concat(live.Where(target => !representativeKeys.Contains(target.Key)))
            .Concat(departedByType.Values.OrderByDescending(target => target.EndFrame)
                .ThenBy(target => target.Eid, StringComparer.Ordinal))
            .DistinctBy(target => target.Key).ToArray();
        RefreshSelections(observed);
        if (newBattle) cardScroll.ScrollToTop();
    }

    private void RefreshSelections(IReadOnlyDictionary<string, Target>? observed = null)
    {
        var assigned = new HashSet<string>(StringComparer.Ordinal);
        var types = new HashSet<string>(StringComparer.Ordinal);
        var slots = new Target?[cards.Length];
        var byKey = queue.ToDictionary(target => target.Key, StringComparer.Ordinal);

        void Assign(int index, Target target)
        {
            slots[index] = target;
            assigned.Add(target.Key);
            types.Add(target.Eid);
        }

        for (int index = 0; index < cards.Length; index++)
        {
            var card = cards[index];
            if (!card.Pinned || card.Current is not { } selected) continue;
            if (observed is not null)
            {
                if (observed.TryGetValue(selected.Key, out var update))
                {
                    if (!selected.Departed || update.Departed)
                        selected = update with { Row = KeepReadings(update.Row, selected.Row) };
                    card.LastObservation = false;
                }
                else card.LastObservation = !selected.Departed;
            }
            Assign(index, selected);
        }
        for (int index = 0; index < cards.Length; index++)
        {
            var card = cards[index];
            if (slots[index] is not null || !card.Manual || card.Current is not { } selected) continue;
            if (byKey.TryGetValue(selected.Key, out var update) && !assigned.Contains(update.Key)
                && (!selected.Live || update.Live))
                Assign(index, update);
        }
        for (int index = 0; index < cards.Length; index++)
        {
            var card = cards[index];
            if (slots[index] is not null || card.Current is not { } selected) continue;
            if (byKey.TryGetValue(selected.Key, out var update) && update.Live
                && !assigned.Contains(update.Key) && !types.Contains(update.Eid))
                Assign(index, update);
        }
        for (int index = 0; index < cards.Length; index++)
        {
            var card = cards[index];
            if (slots[index] is null)
            {
                var remaining = queue.Where(target => !assigned.Contains(target.Key));
                var choice = remaining.FirstOrDefault(target => !types.Contains(target.Eid))
                    ?? remaining.FirstOrDefault();
                if (choice is not null) Assign(index, choice);
            }
            if (!card.Pinned) card.LastObservation = false;
            if (slots[index]?.Key != card.Current?.Key) card.Manual = false;
            card.Current = slots[index];
        }
        RenderCards();
    }

    private void LockChanged(TargetCard card)
    {
        if (card.Current is null) return;
        if (card.Pinned) RenderCards();
        else RefreshSelections();
    }

    private Target? Adjacent(TargetCard card, int direction)
    {
        if (card.Pinned || card.Current is not { } current) return null;
        int index = Array.FindIndex(queue, target => target.Key == current.Key);
        if (index < 0) return null;
        for (int next = index + direction; next >= 0 && next < queue.Length; next += direction)
        {
            var target = queue[next];
            if (!cards.Any(other => other != card && other.Current?.Key == target.Key))
                return target;
        }
        return null;
    }

    private void Page(TargetCard card, int direction)
    {
        if (Adjacent(card, direction) is not { } target) return;
        card.Current = target;
        card.Manual = true;
        RenderCards();
    }

    private void RenderCards()
    {
        bool haveTarget = cards.Any(card => card.Current is not null);
        foreach (var card in cards)
        {
            card.Render(Adjacent(card, -1) is not null, Adjacent(card, 1) is not null,
                !haveTarget && card == cards[0], status);
        }
    }

    private static JsonElement KeepReadings(JsonElement row, JsonElement previous)
    {
        var missing = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        foreach (var key in new[] { "atk", "def", "res", "hp" })
        {
            if (row.Get(key).ValueKind != JsonValueKind.Number && previous.Get(key).ValueKind == JsonValueKind.Number)
                missing[key] = previous.Get(key);
        }
        if (!row.Bool("hp_known") && previous.Bool("hp_known") && previous.Number("max_hp") > 0 &&
            row.Get("max_hp_lower_bound").ValueKind != JsonValueKind.Number)
        {
            missing["max_hp"] = previous.Get("max_hp");
            missing["hp_known"] = previous.Get("hp_known");
        }
        if (missing.Count == 0) return row;
        missing["uses_last_readings"] = JsonSerializer.SerializeToElement(true);
        foreach (var property in row.EnumerateObject()) missing.TryAdd(property.Name, property.Value);
        return JsonSerializer.SerializeToElement(missing);
    }

    private sealed record Target(string Key, string Uid, string Eid, JsonElement Row)
    {
        internal bool Live => Row.Bool("alive") && Row.Text("lifecycle") is "active" or "rebirthing";
        internal bool Departed => Row.Text("lifecycle") == "departed";
        internal double EndFrame => Row.Number("end_frame", Row.Number("spawn_frame", -1));

        internal static Target? Read(JsonElement row)
        {
            string eid = row.Text("eid");
            if (eid.Length == 0 || row.Bool("entry_inferred")) return null;
            string uid = EnemyItem.InstanceUid(row);
            if (uid.Length > 0) return new($"uid:{uid}/{eid}", uid, eid, row);
            string roster = EnemyItem.RosterIdentity(row);
            return roster.Length > 0 ? new($"roster:{roster}", "", eid, row) : null;
        }
    }

    private sealed class TargetCard : Border
    {
        private static readonly Geometry UnlockedGlyph = Geometry.Parse("M 3,7 L 3,4 C 3,0 9,0 9,4 M 1,7 L 11,7 L 11,14 L 1,14 Z M 6,10 L 6,12");
        private static readonly Geometry LockedGlyph = Geometry.Parse("M 3,7 L 3,4 C 3,0 9,0 9,4 L 9,7 M 1,7 L 11,7 L 11,14 L 1,14 Z M 6,10 L 6,12");
        private static readonly Lazy<ImageSource> PeoplePortraitTrim = new(() => new BitmapImage(
            new Uri("pack://application:,,,/Assets/People/Components/frame-a-red.png")));
        private readonly TextBlock name = Label(17, "ConsoleInk", bold: true);
        private readonly TextBlock identity = Label(12, "ConsoleMuted");
        private readonly TextBlock badge = Label(28, "ConsoleMuted", bold: true);
        private readonly Image portrait = new() { Stretch = Stretch.Uniform, Margin = new Thickness(1) };
        private readonly Image portraitTrim = new()
        {
            Stretch = Stretch.Uniform, Height = 93, Visibility = Visibility.Collapsed, IsHitTestVisible = false,
        };
        private readonly Border headerMark = new()
        {
            Width = 3, Height = 25, Margin = new Thickness(0, 0, 7, 0),
            VerticalAlignment = VerticalAlignment.Center, Visibility = Visibility.Collapsed,
        };
        private readonly TextBlock health;
        private readonly TextBlock attack;
        private readonly TextBlock defense;
        private readonly TextBlock resistance;
        private readonly ToggleButton targetLock;
        private readonly Path lockIcon;
        private readonly Button previous;
        private readonly Button next;

        internal Target? Current { get; set; }
        internal bool Manual { get; set; }
        internal bool LastObservation { get; set; }
        internal bool Pinned => targetLock.IsChecked == true;

        internal TargetCard(int slot, Action<TargetCard, int> page, Action<TargetCard> lockChanged)
        {
            SetResourceReference(BackgroundProperty, "Console");
            SetResourceReference(BorderBrushProperty, "ConsoleLine");
            BorderThickness = new Thickness(1);
            Padding = new Thickness(8, 4, 8, 5);
            Margin = new Thickness(0, 0, 0, 4);
            AutomationProperties.SetAutomationId(this, $"LiveEnemyCard{slot}");
            string suffix = slot == 1 ? "" : slot.ToString(System.Globalization.CultureInfo.InvariantCulture);

            var card = new StackPanel();
            Child = card;
            var header = new DockPanel { Margin = new Thickness(0, 0, 0, 3) };
            card.Children.Add(header);
            targetLock = new ToggleButton { Width = 32, Height = 30, MinHeight = 0,
                Padding = new Thickness(0), Margin = new Thickness(4, 0, 0, 0),
                VerticalAlignment = VerticalAlignment.Top };
            lockIcon = Icon(UnlockedGlyph);
            lockIcon.SetBinding(Shape.StrokeProperty, new Binding(nameof(Control.Foreground)) { Source = targetLock });
            targetLock.Content = lockIcon;
            AutomationProperties.SetAutomationId(targetLock, "LockLiveEnemy" + suffix);
            targetLock.Checked += (_, _) => lockChanged(this);
            targetLock.Unchecked += (_, _) => lockChanged(this);
            DockPanel.SetDock(targetLock, Dock.Right);
            header.Children.Add(targetLock);
            headerMark.SetResourceReference(BackgroundProperty, "Signal");
            DockPanel.SetDock(headerMark, Dock.Left);
            header.Children.Add(headerMark);
            var heading = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            heading.Children.Add(name);
            heading.Children.Add(identity);
            header.Children.Add(heading);

            var body = new Grid();
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(112) });
            body.ColumnDefinitions.Add(new ColumnDefinition());
            card.Children.Add(body);

            badge.HorizontalAlignment = HorizontalAlignment.Center;
            badge.VerticalAlignment = VerticalAlignment.Center;
            var art = new Grid();
            art.Children.Add(badge);
            art.Children.Add(portrait);
            art.Children.Add(portraitTrim);
            var frame = new Border { Child = art, Height = 100, BorderThickness = new Thickness(1) };
            frame.SetResourceReference(BackgroundProperty, "ConsoleCard");
            frame.SetResourceReference(BorderBrushProperty, "ConsoleLine");
            SizeChanged += (_, args) =>
            {
                if (args.WidthChanged) frame.Height = args.NewSize.Width < 230 ? 94 : 100;
            };
            var overlay = new Grid { Margin = new Thickness(0, 0, 8, 0) };
            overlay.Children.Add(frame);
            previous = Arrow(true, suffix, () => page(this, -1));
            next = Arrow(false, suffix, () => page(this, 1));
            overlay.Children.Add(previous);
            overlay.Children.Add(next);
            body.Children.Add(overlay);

            var stats = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            Grid.SetColumn(stats, 1);
            body.Children.Add(stats);
            health = Reading(stats, "HP");
            attack = Reading(stats, "ATK");
            defense = Reading(stats, "DEF");
            resistance = Reading(stats, "RES");
        }

        internal void Reset()
        {
            Current = null;
            Manual = LastObservation = false;
            targetLock.IsChecked = false;
        }

        internal void Render(bool canPrevious, bool canNext, bool showEmpty, string status)
        {
            Visibility = Current is not null || showEmpty ? Visibility.Visible : Visibility.Collapsed;
            previous.Visibility = next.Visibility = canPrevious || canNext ? Visibility.Visible : Visibility.Collapsed;
            previous.IsEnabled = canPrevious;
            next.IsEnabled = canNext;
            targetLock.IsEnabled = Current is { Uid.Length: > 0 };
            if (Visibility == Visibility.Collapsed) return;
            portraitTrim.Visibility = headerMark.Visibility = Theme.IsPeople ? Visibility.Visible : Visibility.Collapsed;
            if (Theme.IsPeople) portraitTrim.Source ??= PeoplePortraitTrim.Value;
            lockIcon.Data = Pinned ? LockedGlyph : UnlockedGlyph;
            string lockLabel = Pinned ? "解锁当前敌人" : "锁定当前敌人";
            targetLock.ToolTip = lockLabel;
            AutomationProperties.SetName(targetLock, lockLabel);
            if (Current is { } current)
            {
                var row = current.Row;
                var target = new EnemyItem(row, current.Uid, EnemyItem.RosterIdentity(row));
                Show(name, target.Name);
                var code = row.Text("code");
                string lifecycle = LastObservation ? "最后观测 · 状态未知" : target.Status;
                Show(identity, code.Length == 0 ? lifecycle : code + " · " + lifecycle);
                identity.ToolTip = row.Bool("uses_last_readings")
                    ? target.Tooltip + "\n部分读数保留最后一次观测值"
                    : target.Tooltip;
                Show(badge, target.Portrait is null ? target.Badge : "");
                portrait.Source = target.Portrait;
                portrait.ToolTip = target.Tooltip;
                Show(health, target.HpText);
                Show(attack, Json.UiInt(row, "atk"));
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
                Show(attack, "—");
                Show(defense, "—");
                Show(resistance, "—");
            }
        }

        private static Path Icon(Geometry geometry) => new()
        {
            Data = geometry, StrokeThickness = 1.7, StrokeStartLineCap = PenLineCap.Round,
            StrokeEndLineCap = PenLineCap.Round, StrokeLineJoin = PenLineJoin.Round, IsHitTestVisible = false,
        };

        private static Button Arrow(bool previous, string suffix, Action page)
        {
            var label = previous ? "上一名敌人" : "下一名敌人";
            var icon = Icon(Geometry.Parse(previous ? "M 7,1 L 2,6 L 7,11" : "M 2,1 L 7,6 L 2,11"));
            icon.SetResourceReference(Shape.StrokeProperty, "Ink");
            var arrow = new Button
            {
                Content = icon, Width = 24, Height = 34, Padding = new Thickness(0),
                HorizontalAlignment = previous ? HorizontalAlignment.Left : HorizontalAlignment.Right,
                VerticalAlignment = VerticalAlignment.Center,
                Margin = previous ? new Thickness(-5, 0, 0, 0) : new Thickness(0, 0, -5, 0),
                ToolTip = label,
            };
            AutomationProperties.SetName(arrow, label);
            AutomationProperties.SetAutomationId(arrow, (previous ? "PreviousLiveEnemy" : "NextLiveEnemy") + suffix);
            arrow.Click += (_, _) => page();
            return arrow;
        }

        private static TextBlock Label(double size, string color, bool bold = false)
        {
            var label = new TextBlock { FontSize = size, TextTrimming = TextTrimming.CharacterEllipsis,
                FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal };
            label.SetResourceReference(TextBlock.ForegroundProperty, color);
            return label;
        }

        private static TextBlock Reading(Panel host, string caption)
        {
            var grid = new Grid();
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.ColumnDefinitions.Add(new ColumnDefinition());
            var label = Label(12, "ConsoleMuted");
            label.Text = caption;
            label.Margin = new Thickness(0, 0, 4, 0);
            var value = Label(12, "ConsoleInk", bold: true);
            value.TextAlignment = TextAlignment.Right;
            grid.Children.Add(label);
            Grid.SetColumn(value, 1);
            grid.Children.Add(value);
            var row = new Border { Child = grid, BorderThickness = new Thickness(0, 0, 0, 1),
                Padding = new Thickness(0, 2, 0, 2) };
            row.SetResourceReference(BorderBrushProperty, "ConsoleLine");
            host.Children.Add(row);
            return value;
        }

        private static void Show(TextBlock element, string value)
        {
            if (element.Text == value) return;
            element.Text = value;
            element.ToolTip = value;
        }
    }
}
