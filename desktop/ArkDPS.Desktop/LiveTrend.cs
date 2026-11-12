using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace ArkDPS.Desktop;

public enum LiveTrendKind { Compact, Timeline, Operator }

internal sealed record LiveTrendOperator(string Id, string Name, int ColorIndex);

internal sealed record LiveTrendPoint(double Time, long Segment, bool StartsSegment,
    double? Team, double? Total, IReadOnlyDictionary<string, double?> Operators);

internal sealed class LiveTrendModel
{
    private const int Duration = 600;
    private const int MaxInterval = 5;
    private readonly List<LiveTrendPoint> points = new();
    private readonly Dictionary<string, LiveTrendOperator> operators = new(StringComparer.Ordinal);
    private bool breakPending;

    internal string BattleId { get; private set; } = "";
    internal string Status { get; private set; } = "waiting";
    internal IReadOnlyList<LiveTrendPoint> Points => points;
    internal IReadOnlyCollection<LiveTrendOperator> Operators => operators.Values;
    internal event Action? Changed;

    internal void Interrupt()
    {
        breakPending = true;
        if (Status == "stale") return;
        Status = "stale";
        Changed?.Invoke();
    }

    internal void Apply(JsonElement state)
    {
        var data = state.Get("live_analysis");
        var battle = data.Text("battle_id");
        if (battle.Length == 0) battle = state.Text("battle_id");
        bool newBattle = battle.Length > 0 && BattleId != battle;
        if (newBattle)
        {
            BattleId = battle;
            points.Clear();
            operators.Clear();
            breakPending = false;
        }

        string status = state.Text("status");
        bool statusChanged = Status != status;
        Status = status;
        bool settling = status is "finalizing" or "ended";
        double time = data.Get("game_time").TryNumber(double.NaN);
        if (!double.IsFinite(time) || time < 0 || battle.Length == 0 ||
            status is "disconnected" or "stale" or "locating" or "waiting" or "frozen")
        {
            breakPending = true;
            if (newBattle || statusChanged) Changed?.Invoke();
            return;
        }
        if (points.Count > 0 && (status == "paused" || time < points[^1].Time ||
            (breakPending && !settling && time == points[^1].Time)))
        {
            if (statusChanged) Changed?.Invoke();
            return;
        }

        var segmentValue = data.Get("segment");
        if (segmentValue.ValueKind != JsonValueKind.Number || !segmentValue.TryGetInt64(out long segment))
        {
            breakPending = true;
            if (newBattle || statusChanged) Changed?.Invoke();
            return;
        }

        var rates = new Dictionary<string, double?>(StringComparer.Ordinal);
        bool operatorChanged = false;
        foreach (var row in data.Get("operators").Items().OrderByDescending(item => Nonnegative(item.Get("recent_dps"))))
        {
            string id = row.Text("owner_id");
            if (id.Length == 0) continue;
            string name = row.Text("name");
            if (name.Length == 0) name = id;
            if (!operators.TryGetValue(id, out var op) || op.Name != name)
            {
                operators[id] = new(id, name, op?.ColorIndex ?? operators.Count);
                operatorChanged = true;
            }
            rates[id] = Nonnegative(row.Get("recent_dps"));
        }

        double? team = Nonnegative(data.Get("team_recent_dps"));
        double? total = Nonnegative(data.Get("team_total_damage"));
        bool replace = points.Count > 0 && Math.Floor(points[^1].Time) == Math.Floor(time);
        bool starts = breakPending || (points.Count > 0 &&
            (points[^1].Segment != segment || time - points[^1].Time > MaxInterval));
        if (replace && !starts && settling)
        {
            // Settlement can revise the cumulative total in the current bucket
            // while clearing rate fields. Keep that bucket's observed rates.
            var previous = points[^1];
            team ??= previous.Team;
            foreach (var (id, rate) in previous.Operators)
                if (!rates.TryGetValue(id, out var latest) || !latest.HasValue) rates[id] = rate;
        }
        if (replace) starts |= points[^1].StartsSegment;
        if (replace && !operatorChanged && !statusChanged)
        {
            var last = points[^1];
            if (last.Time == time && last.Segment == segment && last.StartsSegment == starts &&
                last.Team == team && last.Total == total && last.Operators.Count == rates.Count &&
                rates.All(pair => last.Operators.TryGetValue(pair.Key, out var value) && value == pair.Value)) return;
        }

        var point = new LiveTrendPoint(time, segment, starts, team, total, rates);
        if (replace) points[^1] = point;
        else points.Add(point);
        breakPending = false;
        int expired = points.FindIndex(item => item.Time >= time - Duration);
        if (expired > 0) points.RemoveRange(0, expired);
        Changed?.Invoke();
    }

    private static double? Nonnegative(JsonElement value)
    {
        double number = value.TryNumber(double.NaN);
        return double.IsFinite(number) && number >= 0 ? number : null;
    }
}

