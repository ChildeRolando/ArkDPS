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
    public string Id => Entry.Text("owner_id");
    public string Name => Entry.Text("name");
    public string Initial => Name.Length > 0 ? Name[..1] : "?";
    public string Value => Entry.Text("display_value");
    public string Presence => (Entry.Bool("portrait_muted") ? "已离场" : "统计条目") + (Entry.Bool("incomplete") ? " · 部分覆盖" : "");
    public string Secondary => Entry.Text("share_display") + (Entry.Text("rate_label").Length > 0 ? "   " + Entry.Text("rate_display") + "/s" : "");
    public string Tooltip => Name + "\n排行原值 " + Entry.Text("exact_value") + "\n" + Entry.Text("rate_label") + "速率 " + Entry.Exact("rate") + "\n占比 " + Entry.Exact("share");
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
    public static readonly DependencyProperty DataProperty=DependencyProperty.Register(nameof(Data),typeof(JsonElement),typeof(ContributionBar),new FrameworkPropertyMetadata(default(JsonElement),FrameworkPropertyMetadataOptions.AffectsRender));
    public JsonElement Data { get => (JsonElement)GetValue(DataProperty); set => SetValue(DataProperty,value); }
    protected override void OnRender(DrawingContext dc)
    {
        base.OnRender(dc);
        dc.DrawRoundedRectangle(Theme.Resource("ConsoleCard"),null,new Rect(0,0,ActualWidth,ActualHeight),2,2);
        double width=ActualWidth*Math.Clamp(Data.Number("bar_ratio"),0,1), x=0;
        var segments=Data.Get("segments").Items().ToArray();
        if (segments.Length==0)
        {
            dc.DrawRoundedRectangle(Data.Text("bar_color").Length>0 ? Theme.Brush(Data.Text("bar_color")) : Theme.Resource("Signal"),null,new Rect(0,0,width,ActualHeight),2,2); return;
        }
        foreach(var segment in segments)
        {
            double size=Math.Min(width-x,width*Math.Clamp(segment.Number("ratio"),0,1));
            if(size>0) dc.DrawRectangle(Theme.Brush(segment.Text("color")),null,new Rect(x,0,Math.Max(0,size-2),ActualHeight));
            x+=size;
        }
        if (x<width) dc.DrawRectangle(Theme.Resource("ConsoleMuted"),null,new Rect(x,0,width-x,ActualHeight));
    }
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
    internal Action<string>? HudRequested, PinRequested, LockRequested;
    internal Action<string,double>? AlphaRequested;
    private readonly ObservableCollection<RowItem> items=new();
    private JsonElement view;
    private bool updating, hold;
    internal bool IsHistory, IsHud;
    public string PanelId => view.Get("config").Text("panel_id");
    internal JsonElement View => view;
    public StatisticPanel() { InitializeComponent(); Rows.ItemsSource=items; }
    internal void Update(JsonElement value, bool canDelete=true)
    {
        view=value; updating=true;
        Heading.Text=value.Text("title");
        var config=value.Get("config");
        Select(Category,config.Text("category")); Select(Ranking,config.Text("ranking")); Select(Rate,config.Text("rate_basis"));
        Rate.IsEnabled=config.Text("category")!="shield";
        RateHeading.Text=(value.Text("rate_label").Length>0 ? value.Text("rate_label")+" /s · " : "")+"占比";
        Actions.Visibility=IsHistory||IsHud ? Visibility.Collapsed : Visibility.Visible;
        Selectors.Visibility=IsHud ? Visibility.Collapsed : Visibility.Visible;
        DeleteButton.IsEnabled=canDelete;
        Legend.Visibility=config.Text("category")=="damage" ? Visibility.Visible : Visibility.Collapsed;
        Coverage.Text=value.Bool("share_known") ? "全队同口径占比 · 点击干员固定明细" : value.Text("share_reason");
        if(IsHud){Plot.MinHeight=80;Rows.MinHeight=35;Rows.MaxHeight=double.PositiveInfinity;}
        if(IsHud) Coverage.Text="跟随源面板 · "+value.Text("metric_label");
        var incoming=value.Get("entries").Items().ToArray();
        var ids=incoming.Select(e=>e.Text("owner_id")).ToHashSet();
        foreach(var old in items.Where(x=>!ids.Contains(x.Id)).ToArray()) items.Remove(old);
        var map=items.ToDictionary(x=>x.Id);
        foreach(var entry in incoming)
        {
            if(map.TryGetValue(entry.Text("owner_id"),out var item)) item.Entry=entry;
            else items.Add(new RowItem{Entry=entry,Compact=IsHud});
        }
        if(!hold)
            for(int i=0;i<incoming.Length;i++)
            {
                int index=items.ToList().FindIndex(x=>x.Id==incoming[i].Text("owner_id"));
                if(index!=i) items.Move(index,i);
            }
        Empty.Text=value.Text("empty_text"); Empty.Visibility=items.Count==0 ? Visibility.Visible : Visibility.Collapsed;
        updating=false;
    }
    private static void Select(ComboBox box,string tag)
    {
        foreach(ComboBoxItem item in box.Items) if((string)item.Tag==tag) {box.SelectedItem=item;break;}
    }
    internal Dictionary<string,object?> Configuration() => new()
    {
        ["panel_id"]=PanelId,
        ["category"]=((ComboBoxItem)Category.SelectedItem).Tag,
        ["ranking"]=((ComboBoxItem)Ranking.SelectedItem).Tag,
        ["rate_basis"]=Category.SelectedIndex==2 ? "recent_5s" : ((ComboBoxItem)Rate.SelectedItem).Tag
    };
    private async void ConfigurationChanged(object sender,SelectionChangedEventArgs e)
    {
        if(updating || Command==null || Category.SelectedItem==null || Ranking.SelectedItem==null || Rate.SelectedItem==null) return;
        await Command(IsHistory ? "history_config" : "panel_update",new(){["config"]=Configuration()});
    }
    private async void Duplicate(object sender,RoutedEventArgs e) {if(Command!=null) await Command("panel_duplicate",new(){["panel_id"]=PanelId});}
    private async void Delete(object sender,RoutedEventArgs e) {if(Command!=null) await Command("panel_delete",new(){["panel_id"]=PanelId});}
    private void OpenHud(object sender,RoutedEventArgs e) => HudRequested?.Invoke(PanelId);
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
