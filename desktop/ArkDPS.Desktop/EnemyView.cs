using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.IO;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace ArkDPS.Desktop;

internal sealed record EnemyPoint(double GameTime, double? TotalHp, bool TotalComplete,
    double? RemainingHp, bool RemainingComplete, double? Damage, bool DamageComplete, bool BreakBefore);

public sealed class EnemyItem : INotifyPropertyChanged
{
    private sealed record Display(string Badge, string Name, string Eid, string Status,
        string HpText, string PhaseHpText, string AttrsText, string Tooltip,
        double HpPercent, bool HpPercentKnown, ImageSource? Portrait);

    private Display display;
    internal string Uid { get; private set; }
    internal string RosterKey { get; private set; }
    public string Badge => display.Badge;
    public string Name => display.Name;
    public string Eid => display.Eid;
    public string Status => display.Status;
    public string HpText => display.HpText;
    public string PhaseHpText => display.PhaseHpText;
    public string AttrsText => display.AttrsText;
    public string Tooltip => display.Tooltip;
    public double HpPercent => display.HpPercent;
    public bool HpPercentKnown => display.HpPercentKnown;
    public double Opacity => 1;
    public ImageSource? Portrait => display.Portrait;
    public event PropertyChangedEventHandler? PropertyChanged;

    internal EnemyItem(JsonElement row, string uid, string rosterKey)
    {
        Uid=uid;
        RosterKey=rosterKey;
        display=Read(row);
    }

    internal void Update(JsonElement row, string uid, string rosterKey)
    {
        if(uid.Length>0) Uid=uid;
        if(rosterKey.Length>0) RosterKey=rosterKey;
        var next=Read(row);
        if(display==next) return;
        display=next;
        PropertyChanged?.Invoke(this,new PropertyChangedEventArgs(null));
    }

    internal static string InstanceUid(JsonElement row)
    {
        var uid=row.Text("instance_uid");
        return uint.TryParse(uid,NumberStyles.None,CultureInfo.InvariantCulture,out uint id)
            && id.ToString(CultureInfo.InvariantCulture)==uid ? uid : "";
    }

    internal static string RosterIdentity(JsonElement row)
    {
        string eid=row.Text("eid");
        return eid.Length>0 && row.Get("roster_id").ValueKind==JsonValueKind.Number
            && row.Get("roster_id").TryGetInt32(out int roster) && roster>=0
            && row.Get("spawn_order").ValueKind==JsonValueKind.Number
            && row.Get("spawn_order").TryGetInt32(out int order) && order>=0
            ? $"{roster}/{order}/{eid}" : "";
    }

    private static string Int(JsonElement value)
    {
        var number = value.TryNumber(double.NaN);
        return double.IsFinite(number)
            ? Math.Round(number, MidpointRounding.AwayFromZero).ToString("0", CultureInfo.InvariantCulture)
            : "—";
    }