public sealed class LiveTrendView : Border
{
    private enum Mode { Team, Stack, Total }
    private const int MaxSeries = 6;
    private static readonly string[] SeriesColors =
    {
        "#FF912D", "#5AC8E5", "#54AACA", "#548EB9", "#688CB8", "#A6B6C5",
        "#FFD672", "#69ADFF", "#FF9A9C", "#B9E07B", "#D9A2F5", "#91CDD0",
        "#FFBDAB", "#ACB6FF", "#F9AD71", "#B6DBED",
    };
    private static readonly Brush[] SeriesBrushes = SeriesColors.Select(ColorBrush).ToArray();
    private static readonly Brush[] AreaBrushes = SeriesBrushes.Select(AreaBrush).ToArray();
    private static readonly Brush ChartWell = ColorBrush("#171F27");
    private static readonly Brush HeaderMetal = ColorBrush("#E3E8EC");
    private static readonly Brush HeaderInk = ColorBrush("#162230");
    private static readonly Brush HeaderAccent = ColorBrush("#F48729");
    private static readonly Brush Subdivision = ColorBrush("#263039");
    private static readonly Brush MajorGrid = ColorBrush("#37424D");
    private static readonly Brush AxisInk = ColorBrush("#CBD7E3");
    private static readonly Brush OtherBrush = ColorBrush("#8797A7");
    private static readonly Typeface NumberFont = new("Cascadia Mono, Consolas");
    private static readonly Typeface TextFont = new("Segoe UI, Microsoft YaHei UI");
    private static readonly Typeface StrongText = new(TextFont.FontFamily, FontStyles.Normal, FontWeights.SemiBold, FontStretches.Normal);

    private readonly ChartSurface chart;
    private readonly TextBlock heading = new();
    private readonly TextBlock readout = new();
    private readonly ComboBox modes = new();
    private readonly Button seriesButton = new() { Content = "干员", Visibility = Visibility.Collapsed };
    private readonly Border marker = new() { Width = 8, Height = 12, Margin = new Thickness(8, 0, 8, 0),
        VerticalAlignment = VerticalAlignment.Center, Background = HeaderAccent, LayoutTransform = new SkewTransform(-21, 0) };
    private readonly WrapPanel legend = new();
    private readonly Dictionary<string, CheckBox> choices = new(StringComparer.Ordinal);
    private readonly HashSet<string> selected = new(StringComparer.Ordinal);
    private LiveTrendModel? model;
    private LiveTrendKind kind;
    private Mode mode;
    private string? owner;
    private string displayedBattle = "";
    private double? cursor;
    private bool changingChoices;
    private bool customizedChoices;
    private bool customizedMode;
    private bool settingMode;
    private bool showSeries;

    private bool IsReference => Theme.Key == "lone-trail" && Kind != LiveTrendKind.Operator;
    private bool IsReferenceCompact => IsReference && Kind == LiveTrendKind.Compact;

    public LiveTrendView()
    {
        SetResourceReference(BackgroundProperty, "ConsoleCard");
        SetResourceReference(BorderBrushProperty, "ConsoleLine");
        SetResourceReference(CornerRadiusProperty, "CardRadius");
        BorderThickness = new Thickness(1);
        Padding = new Thickness(12, 9, 12, 8);
        heading.FontSize = 14;
        heading.FontWeight = FontWeights.SemiBold;
        heading.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleInk");
        readout.FontSize = 11;
        readout.MinHeight = 17;
        readout.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleMuted");
        modes.ItemsSource = new[] { "全队近 5 秒 DPS", "干员贡献", "累计伤害（含过量）" };
        modes.MinWidth = 112;
        modes.SelectedIndex = 0;
        modes.SelectionChanged += (_, _) =>
        {
            if (modes.SelectedIndex < 0 || settingMode) return;
            customizedMode = true;
            mode = (Mode)modes.SelectedIndex;
            Refresh();
        };
        AutomationProperties.SetName(modes, "切换战斗时间轴指标");
        AutomationProperties.SetName(seriesButton, "选择要显示的干员");
        seriesButton.Click += (_, _) => { showSeries = !showSeries; Refresh(); };
        chart = new ChartSurface(this);
        IsVisibleChanged += (_, _) => { if (IsVisible) Refresh(); };
        Compose();
    }

    public LiveTrendKind Kind
    {
        get => kind;
        set
        {
            if (kind == value) return;
            kind = value;
            Compose();
        }
    }

    internal void Bind(LiveTrendModel value)
    {
        if (ReferenceEquals(value, model)) return;
        if (model != null) model.Changed -= OnData;
        model = value;
        model.Changed += OnData;
        UpdateBattle();
        Refresh();
    }

    internal void SetOwner(string? value)
    {
        if (owner == value) return;
        owner = value;
        cursor = null;
        if (Kind == LiveTrendKind.Operator) Visibility = value == null ? Visibility.Collapsed : Visibility.Visible;
        if (IsVisible) Refresh();
    }

