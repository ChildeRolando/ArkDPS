using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;

namespace ArkDPS.Desktop;

public sealed class EnemyPoint
{
    public double GameTime { get; init; }
    public double TotalHp { get; init; }
    public double RemainingHp { get; init; }
    public double Damage { get; init; }
}

public sealed class EnemyItem
{
    public string Badge { get; init; } = "?";
    public string Name { get; init; } = "";
    public string Code { get; init; } = "";
    public string Eid { get; init; } = "";
    public string State { get; init; } = "";
    public string Status { get; init; } = "";
    public string HpText { get; init; } = "";
    public string AttrsText { get; init; } = "";
    public string Tooltip { get; init; } = "";
    public double HpPercent { get; init; }
    public double Opacity { get; init; } = 1;

    private static string Int(JsonElement value)
    {
        var number = value.TryNumber();
        return double.IsFinite(number)
            ? Math.Round(number, MidpointRounding.AwayFromZero).ToString("0", CultureInfo.InvariantCulture)
            : "—";
    }

    public static EnemyItem From(JsonElement row)
    {
        string name = row.Text("name");
        string eid = row.Text("eid");
        string code = row.Text("code");
        if (name.Length == 0) name = eid.Length > 0 ? eid : "未知敌人";
        string badge = code.Length is > 0 and <= 4 ? code
            : name[..Math.Min(1, name.Length)];
        bool alive = row.Bool("alive");
        string lifecycle = row.Text("lifecycle");
        string status = lifecycle == "departed" ? "已离场" : alive ? "存活" : "状态未知";
        bool hpKnown = row.Bool("hp_known");
        string hpText = hpKnown
            ? $"{Int(row.Get("hp"))} / {Int(row.Get("max_hp"))}"
            : "HP 未知";
        string attrs = $"攻击 {Int(row.Get("atk"))}  ·  防御 {Int(row.Get("def"))}  ·  法抗 {Int(row.Get("res"))}" +
                       $"  ·  移速 {row.Get("move_speed").TryNumber():0.##}  ·  攻速 {Int(row.Get("attack_speed"))}";
        string detail = string.Join("\n", new[]
        {
            name,
            eid.Length > 0 ? eid : null,
            code.Length > 0 ? "图鉴 " + code : null,
            "状态 " + row.Text("state"),
            row.Get("spawn_frame").ValueKind == JsonValueKind.Number ? "入场帧 " + Int(row.Get("spawn_frame")) : null,
            row.Get("end_frame").ValueKind == JsonValueKind.Number ? "离场帧 " + Int(row.Get("end_frame")) : null,
        }.Where(x => !string.IsNullOrEmpty(x)));
        return new EnemyItem
        {
            Badge = badge, Name = name, Code = code, Eid = eid,
            State = row.Text("state"), Status = status,
            HpText = hpText, AttrsText = attrs, Tooltip = detail,
            HpPercent = hpKnown ? Math.Clamp(row.Get("hp_ratio").TryNumber() * 100, 0, 100) : 0,
            Opacity = alive ? 1 : .56,
        };
    }
}

public sealed class EnemyChart : FrameworkElement
{
    private IReadOnlyList<EnemyPoint> points = Array.Empty<EnemyPoint>();

    public EnemyChart()
    {
        ClipToBounds = true;
        MinHeight = 220;
    }

    public void SetData(IReadOnlyList<EnemyPoint> value)
    {
        points = value ?? Array.Empty<EnemyPoint>();
        InvalidateVisual();
    }

    private static string Compact(double value)
    {
        if (!double.IsFinite(value)) return "—";
        if (Math.Abs(value) >= 10000) return (value / 10000).ToString("0.#", CultureInfo.InvariantCulture) + "万";
        return Math.Round(value, MidpointRounding.AwayFromZero).ToString("0", CultureInfo.InvariantCulture);
    }

    private static string Time(double seconds)
    {
        int total = (int)Math.Max(0, seconds);
        return $"{total / 60:00}:{total % 60:00}";
    }

