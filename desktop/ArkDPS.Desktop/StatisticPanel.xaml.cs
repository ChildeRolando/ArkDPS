using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace ArkDPS.Desktop;

internal readonly record struct SharePresentation(string Label, string Details, bool Available)
{
    public string Description => Join(Label, Details);

    public static SharePresentation From(JsonElement view)
    {
        string scope=view.Text("share_scope");
        if(scope!="team" && scope!="known" && scope!="unavailable")
            scope=view.Bool("share_known") ? "team" : "unavailable";
        string label=scope switch
        {
            "team"=>"全队同口径占比",
            "known"=>"已知数据内占比",
            _=>"占比未知"
        };
        string reason=scope=="team" ? "" : view.Text("share_reason");
        return new(label,Join(reason,view.Text("share_note")),scope!="unavailable");
    }

    private static string Join(string first,string second) =>
        first.Length==0 ? second : second.Length==0 ? first : first+" · "+second;
}

public sealed class RowItem : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;
    internal bool Compact;
    public double RowHeight=>Compact ? 44 : 63;
    public double PortraitSize=>Compact ? 30 : 36;
    public Thickness RowMargin=>Compact ? new Thickness(0,2,0,2) : new Thickness(0,4,0,6);
    public double ValueSize=>Compact ? 18 : 21;
    private JsonElement entry;
    public JsonElement Entry { get => entry; set { entry=value; PropertyChanged?.Invoke(this,new PropertyChangedEventArgs(null)); } }
    private int rank;
    public int Rank => rank;
    internal void SetRank(int value) { if(rank!=value){rank=value;PropertyChanged?.Invoke(this,new PropertyChangedEventArgs(nameof(Rank)));} }
    public string Id => Entry.Text("owner_id");
    public string Name => Entry.Text("name");
    public string CoverageMarker => MetricCoverage.HasPartial(Entry.Get("coverage")) ? "*" : "";
    public Visibility CoverageMarkerVisibility => CoverageMarker.Length>0 ? Visibility.Visible : Visibility.Collapsed;
    public string DisplayName => CoverageMarker.Length>0 ? CoverageMarker+" "+Name : Name;
    public string Initial => Name.Length > 0 ? Name[..1] : "?";
    public string Total => Entry.Text("display_value");
    public string CumulativeTotal => Entry.Text("cumulative_display_value") is { Length: >0 } total ? total : Total;
    public string Rate => Entry.Text("rate_text");
    public string CompactRate => Rate.Length > 0 ? Rate : "—";
    public string Share => Entry.Text("share_display");
    public string MetricCategory { get; internal set; } = "";
    internal SharePresentation ShareInfo { get; set; }=new("占比","",false);
    public string Presence => Entry.Bool("portrait_muted") ? "已离场" : (Entry.Bool("incomplete") ? "部分覆盖" : "");
    internal string CoverageDescription => MetricCoverage.Summary(Entry.Get("coverage"));
    public string BarTooltip
    {
        get
        {
            var detail=Entry.Get("detail");var types=detail.Get("damage_by_type");
            string Value(string key) => types.ValueKind==JsonValueKind.Object ? types.UiInt(key) : "—";
            string over=detail.UiInt("overkill_damage");
            return "物理 "+Value("physical")+Environment.NewLine
                +"法术 "+Value("arts")+Environment.NewLine
                +"元素 "+Value("elemental")+Environment.NewLine
                +"真实 "+Value("true")+Environment.NewLine
                +"过量 "+over+CoverageSuffix;
        }
    }
    public string RankedBarTooltip
    {
        get
        {
            string value = Json.UiText(Entry.Text("exact_value"));
            string description=MetricCategory switch
            {
                "healing" => "治疗排行原值 " + value,
                "shield" => "护盾吸收排行原值 " + value,
                _ => string.Join(Environment.NewLine, Entry.Get("segments").Items().Select(segment =>
                    (segment.Text("kind") switch
                    {
                        "physical" => "物理", "arts" => "法术", "elemental" => "元素", "true" => "真实", _ => "其他",
                    }) + " " + segment.Ui("value")
                       + (segment.Get("overkill").ValueKind == JsonValueKind.Number ? "（过量 " + segment.Ui("overkill") + "）" : ""))
                    .Prepend("伤害排行原值 " + value)),
            };
            return description+CoverageSuffix;
        }
    }
    private string CoverageSuffix => CoverageDescription is { Length:>0 } note ? Environment.NewLine+note : "";
    public string Tooltip => Name + Environment.NewLine + "排行原值 " + Json.UiText(Entry.Text("exact_value"))
        + Environment.NewLine + Entry.Text("rate_label") + "速率 " + Entry.Ui("rate")
        + (Entry.Text("rate_note").Length > 0 ? "（" + Entry.Text("rate_note") + "）" : "")
        + Environment.NewLine + ShareInfo.Label + " " + Share
        + (ShareInfo.Details.Length>0 ? Environment.NewLine + ShareInfo.Details : "")
        + Environment.NewLine + "过量伤害 " + Entry.Get("detail").Ui("overkill_damage") + CoverageSuffix;
    public ImageSource? Portrait => Portraits.Get(Id, Entry.Bool("portrait_muted"));
    public double PortraitOpacity => Entry.Bool("portrait_muted") ? .65 : 1;
}