    internal void RefreshTheme()
    {
        ApplyAppearance();
        Refresh();
    }

    private void Compose()
    {
        if (Child is Panel previous)
        {
            foreach (var panel in previous.Children.OfType<Panel>()) panel.Children.Clear();
            previous.Children.Clear();
        }
        AutomationProperties.SetHelpText(chart, "方向键查看相邻采样点，Home 和 End 跳到首尾，Escape 清除选择。");

        var header = new DockPanel { LastChildFill = true, Margin = new Thickness(0, 0, 0, 3) };
        if (Kind == LiveTrendKind.Timeline)
        {
            DockPanel.SetDock(modes, Dock.Right);
            header.Children.Add(modes);
            DockPanel.SetDock(seriesButton, Dock.Right);
            header.Children.Add(seriesButton);
        }
        DockPanel.SetDock(marker, Dock.Left);
        header.Children.Add(marker);
        header.Children.Add(heading);
        var body = new StackPanel();
        body.Children.Add(header);
        body.Children.Add(chart);
        body.Children.Add(readout);
        if (Kind == LiveTrendKind.Timeline)
        {
            legend.Margin = new Thickness(0, 4, 0, 0);
            body.Children.Add(legend);
        }
        Child = body;
        ApplyAppearance();
        if (Kind == LiveTrendKind.Operator && owner == null) Visibility = Visibility.Collapsed;
        Refresh();
    }

    private void ApplyAppearance()
    {
        if (Child is not StackPanel body || body.Children[0] is not DockPanel header) return;
        bool reference = IsReference;
        bool metal = reference && Kind == LiveTrendKind.Timeline;
        heading.Text = Kind switch
        {
            LiveTrendKind.Compact => reference ? "实时 DPS 趋势 / LIVE OUTPUT (STACKED)" : "近 60 游戏秒 · 全队 DPS",
            LiveTrendKind.Operator => "干员近 5 秒 DPS 趋势",
            _ => reference ? "战斗输出趋势 / COMBAT TIMELINE" : "战斗输出时间轴",
        };
        AutomationProperties.SetName(chart, Kind == LiveTrendKind.Timeline ? "战斗输出时间轴" : heading.Text);
        chart.Height = Kind switch { LiveTrendKind.Compact => reference ? 138 : 52,
            LiveTrendKind.Operator => 108, _ => reference ? 158 : 220 };
        chart.Margin = metal ? new Thickness(8, 3, 8, 4) : new Thickness();
        marker.Visibility = reference ? Visibility.Visible : Visibility.Collapsed;
        header.Background = metal ? HeaderMetal : Brushes.Transparent;
        header.MinHeight = metal ? 29 : 0;
        heading.FontSize = reference ? 13 : 14;
        heading.VerticalAlignment = VerticalAlignment.Center;
        if (metal)
        {
            Background = ChartWell;
            BorderBrush = HeaderMetal;
            heading.Foreground = HeaderInk;
            modes.Background = HeaderMetal;
            modes.BorderBrush = HeaderMetal;
            modes.Foreground = HeaderInk;
            modes.MinHeight = 28;
            seriesButton.Background = HeaderMetal;
            seriesButton.Foreground = HeaderInk;
            seriesButton.BorderBrush = HeaderMetal;
            seriesButton.Padding = new Thickness(6, 2, 6, 2);
        }
        else
        {
            if (IsReferenceCompact) Background = ChartWell;
            else SetResourceReference(BackgroundProperty, "ConsoleCard");
            SetResourceReference(BorderBrushProperty, "ConsoleLine");
            heading.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleInk");
            modes.ClearValue(Control.BackgroundProperty);
            modes.ClearValue(Control.ForegroundProperty);
            modes.ClearValue(Control.BorderBrushProperty);
            modes.ClearValue(MinHeightProperty);
        }
        Padding = reference ? new Thickness(0) : new Thickness(12, 9, 12, 8);
        BorderThickness = IsReferenceCompact ? new Thickness(0) : new Thickness(1);
        readout.Margin = reference ? new Thickness(10, 0, 10, 2) : new Thickness();
    }

    private void OnData()
    {
        UpdateBattle();
        if (IsVisible) Refresh();
    }

    private void UpdateBattle()
    {
        if (model == null || displayedBattle == model.BattleId) return;
        displayedBattle = model.BattleId;
        cursor = null;
        choices.Clear();
        selected.Clear();
        legend.Children.Clear();
        customizedChoices = false;
    }

    private void Refresh()
    {
        if (Kind == LiveTrendKind.Timeline)
        {
            if (!customizedMode)
            {
                mode = IsReference && model?.Points.Any(point => point.Team.HasValue &&
                    point.Operators.Values.Count(rate => rate > 0) >= 2) == true ? Mode.Stack : Mode.Team;
                settingMode = true;
                modes.SelectedIndex = (int)mode;
                settingMode = false;
            }
            bool stack = mode == Mode.Stack;
            seriesButton.Visibility = IsReference && stack ? Visibility.Visible : Visibility.Collapsed;
            legend.Visibility = stack && (!IsReference || showSeries) ? Visibility.Visible : Visibility.Collapsed;
            if (stack) UpdateChoices();
        }
        if (IsReferenceCompact) SelectDefaultOperators();
        UpdateReadout();
        chart.InvalidateVisual();
    }