    private static Display Read(JsonElement row)
    {
        string name = row.Text("name");
        string eid = row.Text("eid");
        string code = row.Text("code");
        if (name.Length == 0) name = eid.Length > 0 ? eid : "未知敌人";
        string badge = code.Length is > 0 and <= 4 ? code
            : name[..Math.Min(1, name.Length)];
        string lifecycle = row.Text("lifecycle");
        bool departed = lifecycle == "departed";
        string status = lifecycle switch
        {
            "departed" => row.Text("end_reason") switch
            {
                "death" => "已击倒",
                "reach_exit" => "到达终点",
                _ => "已离场",
            },
            "rebirthing" => "等待重生",
            "entered" => "已入场 · 状态未知",
            _ => row.Bool("alive") ? "在场" : "状态未知",
        };
        string hpText=Int(row.Get("hp"));
        double floor=row.Number("max_hp_lower_bound",double.NaN);
        bool hasFloor=double.IsFinite(floor) && floor>0;
        bool maxKnown=row.Bool("hp_known") && row.Number("max_hp",double.NaN)>0;
        string maxHp=hasFloor ? $"≥ {Int(row.Get("max_hp_lower_bound"))}"
            : maxKnown ? Int(row.Get("max_hp")) : "—";
        string health=hpText=="—" && maxHp=="—" ? "HP 未知" : $"{hpText} / {maxHp}";
        double hpRatio=row.Number("hp_ratio",double.NaN);
        bool ratioKnown=!hasFloor && maxKnown && hpText!="—" && hpRatio is >= 0 and <= 1;
        var phaseTotal = row.Get("phase_total_hp");
        string? phaseHp = phaseTotal.ValueKind == JsonValueKind.Number
            ? $"已记录 {Int(row.Get("phase_count"))} 阶段起始 HP 合计 {Int(phaseTotal)}"
            : null;
        if (phaseHp != null)
        {
            if (!row.Bool("phase_from_birth") || !row.Bool("phase_coverage_complete"))
                phaseHp += "（观测不完整）";
        }
        string phaseFloor=Int(row.Get("phase_max_hp_lower_bound"));
        if(phaseFloor!="—")
            phaseHp=(phaseHp==null ? "" : phaseHp+" · ")+"本阶段 HP 容量 ≥ "+phaseFloor;
        double speed = row.Get("move_speed").TryNumber(double.NaN);
        string moveSpeed = double.IsFinite(speed) ? speed.ToString("0.##", CultureInfo.InvariantCulture) : "—";
        bool entryAttributes = lifecycle == "entered" || row.Bool("attributes_from_entry");
        bool anyAttribute = new[] { "atk", "def", "res", "move_speed", "attack_speed" }
            .Any(key => row.Get(key).ValueKind == JsonValueKind.Number);
        string attrs = anyAttribute
            ? (entryAttributes ? "入场属性 · " : "") +
              $"攻击 {Int(row.Get("atk"))}  ·  防御 {Int(row.Get("def"))}  ·  法抗 {Int(row.Get("res"))}" +
              $"  ·  移速 {moveSpeed}  ·  攻速 {Int(row.Get("attack_speed"))}"
            : "属性未知";
        string detail = string.Join("\n", new[]
        {
            name,
            eid.Length > 0 ? eid : null,
            code.Length > 0 ? "图鉴 " + code : null,
            "状态 " + row.Text("state"),
            "当前 HP / 最大 HP " + health,
            phaseHp,
            row.Get("spawn_frame").ValueKind == JsonValueKind.Number ? "入场帧 " + Int(row.Get("spawn_frame")) : null,
            row.Get("end_frame").ValueKind == JsonValueKind.Number ? "离场帧 " + Int(row.Get("end_frame")) : null,
        }.Where(x => !string.IsNullOrEmpty(x)));
        return new Display(badge,name,eid,status,health,phaseHp??"",attrs,detail,
            ratioKnown ? hpRatio*100 : 0,ratioKnown,EnemyPortraits.Get(eid,departed));
    }
}

internal static class EnemyPortraits
{
    private static JsonElement manifest;
    private static BitmapImage? normal, muted;
    private static readonly Dictionary<string, ImageSource> cache = new();
    private static bool loaded;

    public static ImageSource? Get(string id, bool gray)
    {
        if (string.IsNullOrEmpty(id)) return null;
        if (!loaded)
        {
            loaded = true;
            try
            {
                var path = Path.Combine(App.Root, "assets", "enemy_portraits");
                using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(path, "manifest.json")));
                manifest = doc.RootElement.Clone();
                normal = Load(Path.Combine(path, "normal.png"));
                muted = Load(Path.Combine(path, "muted.png"));
            }
            catch (Exception ex) when (ex is IOException or JsonException or NotSupportedException)
            {
                return null;
            }
        }
        var metadata = manifest.Get("portraits").Get(id);
        if (metadata.ValueKind != JsonValueKind.Object || normal == null || muted == null) return null;
        string key = id + gray;
        if (!cache.TryGetValue(key, out var image))
        {
            int index = (int)metadata.Number("index");
            int cell = (int)manifest.Number("cell_size");
            int cols = (int)manifest.Number("columns");
            image = new CroppedBitmap(gray ? muted : normal,
                new Int32Rect(index % cols * cell, index / cols * cell, cell, cell));
            image.Freeze();
            cache[key] = image;
        }
        return image;
    }

    private static BitmapImage Load(string path)
    {
        var image = new BitmapImage();
        image.BeginInit();
        image.CacheOption = BitmapCacheOption.OnLoad;
        image.UriSource = new Uri(path);
        image.EndInit();
        image.Freeze();
        return image;
    }
}