internal static class Portraits
{
    private static JsonElement manifest;
    private static BitmapImage? normal, muted;
    private static readonly Dictionary<string,ImageSource> cache = new();
    private static bool loaded;
    public static ImageSource? Get(string id, bool gray)
    {
        if (!loaded)
        {
            loaded=true;
            try
            {
                var path=Path.Combine(App.Root,"assets","portraits");
                using var doc=JsonDocument.Parse(File.ReadAllText(Path.Combine(path,"manifest.json")));
                manifest=doc.RootElement.Clone(); normal=Load(Path.Combine(path,"normal.png")); muted=Load(Path.Combine(path,"muted.png"));
            }
            catch (Exception ex) when (ex is IOException or JsonException or NotSupportedException) { return null; }
        }
        var metadata=manifest.Get("portraits").Get(id);
        if (metadata.ValueKind != JsonValueKind.Object || normal==null || muted==null) return null;
        string key=id+gray;
        if (!cache.TryGetValue(key,out var image))
        {
            int index=(int)metadata.Number("index"), cell=(int)manifest.Number("cell_size"), cols=(int)manifest.Number("columns");
            image=new CroppedBitmap(gray ? muted : normal,new Int32Rect(index%cols*cell,index/cols*cell,cell,cell)); image.Freeze(); cache[key]=image;
        }
        return image;
    }
    private static BitmapImage Load(string path)
    {
        var image=new BitmapImage(); image.BeginInit(); image.CacheOption=BitmapCacheOption.OnLoad; image.UriSource=new Uri(path); image.EndInit(); image.Freeze(); return image;
    }
}

public sealed class ContributionBar : FrameworkElement
{
    private static readonly DrawingBrush OverkillHatch=CreateOverkillHatch();
    private static DrawingBrush CreateOverkillHatch()
    {
        var group=new DrawingGroup();
        var dark=new Pen(new SolidColorBrush(Color.FromArgb(150,0,0,0)),1);
        var light=new Pen(new SolidColorBrush(Color.FromArgb(150,255,255,255)),1);
        group.Children.Add(new GeometryDrawing(null,dark,new LineGeometry(new Point(0,6),new Point(6,0))));
        group.Children.Add(new GeometryDrawing(null,light,new LineGeometry(new Point(1,7),new Point(7,1))));
        var brush=new DrawingBrush(group){TileMode=TileMode.Tile,Viewport=new Rect(0,0,6,6),ViewportUnits=BrushMappingMode.Absolute,Stretch=Stretch.None};
        brush.Freeze(); return brush;
    }
    public static readonly DependencyProperty DataProperty=DependencyProperty.Register(nameof(Data),typeof(JsonElement),typeof(ContributionBar),new FrameworkPropertyMetadata(default(JsonElement),FrameworkPropertyMetadataOptions.AffectsRender));
    public JsonElement Data { get => (JsonElement)GetValue(DataProperty); set => SetValue(DataProperty,value); }
    public static readonly DependencyProperty MetricCategoryProperty = DependencyProperty.Register(
        nameof(MetricCategory), typeof(string), typeof(ContributionBar),
        new FrameworkPropertyMetadata("", FrameworkPropertyMetadataOptions.AffectsRender));
    public string MetricCategory
    {
        get => (string)GetValue(MetricCategoryProperty);
        set => SetValue(MetricCategoryProperty, value);
    }
    protected override void OnRender(DrawingContext dc)
    {
        base.OnRender(dc);
        double height=Theme.IsBabel ? Math.Min(5,ActualHeight) : ActualHeight;
        double top=(ActualHeight-height)/2;
        double radius=Theme.IsBabel ? 0 : 1;
        var track=Theme.Resource("ContributionTrack");
        dc.DrawRoundedRectangle(track,null,new Rect(0,top,ActualWidth,height),radius,radius);
        double width=ActualWidth*Math.Clamp(Data.Number("bar_ratio"),0,1), x=0;
        var segments=Data.Get("segments").Items().ToArray();
        if (segments.Length==0)
        {
            var color=Data.Text("bar_color");
            var fill = Theme.IsPeople || Theme.IsBabel ? Theme.Resource(MetricCategory switch
            {
                "healing" => "Heal",
                "shield" => "Shield",
                _ => Theme.IsBabel ? "Physical" : "Accent",
            }) : color.Length > 0 ? Theme.Brush(color) : Theme.Resource("Accent");
            dc.DrawRoundedRectangle(fill,null,new Rect(0,top,width,height),radius,radius); return;
        }
        foreach(var segment in segments)
        {
            double size=Math.Min(width-x,width*Math.Clamp(segment.Number("ratio"),0,1));
            if(size>0)
            {
                double body=size;
                dc.DrawRectangle(SegmentBrush(segment),null,new Rect(x,top,body,height));
                if(segment.TryGetProperty("overkill_ratio",out var mark) && mark.ValueKind==JsonValueKind.Number)
                {
                    double ratio=Math.Clamp(mark.GetDouble(),0,1), overkill=body*ratio;
                    if(overkill>0)
                    {
                        double start=x+body-overkill;
                        dc.DrawRectangle(OverkillHatch,null,new Rect(start,top,overkill,height));
                        var pen=new Pen(Theme.Resource("Signal"),1){DashStyle=DashStyles.Dash};
                        pen.Freeze();
                        dc.DrawLine(pen,new Point(start,top),new Point(start,top+height));
                    }
                }
            }
            x+=size;
        }
        if (x<width) dc.DrawRectangle(track,null,new Rect(x,top,width-x,height));
    }
    private static Brush SegmentBrush(JsonElement segment) => segment.Text("kind") switch
    {
        "physical"=>Theme.Resource("Physical"),"arts"=>Theme.Resource("Arts"),
        "elemental"=>Theme.Resource("Elemental"),"true"=>Theme.Resource("True"),
        _=>Theme.Resource("Accent")
    };
}

