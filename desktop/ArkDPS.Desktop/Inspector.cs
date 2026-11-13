using System;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;

namespace ArkDPS.Desktop;

public sealed class Inspector : Border
{
    private readonly StackPanel body=new();
    private string? owner;
    private bool compactReference;
    private JsonElement lastView;
    private ReferenceReport? referenceReport;
    private (string Owner, string Theme, string Row, string Entry, string Detail,
        string Rates, int Rank, bool Effective, string Metric, string Ranking,
        string RateBasis, string RateReason, string RateHint, string ShareDescription)? displayed;
    internal string? Owner => owner;
    public bool CompactReference
    {
        get => compactReference;
        set
        {
            if(compactReference==value && (!value || referenceReport?.Skin==Theme.Key)) return;
            compactReference=value;
            displayed=null;
            Empty();
            if(owner!=null) Update(lastView);
        }
    }
    public Inspector()
    {
        SetResourceReference(BackgroundProperty,"Card");
        SetResourceReference(BorderBrushProperty,"Line");
        SetResourceReference(CornerRadiusProperty,"CardRadius");
        BorderThickness=new Thickness(1); Padding=new Thickness(14);
        AutomationProperties.SetName(this,"干员详情");
        Child=body; Empty();
    }
    internal void Empty()
    {
        displayed=null;
        if(compactReference)
        {
            ShowReference().Reset();
            return;
        }
        UseFullReport();
        body.Children.Clear();
        body.Children.Add(Text("干员详情",18,true,"Ink",new Thickness(0,0,0,7)));
        body.Children.Add(Text("从排行选择干员，查看伤害构成、技能覆盖和接敌速率。",12,false,"Muted"));
    }
    internal void Pin(string id,JsonElement view) {owner=id;Update(view);}
    internal void Reset() {owner=null;lastView=default;Empty();}
    internal void Update(JsonElement view)
    {
        lastView=view;
        if(owner==null) return;
        var row=view.Get("source").Get(owner);
        if(row.ValueKind!=JsonValueKind.Object)
        {
            if(displayed.HasValue && displayed.Value.Owner!=owner) Empty();
            return;
        }
        var entries=view.Get("entries").Items().ToArray();
        int index=Array.FindIndex(entries,x=>x.Text("owner_id")==owner);
        var entry=index<0 ? default : entries[index];
        var config=view.Get("config");
        bool effective=config.Text("category")=="damage" && config.Bool("effective_only");
        var detail=view.Get("details_by_owner").Get(owner);
        if(detail.ValueKind!=JsonValueKind.Object) detail=entry.Get("detail");
        var rates=view.Get("rates").Get(owner);
        var shareInfo=SharePresentation.From(view);
        string metric=view.Text("metric_label");
        string ranking=config.Text("ranking");
        string rateReason=view.Text("rate_reason"), rateHint=view.Text("rate_hint");
        var signature=(owner,Theme.Key,Raw(row),Raw(entry),Raw(detail),Raw(rates),
            index,effective,metric,ranking,config.Text("rate_basis"),rateReason,rateHint,shareInfo.Description);
        if(displayed==signature) return;
        displayed=signature;

        if(compactReference)
        {
            ShowReference().Update(owner,row,view,entry,detail,effective,ranking,shareInfo);
            return;
        }
        UseFullReport();

        string rank=index<0 ? "—" : (index+1).ToString();
        string share=Percentage(entry.Number("share",double.NaN));
        string selected=ranking=="dps" ? EntryRate(entry) : Number(entry.Get("value"));
        body.Children.Clear();

        var top=new Grid{Margin=new Thickness(0,0,0,10)};
        top.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});
        top.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(1,GridUnitType.Star)});
        top.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});
        var rankBox=new Border{Background=Theme.Resource("Inset"),BorderBrush=Theme.Resource("Accent"),BorderThickness=new Thickness(1),
            CornerRadius=new CornerRadius(2),Padding=new Thickness(6,4,6,4),Margin=new Thickness(0,0,8,0),VerticalAlignment=VerticalAlignment.Center};
        var rankStack=new StackPanel();rankStack.Children.Add(Text("排名",10,true,"Muted"));rankStack.Children.Add(Mono(rank,16,true,"Accent"));
        rankBox.Child=rankStack;top.Children.Add(rankBox);

        var identity=new StackPanel{Orientation=Orientation.Horizontal,VerticalAlignment=VerticalAlignment.Center};
        var portrait=Portraits.Get(owner,row.Get("present").ValueKind==JsonValueKind.False);
        var frame=new Border{Width=42,Height=42,CornerRadius=new CornerRadius(2),ClipToBounds=true,
            BorderBrush=Theme.Resource("Line"),BorderThickness=new Thickness(1),Background=Theme.Resource("Inset"),Margin=new Thickness(0,0,8,0)};
        var frameGrid=new Grid();
        var initial=Text(row.Text("name").Length>0?row.Text("name")[..1]:"?",16,true,"Accent");initial.HorizontalAlignment=HorizontalAlignment.Center;initial.VerticalAlignment=VerticalAlignment.Center;frameGrid.Children.Add(initial);
        if(portrait!=null) frameGrid.Children.Add(new Image{Source=portrait,Stretch=Stretch.UniformToFill,Opacity=row.Get("present").ValueKind==JsonValueKind.False?.65:1});
        frame.Child=frameGrid;identity.Children.Add(frame);
        var copy=new StackPanel{VerticalAlignment=VerticalAlignment.Center};
        var present=row.Get("present").ValueKind;
        copy.Children.Add(Text(present switch{JsonValueKind.True=>"在场",JsonValueKind.False=>"已离场",_=>"在场状态未知"},
            10,false,present==JsonValueKind.True ? "Good" : "Muted"));
        copy.Children.Add(Text(row.Text("name"),18,true,"Ink"));
        identity.Children.Add(copy);Grid.SetColumn(identity,1);top.Children.Add(identity);

        var shareStack=new StackPanel{HorizontalAlignment=HorizontalAlignment.Right,VerticalAlignment=VerticalAlignment.Center,ToolTip=shareInfo.Description};
        shareStack.Children.Add(Mono(share,20,true,"Accent"));
        shareStack.Children.Add(Text(shareInfo.Label,9,false,"Muted"));
        Grid.SetColumn(shareStack,2);top.Children.Add(shareStack);
        body.Children.Add(top);

        var hero=new Border{Background=Theme.Resource("Inset"),BorderBrush=Theme.Resource("Line"),BorderThickness=new Thickness(1),Padding=new Thickness(10,8,10,8),Margin=new Thickness(0,0,0,10)};
        var heroDock=new DockPanel();
        var heroValue=Mono(selected,28,true,"Accent");heroValue.HorizontalAlignment=HorizontalAlignment.Right;
        DockPanel.SetDock(heroValue,Dock.Right);heroDock.Children.Add(heroValue);
        heroDock.Children.Add(Text(entry.ValueKind==JsonValueKind.Object?metric:"当前榜单无该干员",11,true,"Muted"));
        hero.Child=heroDock;body.Children.Add(hero);

        var stats=new WrapPanel{Margin=new Thickness(0,0,0,4)};
        AddStat(stats,"总伤害",row.UiInt("damage"));
        AddStat(stats,"有效伤害",Effective(row));
        AddStat(stats,"过量伤害",row.UiInt("overkill_damage"));
        AddStat(stats,"溢出伤害率",OverkillRate(row));
        AddStat(stats,"治疗",row.UiInt("healing"));
        AddStat(stats,"有效护盾",row.UiInt("shield_absorbed"));
        if(stats.Children.Count>0) body.Children.Add(stats);
        body.Children.Add(Text(OverkillNote(row,view),10,false,"Muted",new Thickness(0,4,0,2)));
        if(row.Get("burst_unknown_reason").ValueKind==JsonValueKind.String)
            body.Children.Add(Text("元素爆条归属未知："+row.Text("burst_unknown_reason"),10,false,"Muted",new Thickness(0,2,0,0)));

        Section("技能 / 非技能");
        string skillKey=effective ? "skill_damage_effective" : "skill_damage";
        string nonSkillKey=effective ? "non_skill_damage_effective" : "non_skill_damage";
        double skillDamage=detail.Number(skillKey,double.NaN);
        double nonSkillDamage=detail.Number(nonSkillKey,double.NaN);
        bool skillKnown=detail.Bool(effective ? "skill_split_effective_complete" : "skill_split_complete")
            && double.IsFinite(skillDamage) && skillDamage>=0
            && double.IsFinite(nonSkillDamage) && nonSkillDamage>=0;
        double coverageValue=detail.Number("skill_coverage",double.NaN);
        bool coverageKnown=detail.Bool("skill_coverage_complete") && coverageValue is >= 0 and <= 1;
        var splitStats = new WrapPanel();
        AddStat(splitStats, effective ? "技能有效伤害" : "技能伤害",
            skillKnown ? detail.UiInt(skillKey) : "—",showZero:true);
        AddStat(splitStats, effective ? "非技能有效伤害" : "非技能伤害",
            skillKnown ? detail.UiInt(nonSkillKey) : "—",showZero:true);
        AddStat(splitStats, "技能覆盖率", coverageKnown
            ? SkillCoverageText(coverageValue) : "—");
        AddStat(splitStats, "技能开启秒", detail.Bool("skill_coverage_complete")
            ? detail.UiInt("skill_active_seconds") : "—",showZero:true);
        AddStat(splitStats, "在场秒", row.Bool("presence_complete")
            ? row.UiInt("presence_seconds") : "—",showZero:true);
        body.Children.Add(splitStats);
        double splitTotal=skillDamage+nonSkillDamage;
        if (skillKnown && double.IsFinite(splitTotal) && splitTotal>0)
        {
            double skillShare=skillDamage/splitTotal;
            string splitSummary=$"技能伤害占比 {Percentage(skillShare)} · 非技能伤害占比 {Percentage(1-skillShare)}";
            var splitBar = new Grid { Height = 8, Margin = new Thickness(0, 3, 0, 5) };
            AutomationProperties.SetName(splitBar,splitSummary);
            AutomationProperties.SetAutomationId(splitBar,"InspectorSkillSplitBar");
            splitBar.ToolTip=splitSummary;
            splitBar.ColumnDefinitions.Add(new ColumnDefinition { Width=new GridLength(skillShare,GridUnitType.Star) });
            splitBar.ColumnDefinitions.Add(new ColumnDefinition { Width=new GridLength(1-skillShare,GridUnitType.Star) });
            var skillPart = new Border { Background = Theme.Resource("Accent"),
                CornerRadius = new CornerRadius(2, 0, 0, 2) };
            var normalPart = new Border { Background = Theme.Resource("Muted"),
                CornerRadius = new CornerRadius(0, 2, 2, 0) };
            Grid.SetColumn(normalPart, 1);
            splitBar.Children.Add(skillPart);
            splitBar.Children.Add(normalPart);
            body.Children.Add(splitBar);
        }
        string splitReason=detail.Text("skill_split_reason");
        if (!skillKnown)
            body.Children.Add(Text(splitReason.Length > 0
                ? "技能拆分未知：" + splitReason
                : effective && detail.Bool("skill_split_complete")
                    ? "有效技能拆分未知：过量伤害证据不足。"
                    : "技能拆分未知：事件观测或技能窗口未覆盖。",
                11, false, "Muted", new Thickness(0, 1, 0, 2)));
        var coverageNote=coverageKnown
            ? "覆盖率按技能开启时间占在场时间计算；被动技能计入非技能。"
            : splitReason.Length>0 ? "技能覆盖率未知："+splitReason
                : "技能覆盖率未知：在场时间或技能窗口证据不足。";
        body.Children.Add(Text(coverageNote,11,false,"Muted"));

        Section("被减伤率");
        var mitigationReading=MitigationReading(row);
        Pair(mitigationReading.Label,Percentage(mitigationReading.Fraction));
        body.Children.Add(Text(mitigationReading.Explanation,10,false,"Muted",new Thickness(0,5,0,0)));

        Section(effective ? "有效伤害构成" : "伤害构成");
        var types=effective ? detail.Get("effective_damage_by_type") : row.Get("damage_by_type");
        bool typeKnown=types.ValueKind==JsonValueKind.Object
            && (effective || (row.Bool("types_available") && !row.Bool("types_stale")));
        if(!typeKnown)
        {
            body.Children.Add(Text(effective
                ? "有效伤害构成未知：按类型的过量证据不足。"
                : "伤害构成未知：累计类型字段不完整。",11,false,"Muted"));
        }
        else
        {
            var kinds=new[]{("physical","物理","Physical"),("arts","法术","Arts"),
                ("elemental","元素","Elemental"),("true","真实","True")};
            double total=kinds.Sum(kind=>types.Number(kind.Item1));
            var mix=new WrapPanel();
            foreach(var (key,label,token) in kinds)
            {
                double amount=types.Number(key);
                if(amount<=0) continue;
                string fraction=total>0 ? Percentage(amount/total) : "—";
                var card=new Border{Background=Theme.Resource("Inset"),BorderBrush=Theme.Resource(token),BorderThickness=new Thickness(1),Padding=new Thickness(6,5,6,5),Margin=new Thickness(0,0,4,4),MinWidth=66};
                var stack=new StackPanel();stack.Children.Add(Text(label,11,false,"Muted"));stack.Children.Add(Mono(fraction,13,true,"Ink"));stack.Children.Add(Text(types.UiInt(key),11,false,"Muted"));
                card.Child=stack;mix.Children.Add(card);
            }
            if(mix.Children.Count>0) body.Children.Add(mix);
            else body.Children.Add(Text("已观测伤害为 0",11,false,"Muted"));
        }

        Section("独立接敌速率");
        var damageRate=rates.Get("damage");var healingRate=rates.Get("healing");
        var rateStats=new WrapPanel();
        AddStat(rateStats,effective ? "有效 DPS" : "DPS",RateText(row,damageRate,"engagement_damage",
            effective ? detail : default, effective ? "effective_engagement_damage" : ""));
        AddStat(rateStats,"HPS",RateText(row,healingRate,"engagement_healing",default,""));
        AddStat(rateStats,"伤害活跃秒",row.UiInt("active_damage"));
        AddStat(rateStats,"治疗活跃秒",row.UiInt("active_healing"));
        body.Children.Add(rateStats);
        var rateNote=damageRate.Text("note");if(rateNote.Length==0) rateNote=healingRate.Text("note");
        if(rateNote.Length>0) body.Children.Add(Text(rateNote,10,false,"Muted",new Thickness(0,5,0,0)));
        if(rateReason.Length>0) body.Children.Add(Text(rateReason,11,false,"Muted",new Thickness(0,3,0,0)));
        if(rateHint.Length>0) body.Children.Add(Text(rateHint,11,false,"Muted",new Thickness(0,3,0,0)));

        if(!row.Bool("event_coverage_complete"))
            body.Children.Add(Text("事件覆盖不完整：未建立事件覆盖的区间保持未知，不补零",10,false,"Muted",new Thickness(0,5,0,0)));

        Section("归属来源");
        string sources=string.Join("\n",row.Get("sources").Items().Select(x=>x.Text()));
        body.Children.Add(Text(sources.Length>0 ? sources : "—",10,false,"Muted"));
    }
    private static string Raw(JsonElement value) => value.ValueKind==JsonValueKind.Undefined ? "" : value.GetRawText();

    private ReferenceReport ShowReference()
    {
        if(referenceReport is { } existing && existing.Skin==Theme.Key && ReferenceEquals(Child,existing)) return existing;
        if(referenceReport?.Skin!=Theme.Key) referenceReport=new ReferenceReport();
        Background=Brushes.Transparent;
        BorderThickness=new Thickness(0);
        Padding=new Thickness(0);
        Child=referenceReport;
        return referenceReport!;
    }

    private void UseFullReport()
    {
        if(ReferenceEquals(Child,body)) return;
        SetResourceReference(BackgroundProperty,"Card");
        BorderThickness=new Thickness(1);
        Padding=new Thickness(14);
        Child=body;
    }

    private sealed class ReferenceReport : StackPanel
    {
        private static readonly Brush DarkInk=Theme.Brush("#F1F4F7");
        private static readonly Brush MutedInk=Theme.Brush("#D2D9E0");
        private static readonly Brush Divider=Theme.Brush("#3D4751");
        private static readonly Brush Accent=Theme.Brush("#FF8A26");
        private static readonly Brush Cyan=Theme.Brush("#58C7E5");
        private static readonly Brush DarkFace=new LinearGradientBrush(
            Color.FromRgb(24,33,41),Color.FromRgb(38,46,54),90);
        private static readonly (string Key,string Label,string Token,Brush Color)[] DamageTypes=
        {
            ("physical","物理","Physical",Accent),
            ("arts","法术","Arts",Cyan),
            ("elemental","元素","Elemental",Theme.Brush("#B68CF4")),
            ("true","真实","True",Theme.Brush("#B4C1D0")),
        };
        private static bool UseThemeColors => Theme.IsPeople || Theme.IsBabel;
        private static Brush MetricInk => UseThemeColors ? Theme.Resource("Ink") : DarkInk;
        private static Brush MetricMuted => UseThemeColors ? Theme.Resource("Muted") : MutedInk;
        private static Brush MetricDivider => UseThemeColors ? Theme.Resource("Line") : Divider;
        private static Brush ChartInk => UseThemeColors ? Theme.Resource("ConsoleInk") : DarkInk;
        private static Brush ChartMuted => UseThemeColors ? Theme.Resource("ConsoleMuted") : MutedInk;
        private static Brush ChartDivider => UseThemeColors ? Theme.Resource("ConsoleLine") : Divider;
        private static Brush DamageColor(int index) => UseThemeColors
            ? Theme.Resource(DamageTypes[index].Token) : DamageTypes[index].Color;
        private static readonly string[] DefaultLabels={"总伤","DPS（近 5 秒）","伤害占比","技能覆盖率","站场总时间"};
        private static readonly string[] MetricIcons=
        {
            "M 6,0 V 12 M 0,6 H 12 M 2,2 L 10,10 M 10,2 L 2,10",
            "M 1,9 A 5,5 0 1 1 11,9 M 6,7 L 9,4 M 3,11 H 9",
            "M 6,1 L 11,4 V 9 L 6,12 L 1,9 V 4 Z M 1,4 L 6,7 L 11,4 M 6,7 V 12",
            "M 6,1 L 11,4 V 9 L 6,12 L 1,9 V 4 Z M 4,7 L 6,5 L 8,7",
            "M 6,1 A 5,5 0 1 1 5.9,1 M 6,3 V 6 L 8,7",
        };
        private readonly Image portrait=new(){Stretch=Stretch.UniformToFill};
        internal string Skin { get; } = Theme.Key;
        private readonly TextBlock initial=Label("",21,MetricInk,true);
        private readonly Border avatar;
        private readonly TextBlock[] metricLabels=new TextBlock[DefaultLabels.Length];
        private readonly TextBlock[] metricValues=new TextBlock[DefaultLabels.Length];
        private readonly Border[] metricFields=new Border[DefaultLabels.Length];
        private readonly RatioField overkill=new("伤害溢出率",
            UseThemeColors ? Theme.Resource("Accent") : Accent,"Overkill");
        private readonly RatioField mitigation=new("被减伤率",
            UseThemeColors ? Theme.Resource("Muted") : Theme.Brush("#A7BBCF"),"Mitigation");
        private readonly RatioField skillShare=new("技能期伤害占比",
            UseThemeColors ? Theme.Resource("Shield") : Cyan,"SkillShare");
        private readonly DockPanel[] typeFields=new DockPanel[DamageTypes.Length];
        private readonly TextBlock[] typeValues=new TextBlock[DamageTypes.Length];
        private readonly TextBlock compositionTitle=Label("伤害属性构成",15,ChartInk,true);
        private readonly TextBlock ringTitle=Label("总伤",14,ChartInk,true);
        private readonly TextBlock ringValue=Label("—",17,ChartInk,true);
        private readonly Ring ring=new(Theme.IsBabel ? 7 : 14,ChartDivider,Enumerable.Range(0,DamageTypes.Length).Select(DamageColor).ToArray());
        private readonly Border compositionField;

        internal ReferenceReport()
        {
            var modules=new Grid();
            var primaryWidth=new GridLength(1.28,GridUnitType.Star);
            var chartWidth=new GridLength(1,GridUnitType.Star);
            modules.ColumnDefinitions.Add(new ColumnDefinition{Width=primaryWidth});
            modules.ColumnDefinitions.Add(new ColumnDefinition{Width=chartWidth});
            modules.RowDefinitions.Add(new RowDefinition{Height=GridLength.Auto});
            modules.RowDefinitions.Add(new RowDefinition{Height=GridLength.Auto});

            const int primaryMetricCount=3;
            var readings=new Grid{Margin=new Thickness(0,2,0,0)};
            readings.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});
            readings.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(1,GridUnitType.Star)});

            var portraitCanvas=new Grid{Background=UseThemeColors ? Theme.Resource("Inset") : DarkFace,ClipToBounds=true};
            initial.HorizontalAlignment=HorizontalAlignment.Center;
            initial.VerticalAlignment=VerticalAlignment.Center;
            portraitCanvas.Children.Add(initial);
            portraitCanvas.Children.Add(portrait);
            avatar=new Border{Child=portraitCanvas,Width=46,Height=46,VerticalAlignment=VerticalAlignment.Center,
                Margin=new Thickness(0,0,8,0),BorderBrush=MetricDivider,BorderThickness=new Thickness(1)};
            AutomationProperties.SetAutomationId(avatar,"CompactOperatorPortrait");
            Grid.SetRowSpan(avatar,primaryMetricCount);
            readings.Children.Add(avatar);

            for(int index=0;index<metricLabels.Length;index++)
            {
                readings.RowDefinitions.Add(new RowDefinition{MinHeight=23});
                var row=new DockPanel();
                var key=metricLabels[index]=Label(DefaultLabels[index],14,MetricMuted);
                key.TextTrimming=TextTrimming.CharacterEllipsis;
                var value=metricValues[index]=Label("—",14,MetricInk,true);
                value.Margin=new Thickness(6,0,0,0);
                value.SetResourceReference(TextBlock.FontFamilyProperty,"DataFont");
                value.TextTrimming=TextTrimming.CharacterEllipsis;
                value.MaxWidth=120;
                DockPanel.SetDock(value,Dock.Right);
                row.Children.Add(value);
                var icon=new System.Windows.Shapes.Path
                {
                    Data=Geometry.Parse(MetricIcons[index]),Stroke=MetricInk,StrokeThickness=1.2,
                    Stretch=Stretch.Uniform,Width=12,Height=12,VerticalAlignment=VerticalAlignment.Center,
                    Margin=new Thickness(0,0,6,0),IsHitTestVisible=false,
                };
                DockPanel.SetDock(icon,Dock.Left);
                row.Children.Add(icon);
                row.Children.Add(key);
                var cell=metricFields[index]=new Border{Child=row,Background=Brushes.Transparent,BorderBrush=MetricDivider,
                    BorderThickness=new Thickness(0,0,0,index==metricLabels.Length-1 ? 0 : 1)};
                AutomationProperties.SetAutomationId(cell,$"CompactOperatorMetric{index}");
                Grid.SetRow(cell,index);
                if(index<primaryMetricCount) Grid.SetColumn(cell,1);
                else Grid.SetColumnSpan(cell,2);
                readings.Children.Add(cell);
            }
            var ratios=new UniformGrid{Rows=1,Columns=3};
            ratios.Children.Add(overkill);
            ratios.Children.Add(mitigation);
            ratios.Children.Add(skillShare);
            AutomationProperties.SetAutomationId(ratios,"CompactOperatorRatios");
            var statsBody=new StackPanel();
            statsBody.Children.Add(readings);
            statsBody.Children.Add(new Border
            {
                Child=ratios,BorderBrush=MetricDivider,BorderThickness=new Thickness(0,1,0,0),
                Margin=new Thickness(0,4,0,0),
            });

            var statsPanel=PanelFrame(Module(Label("主要数据",15,MetricInk,true),statsBody,MetricDivider),
                new Thickness(0,0,3,0),UseThemeColors ? Theme.Resource("Card") : DarkFace,MetricDivider);
            AutomationProperties.SetAutomationId(statsPanel,"CompactOperatorStats");
            modules.Children.Add(statsPanel);

            var chart=new Grid{MinHeight=115};
            chart.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(1,GridUnitType.Star)});
            chart.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});
            chart.Children.Add(ring);
            var center=new StackPanel{HorizontalAlignment=HorizontalAlignment.Center,VerticalAlignment=VerticalAlignment.Center};
            ringTitle.HorizontalAlignment=HorizontalAlignment.Center;
            ringValue.HorizontalAlignment=HorizontalAlignment.Center;
            ringValue.SetResourceReference(TextBlock.FontFamilyProperty,"DisplayFont");
            center.Children.Add(ringTitle);
            center.Children.Add(ringValue);
            chart.Children.Add(new Viewbox{Child=center,StretchDirection=StretchDirection.DownOnly,
                Margin=new Thickness(15,0,15,0)});
            var legend=new StackPanel{VerticalAlignment=VerticalAlignment.Center,Margin=new Thickness(5,0,0,0)};
            for(int index=0;index<DamageTypes.Length;index++)
            {
                var line=typeFields[index]=new DockPanel{MinHeight=25,Background=Brushes.Transparent};
                var item=DamageTypes[index];
                var value=typeValues[index]=Label("—",13,ChartInk);
                value.SetResourceReference(TextBlock.FontFamilyProperty,"DataFont");
                value.Margin=new Thickness(9,0,0,0);
                value.MinWidth=33;
                value.TextAlignment=TextAlignment.Right;
                DockPanel.SetDock(value,Dock.Right);line.Children.Add(value);
                var swatch=new Border{Width=10,Height=10,Background=DamageColor(index),CornerRadius=new CornerRadius(1),
                    VerticalAlignment=VerticalAlignment.Center,Margin=new Thickness(0,0,5,0)};
                DockPanel.SetDock(swatch,Dock.Left);line.Children.Add(swatch);
                line.Children.Add(Label(item.Label,13,ChartMuted));
                legend.Children.Add(line);
            }
            Grid.SetColumn(legend,1);chart.Children.Add(legend);
            var chartPanel=compositionField=PanelFrame(Module(compositionTitle,chart,ChartDivider),
                new Thickness(3,0,0,0),UseThemeColors ? Theme.Resource("Console") : DarkFace,ChartDivider);
            AutomationProperties.SetAutomationId(chartPanel,"CompactOperatorComposition");
            Grid.SetColumn(chartPanel,1);
            modules.Children.Add(chartPanel);
            Children.Add(modules);

            bool stacked=false;
            modules.SizeChanged+=(_,e)=>
            {
                if(!e.WidthChanged) return;
                bool narrow=e.NewSize.Width<410;
                if(stacked==narrow) return;
                stacked=narrow;
                modules.ColumnDefinitions[0].Width=stacked ? chartWidth : primaryWidth;
                modules.ColumnDefinitions[1].Width=stacked ? new GridLength(0) : chartWidth;
                statsPanel.Margin=stacked ? new Thickness(0,0,0,6) : new Thickness(0,0,3,0);
                chartPanel.Margin=stacked ? new Thickness(0) : new Thickness(3,0,0,0);
                Grid.SetColumn(chartPanel,stacked ? 0 : 1);
                Grid.SetRow(chartPanel,stacked ? 1 : 0);
            };
            Reset();
        }

        private static DockPanel Module(TextBlock title, UIElement content, Brush divider)
        {
            var module=new DockPanel{Margin=new Thickness(8,3,8,4)};
            var heading=new Border{Child=title,Height=26,BorderBrush=divider,BorderThickness=new Thickness(0,0,0,1)};
            if(UseThemeColors)
                title.SetResourceReference(TextBlock.FontFamilyProperty,"HeadingFont");
            DockPanel.SetDock(heading,Dock.Top);
            module.Children.Add(heading);
            module.Children.Add(content);
            return module;
        }

        private static Border PanelFrame(UIElement content,Thickness margin,Brush face,Brush divider)=>new()
        {
            Child=content,Background=face,BorderBrush=divider,BorderThickness=new Thickness(Theme.IsBabel ? 0 : Theme.IsPeople ? 1 : 2),
            CornerRadius=new CornerRadius(Theme.IsBabel ? 0 : Theme.IsPeople ? 1 : 5),Margin=margin
        };

        private static TextBlock Label(string text,double size,Brush brush,bool bold=false)=>new()
        {
            Text=text,FontSize=size,Foreground=brush,FontWeight=bold ? FontWeights.SemiBold : FontWeights.Normal,
            VerticalAlignment=VerticalAlignment.Center,
        };

        internal void Reset()
        {
            initial.Text="—";
            portrait.Source=null;
            avatar.ToolTip="从排行选择干员";
            AutomationProperties.SetName(avatar,"干员头像");
            SetMetrics(default,default,default,default,false,"",SharePresentation.From(default));
        }

        internal void Update(string owner,JsonElement row,JsonElement view,JsonElement entry,
            JsonElement detail,bool effective,string ranking,SharePresentation shareInfo)
        {
            string displayName=row.Text("name");
            if(displayName.Length==0) displayName=owner;
            initial.Text=displayName[..1];
            portrait.Source=Portraits.Get(owner,row.Get("present").ValueKind==JsonValueKind.False);
            avatar.ToolTip=displayName;
            AutomationProperties.SetName(avatar,displayName+"头像");
            SetMetrics(row,view,entry,detail,effective,ranking,shareInfo);
        }

        private void SetMetrics(JsonElement row,JsonElement view,JsonElement entry,JsonElement detail,
            bool effective,string ranking,SharePresentation shareInfo)
        {
            string category=view.Get("config").Text("category");
            double skill=detail.Number(effective ? "skill_damage_effective" : "skill_damage",double.NaN);
            double ordinary=detail.Number(effective ? "non_skill_damage_effective" : "non_skill_damage",double.NaN);
            double splitTotal=skill+ordinary;
            bool splitKnown=detail.Bool(effective ? "skill_split_effective_complete" : "skill_split_complete")
                && double.IsFinite(splitTotal) && skill>=0 && ordinary>=0;
            double skillCoverage=detail.Number("skill_coverage",double.NaN);
            bool coverageKnown=detail.Bool("skill_coverage_complete") && skillCoverage is >= 0 and <= 1;
            string reason=detail.Text("skill_split_reason");
            string share=Percentage(entry.Number("share",double.NaN));
            string rate=EntryRate(entry);
            string rateLabel=view.Text("rate_label");
            double seconds=row.Number("presence_seconds",double.NaN);
            string presence=row.Bool("presence_complete") && seconds>=0 && seconds<TimeSpan.MaxValue.TotalSeconds
                ? FormatTime(seconds) : "—";
            metricLabels[0].Text=effective ? "有效伤害" : "总伤";
            metricLabels[1].Text=rateLabel.Length==0 && category!="shield" ? DefaultLabels[1]
                : category=="shield" ? "有效护盾"
                : rateLabel=="全周期DPS" && category=="damage" ? "全周期 DPS"
                : $"{(category=="healing" ? "HPS" : "DPS")}（{rateLabel}）";
            metricLabels[2].Text=view.Text("share_scope")=="known" ? shareInfo.Label : (ranking,category) switch
            {
                ("dps","healing") => "HPS 占比",
                ("dps",_) => "DPS 占比",
                (_,"healing") => "治疗占比",
                (_,"shield") => "护盾吸收占比",
                ("recent",_) => "近期伤害占比",
                _ => "伤害占比",
            };
            SetMetric(0,effective ? Number(detail.Get("effective_damage")) : Number(row.Get("damage")),
                effective ? "总伤害减去过量伤害。"+(detail.Get("effective_damage").ValueKind!=JsonValueKind.Number
                    ? "\n"+OverkillNote(row,view) : "") : "归属于该干员的累计结算伤害，含过量伤害。");
            string rateHint=category=="shield" ? "累计归属于该干员的实际护盾吸收量。"
                : DescribeRate(view.Get("config").Text("rate_basis"),category=="healing",effective);
            SetMetric(1,category=="shield" ? Number(row.Get("shield_absorbed")) : rate,
                WithNote(rateHint,entry.Text("rate_note")));
            SetMetric(2,share,WithNote("该干员当前排行指标 ÷ 同口径指标合计。",shareInfo.Description));
            SetMetric(3,coverageKnown ? SkillCoverageText(skillCoverage) : "—",
                WithNote("技能开启时间 ÷ 干员在场时间。被动技能不计入开启时间。",
                    coverageKnown ? "" : (reason.Length>0 ? reason : "在场时间或技能窗口证据不足。")));
            SetMetric(4,presence,WithNote("累加干员确认在场的战斗时长。",
                presence=="—" ? "在场时间覆盖不足。" : ""));

            double overflow=OverkillFraction(row);
            string overflowNote=double.IsFinite(overflow) ? ""
                : row.Get("damage").TryNumber(double.NaN)==0 ? "累计总伤为零，无法计算占比。"
                : OverkillNote(row,view);
            overkill.Update(overflow,WithNote("过量伤害 ÷ 总伤害（含过量）。",overflowNote));
            var mitigationReading=MitigationReading(row);
            mitigation.Caption=mitigationReading.Label;
            mitigation.Update(mitigationReading.Fraction,mitigationReading.Explanation);
            string splitBasis=effective ? "技能期有效伤害 ÷ 已拆分的总有效伤害。" : "技能期伤害 ÷ 已拆分的总伤害。";
            skillShare.Caption=effective ? "技能期有效伤害占比" : "技能期伤害占比";
            skillShare.Update(splitKnown && splitTotal>0 ? skill/splitTotal : double.NaN,
                WithNote(splitBasis+"被动技能的伤害归入非技能期。",
                    !splitKnown ? (reason.Length>0 ? reason : "技能期伤害覆盖不足。")
                        : splitTotal==0 ? "已拆分的伤害为零，无法计算占比。" : ""));
            SetComposition(effective,row,detail);
        }

        private static string DescribeRate(string basis,bool healing,bool effective)
        {
            string output=healing ? "治疗量" : effective ? "有效伤害" : "伤害";
            return basis switch
            {
                "engagement" => $"已观测的{output} ÷ 活跃时长。每次事件产生 1.5 秒活跃区间，重叠区间合并，超出观测末尾的部分截断。",
                "full_cycle" => $"累计{output} ÷ 在场时长。已观测活跃时长更长时，以活跃时长为准。",
                _ => $"最近最多 5 个游戏秒内的{output} ÷ 该观测窗口的时长。",
            };
        }

        private static string WithNote(string formula,string note)=>note.Length==0 ? formula : formula+"\n"+note;

        private void SetMetric(int index,string value,string explanation)
        {
            metricValues[index].Text=value.Length>0 ? value : "—";
            DescribeField(metricFields[index],metricLabels[index].Text,metricValues[index].Text,explanation);
        }

        private static void DescribeField(FrameworkElement field,string label,string value,string explanation)
        {
            if(field.ToolTip is not ToolTip {Content:TextBlock text})
            {
                text=Text("",13,token:"ConsoleInk");
                field.ToolTip=new ToolTip{Content=text,MaxWidth=360};
                field.Focusable=true;
                ToolTipService.SetShowsToolTipOnKeyboardFocus(field,true);
            }
            string heading=label+"  "+value;
            string description=heading+"\n"+explanation;
            if(text.Text==description) return;
            text.Text=description;
            AutomationProperties.SetName(field,heading);
            AutomationProperties.SetHelpText(field,explanation);
        }

        private void SetComposition(bool effective,JsonElement row,JsonElement detail)
        {
            compositionTitle.Text=effective ? "有效伤害构成" : "伤害属性构成";
            ringTitle.Text=effective ? "有效" : "总伤";
            ringValue.Text=effective ? Number(detail.Get("effective_damage")) : Number(row.Get("damage"));
            var types=effective ? detail.Get("effective_damage_by_type") : row.Get("damage_by_type");
            bool known=types.ValueKind==JsonValueKind.Object
                && (effective || (row.Bool("types_available") && !row.Bool("types_stale")));
            double[] values=DamageTypes.Select(item=>known ? types.Number(item.Key) : 0).ToArray();
            double total=values.Sum();
            string damageLabel=effective ? "有效伤害" : "伤害";
            string coverageNote=known ? "" : effective ? "分属性的过量证据不足。" : "分属性的累计伤害不可用。";
            DescribeField(compositionField,compositionTitle.Text,ringValue.Text,WithNote(
                $"各属性的{damageLabel} ÷ 已记录的各属性{damageLabel}之和。",coverageNote));
            for(int index=0;index<values.Length;index++)
            {
                typeValues[index].Text=known && total>0 ? Percentage(values[index]/total) : "—";
                string amountNote=known ? "该属性累计"+damageLabel+" "+Number(values[index])+"。" : coverageNote;
                DescribeField(typeFields[index],DamageTypes[index].Label,typeValues[index].Text,
                    WithNote($"该属性的{damageLabel} ÷ 已记录的各属性{damageLabel}之和。",amountNote));
            }
            ring.Set(values,total);
        }

        private static string FormatTime(double seconds)
        {
            double rounded=Math.Round(seconds,MidpointRounding.AwayFromZero);
            return FormattableString.Invariant($"{Math.Floor(rounded/60):00}:{rounded%60:00}");
        }

        private sealed class RatioField : Border
        {
            private readonly TextBlock title;
            internal string Caption { get=>title.Text; set=>title.Text=value; }
            private readonly TextBlock value=Label("—",15,MetricInk,true);
            private readonly Ring ring;

            internal RatioField(string label,Brush color,string automationSuffix)
            {
                ring=new Ring(Theme.IsBabel ? 2.5 : 4,MetricDivider,color);
                Background=Brushes.Transparent;
                Padding=new Thickness(2,5,2,4);
                var dial=new Grid{Width=50,Height=50,HorizontalAlignment=HorizontalAlignment.Center};
                dial.Children.Add(ring);
                value.SetResourceReference(TextBlock.FontFamilyProperty,"DataFont");
                value.HorizontalAlignment=HorizontalAlignment.Center;
                dial.Children.Add(new Viewbox{Child=value,StretchDirection=StretchDirection.DownOnly,
                    Margin=new Thickness(7)});
                var content=new StackPanel();
                content.Children.Add(dial);
                title=Label(label,12,MetricMuted,true);
                title.Margin=new Thickness(0,2,0,0);
                title.TextWrapping=TextWrapping.Wrap;
                title.TextAlignment=TextAlignment.Center;
                content.Children.Add(title);
                Child=content;
                AutomationProperties.SetAutomationId(this,"CompactOperatorRatio"+automationSuffix);
            }

            internal void Update(double fraction,string explanation)
            {
                value.Text=Percentage(fraction);
                ring.SetFraction(fraction);
                DescribeField(this,Caption,value.Text,explanation);
            }
        }

        private sealed class Ring(double thickness,Brush track,params Brush[] colors) : FrameworkElement
        {
            private double[] values=new double[colors.Length];
            private double total;
            private bool reverse;

            internal void SetFraction(double fraction)
            {
                double portion=double.IsFinite(fraction) ? Math.Min(Math.Abs(fraction),1) : 0;
                bool negative=fraction<0;
                if(total==1 && values[0]==portion && reverse==negative) return;
                values[0]=portion;
                reverse=negative;
                total=1;
                InvalidateVisual();
            }

            internal void Set(double[] next,double sum)
            {
                if(total==sum && values.SequenceEqual(next)) return;
                values=next;total=sum;InvalidateVisual();
            }

            protected override void OnRender(DrawingContext context)
            {
                double radius=Math.Min(ActualWidth,ActualHeight)/2-thickness/2-2;
                if(radius<=0) return;
                var center=new Point(ActualWidth/2,ActualHeight/2);
                context.DrawEllipse(null,new Pen(track,thickness),center,radius,radius);
                if(total<=0 || !double.IsFinite(total)) return;
                double angle=-Math.PI/2;
                Point At(double direction)=>new(center.X+radius*Math.Cos(direction),center.Y+radius*Math.Sin(direction));
                for(int index=0;index<values.Length;index++)
                {
                    if(values[index]<=0) continue;
                    double sweep=2*Math.PI*values[index]/total;
                    var pen=new Pen(colors[index],thickness);
                    if(sweep>=2*Math.PI-.0001)
                    {
                        context.DrawEllipse(null,pen,center,radius,radius);
                        break;
                    }
                    var geometry=new StreamGeometry();
                    using(var path=geometry.Open())
                    {
                        path.BeginFigure(At(angle),false,false);
                        double end=angle+(reverse ? -sweep : sweep);
                        path.ArcTo(At(end),new Size(radius,radius),0,sweep>Math.PI,
                            reverse ? SweepDirection.Counterclockwise : SweepDirection.Clockwise,true,false);
                    }
                    geometry.Freeze();
                    context.DrawGeometry(null,pen,geometry);
                    angle+=sweep;
                }
            }
        }
    }

    private static string Number(JsonElement value)=>Number(value.TryNumber(double.NaN));

    private static string Number(double value)=>double.IsFinite(value)
        ? Math.Round(value,MidpointRounding.AwayFromZero).ToString("N0",CultureInfo.InvariantCulture) : "—";

    private static string Percentage(double fraction)
    {
        double percent=fraction*100;
        return double.IsFinite(percent) ? Number(percent)+"%" : "—";
    }

    private static string SkillCoverageText(double fraction) =>
        fraction is >0 and <0.01 ? "<1%" : Percentage(fraction);

    private static string EntryRate(JsonElement entry)=>entry.Text("rate_state") switch
    {
        "value"=>Number(entry.Get("rate")),
        "no_engagement"=>"无接敌",
        _=>"—",
    };

    private static string RateText(JsonElement row,JsonElement rate,string exact,
        JsonElement detail=default,string detailExact="")
    {
        bool effective=detailExact.Length>0;
        if(rate.Text("state")=="value")
            return effective ? detail.UiInt(detailExact) : row.UiInt(exact);
        string text=rate.Text("text");
        if(text.Length>0) return text;
        return effective ? detail.UiInt(detailExact) : row.UiInt(exact);
    }
    private static string Effective(JsonElement row)
    {
        var direct=row.Get("effective_damage");
        if(direct.ValueKind==JsonValueKind.Number) return row.UiInt("effective_damage");
        var damage=row.Get("damage");var overkill=row.Get("overkill_damage");
        if(damage.ValueKind==JsonValueKind.Number&&overkill.ValueKind==JsonValueKind.Number)
            return Json.UiIntText((damage.GetDouble()-overkill.GetDouble()).ToString(System.Globalization.CultureInfo.InvariantCulture));
        return "—";
    }
    private static double OverkillFraction(JsonElement row)
    {
        double total=row.Get("damage").TryNumber(double.NaN);
        return total>0 ? row.Get("overkill_damage").TryNumber(double.NaN)/total : double.NaN;
    }
    private static string OverkillRate(JsonElement row)=>Percentage(OverkillFraction(row));

    private static (double Fraction,string Label,string Explanation) MitigationReading(JsonElement row)
    {
        const string formula="1 − 经护甲/魔抗和物法减伤减少后的伤害 ÷ 原始伤害。\n观测值使用同一击的原始伤害与护盾吸收前的结算伤害。结算伤害包含其他伤害修正。";
        const string negativeNote="\n结算伤害高于公式前伤害时，比例为负。当前观测记录两端数值，无法确定差额来自哪项修正。";
        double full=row.Get("mitigation_rate").TryNumber(double.NaN);
        if(double.IsFinite(full)) return (full/100,"被减伤率",full<0 ? formula+negativeNote : formula);

        string reason=row.Text("mitigation_unknown_reason");
        double observed=row.Get("observed_mitigation_rate").TryNumber(double.NaN);
        double hits=row.Get("mitigation_matched_hits").TryNumber();
        if(double.IsFinite(observed) && double.IsFinite(hits) && hits>0)
        {
            string coverage=$"仅统计已配对的 {Number(hits)} 击。";
            double after=row.Get("observed_mitigation_after").TryNumber(double.NaN);
            double total=row.Get("damage").TryNumber(double.NaN);
            if(row.Bool("counter_snapshot_complete") && after>=0 && total>0 && after<=total)
                coverage+=$"这些命中的结算伤害占当前累计总伤的 {Percentage(after/total)}。";
            if(reason.Length>0) coverage+="\n整局统计缺口："+reason;
            string explanation=observed<0 ? formula+negativeNote : formula;
            return (observed/100,"已观测减伤率",explanation+"\n"+coverage);
        }
        return (double.NaN,"被减伤率",formula+"\n"+(reason.Length>0 ? reason : "尚无同一击的公式前伤害与结算伤害。"));
    }
    private static string OverkillNote(JsonElement row,JsonElement view)
    {
        if(row.Bool("overkill_coverage_complete")) return "有效伤害 = 总伤害 − 过量伤害";
        if(!row.Bool("event_coverage_complete"))
        {
            string reason=view.Text("rate_reason");
            if(reason.Length==0) reason="事件观测未建立覆盖或已中断";
            return "过量未知："+reason;
        }
        var overkillReason=row.Text("overkill_unknown_reason");
        return "过量未知："+(overkillReason.Length>0 ? overkillReason : "目标或实际扣血证据不足");
    }
    private void AddStat(Panel host,string label,string value,bool showZero=false)
    {
        if(!showZero && value=="0") return;
        var border=new Border{Background=Theme.Resource("Inset"),BorderBrush=Theme.Resource("Line"),BorderThickness=new Thickness(1),Padding=new Thickness(7,5,7,5),Margin=new Thickness(0,0,4,4),MinWidth=92};
        var stack=new StackPanel();stack.Children.Add(Text(label,11,false,"Muted"));stack.Children.Add(Mono(value,13,true,"Ink"));
        border.Child=stack;host.Children.Add(border);
    }
    private void Section(string title)
    {
        var row=new StackPanel{Orientation=Orientation.Horizontal,Margin=new Thickness(0,14,0,5)};
        row.Children.Add(new Border{Width=3,Height=13,CornerRadius=new CornerRadius(1),Background=Theme.Resource("Accent"),Margin=new Thickness(0,0,7,0),VerticalAlignment=VerticalAlignment.Center});
        row.Children.Add(Text(title,11,true,"Ink"));
        body.Children.Add(row);
    }
    private void Pair(string label,string value)
    {
        if(value=="0") return;
        var grid=new Grid{Margin=new Thickness(0,0,0,0)};
        grid.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(1,GridUnitType.Star)});
        grid.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});
        grid.Children.Add(Text(label,10,false,"Muted"));
        var number=Mono(value,12,true,"Ink");number.HorizontalAlignment=HorizontalAlignment.Right;number.Margin=new Thickness(10,0,0,0);number.ToolTip=value;
        Grid.SetColumn(number,1);grid.Children.Add(number);
        body.Children.Add(new Border{Child=grid,BorderBrush=Theme.Resource("Line"),BorderThickness=new Thickness(0,0,0,1),Padding=new Thickness(0,5,0,5)});
    }
    internal static TextBlock Text(string text,double size=12,bool bold=false,string token="Ink",Thickness? margin=null)
    {
        var block=new TextBlock{Text=text,FontSize=size,FontWeight=bold ? FontWeights.SemiBold : FontWeights.Normal,TextWrapping=TextWrapping.Wrap,Margin=margin??new Thickness(0)};
        block.SetResourceReference(TextBlock.ForegroundProperty,token);return block;
    }
    internal static TextBlock Mono(string text,double size,bool bold=false,string token="Ink")
    {
        var block=Text(text,size,bold,token);block.SetResourceReference(TextBlock.FontFamilyProperty,"DataFont");return block;
    }
}