public sealed class EnemyChart : FrameworkElement
{
    private IReadOnlyList<EnemyPoint> points = Array.Empty<EnemyPoint>();
    private double? cursorTime;
    internal IReadOnlyList<EnemyPoint> Points => points;
    internal string DamageLabel { get; set; } = "我方有效伤害";

    public EnemyChart()
    {
        ClipToBounds = true;
        Focusable = true;
        MinHeight = 175;
        AutomationProperties.SetName(this,"敌方血量与我方有效伤害");
        AutomationProperties.SetAutomationId(this,"EnemyHealthChart");
        IsKeyboardFocusedChanged+=(_,_)=>InvalidateVisual();
        UpdateReadout();
    }

    internal void SetData(IReadOnlyList<EnemyPoint> value)
    {
        if(points.SequenceEqual(value)) return;
        points = value;
        if(points.Count==0) cursorTime=null;
        UpdateReadout();
        InvalidateVisual();
    }

    private int CursorIndex() => cursorTime is not double time || points.Count==0
        ? -1 : Enumerable.Range(0,points.Count).MinBy(i=>Math.Abs(points[i].GameTime-time));

    private void UpdateReadout()
    {
        int index=CursorIndex();
        string readout=index<0 ? "按方向键逐点查看。" : Describe(points[index]);
        ToolTip=index<0 ? null : readout;
        AutomationProperties.SetHelpText(this,readout);
    }

    private string Describe(EnemyPoint point) => Time(point.GameTime)
        + "\n已入场总血量 " + Reading(point.TotalHp,point.TotalComplete)
        + "\n敌方剩余 " + Reading(point.RemainingHp,point.RemainingComplete)
        + "\n" + DamageLabel + " " + Reading(point.Damage,point.DamageComplete);

    private static string Reading(double? value,bool complete) =>
        value is double number ? (complete ? "" : "≥ ")+Compact(number) : "—";

    private void SelectPoint(int index)
    {
        double time=points[index].GameTime;
        if(cursorTime==time) return;
        cursorTime=time;
        UpdateReadout();
        InvalidateVisual();
    }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        base.OnKeyDown(e);
        if(points.Count==0 || e.Key is not (Key.Left or Key.Right or Key.Home or Key.End)) return;
        int current=CursorIndex();
        int next=e.Key switch
        {
            Key.Home => 0,
            Key.End => points.Count-1,
            Key.Left => current<0 ? points.Count-1 : Math.Max(0,current-1),
            _ => current<0 ? 0 : Math.Min(points.Count-1,current+1),
        };
        SelectPoint(next);
        e.Handled=true;
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        if(points.Count==0) return;
        double t=Math.Clamp((e.GetPosition(this).X-48)/Math.Max(1,ActualWidth-62),0,1);
        double time=points[0].GameTime+t*(points[^1].GameTime-points[0].GameTime);
        SelectPoint(Enumerable.Range(0,points.Count).MinBy(i=>Math.Abs(points[i].GameTime-time)));
    }

    protected override void OnMouseLeave(MouseEventArgs e)
    {
        base.OnMouseLeave(e);
        if(IsKeyboardFocused) return;
        cursorTime=null;
        UpdateReadout();
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
        double dpi = VisualTreeHelper.GetDpi(this).PixelsPerDip;
        var typeface = new Typeface("Cascadia Mono, Consolas");
        dc.DrawRectangle(Brushes.Transparent,null,new Rect(RenderSize));
        if (points.Count == 0)
        {
            var empty = new FormattedText("等待可靠的血量或伤害样本", CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight, new Typeface("Microsoft YaHei UI"), 12, muted, dpi);
            dc.DrawText(empty, new Point(Math.Max(8, (ActualWidth - empty.Width) / 2),
                                         Math.Max(8, (ActualHeight - empty.Height) / 2)));
            return;
        }
        double left = 48, right = 14, top = 14, bottom = 28;
        double width = Math.Max(1, ActualWidth - left - right);
        double height = Math.Max(1, ActualHeight - top - bottom);
        double maxY = points.Max(p => Math.Max(p.TotalHp??0, Math.Max(p.Damage??0, p.RemainingHp??0)));
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

        void Series(Func<EnemyPoint,double?> value, Func<EnemyPoint,bool> complete,
            Brush brush, double thickness, bool step=false, bool alternate=false)
        {
            var exact=new Pen(brush,thickness)
            {
                LineJoin = PenLineJoin.Round,
                StartLineCap = PenLineCap.Round,
                EndLineCap = PenLineCap.Round,
            };
            if(alternate) exact.DashStyle=DashStyles.Dash;
            var partial=new Pen(brush,thickness){DashStyle=DashStyles.Dot};
            for (int i = 1; i < points.Count; i++)
            {
                var prior=points[i-1];
                var current=points[i];
                double? fromValue=value(prior),toValue=value(current);
                var from=Map(prior.GameTime,fromValue.GetValueOrDefault());
                var to=Map(current.GameTime,toValue.GetValueOrDefault());
                if(current.BreakBefore)
                {
                    ChartLine.DrawGap(dc,partial,from,to,top+height);
                    continue;
                }
                bool hasBothValues=fromValue.HasValue && toValue.HasValue;
                var pen=hasBothValues && complete(prior) && complete(current) ? exact : partial;
                if (step && hasBothValues && from.Y != to.Y)
                {
                    var change = new Point(to.X, from.Y);
                    dc.DrawLine(pen, from, change);
                    from = change;
                }
                dc.DrawLine(pen, from, to);
            }
            var last=points[^1];
            var lastValue=value(last);
            bool finalKnown=lastValue.HasValue && complete(last);
            double radius=thickness+1;
            dc.DrawEllipse(finalKnown ? brush : null,finalKnown ? null : exact,
                Map(last.GameTime,lastValue.GetValueOrDefault()),radius,radius);
        }

        Series(point=>point.TotalHp,point=>point.TotalComplete,accent,2.2,step:true);
        Series(point=>point.Damage,point=>point.DamageComplete,good,2.2);
        Series(point=>point.RemainingHp,point=>point.RemainingComplete,muted,1.4,alternate:true);
        int cursor=CursorIndex();
        if(cursor>=0)
        {
            double x=Map(points[cursor].GameTime,0).X;
            dc.DrawLine(new Pen(Theme.Resource("Ink"),1),new Point(x,top),new Point(x,top+height));
        }
        var frameBrush=IsKeyboardFocused ? accent : line;
        dc.DrawRectangle(null,new Pen(frameBrush,IsKeyboardFocused ? 2 : 1),
            new Rect(left, top, width, height));
    }
}