    private void UpdateChoices()
    {
        if (model == null) return;
        foreach (var op in model.Operators)
        {
            if (!choices.TryGetValue(op.Id, out var toggle))
            {
                toggle = new CheckBox
                {
                    Foreground = SeriesBrushes[op.ColorIndex % SeriesBrushes.Length],
                    Margin = new Thickness(0, 0, 14, 2),
                };
                toggle.Checked += (_, _) => ChangeChoice(op.Id, toggle, true);
                toggle.Unchecked += (_, _) => ChangeChoice(op.Id, toggle, false);
                choices.Add(op.Id, toggle);
                legend.Children.Add(toggle);
            }
            if (!Equals(toggle.Content, op.Name)) toggle.Content = op.Name;
            if (!customizedChoices && selected.Count < MaxSeries && !selected.Contains(op.Id) &&
                model.Points.Any(point => point.Operators.TryGetValue(op.Id, out var value) && value > 0))
            {
                selected.Add(op.Id);
                changingChoices = true;
                toggle.IsChecked = true;
                changingChoices = false;
            }
        }
    }

    private void SelectDefaultOperators()
    {
        if (model == null || selected.Count >= MaxSeries) return;
        foreach (var op in model.Operators)
        {
            if (selected.Count >= MaxSeries) break;
            if (model.Points.Any(point => point.Team.HasValue && point.Operators.TryGetValue(op.Id, out var rate) && rate > 0))
                selected.Add(op.Id);
        }
    }

    private void ChangeChoice(string id, CheckBox toggle, bool enabled)
    {
        if (changingChoices) return;
        customizedChoices = true;
        if (enabled && selected.Count == MaxSeries)
        {
            changingChoices = true;
            toggle.IsChecked = false;
            changingChoices = false;
            readout.Visibility = Visibility.Visible;
            readout.Text = "最多显示六名干员，请先取消一项。";
            return;
        }
        if (enabled) selected.Add(id);
        else selected.Remove(id);
        Refresh();
    }

    private LiveTrendPoint[] VisiblePoints()
    {
        if (model == null || model.Points.Count == 0) return Array.Empty<LiveTrendPoint>();
        if (Kind != LiveTrendKind.Compact) return model.Points.ToArray();
        double start = model.Points[^1].Time - 60;
        return model.Points.Where(point => point.Time >= start).ToArray();
    }

    private IEnumerable<LiveTrendOperator> SelectedOperators() =>
        model?.Operators.Where(op => selected.Contains(op.Id)) ?? Enumerable.Empty<LiveTrendOperator>();

    private double? Value(LiveTrendPoint point)
    {
        if (Kind == LiveTrendKind.Operator)
            return owner != null && point.Operators.TryGetValue(owner, out var value) ? value : null;
        return Kind == LiveTrendKind.Timeline && mode == Mode.Total ? point.Total : point.Team;
    }

    private void UpdateReadout()
    {
        var points = VisiblePoints();
        string stateNote = model?.Status switch
        {
            "paused" => " · 暂停",
            "stale" or "disconnected" or "locating" => " · 采样中断，保留最近数据",
            "frozen" => " · 战局冻结，保留最近数据",
            _ => "",
        };
        readout.Visibility = !IsReference || points.Length == 0 || cursor.HasValue || stateNote.Length > 0
            ? Visibility.Visible : Visibility.Collapsed;
        if (points.Length == 0)
        {
            readout.Text = "等待可靠战斗采样" + (model?.Status switch
            {
                "paused" => " · 暂停",
                "frozen" => " · 战局冻结",
                "stale" or "disconnected" or "locating" => " · 采样中断",
                _ => "",
            });
            return;
        }
        var point = cursor is double time ? Nearest(points, time) : points[^1];
        string prefix = TimeLabel(point.Time);
        if (Kind == LiveTrendKind.Timeline && mode == Mode.Stack)
        {
            var parts = SelectedOperators().Select(op => op.Name + " " +
                Number(point.Operators.TryGetValue(op.Id, out var value) ? value : null));
            readout.Text = selected.Count == 0 ? "选择最多六名干员查看近期 DPS" + stateNote
                : prefix + " · 近 5 秒 DPS · " + string.Join("   ", parts) + stateNote;
        }
        else
        {
            string units = Kind == LiveTrendKind.Timeline && mode == Mode.Total ? " 累计伤害" : " DPS";
            readout.Text = prefix + " · " + Number(Value(point)) + units + stateNote;
            if (IsReferenceCompact && cursor.HasValue && point.Team is > 0)
            {
                var shares = Participation(point, SelectedOperators());
                if (shares.Count > 0) readout.Text += " · 近 5 秒贡献 " + string.Join(" · ", shares.Select(entry =>
                    entry.Name + " " + (entry.Rate / point.Team.Value).ToString("0.0%", CultureInfo.CurrentCulture)));
            }
        }
    }

