using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
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
    public string Initial => Name.Length > 0 ? Name[..1] : "?";
    public string Total => Entry.Text("display_value");
    public string Rate => Entry.Text("rate_text");
    public string Share => Entry.Text("share_display");
    public double SharePercent => Math.Clamp(Entry.Number("share"),0,1)*100;
    internal SharePresentation ShareInfo { get; set; }=new("占比","",false);
    public string Presence => Entry.Bool("portrait_muted") ? "已离场" : (Entry.Bool("incomplete") ? "部分覆盖" : "");
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
                +"过量 "+over;
        }
    }
    public string Tooltip => Name + Environment.NewLine + "排行原值 " + Json.UiText(Entry.Text("exact_value"))
        + Environment.NewLine + Entry.Text("rate_label") + "速率 " + Entry.Ui("rate")
        + (Entry.Text("rate_note").Length > 0 ? "（" + Entry.Text("rate_note") + "）" : "")
        + Environment.NewLine + ShareInfo.Label + " " + Share
        + (ShareInfo.Details.Length>0 ? Environment.NewLine + ShareInfo.Details : "")
        + Environment.NewLine + "过量伤害 " + Entry.Get("detail").Ui("overkill_damage");
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
    protected override void OnRender(DrawingContext dc)
    {
        base.OnRender(dc);
        dc.DrawRoundedRectangle(Theme.Resource("Track"),null,new Rect(0,0,ActualWidth,ActualHeight),1,1);
        double width=ActualWidth*Math.Clamp(Data.Number("bar_ratio"),0,1), x=0;
        var segments=Data.Get("segments").Items().ToArray();
        if (segments.Length==0)
        {
            var color=Data.Text("bar_color");
            dc.DrawRoundedRectangle(color.Length>0 ? Theme.Brush(color) : Theme.Resource("Accent"),null,new Rect(0,0,width,ActualHeight),1,1); return;
        }
        foreach(var segment in segments)
        {
            double size=Math.Min(width-x,width*Math.Clamp(segment.Number("ratio"),0,1));
            if(size>0)
            {
                double body=size;
                dc.DrawRectangle(SegmentBrush(segment),null,new Rect(x,0,body,ActualHeight));
                if(segment.TryGetProperty("overkill_ratio",out var mark) && mark.ValueKind==JsonValueKind.Number)
                {
                    double ratio=Math.Clamp(mark.GetDouble(),0,1), overkill=body*ratio;
                    if(overkill>0)
                    {
                        double start=x+body-overkill;
                        dc.DrawRectangle(OverkillHatch,null,new Rect(start,0,overkill,ActualHeight));
                        var pen=new Pen(Theme.Resource("Signal"),1){DashStyle=DashStyles.Dash};
                        pen.Freeze();
                        dc.DrawLine(pen,new Point(start,0),new Point(start,ActualHeight));
                    }
                }
            }
            x+=size;
        }
        if (x<width) dc.DrawRectangle(Theme.Resource("Track"),null,new Rect(x,0,width-x,ActualHeight));
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
    internal Action<string>? HudRequested, PinRequested, LockRequested, RateActionRequested;
    internal Action? CloseRequested;
    internal Action<string,double>? AlphaRequested;
    private readonly ObservableCollection<RowItem> items=new();
    private JsonElement view;
    private bool updating, hold;
    private bool referenceStyle;
    internal bool IsHistory, IsHud;
    public string PanelId => view.Get("config").Text("panel_id");
    internal JsonElement View => view;
    public StatisticPanel() { InitializeComponent(); Rows.ItemsSource=items; }

    internal void SetReferenceStyle(bool enable)
    {
        enable=enable&&!IsHistory&&!IsHud;
        if(referenceStyle==enable) return;
        referenceStyle=enable;
        MinWidth=enable ? 0 : 400;
        PanelFrame.SetResourceReference(Border.BackgroundProperty,enable ? "Console" : "Card");
        PanelFrame.Margin=enable ? new Thickness(0) : new Thickness(0,0,12,14);
        PanelTitle.Visibility=enable ? Visibility.Collapsed : Visibility.Visible;
        TitleBar.Visibility=enable ? Visibility.Collapsed : Visibility.Visible;
        TitleBar.Margin=enable ? new Thickness(5,3,5,2) : new Thickness(14,10,12,8);
        Selectors.Margin=enable ? new Thickness(5,0,5,4) : new Thickness(14,0,14,8);
        RateNoticePanel.Margin=enable ? new Thickness(5,0,5,3) : new Thickness(14,0,14,8);
        RateNoticePanel.Padding=enable ? new Thickness(5,2,5,2) : new Thickness(10,7,10,7);
        HudControls.Margin=enable ? new Thickness(5,2,5,4) : new Thickness(14,0,14,10);
        Coverage.Margin=enable ? new Thickness(5,3,5,4) : new Thickness(14,8,14,9);
        Coverage.TextWrapping=enable ? TextWrapping.NoWrap : TextWrapping.Wrap;
        Coverage.TextTrimming=enable ? TextTrimming.CharacterEllipsis : TextTrimming.None;
        Coverage.SetResourceReference(TextBlock.ForegroundProperty,enable ? "ConsoleMuted" : "Muted");
        Empty.SetResourceReference(TextBlock.ForegroundProperty,enable ? "ConsoleMuted" : "Muted");
        Plot.MinHeight=enable ? 0 : 120;
        Rows.MinHeight=enable ? 56 : 80;
        Rows.MaxHeight=enable ? 168 : 520;
        Rows.ItemContainerStyle=(Style)FindResource(enable ? "ReferenceRankedRow" : "RankedRow");
        LegendRow.Visibility=enable ? Visibility.Collapsed : Visibility.Visible;
        Grid.SetRow(Plot,enable ? 0 : 4);
        Grid.SetRow(Coverage,enable ? 1 : 5);
        Grid.SetRow(Selectors,enable ? 2 : 1);
        Grid.SetRow(TitleBar,enable ? 3 : 0);
        Grid.SetRow(RateNoticePanel,enable ? 4 : 2);
        Grid.SetRow(LegendRow,enable ? 5 : 3);
        var panelGrid=(Grid)PanelFrame.Child;
        panelGrid.RowDefinitions[0].Height=enable ? new GridLength(1,GridUnitType.Star) : GridLength.Auto;
        panelGrid.RowDefinitions[4].Height=enable ? GridLength.Auto : new GridLength(1,GridUnitType.Star);
        if(enable)
        {
            LegendRow.Children.Remove(EffectiveOnly);
            Selectors.Children.Add(EffectiveOnly);
            ActionStack.Children.Remove(Actions);
            Selectors.Children.Add(Actions);
        }
        else
        {
            Selectors.Children.Remove(EffectiveOnly);
            LegendRow.Children.Insert(0,EffectiveOnly);
            Selectors.Children.Remove(Actions);
            ActionStack.Children.Add(Actions);
        }
        Actions.Margin=enable ? new Thickness(5,0,0,0) : new Thickness(0);
        EffectiveOnly.Margin=enable ? new Thickness(3,0,0,0) : new Thickness(10,0,0,0);
        EffectiveOnly.Height=enable ? 24 : 28;
        StyleSelector(Category,CategoryField,CategoryLabel,enable,74,88);
        StyleSelector(Ranking,RankingField,RankingLabel,enable,68,96);
        StyleSelector(Rate,RateField,RateLabel,enable,90,104);
        foreach(var button in new[]{OpenHudButton,DuplicateButton,DeleteButton,RateAction,LockButton})
        {
            if(enable)
            {
                button.MinHeight=22;
                button.FontSize=11;
                button.Padding=new Thickness(7,1,7,1);
            }
            else
            {
                button.ClearValue(MinHeightProperty);
                if(button==OpenHudButton||button==DuplicateButton||button==DeleteButton) button.FontSize=13;
                else button.ClearValue(FontSizeProperty);
                button.Padding=button==RateAction ? new Thickness(9,4,9,4)
                    : button==OpenHudButton||button==LockButton ? new Thickness(10,5,10,5) : new Thickness(9,5,9,5);
            }
        }
        if(view.ValueKind==JsonValueKind.Object) Update(view,DeleteButton.IsEnabled);
    }

    private static void StyleSelector(ComboBox combo, StackPanel field, TextBlock label,
        bool compact, double compactWidth, double standardWidth)
    {
        combo.Width=compact ? compactWidth : standardWidth;
        if(compact)
        {
            combo.MinHeight=24;
            combo.FontSize=11;
            combo.Margin=new Thickness(0);
        }
        else
        {
            combo.ClearValue(MinHeightProperty);
            combo.ClearValue(FontSizeProperty);
            combo.ClearValue(MarginProperty);
        }
        field.Margin=new Thickness(0,0,compact ? 4 : 8,0);
        label.Visibility=compact ? Visibility.Collapsed : Visibility.Visible;
    }

    internal void Update(JsonElement value, bool canDelete=true)
    {
        view=value; updating=true;
        var config=value.Get("config");
        var share=SharePresentation.From(value);
        var title=value.Text("title");
        if(IsHud)
        {
            title=config.Text("category") switch
            {
                "healing"=>"总治疗",
                "shield"=>"总护盾",
                _=>"总伤"
            };
        }
        Heading.Text=title;
        var knownShare=value.Text("share_scope")=="known";
        PanelKind.Text=IsHud ? share.Label : IsHistory ? "HISTORY PANEL" : "LIVE PANEL";
        PanelKind.Visibility=referenceStyle||IsHud&&!knownShare ? Visibility.Collapsed : Visibility.Visible;
        PanelKind.ToolTip=IsHud ? share.Description : null;
        Heading.ToolTip=IsHud ? share.Description : null;
        HudClose.Visibility=IsHud ? Visibility.Visible : Visibility.Collapsed;
        Rows.ItemTemplate=(DataTemplate)FindResource(IsHud ? "HudRow" : referenceStyle ? "ReferenceRow" : "FullRow");
        TableHeader.Visibility=IsHud||referenceStyle ? Visibility.Collapsed : Visibility.Visible;
        ReferenceTableHeader.Visibility=referenceStyle ? Visibility.Visible : Visibility.Collapsed;
        Select(Category,config.Text("category")); Select(Ranking,config.Text("ranking")); Select(Rate,config.Text("rate_basis"));
        var category=config.Text("category");
        if(category=="shield") Select(Ranking,"total");
        Ranking.IsEnabled=category!="shield";
        ((ComboBoxItem)Ranking.Items[0]).Content=category=="healing" ? "总治疗" : category=="shield" ? "总护盾" : "总伤";
        ((ComboBoxItem)Ranking.Items[1]).Content=category=="shield" ? "近期" : "DPS";
        Rate.ToolTip=config.Text("rate_basis")=="full_cycle" ? "全周期 DPS = 总伤 / 在场时间（自本次观测起）" : null;
        Rate.IsEnabled=config.Text("category")!="shield";
        EffectiveOnly.IsChecked=config.Bool("effective_only");
        EffectiveOnly.Visibility=!IsHistory&&!IsHud&&config.Text("category")=="damage"
            ? Visibility.Visible : Visibility.Collapsed;
        // The engagement column depends on event observation. Never leave it as a
        // silently blank column: show the reason and a retry/settings entry while
        // still allowing a switch back to the five-second window.
        var isEngagement=config.Text("category")!="shield" && config.Text("rate_basis")=="engagement";
        var reason=value.Text("rate_reason");
        var action=isEngagement ? value.Text("rate_action") : "";
        RateNotice.Text=reason;
        RateNotice.Visibility=isEngagement && reason.Length>0 ? Visibility.Visible : Visibility.Collapsed;
        RateNoticePanel.Visibility=RateNotice.Visibility;
        RateAction.Content=value.Text("rate_action_label");
        RateAction.Visibility=isEngagement && action.Length>0 && value.Text("rate_action_label").Length>0 ? Visibility.Visible : Visibility.Collapsed;
        Rate.ToolTip=isEngagement && reason.Length>0 ? reason
            : referenceStyle ? "选择 DPS 算法" : null;
        RateHeading.Text=config.Text("category") switch{"healing"=>"HPS","shield"=>"—",_=>"DPS"};
        ReferenceRateHeading.Text=RateHeading.Text;
        ReferenceTotalHeading.Text=category switch{"healing"=>"总治疗","shield"=>"总护盾",_=>"总伤"};
        Actions.Visibility=IsHistory||IsHud ? Visibility.Collapsed : Visibility.Visible;
        Selectors.Visibility=IsHud ? Visibility.Collapsed : Visibility.Visible;
        DeleteButton.IsEnabled=canDelete;
        Legend.Visibility=!IsHud && !referenceStyle && config.Text("category")=="damage" ? Visibility.Visible : Visibility.Collapsed;
        LegendRow.MinHeight=IsHud ? 0 : 28;
        OverkillLegend.Visibility=value.Get("entries").Items().Any(entry=>entry.Get("segments").Items().Any(segment=>
            segment.TryGetProperty("overkill_ratio",out var mark)&&mark.ValueKind==JsonValueKind.Number&&mark.GetDouble()>0))
            ? Visibility.Visible : Visibility.Collapsed;
        ShareHeading.ToolTip=share.Description;
        if(referenceStyle)
        {
            Coverage.ToolTip=share.Description;
            var total=value.Get("total");
            var totalLabel=value.Bool("share_known") ? "∑  全队合计   " : "∑  榜单合计   ";
            Coverage.Text=total.ValueKind==JsonValueKind.Number ? totalLabel+Json.UiText(total.GetRawText()) : "";
            if(!value.Bool("share_known"))
                Coverage.Text=Coverage.Text.Length>0 ? share.Label+" · "+Coverage.Text : share.Description;
        }
        else
        {
            Coverage.ToolTip=null;
            Coverage.Text=share.Description+(share.Available ? " · 点击干员固定明细" : "");
        }
        if(IsHud){Plot.MinHeight=56;Rows.MinHeight=30;Rows.MaxHeight=double.PositiveInfinity;}
        Coverage.Visibility=IsHud||referenceStyle&&Coverage.Text.Length==0 ? Visibility.Collapsed : Visibility.Visible;
        var incoming=value.Get("entries").Items().ToArray();
        var ids=incoming.Select(e=>e.Text("owner_id")).ToHashSet();
        foreach(var old in items.Where(x=>!ids.Contains(x.Id)).ToArray()) items.Remove(old);
        var map=items.ToDictionary(x=>x.Id);
        foreach(var entry in incoming)
        {
            if(map.TryGetValue(entry.Text("owner_id"),out var item))
            {
                item.ShareInfo=share;
                item.Entry=entry;
            }
            else items.Add(new RowItem{Entry=entry,Compact=IsHud,ShareInfo=share});
        }
        if(!hold)
            for(int i=0;i<incoming.Length;i++)
            {
                int index=items.ToList().FindIndex(x=>x.Id==incoming[i].Text("owner_id"));
                if(index!=i) items.Move(index,i);
            }
        for(int i=0;i<items.Count;i++) items[i].SetRank(i+1);
        Empty.Text=value.Text("empty_text"); Empty.Visibility=items.Count==0 ? Visibility.Visible : Visibility.Collapsed;
        updating=false;
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
        ["effective_only"]=EffectiveOnly.IsChecked==true&&Category.SelectedIndex==0
    };
    private async void ConfigurationChanged(object sender,SelectionChangedEventArgs e)
    {
        if(updating || Command==null || Category.SelectedItem==null || Ranking.SelectedItem==null || Rate.SelectedItem==null) return;
        await Command(IsHistory ? "history_config" : "panel_update",new(){["config"]=Configuration()});
    }
    private async void EffectiveChanged(object sender,RoutedEventArgs e)
    {
        if(updating||Command==null||Category.SelectedItem==null||Ranking.SelectedItem==null||Rate.SelectedItem==null) return;
        await Command(IsHistory ? "history_config" : "panel_update",new(){["config"]=Configuration()});
    }
    private async void Duplicate(object sender,RoutedEventArgs e) {if(Command!=null) await Command("panel_duplicate",new(){["panel_id"]=PanelId});}
    private async void Delete(object sender,RoutedEventArgs e) {if(Command!=null) await Command("panel_delete",new(){["panel_id"]=PanelId});}
    private void OpenHud(object sender,RoutedEventArgs e) => HudRequested?.Invoke(PanelId);
    private void CloseHud(object sender,RoutedEventArgs e) => CloseRequested?.Invoke();
    private void RateActionClicked(object sender,RoutedEventArgs e) => RateActionRequested?.Invoke(view.Text("rate_action"));
    private void RowSelected(object sender,SelectionChangedEventArgs e) {if(!updating && Rows.SelectedItem is RowItem item) PinRequested?.Invoke(item.Id);}
    private void HoldOrder(object sender,MouseEventArgs e) => hold=true;
    private void ReleaseOrder(object sender,MouseEventArgs e) {hold=false;if(view.ValueKind==JsonValueKind.Object) Update(view,DeleteButton.IsEnabled);}
    private void ToggleLock(object sender,RoutedEventArgs e) => LockRequested?.Invoke(PanelId);
    private void OpacityChanged(object sender,RoutedPropertyChangedEventArgs<double> e) {if(!updating) AlphaRequested?.Invoke(PanelId,e.NewValue);}
    internal void HudState(bool open,bool locked,double opacity)
    {
        updating=true; HudControls.Visibility=open && !IsHud ? Visibility.Visible : Visibility.Collapsed;
        LockButton.Content=locked ? "解锁 HUD" : "锁定 HUD"; Alpha.Value=opacity; updating=false;
    }
    internal void ClearSelection() => Rows.SelectedItem=null;
}