public partial class MainWindow
{
    private readonly ObservableCollection<EnemyItem> enemyRows=new();
    private readonly List<(double Start,double End)> enemyGaps=new();
    private string enemyBattle="";
    private double? enemyGapStart;
    private JsonElement shownEnemy;

    private void UpdateEnemies(JsonElement enemy,string battleId,string battleStatus)
    {
        if(EnemyList==null) return;
        if(!ReferenceEquals(EnemyList.ItemsSource,enemyRows)) EnemyList.ItemsSource=enemyRows;
        if(enemyBattle!=battleId)
        {
            enemyBattle=battleId;
            enemyRows.Clear();
            enemyGaps.Clear();
            enemyGapStart=null;
            shownEnemy=default;
            EnemyChart.SetData(Array.Empty<EnemyPoint>());
        }

        string status=enemy.Text("status");
        bool blocked=battleStatus is "stale" or "disconnected" or "locating" or "frozen";
        bool ready=battleId.Length>0 && status=="ready" && !blocked;
        // An unavailable enemy sample does not erase the last published rows for this battle.
        if(ready || (shownEnemy.ValueKind==JsonValueKind.Undefined && status=="ready" && battleId.Length>0))
        {
            shownEnemy=enemy;
            EnemyChart.DamageLabel=enemy.Text("damage_scope")=="entered" ? "我方对敌扣血" : "我方有效伤害";
            UpdateEnemyRows(enemy.Get("rows"));
            UpdateEnemyChart(enemy.Get("series"));
        }
        if(!ready && EnemyChart.Points.Count>0)
            enemyGapStart??=EnemyChart.Points[^1].GameTime;

        bool hasReading=shownEnemy.ValueKind==JsonValueKind.Object;
        var totals=shownEnemy.Get("totals");
        bool totalComplete=EnemyComplete(totals,"total_hp_complete");
        bool remainingComplete=EnemyComplete(totals,"remaining_hp_complete");
        var last=shownEnemy.Get("last_point");
        bool scopedDamage=shownEnemy.Text("damage_scope")=="entered";
        bool damageComplete=last.Get("our_damage_complete").ValueKind!=JsonValueKind.False;
        var entryCoverage=shownEnemy.Get("entry_events_complete").ValueKind;
        bool entryComplete=entryCoverage==JsonValueKind.True
            && shownEnemy.Get("enemy_state_complete").ValueKind!=JsonValueKind.False;
        EnemyEntered.Text=EnemyReading(totals,"entered_count",entryComplete);
        EnemyTotalHp.Text=EnemyReading(totals,"entered_total_hp",totalComplete);
        EnemyRemaining.Text=EnemyReading(totals,"remaining_hp",remainingComplete);
        EnemyOurDamageLabel.Text=scopedDamage ? "我方对敌扣血" : "我方有效伤害";
        EnemyOurDamage.Text=last.Bool("our_damage_known")
            ? EnemyReading(last,"our_effective_damage",damageComplete) : "—";

        string enemyStatus=status switch
        {
            "ready" => enemy.Bool("enemy_state_complete") ? "事件更新" : "快照读取",
            "locating" => "正在定位敌人",
            "stale" => "敌情快照不完整",
            "unavailable" => "敌情遥测不可用",
            _ => "等待敌情数据",
        };
        EnemyStatusText.Text=battleStatus switch
        {
            "paused" => "战斗暂停" + (status=="ready" ? "" : " · "+enemyStatus),
            "ended" => "战斗结束" + (status=="ready" ? "" : " · "+enemyStatus),
            "finalizing" => "等待战斗结算",
            "disconnected" => "采集连接中断",
            "frozen" => "战局冻结",
            "stale" => "战斗数据已过期",
            _ => enemyStatus,
        };

        var hints=new List<string>();
        string error=enemy.Text("error");
        if(error.Length>0) hints.Add(error);
        if(!ready && hasReading) hints.Add("显示本局最后一次可用的敌情。");
        string entryHint = entryCoverage switch
        {
            JsonValueKind.True => "已入场人数合并入场事件与快照。",
            JsonValueKind.False => "入场事件覆盖不完整，已入场人数为已确认下限。",
            _ => "已入场人数来自已观测的敌人快照。",
        };
        if(hasReading)
        {
            hints.Add(entryHint+"已入场总血量含已记录的重生阶段。");
            if(!totalComplete || !remainingComplete || (last.Bool("our_damage_known") && !damageComplete))
                hints.Add("≥ 表示已知下限，曲线用点线标记。");
            if(totals.Number("planned_count")>0
                || (totals.Get("planned_count").ValueKind==JsonValueKind.Undefined
                    && totals.Get("stage_total_hp_estimate").ValueKind==JsonValueKind.Number))
                hints.Add("关卡预估血量（出怪计划） "+EnemyReading(totals,"stage_total_hp_estimate",
                    totals.Bool("stage_total_hp_complete"))+"。");
            if(totals.Get("departed_hp").TryNumber()>0)
                hints.Add("离场时尚余 "+totals.UiInt("departed_hp")+" HP（"
                    +totals.UiInt("departed_hp_count")+" 个实例）。");
            if(totals.Get("recovered_hp").TryNumber()>0)
            {
                hints.Add("已观测敌方回血 "+totals.UiInt("recovered_hp")+" HP。");
                hints.Add("含回血累计可承受量 "+EnemyReading(totals,"damage_capacity_with_recovery",false)+" HP。");
            }
            if(shownEnemy.Get("non_team_native_damage").TryNumber()>0)
                hints.Add("其他来源原生伤害 "+shownEnemy.UiInt("non_team_native_damage")+"。");
            if(scopedDamage)
                hints.Add("我方对敌扣血按已入场实例统计，从事件采集起点累计。");
            else if(shownEnemy.Get("damage_baseline").ValueKind==JsonValueKind.Number)
                hints.Add("我方有效伤害从敌情观测起点累计（起点 "+shownEnemy.UiInt("damage_baseline")+"）。");
        }
        EnemyStatusHint.Text=string.Join("\n",hints);
        EnemyEmpty.Visibility=enemyRows.Count==0 ? Visibility.Visible : Visibility.Collapsed;
        EnemyEmpty.Text=status switch
        {
            "ready" when ready => "当前未确认有敌人入场。",
            "locating" => "正在定位敌人。",
            "unavailable" => "敌人遥测不可用；干员统计不受影响。",
            "stale" => "没有可显示的可靠敌情。",
            _ => "等待可靠的敌情数据。",
        };
        EnemyChartLegendTotal.Text = "已入场总血量  " + EnemyTotalHp.Text;
        EnemyChartLegendDamage.Text = EnemyOurDamageLabel.Text + "  " + EnemyOurDamage.Text;
        EnemyChartLegendRemaining.Text = "敌方剩余  " + EnemyRemaining.Text;
    }