    private static int NearestIndex(IReadOnlyList<LiveTrendPoint> points, double time)
    {
        int start = 0, end = points.Count - 1;
        while (start < end)
        {
            int middle = start + (end - start) / 2;
            if (points[middle].Time < time) start = middle + 1;
            else end = middle;
        }
        if (start > 0 && time - points[start - 1].Time <= points[start].Time - time) return start - 1;
        return start;
    }

    private static LiveTrendPoint Nearest(IReadOnlyList<LiveTrendPoint> points, double time) =>
        points[NearestIndex(points, time)];

    private static string TimeLabel(double seconds)
    {
        int whole = (int)Math.Clamp(seconds, 0, int.MaxValue);
        return $"{whole / 60:00}:{whole % 60:00}";
    }

    private static string Number(double? number) => number?.ToString("#,0.#", CultureInfo.CurrentCulture) ?? "—";

    private static string Abbreviate(double value) => value switch
    {
        >= 1_000_000 => (value / 1_000_000).ToString("0.#", CultureInfo.InvariantCulture) + "M",
        >= 1_000 => (value / 1_000).ToString("0.#", CultureInfo.InvariantCulture) + "K",
        _ => value.ToString("0.#", CultureInfo.InvariantCulture),
    };

    private static string Axis(double number) => number >= 10_000
        ? (number / 10_000).ToString("0.#", CultureInfo.CurrentCulture) + "万"
        : Number(number);

    private static Brush ColorBrush(string hex)
    {
        var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
        brush.Freeze();
        return brush;
    }

    private static Brush AreaBrush(Brush color)
    {
        var brush = color.Clone();
        brush.Opacity = .75;
        brush.Freeze();
        return brush;
    }

    private static Brush ChartSignal()
    {
        var signal = Theme.Resource("Signal");
        if (signal is SolidColorBrush line && Theme.Resource("ConsoleCard") is SolidColorBrush surface)
        {
            double a = Luminance(line.Color), b = Luminance(surface.Color);
            if ((Math.Max(a, b) + .05) / (Math.Min(a, b) + .05) >= 3) return signal;
        }
        return Theme.Resource("ConsoleInk");
    }

    private static double Luminance(Color color) => .2126 * Linear(color.R) + .7152 * Linear(color.G) + .0722 * Linear(color.B);

    private static double Linear(byte channel)
    {
        double value = channel / 255d;
        return value <= .04045 ? value / 12.92 : Math.Pow((value + .055) / 1.055, 2.4);
    }

    private static (double Ceiling, int Ticks) ReferenceRange(double maximum)
    {
        double scale = Math.Pow(10, Math.Floor(Math.Log10(maximum / 5)));
        double unit = maximum / (5 * scale);
        double step = (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10) * scale;
        int ticks = (int)Math.Ceiling(maximum / step);
        return (step * ticks, ticks);
    }

    private static List<(string Name, double Rate, Brush Brush)> Participation(LiveTrendPoint point,
        IEnumerable<LiveTrendOperator> operators)
    {
        var entries = new List<(string Name, double Rate, Brush Brush)>();
        if (point.Team is not > 0) return entries;
        foreach (var op in operators)
        {
            if (!point.Operators.TryGetValue(op.Id, out var rate) || !rate.HasValue) return new();
            entries.Add((op.Name, rate.Value, SeriesBrushes[op.ColorIndex % SeriesBrushes.Length]));
        }
        if (entries.Count == 0) return entries;
        double remainder = point.Team.Value - entries.Sum(entry => entry.Rate);
        double tolerance = Math.Max(.0001, point.Team.Value * 1e-8);
        if (remainder > tolerance) entries.Add(("其他", remainder, OtherBrush));
        else if (remainder < -tolerance) entries.Clear();
        return entries;
    }

    private sealed class ChartSurface : FrameworkElement
    {
        private readonly LiveTrendView view;
        private Rect plot;
        private double minTime, maxTime;

        internal ChartSurface(LiveTrendView view)
        {
            this.view = view;
            Focusable = true;
            ClipToBounds = true;
        }

        protected override AutomationPeer OnCreateAutomationPeer() => new ChartAutomationPeer(this);

        private sealed class ChartAutomationPeer(ChartSurface owner) : FrameworkElementAutomationPeer(owner)
        {
            protected override string GetClassNameCore() => "LiveTrendChart";
            protected override AutomationControlType GetAutomationControlTypeCore() => AutomationControlType.Custom;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            var point = e.GetPosition(this);
            if (plot.Width > 0 && plot.Contains(point))
                Select(minTime + (point.X - plot.Left) / plot.Width * (maxTime - minTime));
        }

