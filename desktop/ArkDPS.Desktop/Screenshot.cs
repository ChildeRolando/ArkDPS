using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace ArkDPS.Desktop;

internal static class Screenshot
{
    public static byte[] Capture(FrameworkElement element, double dpi=96)
    {
        element.UpdateLayout();
        double scale=dpi/96;
        int width=Math.Max(1,(int)Math.Ceiling(element.ActualWidth*scale)),height=Math.Max(1,(int)Math.Ceiling(element.ActualHeight*scale));
        var target=new RenderTargetBitmap(width,height,dpi,dpi,PixelFormats.Pbgra32);target.Render(element);
        var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(target));
        using var stream=new MemoryStream();encoder.Save(stream);return stream.ToArray();
    }
    public static void Window(Window window,string path,double dpi=96)=>File.WriteAllBytes(path,Capture((FrameworkElement)window.Content,dpi));
    private static string OverkillText(JsonElement entry)
    {
        var detail=entry.Get("detail");var value=detail.Get("overkill_damage");
        if(value.ValueKind==JsonValueKind.Number) return "过量 "+Json.UiText(value.GetRawText());
        var reason=detail.Text("event_reason");
        if(detail.Bool("event_coverage_complete"))
        {
            var overkillReason=detail.Text("overkill_unknown_reason");
            return "过量 — · "+(overkillReason.Length>0 ? overkillReason : "证据不足");
        }
        return "过量 — · "+(reason.Length>0 ? reason : "事件未完整覆盖");
    }
    public static byte[] Statistics(JsonElement selected,string theme)
    {
        var document=StatisticsDocument(selected,theme);
        document.Measure(new Size(900,double.PositiveInfinity));document.Arrange(new Rect(document.DesiredSize));document.UpdateLayout();return Capture(document);
    }
    internal static StackPanel StatisticsDocument(JsonElement selected,string theme)
    {
        var view=selected.Get("view");var document=new StackPanel{Width=900,Background=Theme.Resource("Card")};
        bool people = theme == Theme.People;
        document.SetResourceReference(System.Windows.Documents.TextElement.FontFamilyProperty,"InterfaceFont");
        var header = new DockPanel
        {
            Background = Theme.Resource(people ? "Inset" : "Console"),
            LastChildFill = true
        };
        if (people)
        {
            var artwork = new Image
            {
                Source = new BitmapImage(new Uri("pack://application:,,,/Assets/People/Components/city-and-ranking-pennant-red.png")),
                Width = 86, Height = 66, Stretch = Stretch.Uniform, Margin = new Thickness(24, 8, 0, 8)
            };
            DockPanel.SetDock(artwork, Dock.Left);
            header.Children.Add(artwork);
        }
        var heading = new StackPanel();
        var title = Inspector.Text("ArkDPS / "+view.Text("title"),26,true,people ? "Ink" : "ConsoleInk",new Thickness(24,22,24,12));
        if (people) title.SetResourceReference(TextBlock.FontFamilyProperty, "HeadingFont");
        heading.Children.Add(title);
        heading.Children.Add(Inspector.Text(string.Join(" · ",selected.Get("labels").Items().Select(x=>x.Text())),12,false,
            people ? "Muted" : "ConsoleMuted",new Thickness(24,0,24,18)));
        header.Children.Add(heading);
        document.Children.Add(new Border
        {
            Child = header,
            BorderThickness = new Thickness(0, 0, 0, people ? 3 : 0),
            BorderBrush = Theme.Resource("Accent")
        });
        document.Children.Add(Inspector.Text("有效伤害 = 总伤害 − 过量伤害 · 过量来自事件派生 · 斜纹 = 过量伤害 · 护盾仅实际吸收 · 未知为 —",12,false,"Muted",new Thickness(24,18,24,4)));
        document.Children.Add(Inspector.Text(view.Text("metric_label")+" / "+view.Text("rate_label")+"速率 · 原始值排序",12,false,"Muted",new Thickness(24,0,24,14)));
        foreach(var entry in view.Get("entries").Items())
        {
            var item = new RowItem { Entry = entry };
            string surface = people && item.Rank % 2 == 0 ? "ConsoleCard" : "Console";
            var row = new Grid { MinHeight = 72, Background = Theme.Resource(surface) };
            row.ColumnDefinitions.Add(new(){Width=new GridLength(66)});row.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});row.ColumnDefinitions.Add(new(){Width=new GridLength(280)});
            var portrait=new Image{Source=item.Portrait,Width=40,Height=40,Margin=new Thickness(10,8,10,16)};row.Children.Add(portrait);
            var text=new StackPanel{Margin=new Thickness(0,9,12,16)};text.Children.Add(Inspector.Text(item.Name,16,true,"ConsoleInk"));text.Children.Add(Inspector.Text(item.Presence,11,false,"ConsoleMuted"));Grid.SetColumn(text,1);row.Children.Add(text);
            var values=new StackPanel{Margin=new Thickness(0,8,12,16),HorizontalAlignment=HorizontalAlignment.Right};
            var exact=Inspector.Text(Json.UiText(entry.Text("exact_value")),18,true,"ConsoleInk");exact.SetResourceReference(TextBlock.FontFamilyProperty,"DisplayFont");exact.TextWrapping=TextWrapping.NoWrap;exact.TextAlignment=TextAlignment.Right;
            var secondary=Inspector.Text(item.Share+(item.Rate.Length>0 ? "   "+item.Rate : ""),12,false,"ConsoleMuted");secondary.TextAlignment=TextAlignment.Right;
            var overkill=Inspector.Text(OverkillText(entry),11,false,"ConsoleMuted");overkill.TextAlignment=TextAlignment.Right;
            values.Children.Add(exact);values.Children.Add(secondary);values.Children.Add(overkill);Grid.SetColumn(values,2);row.Children.Add(values);
            var bar=new ContributionBar{Data=entry,MetricCategory=view.Get("config").Text("category"),Height=8,VerticalAlignment=VerticalAlignment.Bottom,Margin=new Thickness(0,0,12,9)};Grid.SetColumn(bar,1);Grid.SetColumnSpan(bar,2);row.Children.Add(bar);
            document.Children.Add(new Border
            {
                Child = row,
                Margin = new Thickness(24, 0, 24, 0),
                BorderBrush = Theme.Resource("ReferenceRankLeader"),
                BorderThickness = new Thickness(people && item.Rank == 1 ? 3 : 0, 0, 0, 0)
            });
        }
        if(!view.Get("entries").Items().Any())document.Children.Add(Inspector.Text(view.Text("empty_text"),16,false,"Muted",new Thickness(24,20,24,20)));
        string damageLegend = people ? "物理红 / 法术橙 / 元素金 / 真实紫" : "物理红 / 法术蓝 / 元素紫 / 真实白";
        document.Children.Add(Inspector.Text("分型："+damageLegend+"；精确构成见原始 JSON。",11,false,"Muted",new Thickness(24,16,24,4)));
        document.Children.Add(Inspector.Text(SharePresentation.From(view).Description,12,false,"Muted",new Thickness(24,0,24,5)));
        var record=selected.Get("record");document.Children.Add(Inspector.Text("事件："+record.Text("event_status")+" · "+record.Text("event_error"),11,false,"Muted",new Thickness(24,0,24,4)));
        document.Children.Add(Inspector.Text("冻结战绩 "+selected.Text("battle_id")+" · "+theme+" · 完整排行（非可见区域截图）",10,false,"Muted",new Thickness(24,0,24,22)));
        return document;
    }
}