    private void UpdateEnemyRows(JsonElement data)
    {
        var next=data.Items()
            .Where(row=>row.ValueKind==JsonValueKind.Object && row.Text("lifecycle")!="pending")
            .Select(row=>(Row:row,Uid:EnemyItem.InstanceUid(row),Roster:EnemyItem.RosterIdentity(row)))
            .ToArray();
        var byUid=enemyRows.ToLookup(item=>item.Uid);
        var byRoster=enemyRows.ToLookup(item=>item.RosterKey);
        var uidCounts=next.ToLookup(row=>row.Uid);
        var rosterCounts=next.ToLookup(row=>row.Roster);
        var retained=new HashSet<EnemyItem>();
        foreach(var row in next)
        {
            EnemyItem? item=null;
            if(row.Uid.Length>0 && uidCounts[row.Uid].Count()==1 && byUid[row.Uid].Count()==1)
                item=byUid[row.Uid].First();
            if(item==null && row.Roster.Length>0 && rosterCounts[row.Roster].Count()==1
                && byRoster[row.Roster].Count()==1)
            {
                var rosterItem=byRoster[row.Roster].First();
                if(row.Uid.Length==0 || rosterItem.Uid.Length==0 || row.Uid==rosterItem.Uid)
                    item=rosterItem;
            }
            if(item!=null && retained.Add(item))
                item.Update(row.Row,row.Uid,row.Roster);
            else
            {
                item=new EnemyItem(row.Row,row.Uid,row.Roster);
                enemyRows.Add(item);
                retained.Add(item);
            }
        }
        for(int index=enemyRows.Count-1; index>=0; index--)
            if(!retained.Contains(enemyRows[index])) enemyRows.RemoveAt(index);
    }