        protected override void OnMouseLeftButtonDown(MouseButtonEventArgs e)
        {
            base.OnMouseLeftButtonDown(e);
            Focus();
        }

        protected override void OnMouseLeave(MouseEventArgs e)
        {
            base.OnMouseLeave(e);
            if (!IsKeyboardFocused) Select(null);
        }

        protected override void OnGotKeyboardFocus(KeyboardFocusChangedEventArgs e)
        {
            base.OnGotKeyboardFocus(e);
            BringIntoView();
            InvalidateVisual();
        }

        protected override void OnLostKeyboardFocus(KeyboardFocusChangedEventArgs e)
        {
            base.OnLostKeyboardFocus(e);
            InvalidateVisual();
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            base.OnKeyDown(e);
            var points = view.VisiblePoints();
            if (points.Length == 0) return;
            int index = view.cursor is double time ? NearestIndex(points, time) : points.Length - 1;
            switch (e.Key)
            {
                case Key.Left: Select(points[Math.Max(0, index - 1)].Time); break;
                case Key.Right: Select(points[Math.Min(points.Length - 1, index + 1)].Time); break;
                case Key.Home: Select(points[0].Time); break;
                case Key.End: Select(points[^1].Time); break;
                case Key.Escape: Select(null); break;
                default: return;
            }
            view.readout.BringIntoView();
            e.Handled = true;
        }

        private void Select(double? time)
        {
            var points = view.VisiblePoints();
            double? sample = time is double value && points.Length > 0 ? Nearest(points, value).Time : null;
            if (view.cursor == sample) return;
            view.cursor = sample;
            view.UpdateReadout();
            AutomationProperties.SetHelpText(this, view.readout.Text + "。方向键查看相邻采样点。");
            InvalidateVisual();
        }

