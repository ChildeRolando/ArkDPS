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

public enum LiveTrendKind { Compact, Operator }

internal static class ChartLine
{
    internal static void DrawGap(DrawingContext dc, Pen pen, Point from, Point to, double baseline)
    {
        var start = new Point(from.X, baseline);
        var end = new Point(to.X, baseline);
        if (from != start) dc.DrawLine(pen, from, start);
        dc.DrawLine(pen, start, end);
        if (end != to) dc.DrawLine(pen, end, to);
    }
}

internal sealed record LiveTrendOperator(string Id, string Name, int ColorIndex);

internal sealed record LiveTrendPoint(double Time, long Segment, bool StartsSegment,
    double? Team, IReadOnlyDictionary<string, double?> Operators);

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
                last.Team == team && last.Operators.Count == rates.Count &&
                rates.All(pair => last.Operators.TryGetValue(pair.Key, out var value) && value == pair.Value)) return;
        }

        var point = new LiveTrendPoint(time, segment, starts, team, rates);
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
    private static readonly Brush HeaderAccent = ColorBrush("#F48729");
    private static readonly Brush Subdivision = ColorBrush("#263039");
    private static readonly Brush MajorGrid = ColorBrush("#37424D");
    private static readonly Brush AxisInk = ColorBrush("#CBD7E3");
    private static readonly Brush OtherBrush = ColorBrush("#8797A7");

    private readonly ChartSurface chart;
    private readonly TextBlock heading = new();
    private readonly TextBlock readout = new();
    private readonly Border marker = new() { Width = 8, Height = 12, Margin = new Thickness(8, 0, 8, 0),
        VerticalAlignment = VerticalAlignment.Center, Background = HeaderAccent, LayoutTransform = new SkewTransform(-21, 0) };
    private readonly HashSet<string> selected = new(StringComparer.Ordinal);
    private LiveTrendModel? model;
    private LiveTrendKind kind;
    private string? owner;
    private string displayedBattle = "";
    private double? cursor;
    private bool IsReferenceCompact => Theme.Key == "lone-trail" && Kind == LiveTrendKind.Compact;

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

    internal void FitOperatorChart(double availableHeight)
    {
        if (Kind != LiveTrendKind.Operator || Visibility != Visibility.Visible) return;
        double chrome = Math.Max(0, ActualHeight - chart.ActualHeight);
        double height = Math.Max(108, availableHeight - chrome);
        if (Math.Abs(chart.Height - height) >= 1) chart.Height = height;
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
        DockPanel.SetDock(marker, Dock.Left);
        header.Children.Add(marker);
        header.Children.Add(heading);
        var body = new StackPanel();
        body.Children.Add(header);
        body.Children.Add(chart);
        body.Children.Add(readout);
        Child = body;
        ApplyAppearance();
        if (Kind == LiveTrendKind.Operator && owner == null) Visibility = Visibility.Collapsed;
        Refresh();
    }

    private void ApplyAppearance()
    {
        bool reference = IsReferenceCompact;
        heading.Text = Kind == LiveTrendKind.Operator ? "干员近 5 秒 DPS 趋势"
            : reference ? "实时 DPS 趋势 / LIVE OUTPUT (STACKED)" : "近 60 游戏秒 · 全队 DPS";
        AutomationProperties.SetName(chart, heading.Text);
        chart.Height = Kind == LiveTrendKind.Operator ? 108 : reference ? 138 : 52;
        marker.Visibility = reference ? Visibility.Visible : Visibility.Collapsed;
        heading.FontSize = reference ? 13 : 14;
        heading.VerticalAlignment = VerticalAlignment.Center;
        if (reference) Background = ChartWell;
        else SetResourceReference(BackgroundProperty, "ConsoleCard");
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
        selected.Clear();
    }

    private void Refresh()
    {
        if (IsReferenceCompact) SelectDefaultOperators();
        UpdateReadout();
        chart.InvalidateVisual();
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
        return point.Team;
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
        readout.Visibility = !IsReferenceCompact || points.Length == 0 || cursor.HasValue || stateNote.Length > 0
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
        readout.Text = TimeLabel(point.Time) + " · " + Json.CompactAmount(Value(point)) + " DPS" + stateNote;
        if (IsReferenceCompact && cursor.HasValue && point.Team is > 0)
        {
            var shares = Participation(point, SelectedOperators());
            if (shares.Count > 0) readout.Text += " · 近 5 秒贡献 " + string.Join(" · ", shares.Select(entry =>
                entry.Name + " " + Number(100 * entry.Rate / point.Team.Value) + "%"));
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

    private static string Number(double? number) => number is double value
        ? Math.Round(value, MidpointRounding.AwayFromZero).ToString("N0", CultureInfo.CurrentCulture) : "—";

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
            bool reference = view.IsReferenceCompact;
            if (reference) dc.DrawRectangle(ChartWell, null, new Rect(RenderSize));
            if (IsKeyboardFocused)
                dc.DrawRectangle(null, new Pen(signal, 1), new Rect(.5, .5, ActualWidth - 1, ActualHeight - 1));
            if (points.Length == 0) return;
            bool bareCompact = view.Kind == LiveTrendKind.Compact && !reference;
            var selected = view.SelectedOperators().ToArray();
            bool stack = reference && selected.Length > 0;
            var layers = selected.Reverse().ToArray();
            var heights = stack
                ? points.Select(point => layers.Select(op => point.Team.HasValue
                    ? point.Operators.GetValueOrDefault(op.Id) : null).ToArray()).ToArray()
                : Array.Empty<double?[]>();
            var values = points.Select(view.Value).ToArray();
            double ceiling = Math.Max(4, stack
                ? heights.Max(rates => rates.Sum(rate => rate.GetValueOrDefault())) : values.Max() ?? 0);
            int ticks = bareCompact ? 2 : 4;
            if (reference) (ceiling, ticks) = ReferenceRange(Math.Max(5, ceiling));
            minTime = points[0].Time;
            maxTime = Math.Max(minTime + 1, points[^1].Time);
            double donutWidth = reference && ActualWidth >= 690 ? Math.Min(360, ActualWidth * .29) : 0;
            var muted = reference ? AxisInk : Theme.Resource("ConsoleMuted");
            double dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
            var numberFont=Theme.Typeface("DataFont",FontWeights.Normal);
            FormattedText Label(string label) => new(label, CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, numberFont, 10, muted, dpi);
            double left = bareCompact ? 3 : Math.Min(ActualWidth * .35, Math.Max(49, Label(Json.CompactAmount(ceiling)).Width + 10));
            double top = reference ? 17 : 7;
            plot = new Rect(left, top, Math.Max(1, ActualWidth - left - 13 - donutWidth),
                Math.Max(1, ActualHeight - top - (bareCompact ? 5 : 20)));
            double X(double time) => plot.Left + (time - minTime) / (maxTime - minTime) * plot.Width;
            double Y(double value) => plot.Bottom - value / ceiling * plot.Height;
            Point Map(int index, double value) => new(X(points[index].Time), Y(value));

            var grid = Theme.Resource("ConsoleLine").Clone();
            grid.Opacity = .45;
            var gridPen = new Pen(reference ? MajorGrid : grid, 1);
            var trace = new Pen(signal, 2);
            var missingTrace = new Pen(signal, 1.3) { DashStyle = DashStyles.Dot };
            var cursorPen = new Pen(Theme.Resource("ConsoleInk"), 1) { DashStyle = DashStyles.Dash };
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
                var units = Label("DPS");
                dc.DrawText(units, new Point(plot.Left - units.Width - 7, 1));
            }
            for (int i = 0; i <= ticks; i++)
            {
                double y = plot.Top + plot.Height * i / ticks;
                dc.DrawLine(gridPen, new Point(plot.Left, y), new Point(plot.Right, y));
                if (bareCompact) continue;
                double axisValue = ceiling * (ticks - i) / ticks;
                var label = Label(Json.CompactAmount(axisValue));
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

            if (reference)
            {
                var axis = new Pen(AxisInk, .8);
                dc.DrawLine(axis, plot.BottomLeft, plot.BottomRight);
                dc.DrawLine(axis, plot.BottomLeft, plot.TopLeft);
            }
            if (stack) DrawStack(dc, layers, points, heights, X, Y);
            else
            {
                for (int i = 1; i < points.Length; i++)
                {
                    var from = Map(i - 1, values[i - 1].GetValueOrDefault());
                    var to = Map(i, values[i].GetValueOrDefault());
                    if (points[i].StartsSegment) ChartLine.DrawGap(dc, missingTrace, from, to, plot.Bottom);
                    else dc.DrawLine(values[i - 1].HasValue && values[i].HasValue ? trace : missingTrace, from, to);
                }
                dc.DrawEllipse(values[^1].HasValue ? signal : null, values[^1].HasValue ? null : missingTrace,
                    Map(points.Length - 1, values[^1].GetValueOrDefault()), 2.6, 2.6);
            }
            if (view.cursor is double active)
            {
                double x = X(Nearest(points, active).Time);
                dc.DrawLine(cursorPen, new Point(x, plot.Top), new Point(x, plot.Bottom));
            }
            if (donutWidth > 0)
            {
                var point = view.cursor is double time ? Nearest(points, time) : points[^1];
                var shareArea = new Rect(ActualWidth - donutWidth + 12, 4, donutWidth - 22, ActualHeight - 8);
                DrawParticipation(dc, shareArea, point, selected, dpi);
            }
        }

        private static void DrawStack(DrawingContext dc, LiveTrendOperator[] operators,
            LiveTrendPoint[] points, double?[][] heights, Func<double, double> x, Func<double, double> y)
        {
            var cumulative = new double[points.Length];
            var previous = new double[points.Length];
            var complete = Enumerable.Repeat(true, points.Length).ToArray();
            for (int series = 0; series < operators.Length; series++)
            {
                for (int i = 0; i < heights.Length; i++)
                {
                    cumulative[i] += heights[i][series].GetValueOrDefault();
                    complete[i] &= heights[i][series].HasValue;
                }
                var shape = new StreamGeometry();
                using (var geometry = shape.Open())
                {
                    int start = 0;
                    while (start < heights.Length)
                    {
                        int end = start;
                        while (end + 1 < heights.Length && !points[end + 1].StartsSegment) end++;
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
                dc.DrawGeometry(AreaBrushes[colorIndex], null, shape);
                var pen = new Pen(SeriesBrushes[colorIndex], .8);
                var missing = new Pen(pen.Brush, 1.2) { DashStyle = DashStyles.Dot };
                for (int i = 1; i < heights.Length; i++)
                {
                    var from = new Point(x(points[i - 1].Time), y(cumulative[i - 1]));
                    var to = new Point(x(points[i].Time), y(cumulative[i]));
                    if (points[i].StartsSegment) ChartLine.DrawGap(dc, missing, from, to, y(0));
                    else dc.DrawLine(complete[i - 1] && complete[i] ? pen : missing, from, to);
                }
                if (heights.Length == 1)
                    dc.DrawLine(pen, new Point(x(points[0].Time), y(previous[0])),
                        new Point(x(points[0].Time), y(cumulative[0])));
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

            var numberFont=Theme.Typeface("DataFont",FontWeights.Normal);
            var textFont=Theme.Typeface("InterfaceFont",FontWeights.Normal);
            var strongText=Theme.Typeface("HeadingFont",FontWeights.SemiBold);
            FormattedText Label(string value, Typeface font, double size) => new(value, CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, font, size, AxisInk, dpi);
            double valueSize = Math.Clamp(radius * .45, 13, 20);
            string rounded = Json.CompactAmount(team);
            var valueLabel = Label(rounded, strongText, valueSize);
            double valueWidth = radius * 2 - thickness - 6;
            if (valueLabel.Width > valueWidth) valueLabel = Label(rounded, strongText, valueSize * valueWidth / valueLabel.Width);
            var dpsLabel = Label("DPS", strongText, Math.Clamp(radius * .28, 9, 12));
            double textTop = center.Y - (valueLabel.Height + dpsLabel.Height) / 2;
            dc.DrawText(valueLabel, new Point(center.X - valueLabel.Width / 2, textTop));
            dc.DrawText(dpsLabel, new Point(center.X - dpsLabel.Width / 2, textTop + valueLabel.Height));

            double left = center.X + radius + thickness / 2 + 12;
            double available = bounds.Right - left;
            if (available < 53) return;
            var title = Label("近 5 秒贡献", strongText, 10);
            dc.DrawText(title, new Point(left, bounds.Top));
            if (entries.Count == 0)
            {
                var empty = Label(team == 0 ? "暂无输出" : "等待完整采样", textFont, 10);
                dc.DrawText(empty, new Point(left, bounds.Top + 18));
                return;
            }
            double rowHeight = Math.Min(18, (bounds.Height - 15) / entries.Count);
            for (int i = 0; i < entries.Count; i++)
            {
                var entry = entries[i];
                double y = bounds.Top + 14 + rowHeight * i;
                var percent = Label(Number(100 * entry.Rate / team!.Value) + "%", numberFont, 10);
                double nameWidth = available - percent.Width - 16;
                if (nameWidth < 5) break;
                var name = Label(entry.Name, textFont, 10.5);
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