    private void UpdateEnemyChart(JsonElement series)
    {
        var samples=series.Items().Where(point=>EnemyNumber(point,"game_time").HasValue).ToArray();
        if(samples.Length==0) return;
        double lastTime=samples[^1].Number("game_time");
        if(enemyGapStart is double start && lastTime>start)
        {
            enemyGaps.Add((start,lastTime));
            enemyGapStart=null;
        }
        enemyGaps.RemoveAll(gap=>gap.End<samples[0].Number("game_time"));
        var next=new List<EnemyPoint>(samples.Length);
        foreach(var sample in samples)
        {
            double time=sample.Number("game_time");
            if(enemyGaps.Any(gap=>time>gap.Start && time<gap.End)) continue;
            bool totalComplete=EnemyComplete(sample,"total_hp_complete");
            bool remainingComplete=EnemyComplete(sample,"remaining_hp_complete");
            bool damageComplete=sample.Get("our_damage_complete").ValueKind!=JsonValueKind.False;
            bool breaks=next.Count>0 && enemyGaps.Any(gap=>next[^1].GameTime<=gap.Start && time>=gap.End);
            next.Add(new EnemyPoint(time,EnemyNumber(sample,"entered_total_hp",totalComplete),totalComplete,
                EnemyNumber(sample,"remaining_hp",remainingComplete),remainingComplete,
                sample.Bool("our_damage_known") ? EnemyNumber(sample,"our_effective_damage",damageComplete) : null,
                damageComplete,breaks));
        }
        EnemyChart.SetData(next);
    }

    private static bool EnemyComplete(JsonElement data,string key) =>
        data.Get(key).ValueKind==JsonValueKind.Undefined ? data.Bool("hp_complete") : data.Bool(key);

    private static double? EnemyNumber(JsonElement data,string key,bool complete=true)
    {
        double value=data.Number(key,double.NaN);
        // Zero is a reading only when coverage certifies it; an empty lower bound has no value.
        return double.IsFinite(value) && value>=0 && (value>0 || complete) ? value : null;
    }

    private static string EnemyReading(JsonElement data,string key,bool complete) =>
        EnemyNumber(data,key,complete).HasValue ? (complete ? "" : "≥ ")+data.UiInt(key) : "—";
}
