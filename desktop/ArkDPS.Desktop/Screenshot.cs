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
    public static byte[] Capture(FrameworkElement element)
    {
        element.UpdateLayout();
        int width=Math.Max(1,(int)Math.Ceiling(element.ActualWidth)),height=Math.Max(1,(int)Math.Ceiling(element.ActualHeight));
        var target=new RenderTargetBitmap(width,height,96,96,PixelFormats.Pbgra32);target.Render(element);
        var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(target));
        using var stream=new MemoryStream();encoder.Save(stream);return stream.ToArray();
    }
    public static void Window(Window window,string path)=>File.WriteAllBytes(path,Capture((FrameworkElement)window.Content));
    private static string OverkillText(JsonElement entry)
    {
        var value=entry.Get("detail").Get("overkill_damage");
        if(value.ValueKind==JsonValueKind.Number) return "过量 "+value.GetDouble().ToString("0.###")+" · 总伤害含过量";
        return entry.Get("detail").Bool("overkill_coverage_complete") ? "过量 0 · 总伤害含过量" : "过量 — · 事件未完整覆盖";
    }
    public static byte[] Statistics(JsonElement selected,string theme)
    {
        var view=selected.Get("view");var document=new StackPanel{Width=900,Background=Theme.Resource("Card")};
        var header=new StackPanel{Background=Theme.Resource("Console"),Margin=new Thickness(0)};
        header.Children.Add(Inspector.Text("ArkDPS / "+view.Text("title"),26,true,"ConsoleInk",new Thickness(24,22,24,12)));
        header.Children.Add(Inspector.Text(string.Join(" · ",selected.Get("labels").Items().Select(x=>x.Text())),12,false,"ConsoleMuted",new Thickness(24,0,24,18)));document.Children.Add(header);
        document.Children.Add(Inspector.Text("总伤害含过量 · 治疗含过量 · 过量伤害为事件派生且不改变排行 · 斜纹 = 按属性过量（事件/root） · 护盾仅实际吸收 · 未知为 —",12,false,"Muted",new Thickness(24,18,24,4)));
        document.Children.Add(Inspector.Text(view.Text("metric_label")+" / "+view.Text("rate_label")+"速率 · 原始值排序",12,false,"Muted",new Thickness(24,0,24,14)));
        foreach(var entry in view.Get("entries").Items())
        {
            var item=new RowItem{Entry=entry};var row=new Grid{Margin=new Thickness(24,0,24,0),MinHeight=72,Background=Theme.Resource("Console")};
            row.ColumnDefinitions.Add(new(){Width=new GridLength(66)});row.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});row.ColumnDefinitions.Add(new(){Width=new GridLength(280)});
            var portrait=new Image{Source=item.Portrait,Width=40,Height=40,Margin=new Thickness(10,8,10,16)};row.Children.Add(portrait);
            var text=new StackPanel{Margin=new Thickness(0,9,12,16)};text.Children.Add(Inspector.Text(item.Name,16,true,"ConsoleInk"));text.Children.Add(Inspector.Text(item.Presence,11,false,"ConsoleMuted"));Grid.SetColumn(text,1);row.Children.Add(text);
            var values=new StackPanel{Margin=new Thickness(0,8,12,16),HorizontalAlignment=HorizontalAlignment.Right};
            var exact=Inspector.Text(entry.Text("exact_value"),18,true,"ConsoleInk");exact.FontFamily=new FontFamily("Bahnschrift");exact.TextWrapping=TextWrapping.NoWrap;exact.TextAlignment=TextAlignment.Right;
            var secondary=Inspector.Text(item.Secondary,12,false,"ConsoleMuted");secondary.TextAlignment=TextAlignment.Right;
            var overkill=Inspector.Text(OverkillText(entry),11,false,"ConsoleMuted");overkill.TextAlignment=TextAlignment.Right;
            values.Children.Add(exact);values.Children.Add(secondary);values.Children.Add(overkill);Grid.SetColumn(values,2);row.Children.Add(values);
            var bar=new ContributionBar{Data=entry,Height=7,VerticalAlignment=VerticalAlignment.Bottom,Margin=new Thickness(0,0,12,9)};Grid.SetColumn(bar,1);Grid.SetColumnSpan(bar,2);row.Children.Add(bar);document.Children.Add(row);
        }
        if(!view.Get("entries").Items().Any())document.Children.Add(Inspector.Text(view.Text("empty_text"),16,false,"Muted",new Thickness(24,20,24,20)));
        document.Children.Add(Inspector.Text("分型：物理红 / 法术蓝 / 元素紫 / 真实白；精确构成见原始 JSON。",11,false,"Muted",new Thickness(24,16,24,4)));
        document.Children.Add(Inspector.Text(view.Bool("share_known") ? "全队同口径占比" : view.Text("share_reason"),12,false,"Muted",new Thickness(24,0,24,5)));
        var record=selected.Get("record");document.Children.Add(Inspector.Text("事件："+record.Text("event_status")+" · "+record.Text("event_error"),11,false,"Muted",new Thickness(24,0,24,4)));
        document.Children.Add(Inspector.Text("冻结战绩 "+selected.Text("battle_id")+" · "+theme+" · 完整排行（非可见区域截图）",10,false,"Muted",new Thickness(24,0,24,22)));
        document.Measure(new Size(900,double.PositiveInfinity));document.Arrange(new Rect(document.DesiredSize));document.UpdateLayout();return Capture(document);
    }
}