/// <summary>Decorative orbital geometry from the reference, never a data chart.</summary>
public sealed class OrbitalSurface : FrameworkElement
{
    protected override void OnRender(DrawingContext dc)
    {
        var pen=new Pen(Theme.Resource("ConsoleLine"),.5); pen.Freeze();
        var center=new Point(ActualWidth*.70,ActualHeight*.45);
        dc.PushClip(new RectangleGeometry(new Rect(RenderSize)));
        dc.DrawEllipse(null,pen,center,ActualWidth*.37,ActualHeight*.31);
        dc.PushTransform(new RotateTransform(-24,center.X,center.Y));
        dc.DrawEllipse(null,pen,center,ActualWidth*.48,ActualHeight*.22); dc.Pop();
        dc.DrawLine(pen,new Point(center.X-9,center.Y),new Point(center.X+9,center.Y));
        dc.DrawLine(pen,new Point(center.X,center.Y-9),new Point(center.X,center.Y+9));
        dc.Pop();
    }
}

public partial class StatisticPanel : UserControl
{
    internal Func<string,Dictionary<string,object?>?,Task>? Command;
    internal Action<string>? HudRequested, PinRequested, RateActionRequested;
    internal Action? CloseRequested;
    private readonly ObservableCollection<RowItem> items=new();
    private JsonElement view;
    private bool updating, hold;
    private bool configurationAvailable=true;
    private bool deleteAvailable;
    private bool referenceStyle, liveToolbar, sideRanking;
    private string? appliedHudSkin;
    private HudLayout? hudLayout;
    private double hudViewportLimit=double.PositiveInfinity;
    private const double ReferenceRowsDefaultHeight=185;
    public const double HudRowHeight=HudLayout.DefaultRowHeight;
    internal const int HudVisibleRows=5;
    internal bool IsHistory, IsHud;
    public string PanelId => view.Get("config").Text("panel_id");
    internal JsonElement View => view;
    public StatisticPanel() { InitializeComponent(); Rows.ItemsSource=items; }

    internal static bool IsBossScope(JsonElement config) =>
        config.Text("category")=="damage" && config.Bool("boss_only");

    internal void ConfigureSideRanking()
    {
        sideRanking = true;
        MinWidth = Plot.MinHeight = Rows.MinHeight = 0;
        Rows.MaxHeight = double.PositiveInfinity;
        Rows.SetValue(ScrollViewer.VerticalScrollBarVisibilityProperty, ScrollBarVisibility.Auto);
        VirtualizingPanel.SetVirtualizationMode(Rows, VirtualizationMode.Recycling);
        Rows.ItemContainerStyle = (Style)FindResource("SideRankedRow");
        AutomationProperties.SetAutomationId(Rows, "SideDpsRanking");
        AutomationProperties.SetName(Rows, "干员排行");
        PanelFrame.Margin = new Thickness(0);
        PanelFrame.SetResourceReference(Border.BackgroundProperty, "Console");
        PanelFrame.SetResourceReference(Border.BorderBrushProperty, "ConsoleLine");
        TitleRegion.SetResourceReference(Border.BackgroundProperty, "ConsoleCard");
        Heading.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleInk");
        Heading.FontSize = 15;
        Heading.TextWrapping = TextWrapping.NoWrap;
        Heading.TextTrimming = TextTrimming.CharacterEllipsis;
        TitleBar.Margin = new Thickness(8, 6, 8, 5);
        RateNoticePanel.Margin = new Thickness(5, 0, 5, 4);
        RateNoticePanel.Padding = new Thickness(5, 3, 5, 3);
        LegendRow.Margin = new Thickness(8, 0, 8, 0);
        foreach (var label in Legend.Children.OfType<TextBlock>())
            label.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleMuted");
        Empty.Margin = new Thickness(8, 4, 8, 4);
        Empty.SetResourceReference(TextBlock.ForegroundProperty, "ConsoleMuted");
        Update(default);
    }

    internal void RestoreSideSelection(string? owner, bool scrollToTop)
    {
        if (scrollToTop && items.Count > 0) Rows.ScrollIntoView(items[0]);
        if (owner is null || Rows.SelectedItem is not null) return;
        var row = items.FirstOrDefault(item => item.Id == owner);
        if (row is null) return;
        updating = true;
        Rows.SelectedItem = row;
        updating = false;
    }