    protected override void OnRender(DrawingContext dc)
    {
        base.OnRender(dc);
        var accent = Theme.Resource("Accent");
        var good = Theme.Resource("Good");
        var muted = Theme.Resource("Muted");
        var line = Theme.Resource("Line");
        var ink = Theme.Resource("Ink");
        double dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
        var typeface = new Typeface("Cascadia Mono, Consolas");
        if (points.Count == 0)
        {
            var empty = new FormattedText("等待敌方血量与我方伤害样本", CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, typeface, 12, muted, dpi);
            dc.DrawText(empty, new Point(Math.Max(8, (ActualWidth - empty.Width) / 2),
                                         Math.Max(8, (ActualHeight - empty.Height) / 2)));
            return;
        }
        double left = 48, right = 14, top = 14, bottom = 28;
        double width = Math.Max(1, ActualWidth - left - right);
        double height = Math.Max(1, ActualHeight - top - bottom);
        double maxY = points.Max(p => Math.Max(p.TotalHp, Math.Max(p.Damage, p.RemainingHp)));
        if (maxY <= 0) maxY = 1;
        double minT = points[0].GameTime;
        double maxT = points[^1].GameTime;
        if (maxT <= minT) maxT = minT + 1;
        Point Map(double t, double y) => new(left + (t - minT) / (maxT - minT) * width,
                                             top + height - Math.Clamp(y / maxY, 0, 1) * height);

        var gridBrush = line.Clone();
        gridBrush.Opacity = .55;
        var gridPen = new Pen(gridBrush, 1);
        for (int i = 0; i <= 4; i++)
        {
            double y = top + height * i / 4;
            dc.DrawLine(gridPen, new Point(left, y), new Point(left + width, y));
            var label = new FormattedText(Compact(maxY * (4 - i) / 4), CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, typeface, 10, muted, dpi);
            dc.DrawText(label, new Point(left - label.Width - 8, y - label.Height / 2));
        }
        for (int i = 0; i <= 4; i++)
        {
            double t = minT + (maxT - minT) * i / 4;
            var label = new FormattedText(Time(t), CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, typeface, 10, muted, dpi);
            dc.DrawText(label, new Point(left + width * i / 4 - label.Width / 2, top + height + 8));
        }

        void Series(Func<EnemyPoint, double> value, Brush brush, double thickness, bool dashed = false)
        {
            var pen = new Pen(brush, thickness)
            {
                LineJoin = PenLineJoin.Round,
                StartLineCap = PenLineCap.Round,
                EndLineCap = PenLineCap.Round,
            };
            if (dashed) pen.DashStyle = new DashStyle(new double[] { 4, 4 }, 0);
            for (int i = 1; i < points.Count; i++)
                dc.DrawLine(pen, Map(points[i - 1].GameTime, value(points[i - 1])),
                            Map(points[i].GameTime, value(points[i])));
            var last = Map(points[^1].GameTime, value(points[^1]));
            dc.DrawEllipse(brush, null, last, thickness + 1.5, thickness + 1.5);
        }

        Series(p => p.TotalHp, accent, 2.2);
        Series(p => p.Damage, good, 2.2);
        Series(p => p.RemainingHp, muted, 1.4, true);
        var frameBrush = line.Clone();
        frameBrush.Opacity = .7;
        dc.DrawRectangle(null, new Pen(frameBrush, 1),
            new Rect(left, top, width, height));
    }
}

public partial class MainWindow
{
    private void UpdateEnemies(JsonElement enemy)
    {
        if (EnemyList == null) return;
        if (enemy.ValueKind != JsonValueKind.Object) enemy = default;
        var status = enemy.Text("status");
        EnemyStatusText.Text = status switch
        {
            "ready" => "实时读取",
            "locating" => "正在定位敌人",
            "stale" => "本帧读取不完整",
            "unavailable" => "敌人遥测不可用",
            _ => "等待战斗开始",
        };
        var rows = enemy.Get("rows").Items().Select(EnemyItem.From).ToArray();
        EnemyList.ItemsSource = rows;
        var totals = enemy.Get("totals");
        bool complete = totals.Get("hp_complete").ValueKind == JsonValueKind.True;
        string entered = Json.UiInt(totals, "entered_count");
        string totalHp = Json.UiInt(totals, "entered_total_hp");
        string remaining = Json.UiInt(totals, "remaining_hp");
        EnemyEntered.Text = entered;
        EnemyTotalHp.Text = complete ? totalHp : "≥ " + totalHp;
        EnemyRemaining.Text = complete ? remaining : "≥ " + remaining;
        var last = enemy.Get("last_point");
        EnemyOurDamage.Text = last.ValueKind == JsonValueKind.Object
            ? Json.UiInt(last, "our_damage") : "—";
        EnemyChart.SetData(enemy.Get("series").Items().Select(p => new EnemyPoint
        {
            GameTime = p.Get("game_time").TryNumber(),
            TotalHp = p.Get("entered_total_hp").TryNumber(),
            RemainingHp = p.Get("remaining_hp").TryNumber(),
            Damage = p.Get("our_damage").TryNumber(),
        }).ToArray());
        var error = enemy.Text("error");
        EnemyStatusHint.Text = error.Length > 0 ? error
            : complete ? "仅统计已入场敌人；总血量按每个已入场实例的 MaxHP 累加。"
            : "部分已入场敌人的 MaxHP 尚未读到，总血量以 ≥ 表示下限。";
        EnemyEmpty.Visibility = rows.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
        EnemyEmpty.Text = status switch
        {
            "locating" => "正在定位敌人链…",
            "unavailable" => "敌人遥测不可用；干员统计不受影响。",
            "stale" => "本帧敌人数据不完整，保留上一帧。",
            _ => "场上暂无已入场敌人。",
        };
        EnemyChartLegendTotal.Text = "敌方总血量  " + (complete ? totalHp : "≥ " + totalHp);
        EnemyChartLegendDamage.Text = "我方累计伤害  " + EnemyOurDamage.Text;
        EnemyChartLegendRemaining.Text = "敌方剩余  " + (complete ? remaining : "≥ " + remaining);
    }
}