        protected override void OnRender(DrawingContext dc)
        {
            base.OnRender(dc);
            dc.DrawRectangle(Brushes.Transparent, null, new Rect(RenderSize));
            var points = view.VisiblePoints();
            plot = Rect.Empty;
            if (ActualWidth < 48 || ActualHeight < 30) return;
            var signal = ChartSignal();
            bool reference = view.IsReference;
            bool compactReference = view.IsReferenceCompact;
            if (reference) dc.DrawRectangle(ChartWell, null, new Rect(RenderSize));
            if (IsKeyboardFocused)
                dc.DrawRectangle(null, new Pen(signal, 1), new Rect(.5, .5, ActualWidth - 1, ActualHeight - 1));
            if (points.Length == 0) return;
            bool bareCompact = view.Kind == LiveTrendKind.Compact && !compactReference;
            var selected = view.SelectedOperators().ToArray();
            bool stack = view.Kind == LiveTrendKind.Timeline && view.mode == Mode.Stack ||
                compactReference && selected.Length > 0;
            var layers = reference ? selected.Reverse().ToArray() : selected;
            var heights = new double[]?[points.Length];
            if (stack && layers.Length > 0)
            {
                for (int i = 0; i < points.Length; i++)
                {
                    if (reference && !points[i].Team.HasValue) continue;
                    var rates = layers.Select(op => points[i].Operators.TryGetValue(op.Id, out var value) ? value : null).ToArray();
                    if (rates.All(value => value.HasValue)) heights[i] = rates.Select(value => value.GetValueOrDefault()).ToArray();
                }
            }
            var values = points.Select((point, index) => stack ? heights[index]?.Sum() : view.Value(point)).ToArray();
            double ceiling = Math.Max(1, values.Max() ?? 1);
            int ticks = bareCompact ? 2 : 4;
            if (reference) (ceiling, ticks) = ReferenceRange(ceiling);
            minTime = points[0].Time;
            maxTime = Math.Max(minTime + 1, points[^1].Time);
            double donutWidth = compactReference && ActualWidth >= 690 ? Math.Min(360, ActualWidth * .29) : 0;
            double left = bareCompact ? 3 : 49;
            double top = reference ? 17 : 7;
            plot = new Rect(left, top, Math.Max(1, ActualWidth - left - 13 - donutWidth),
                Math.Max(1, ActualHeight - top - (bareCompact ? 5 : 20)));
            double X(double time) => plot.Left + (time - minTime) / (maxTime - minTime) * plot.Width;
            double Y(double value) => plot.Bottom - value / ceiling * plot.Height;
            Point Map(int index, double value) => new(X(points[index].Time), Y(value));

            var muted = reference ? AxisInk : Theme.Resource("ConsoleMuted");
            var grid = Theme.Resource("ConsoleLine").Clone();
            grid.Opacity = .45;
            var gridPen = new Pen(reference ? MajorGrid : grid, 1);
            var trace = new Pen(signal, 2);
            var cursorPen = new Pen(Theme.Resource("ConsoleInk"), 1) { DashStyle = DashStyles.Dash };
            double dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
            FormattedText Label(string label) => new(label, CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, NumberFont, 10, muted, dpi);
            int preferredTicks = plot.Width < 250 ? 2 : Math.Clamp((int)(plot.Width / 72), 4, 10);
            int timeTicks = Math.Min(preferredTicks, Math.Max(1, (int)Math.Floor(maxTime - minTime)));
            if (reference)
            {
                var subgrid = new Pen(Subdivision, 1);
                for (int i = 1; i < timeTicks * 4; i++)
                {
                    double x = plot.Left + plot.Width * i / (timeTicks * 4);
                    dc.DrawLine(i % 4 == 0 ? gridPen : subgrid, new Point(x, plot.Top), new Point(x, plot.Bottom));
                }
                for (int i = 1; i < ticks * 2; i += 2)
                {
                    double y = plot.Top + plot.Height * i / (ticks * 2);
                    dc.DrawLine(subgrid, new Point(plot.Left, y), new Point(plot.Right, y));
                }
                var units = Label(view.Kind == LiveTrendKind.Timeline && view.mode == Mode.Total ? "伤害" : "DPS");
                dc.DrawText(units, new Point(plot.Left - units.Width - 7, 1));
            }
            for (int i = 0; i <= ticks; i++)
            {
                double y = plot.Top + plot.Height * i / ticks;
                dc.DrawLine(gridPen, new Point(plot.Left, y), new Point(plot.Right, y));
                if (bareCompact) continue;
                double axisValue = ceiling * (ticks - i) / ticks;
                var label = Label(reference ? Abbreviate(axisValue) : Axis(axisValue));
                dc.DrawText(label, new Point(Math.Max(0, plot.Left - label.Width - 6), y - label.Height / 2));
            }
            if (!bareCompact)
            {
                for (int i = 0; i <= timeTicks; i++)
                {
                    double time = minTime + (maxTime - minTime) * i / timeTicks;
                    var label = Label(TimeLabel(time));
                    dc.DrawText(label, new Point(Math.Min(plot.Right + 10 - label.Width, X(time) - label.Width / 2), plot.Bottom + 5));
                }
            }

            if (stack) DrawStack(dc, layers, points, heights, X, Y, reference);
            else
            {
                for (int i = 0; i < points.Length; i++)
                {
                    if (!values[i].HasValue) continue;
                    bool before = i > 0 && !points[i].StartsSegment && values[i - 1].HasValue;
                    bool after = i + 1 < points.Length && !points[i + 1].StartsSegment && values[i + 1].HasValue;
                    if (before)
                        dc.DrawLine(trace, Map(i - 1, values[i - 1].GetValueOrDefault()), Map(i, values[i].GetValueOrDefault()));
                    if ((!before && !after) || i == points.Length - 1)
                        dc.DrawEllipse(trace.Brush, null, Map(i, values[i].GetValueOrDefault()), 2.6, 2.6);
                }
            }
            if (view.cursor is double active)
            {
                double x = X(Nearest(points, active).Time);
                dc.DrawLine(cursorPen, new Point(x, plot.Top), new Point(x, plot.Bottom));
            }
            if (reference)
            {
                var axis = new Pen(AxisInk, .8);
                dc.DrawLine(axis, plot.BottomLeft, plot.BottomRight);
                dc.DrawLine(axis, plot.BottomLeft, plot.TopLeft);
            }
            if (donutWidth > 0)
            {
                var point = view.cursor is double time ? Nearest(points, time) : points[^1];
                var shareArea = new Rect(ActualWidth - donutWidth + 12, 4, donutWidth - 22, ActualHeight - 8);
                DrawParticipation(dc, shareArea, point, selected, dpi);
            }
        }

        private static void DrawStack(DrawingContext dc, LiveTrendOperator[] operators,
            LiveTrendPoint[] points, double[]?[] heights, Func<double, double> x, Func<double, double> y, bool reference)
        {
            var cumulative = new double[points.Length];
            var previous = new double[points.Length];
            for (int series = 0; series < operators.Length; series++)
            {
                for (int i = 0; i < heights.Length; i++)
                    if (heights[i] is double[] values) cumulative[i] += values[series];
                var shape = new StreamGeometry();
                using (var geometry = shape.Open())
                {
                    int start = 0;
                    while (start < heights.Length)
                    {
                        if (heights[start] == null) { start++; continue; }
                        int end = start;
                        while (end + 1 < heights.Length && heights[end + 1] != null && !points[end + 1].StartsSegment) end++;
                        if (end > start)
                        {
                            geometry.BeginFigure(new Point(x(points[start].Time), y(cumulative[start])), true, true);
                            for (int i = start + 1; i <= end; i++)
                                geometry.LineTo(new Point(x(points[i].Time), y(cumulative[i])), true, false);
                            for (int i = end; i >= start; i--)
                                geometry.LineTo(new Point(x(points[i].Time), y(previous[i])), true, false);
                        }
                        start = end + 1;
                    }
                }
                shape.Freeze();
                int colorIndex = operators[series].ColorIndex % SeriesBrushes.Length;
                dc.DrawGeometry((reference ? AreaBrushes : SeriesBrushes)[colorIndex], null, shape);
                if (reference)
                {
                    var pen = new Pen(SeriesBrushes[colorIndex], .8);
                    for (int i = 0; i < heights.Length; i++)
                    {
                        if (heights[i] == null) continue;
                        bool before = i > 0 && heights[i - 1] != null && !points[i].StartsSegment;
                        bool after = i + 1 < heights.Length && heights[i + 1] != null && !points[i + 1].StartsSegment;
                        if (before)
                            dc.DrawLine(pen, new Point(x(points[i - 1].Time), y(cumulative[i - 1])),
                                new Point(x(points[i].Time), y(cumulative[i])));
                        if (!before && !after && cumulative[i] > previous[i])
                            dc.DrawLine(new Pen(pen.Brush, 2.5), new Point(x(points[i].Time), y(previous[i])),
                                new Point(x(points[i].Time), y(cumulative[i])));
                    }
                }
                Array.Copy(cumulative, previous, points.Length);
            }
        }