    internal void ConfigureHud(Action? close=null)
    {
        if(IsHud) return;
        IsHud=true;
        SetReferenceStyle(false);
        CloseRequested=close;
        MinWidth=0;
        PanelFrame.Margin=new Thickness(0);
        PanelFrame.BorderThickness=new Thickness(0);
        LegendRow.Visibility=Visibility.Collapsed;
        TitleRegion.ClipToBounds=true;
        Heading.SetResourceReference(TextBlock.FontFamilyProperty,"InterfaceFont");
        Heading.TextTrimming=TextTrimming.CharacterEllipsis;
        PanelKind.SetResourceReference(TextBlock.FontFamilyProperty,"InterfaceFont");
        PanelKind.VerticalAlignment=VerticalAlignment.Center;
        PanelKind.TextTrimming=TextTrimming.CharacterEllipsis;
        Grid.SetRow(PanelKind,0);
        Grid.SetColumn(PanelKind,1);
        HudClose.MinWidth=44;
        HudClose.MinHeight=20;
        HudClose.Margin=new Thickness(0);
        TitleAccent.Height=23;
        LoneTrailHudMark.Height=23;
        LoneTrailHudMark.Width=14;
        Plot.MinHeight=0;
        Rows.Padding=new Thickness(0);
        Rows.ItemContainerStyle=(Style)FindResource("HudRankedRow");
        Rows.Template=(ControlTemplate)FindResource("HudRowsTemplate");
        Empty.MaxWidth=440;
        Empty.Margin=new Thickness(0);
        ApplyHudLayout(HudLayout.Default);
        ApplyHudSkin();
    }

    internal void ApplyHudLayout(HudLayout layout)
    {
        if(hudLayout==layout) return;
        hudLayout=layout;
        TitleRegion.Height=layout.TitleHeight;
        TitleBar.Margin=new Thickness(layout.Padding+6,2,layout.Padding,2);
        Heading.FontSize=layout.TitleFont;
        PanelKind.FontSize=Math.Max(6,layout.NumberFont-1);
        PanelKind.Margin=new Thickness(layout.ColumnGap+14,0,0,0);
        HudClose.MinHeight=Math.Max(16,layout.TitleHeight-4);
        Empty.FontSize=layout.NumberFont;
        Set("HudProfileRowHeight",layout.RowHeight);
        Set("HudProfileRowGap",new Thickness(0,0,0,layout.RowGap));
        Set("HudProfileRowPadding",new Thickness(0,0,2*layout.Padding,0));
        Set("HudProfileNameGap",new Thickness(layout.ColumnGap,0,layout.ColumnGap,0));
        Set("HudProfileBarGap",new Thickness(layout.ColumnGap/2,0,layout.ColumnGap*1.5,0));
        Set("HudProfileValueGap",new Thickness(layout.ColumnGap,0,0,0));
        Set("HudProfileNameFont",layout.NameFont);
        Set("HudProfileNumberFont",layout.NumberFont);
        Set("HudProfilePortraitSize",layout.PortraitSize);
        SetColumn("HudProfileRankWidth",layout.RankWidth);
        SetColumn("HudProfilePortraitWidth",layout.PortraitWidth);
        SetColumn("HudProfileNameWidth",layout.NameWidth);
        SetColumn("HudProfileBarWidth",layout.BarWidth);
        SetColumn("HudProfileTotalWidth",layout.TotalWidth);
        SetColumn("HudProfileRateWidth",layout.RateWidth);
        SetColumn("HudProfileShareWidth",layout.ShareWidth);
        ResizeHudRows(items.Count);

        void SetColumn(string key,double width) =>
            Set(key,width==0 ? new GridLength(0) : new GridLength(width,GridUnitType.Star));
        void Set(string key,object value)
        {
            if(!Equals(Resources[key],value)) Resources[key]=value;
        }
    }

    internal void LimitHudViewport(double limit)
    {
        limit=Math.Max(0,limit);
        if(hudViewportLimit==limit) return;
        hudViewportLimit=limit;
        ResizeHudRows(items.Count);
    }

    private void ResizeHudRows(int count)
    {
        var layout=hudLayout??HudLayout.Default;
        Rows.MinHeight=Math.Min(layout.RowPitch,hudViewportLimit);
        Rows.MaxHeight=Math.Min(layout.RowPitch*HudVisibleRows,hudViewportLimit);
        Rows.Height=layout.RowPitch*Math.Clamp(count,1,HudVisibleRows);
    }

    private void ApplyHudSkin()
    {
        if (appliedHudSkin == Theme.Key) return;
        appliedHudSkin = Theme.Key;
        bool loneTrail = Theme.Key == Theme.LoneTrail;
        bool babel = Theme.IsBabel;

        PanelFrame.SetResourceReference(Border.BackgroundProperty, loneTrail ? "Console" : "Card");
        TitleAccent.Visibility = loneTrail || babel ? Visibility.Collapsed : Visibility.Visible;
        LoneTrailHudMark.Visibility = loneTrail ? Visibility.Visible : Visibility.Collapsed;
        LoneTrailHudBackdrop.Visibility = LoneTrailHudChevrons.Visibility = LoneTrailHudMark.Visibility;
        PeopleHudArtwork.Visibility = Theme.IsPeople ? Visibility.Visible : Visibility.Collapsed;
        BabelHudMark.Visibility = BabelHudRule.Visibility = babel ? Visibility.Visible : Visibility.Collapsed;
        TitleRegion.BorderThickness = new Thickness(0, 0, 0, 1);
        TitleRegion.SetResourceReference(Border.BackgroundProperty, loneTrail ? "ConsoleCard" : babel ? "Console" : "Inset");
        TitleRegion.SetResourceReference(Border.BorderBrushProperty, loneTrail ? "ConsoleLine" : "Line");
        HudClose.Background = Brushes.Transparent;
        if(loneTrail || babel)
        {
            PanelFrame.CornerRadius=new CornerRadius(0);
            HudClose.SetResourceReference(StyleProperty,"Quiet");
            HudClose.BorderBrush=Brushes.Transparent;
        }
        else
        {
            PanelFrame.SetResourceReference(Border.CornerRadiusProperty,"CardRadius");
            HudClose.ClearValue(StyleProperty);
            HudClose.SetResourceReference(Control.BorderBrushProperty,"Line");
        }
    }

    internal void SetReferenceStyle(bool enable)
    {
        bool showLiveToolbar=!IsHistory&&!IsHud;
        enable=enable&&showLiveToolbar;
        if(referenceStyle==enable && liveToolbar==showLiveToolbar) return;
        referenceStyle=enable;
        liveToolbar=showLiveToolbar;
        MinWidth=enable ? 0 : 400;
        PanelFrame.SetResourceReference(Border.BackgroundProperty,enable ? "Console" : "Card");
        PanelFrame.Margin=enable ? new Thickness(0) : new Thickness(0,0,12,14);
        PanelTitle.Visibility=enable ? Visibility.Collapsed : Visibility.Visible;
        TitleRegion.Visibility=enable ? Visibility.Collapsed : Visibility.Visible;
        TitleBar.Margin=enable ? new Thickness(5,3,5,2) : new Thickness(14,10,12,8);
        Selectors.Margin=enable ? new Thickness(5,0,5,4) : new Thickness(14,0,14,8);
        RateNoticePanel.Margin=enable ? new Thickness(5,0,5,3) : new Thickness(14,0,14,8);
        RateNoticePanel.Padding=enable ? new Thickness(5,2,5,2) : new Thickness(10,7,10,7);
        Coverage.Margin=enable ? new Thickness(5,2,5,3) : new Thickness(14,8,14,9);
        Coverage.FontSize=enable ? 12 : 10;
        Coverage.SetResourceReference(TextBlock.ForegroundProperty,enable ? "ConsoleMuted" : "Muted");
        Empty.SetResourceReference(TextBlock.ForegroundProperty,enable ? "ConsoleMuted" : "Muted");
        Plot.MinHeight=enable ? 0 : 120;
        Rows.MinHeight=enable ? 74 : 80;
        Rows.MaxHeight=enable ? ReferenceRowsDefaultHeight : 520;
        Rows.ItemContainerStyle=(Style)FindResource(enable ? "ReferenceRankedRow" : "RankedRow");
        LegendRow.Visibility=enable ? Visibility.Collapsed : Visibility.Visible;
        foreach(var label in new[]{CategoryLabel,RankingLabel,RateLabel})
            label.SetResourceReference(TextBlock.ForegroundProperty,enable ? "ConsoleMuted" : "Muted");
        StyleSelector(Category,CategoryField,CategoryLabel,liveToolbar,72,88);
        StyleSelector(Ranking,RankingField,RankingLabel,liveToolbar,64,96);
        StyleSelector(Rate,RateField,RateLabel,liveToolbar,68,104);
        if(enable)
        {
            RateAction.SetResourceReference(StyleProperty,"ReferencePanelAction");
            RateAction.ClearValue(FontSizeProperty);
            RateAction.ClearValue(PaddingProperty);
            RateAction.ClearValue(BackgroundProperty);
        }
        else
        {
            RateAction.ClearValue(StyleProperty);
            RateAction.SetResourceReference(BackgroundProperty,"Card");
            RateAction.Padding=new Thickness(9,4,9,4);
        }
        if(view.ValueKind==JsonValueKind.Object) Update(view,deleteAvailable);
    }

    private void ReferenceScrollChanged(object sender, ScrollChangedEventArgs e)
    {
        if(!(referenceStyle || sideRanking) || e.OriginalSource is not ScrollViewer viewer) return;
        // Keep the fixed header and footer on the same content width as the scrolling rows.
        var gutter = new Thickness(0,0,Math.Max(0,viewer.ActualWidth-viewer.ViewportWidth),0);
        ReferenceTableHeader.Margin=gutter;
        ReferenceFooter.Margin=gutter;
        if(sideRanking) SideHeader.Margin=gutter;
    }

    internal void FitRankingRows(double availableHeight)
    {
        if (!referenceStyle) return;
        double chrome = Math.Max(0, ActualHeight - Rows.ActualHeight);
        double rowLimit = Math.Clamp(availableHeight - chrome, ReferenceRowsDefaultHeight, 520);
        if (Math.Abs(Rows.MaxHeight - rowLimit) >= 1) Rows.MaxHeight = rowLimit;
    }

    private static void StyleSelector(ComboBox combo, StackPanel field, TextBlock label,
        bool compact, double compactWidth, double standardWidth)
    {
        combo.Width=compact ? compactWidth : standardWidth;
        if(compact)
        {
            combo.Height=combo.MinHeight=24;
            combo.FontSize=11;
            combo.Margin=new Thickness(0);
            combo.Padding=new Thickness(6,4,22,4);
            label.Margin=new Thickness(0,0,0,2);
        }
        else
        {
            combo.ClearValue(HeightProperty);
            combo.ClearValue(MinHeightProperty);
            combo.ClearValue(FontSizeProperty);
            combo.ClearValue(MarginProperty);
            combo.ClearValue(PaddingProperty);
            label.ClearValue(MarginProperty);
        }
        field.Margin=compact ? new Thickness(0,0,3,3) : new Thickness(0,0,8,0);
    }