        private static void DrawParticipation(DrawingContext dc, Rect bounds, LiveTrendPoint point,
            LiveTrendOperator[] operators, double dpi)
        {
            double radius = Math.Clamp(Math.Min(bounds.Height * .43, bounds.Width * .27), 25, 47);
            var center = new Point(bounds.Left + radius + 6, bounds.Top + bounds.Height / 2);
            double thickness = Math.Clamp(radius * .30, 9, 15);
            dc.DrawEllipse(null, new Pen(MajorGrid, thickness), center, radius, radius);
            double? team = point.Team;
            var entries = Participation(point, operators);
            double turn = -90;
            foreach (var entry in entries)
            {
                double sweep = entry.Rate / team!.Value * 360;
                DrawArc(dc, new Pen(entry.Brush, thickness), center, radius, turn, sweep);
                turn += sweep;
            }

            FormattedText Label(string value, Typeface font, double size) => new(value, CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, font, size, AxisInk, dpi);
            var valueLabel = Label(team is double rateValue ? Abbreviate(rateValue) : "—", StrongText,
                Math.Clamp(radius * .45, 13, 20));
            var dpsLabel = Label("DPS", StrongText, Math.Clamp(radius * .28, 9, 12));
            double textTop = center.Y - (valueLabel.Height + dpsLabel.Height) / 2;
            dc.DrawText(valueLabel, new Point(center.X - valueLabel.Width / 2, textTop));
            dc.DrawText(dpsLabel, new Point(center.X - dpsLabel.Width / 2, textTop + valueLabel.Height));

            double left = center.X + radius + thickness / 2 + 12;
            double available = bounds.Right - left;
            if (available < 53) return;
            var title = Label("近 5 秒贡献", StrongText, 10);
            dc.DrawText(title, new Point(left, bounds.Top));
            if (entries.Count == 0)
            {
                var empty = Label(team == 0 ? "暂无输出" : "等待完整采样", TextFont, 10);
                dc.DrawText(empty, new Point(left, bounds.Top + 18));
                return;
            }
            double rowHeight = Math.Min(18, (bounds.Height - 15) / entries.Count);
            for (int i = 0; i < entries.Count; i++)
            {
                var entry = entries[i];
                double y = bounds.Top + 14 + rowHeight * i;
                var percent = Label((entry.Rate / team!.Value).ToString("0.0%", CultureInfo.InvariantCulture), NumberFont, 10);
                double nameWidth = available - percent.Width - 16;
                if (nameWidth < 5) break;
                var name = Label(entry.Name, TextFont, 10.5);
                name.MaxTextWidth = nameWidth;
                name.MaxLineCount = 1;
                name.Trimming = TextTrimming.CharacterEllipsis;
                dc.DrawRoundedRectangle(entry.Brush, null, new Rect(left, y + 3, 8, 8), 1, 1);
                dc.DrawText(name, new Point(left + 12, y));
                dc.DrawText(percent, new Point(bounds.Right - percent.Width, y));
            }
        }

        private static void DrawArc(DrawingContext dc, Pen pen, Point center, double radius, double start, double sweep)
        {
            if (sweep <= 0) return;
            if (sweep >= 359.999)
            {
                dc.DrawEllipse(null, pen, center, radius, radius);
                return;
            }
            Point At(double degrees)
            {
                double angle = degrees * Math.PI / 180;
                return new Point(center.X + Math.Cos(angle) * radius, center.Y + Math.Sin(angle) * radius);
            }
            var arc = new StreamGeometry();
            using (var shape = arc.Open())
            {
                shape.BeginFigure(At(start), false, false);
                shape.ArcTo(At(start + sweep), new Size(radius, radius), 0, sweep > 180,
                    SweepDirection.Clockwise, true, false);
            }
            arc.Freeze();
            dc.DrawGeometry(null, pen, arc);
        }
    }
}