    internal void Update(JsonElement value, bool canDelete=true)
    {
        view=value; updating=true;
        var config=value.Get("config");
        var share=SharePresentation.From(value);
        bool bossScope=IsBossScope(config);
        bool bossUnavailable=bossScope && !value.Bool("boss_damage_available");
        string bossReason=bossScope ? value.Text("boss_damage_reason") : "";
        if(bossUnavailable && bossReason.Length==0) bossReason="首领伤害数据不可用。";
        if(IsHud) ApplyHudSkin();
        Heading.Text=IsHud ? HudTitle(config) : value.Text("title");
        var knownShare=value.Text("share_scope")=="known";
        PanelKind.Text=IsHud ? share.Label : IsHistory ? "HISTORY PANEL" : "LIVE PANEL";
        PanelKind.Visibility=sideRanking||referenceStyle||IsHud&&!knownShare ? Visibility.Collapsed : Visibility.Visible;
        PanelKind.ToolTip=IsHud ? share.Description : null;
        Heading.ToolTip=!IsHud ? null : bossScope
            ? string.Join("\n",new[]{Heading.Text,bossReason,share.Description}.Where(text=>text.Length>0))
            : share.Description;
        HudClose.Visibility=IsHud && CloseRequested!=null ? Visibility.Visible : Visibility.Collapsed;
        Rows.ItemTemplate=(DataTemplate)FindResource(sideRanking ? "SideRow" : IsHud ? "HudRow" : referenceStyle ? "ReferenceRow" : "FullRow");
        TableHeader.Visibility=sideRanking||IsHud||referenceStyle ? Visibility.Collapsed : Visibility.Visible;
        ReferenceTableHeader.Visibility=referenceStyle ? Visibility.Visible : Visibility.Collapsed;
        Select(Category,config.Text("category")); Select(Ranking,config.Text("ranking")); Select(Rate,config.Text("rate_basis"));
        var category=config.Text("category");
        if(category=="shield") Select(Ranking,"total");
        ((ComboBoxItem)Ranking.Items[0]).Content=category=="healing" ? "总治疗" : category=="shield" ? "总护盾" : "总伤";
        ((ComboBoxItem)Ranking.Items[1]).Content=category=="shield" ? "近期" : "DPS";
        EffectiveOnly.IsChecked=config.Bool("effective_only");
        EffectiveOnly.Visibility=!IsHistory&&!IsHud&&config.Text("category")=="damage"
            ? Visibility.Visible : Visibility.Collapsed;
        BossOnly.IsChecked=config.Bool("boss_only");
        BossOnly.Visibility=!IsHud && category=="damage" ? Visibility.Visible : Visibility.Collapsed;
        // The engagement column depends on event observation. Never leave it as a
        // silently blank column: show the reason and a retry/settings entry while
        // still allowing a switch back to the five-second window.
        var rateBasis=config.Text("rate_basis");
        var isEngagement=category!="shield" && rateBasis=="engagement";
        var reason=value.Text("rate_reason");
        var action=isEngagement && !bossUnavailable ? value.Text("rate_action") : "";
        RateNotice.Text=string.Join(" · ",new[]{IsHud && bossUnavailable ? "" : bossReason,
            isEngagement && !bossUnavailable ? reason : ""}
            .Where(text=>text.Length>0).Distinct());
        RateNotice.Visibility=RateNotice.Text.Length>0 ? Visibility.Visible : Visibility.Collapsed;
        RateNoticePanel.Visibility=RateNotice.Visibility;
        RateAction.Content=value.Text("rate_action_label");
        RateAction.Visibility=!sideRanking && isEngagement && action.Length>0 && value.Text("rate_action_label").Length>0 ? Visibility.Visible : Visibility.Collapsed;
        RateHeading.Text=category switch{"healing"=>"HPS","shield"=>"—",_=>"DPS"};
        RankingLabel.ToolTip=category=="shield"
            ? "护盾按实际吸收总量排序。"
            : "按累计量或每秒速率，从高到低排序。";
        RateLabel.Text=category=="healing" ? "HPS 口径" : "DPS 口径";
        RateLabel.ToolTip=category=="shield"
            ? "护盾只统计实际吸收总量。"
            : "近 5 秒看最近输出，接敌按交战活跃时间，全周期按在场时间。";
        Rate.ToolTip=isEngagement && reason.Length>0 ? reason
            : rateBasis=="full_cycle" ? "全周期速率 = 累计量 / 在场时间（自本次观察起）"
            : RateLabel.ToolTip;
        ReferenceRateHeading.Text=RateHeading.Text;
        ReferenceTotalHeading.Text=config.Text("ranking")=="recent" ? "近期"
            : category switch{"healing"=>"总治疗","shield"=>"总护盾",_=>"总伤"};
        if(sideRanking) UpdateSideHeadings(config,category,value.Text("title"));
        Actions.Visibility=IsHistory||IsHud ? Visibility.Collapsed : Visibility.Visible;
        Selectors.Visibility=sideRanking||IsHud ? Visibility.Collapsed : Visibility.Visible;
        deleteAvailable=canDelete;
        SetConfigurationAvailable(configurationAvailable);
        Legend.Visibility=!IsHud && !referenceStyle && category=="damage" ? Visibility.Visible : Visibility.Collapsed;
        LegendRow.Visibility=IsHud || referenceStyle || (sideRanking || liveToolbar) && category!="damage" ? Visibility.Collapsed : Visibility.Visible;
        LegendRow.MinHeight=sideRanking||IsHud ? 0 : 28;
        OverkillLegend.Visibility=value.Get("entries").Items().Any(entry=>entry.Get("segments").Items().Any(segment=>
            segment.TryGetProperty("overkill_ratio",out var mark)&&mark.ValueKind==JsonValueKind.Number&&mark.GetDouble()>0))
            ? Visibility.Visible : Visibility.Collapsed;
        var incoming=value.Get("entries").Items().ToArray();
        if(IsHud) ResizeHudRows(incoming.Length);
        ShareHeading.ToolTip=share.Description;
        ReferenceShareHeading.ToolTip=share.Description;
        var hasFooterTotal=value.Get("footer_total").ValueKind==JsonValueKind.Number;
        var hasFooterRate=value.Get("footer_rate").ValueKind==JsonValueKind.Number;
        var showFooter=referenceStyle && (incoming.Length>0 || hasFooterTotal || hasFooterRate);
        ReferenceFooter.Visibility=showFooter ? Visibility.Visible : Visibility.Collapsed;
        if(showFooter)
        {
            var totalComplete=value.Bool("footer_total_complete");
            var rateComplete=value.Bool("footer_rate_complete");
            ReferenceFooterLabel.Text=totalComplete && (!hasFooterRate || rateComplete) ? "全队合计" : "已知合计";
            ReferenceFooterTotal.Text=value.Text("footer_total_display");
            ReferenceFooterTotal.ToolTip=hasFooterTotal
                ? (totalComplete ? "全队" : "已知干员")+ReferenceTotalHeading.Text+" "+value.Ui("footer_total")
                : null;
            ReferenceFooterRate.Text=value.Text("footer_rate_display");
            ReferenceFooterRate.ToolTip=hasFooterRate
                ? (rateComplete ? "全队" : "已知")+"干员"+value.Text("rate_label")+"之和 "+value.Ui("footer_rate")+"/s"
                : rateBasis=="engagement" && reason.Length>0 ? reason : null;
        }
        Coverage.ToolTip=referenceStyle ? share.Description : null;
        Coverage.Text=referenceStyle
            ? showFooter && !value.Bool("share_known") ? share.Label : ""
            : share.Description+(share.Available ? " · 点击干员固定明细" : "");
        Coverage.Visibility=sideRanking||IsHud||referenceStyle&&Coverage.Text.Length==0 ? Visibility.Collapsed : Visibility.Visible;
        var partial=incoming.Where(entry=>MetricCoverage.HasPartial(entry.Get("coverage"))).ToArray();
        if(partial.Length>0)
        {
            string marker=IsHud ? "* 部分统计" : "* 部分统计 · 查看统计失败占比";
            const string hint="悬浮在带 * 的排行行上，查看各项的统计失败占比与统计口径。";
            if(IsHud)
            {
                PanelKind.Text=knownShare ? share.Label+" · "+marker : marker;
                PanelKind.ToolTip=share.Description+"\n"+hint;
                PanelKind.Visibility=Visibility.Visible;
            }
            else
            {
                Coverage.Text=string.Join(" · ",new[]{Coverage.Text,marker}.Where(text=>text.Length>0));
                Coverage.ToolTip=share.Description+"\n"+hint;
                Coverage.Visibility=Visibility.Visible;
            }
        }
        var ids=incoming.Select(e=>e.Text("owner_id")).ToHashSet();
        foreach(var old in items.Where(x=>!ids.Contains(x.Id)).ToArray()) items.Remove(old);
        var map=items.ToDictionary(x=>x.Id);
        foreach(var entry in incoming)
        {
            if(map.TryGetValue(entry.Text("owner_id"),out var item))
            {
                item.ShareInfo=share;
                item.MetricCategory=category;
                item.Entry=entry;
            }
            else items.Add(new RowItem{Entry=entry,Compact=IsHud,ShareInfo=share,MetricCategory=category});
        }
        if(!hold)
            for(int i=0;i<incoming.Length;i++)
            {
                int index=items.ToList().FindIndex(x=>x.Id==incoming[i].Text("owner_id"));
                if(index!=i) items.Move(index,i);
            }
        for(int i=0;i<items.Count;i++) items[i].SetRank(i+1);
        Empty.Text=bossUnavailable ? bossReason
            : sideRanking && value.ValueKind!=JsonValueKind.Object ? "等待战斗数据" : value.Text("empty_text");
        Empty.Visibility=items.Count==0 && (!bossUnavailable || IsHud) ? Visibility.Visible : Visibility.Collapsed;
        updating=false;
    }

    private void UpdateSideHeadings(JsonElement config,string category,string title)
    {
        string rate = category == "healing" ? "HPS" : "DPS";
        string basis = config.Text("rate_basis") switch
        {
            "recent_5s" => "近 5 秒", "engagement" => "接敌", "full_cycle" => "全周期", _ => "",
        };
        string total = category switch
        {
            "healing" => "总治疗", "shield" => "总护盾", _ when config.Bool("effective_only") => "有效伤害", _ => "总伤",
        };
        string rank = config.Text("ranking") switch
        {
            "dps" => rate, "recent" => "近期量", _ => total,
        };
        string scope = IsBossScope(config) ? "仅首领 · " : "";
        Heading.Text = title.Length == 0 ? "干员排行" : scope + "按" + rank + "排行" + (category == "shield" ? "" : " · " + basis + " " + rate);
        Heading.ToolTip = title.Length == 0 ? null : title;
        SideHeader.Visibility = Visibility.Visible;
        SideTotalHeading.Text = total;
        SideRateHeading.Text = category == "shield" ? "—" : rate;
        SideRateHeading.ToolTip = category == "shield" ? "护盾按实际吸收量统计。" : basis + " " + rate;
    }

    private static string HudTitle(JsonElement config)
    {
        string category=config.Text("category"), ranking=config.Text("ranking");
        bool recent=ranking=="recent";
        string metric=category switch
        {
            "shield"=>"有效护盾",
            "healing"=>recent ? "治疗" : "总治疗",
            _ when config.Bool("effective_only")=>"有效伤害",
            _=>"溢出总伤",
        };
        if(recent) metric="近5秒"+metric;
        if(IsBossScope(config)) metric="仅首领·"+metric;
        if(category=="shield") return metric;

        string window=config.Text("rate_basis") switch
        {
            "engagement"=>"接敌",
            "full_cycle"=>"全周期",
            _=>"近5秒",
        };
        return metric+"·"+(ranking=="dps" ? "按" : "")+window+(category=="healing" ? "HPS" : "DPS");
    }

    private static void Select(ComboBox box,string tag)
    {
        foreach(ComboBoxItem item in box.Items) if((string)item.Tag==tag) {box.SelectedItem=item;return;}
        // Old v1 layouts used "recent"; the UI now exposes it as the DPS basis.
        if(tag=="recent") foreach(ComboBoxItem item in box.Items)
            if((string)item.Tag=="dps") {box.SelectedItem=item;return;}
    }
    internal Dictionary<string,object?> Configuration() => new()
    {
        ["panel_id"]=PanelId,
        ["category"]=((ComboBoxItem)Category.SelectedItem).Tag,
        ["ranking"]=((ComboBoxItem)Ranking.SelectedItem).Tag,
        ["rate_basis"]=Category.SelectedIndex==2 ? "recent_5s" : ((ComboBoxItem)Rate.SelectedItem).Tag,
        ["effective_only"]=EffectiveOnly.IsChecked==true&&Category.SelectedIndex==0,
        ["boss_only"]=BossOnly.IsChecked==true
    };
    internal void SetConfigurationAvailable(bool available)
    {
        bool restoreSelection=configurationAvailable && !available && !updating;
        configurationAvailable=available;
        var config=view.Get("config");
        bool hasRate=config.Text("category")!="shield";
        if(restoreSelection && config.ValueKind==JsonValueKind.Object)
        {
            updating=true;
            try
            {
                Select(Category,config.Text("category"));
                Select(Ranking,config.Text("ranking"));
                Select(Rate,config.Text("rate_basis"));
                EffectiveOnly.IsChecked=config.Bool("effective_only");
                BossOnly.IsChecked=config.Bool("boss_only");
            }
            finally{updating=false;}
        }
        Category.IsEnabled=EffectiveOnly.IsEnabled=BossOnly.IsEnabled=DuplicateButton.IsEnabled=available;
        Ranking.IsEnabled=Rate.IsEnabled=available && hasRate;
        DeleteButton.IsEnabled=available && deleteAvailable;
    }
    private async void ConfigurationChanged(object sender,SelectionChangedEventArgs e) => await CommitConfiguration();
    private async void EffectiveChanged(object sender,RoutedEventArgs e) => await CommitConfiguration();
    private async void FilterChanged(object sender,RoutedEventArgs e) => await CommitConfiguration();
    private async Task CommitConfiguration()
    {
        if(updating||!configurationAvailable||Command==null||Category.SelectedItem==null||Ranking.SelectedItem==null||Rate.SelectedItem==null) return;
        await Command(IsHistory ? "history_config" : "panel_update",new(){["config"]=Configuration()});
    }
    private async void Duplicate(object sender,RoutedEventArgs e) {if(Command!=null) await Command("panel_duplicate",new(){["panel_id"]=PanelId});}
    private async void Delete(object sender,RoutedEventArgs e) {if(Command!=null) await Command("panel_delete",new(){["panel_id"]=PanelId});}
    private void OpenHud(object sender,RoutedEventArgs e) => HudRequested?.Invoke(PanelId);
    private void CloseHud(object sender,RoutedEventArgs e) => CloseRequested?.Invoke();
    private void RateActionClicked(object sender,RoutedEventArgs e) => RateActionRequested?.Invoke(view.Text("rate_action"));
    private void RowSelected(object sender,SelectionChangedEventArgs e) {if(!updating && Rows.SelectedItem is RowItem item) PinRequested?.Invoke(item.Id);}
    private void HoldOrder(object sender,MouseEventArgs e) => hold=!sideRanking;
    private void ReleaseOrder(object sender,MouseEventArgs e)
    {
        if(!hold) return;
        hold=false;
        if(view.ValueKind==JsonValueKind.Object) Update(view,deleteAvailable);
    }
    internal void ClearSelection() => Rows.SelectedItem=null;
}
