using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace ArkDPS.Desktop;

public sealed class Inspector : Border
{
    private readonly StackPanel body=new();
    private string? owner;
    private JsonElement source;
    public Inspector()
    {
        SetResourceReference(BackgroundProperty,"Card"); SetResourceReference(BorderBrushProperty,"Line");
        BorderThickness=new Thickness(1); CornerRadius=new CornerRadius(12); Padding=new Thickness(20);
        Effect=new System.Windows.Media.Effects.DropShadowEffect{BlurRadius=18,ShadowDepth=2,Direction=270,Opacity=.10,Color=Colors.Black};
        Child=body; Empty();
    }
    internal void Empty()
    {
        body.Children.Clear();
        body.Children.Add(Text("干员明细",19,true));
        body.Children.Add(Text("点击排行中的干员，固定查看累计、独立接敌区间与数据来源。",12,false,"Muted",new Thickness(0,14,0,8)));
        body.Children.Add(Text("近期输出归零后保留固定条目；新战局清除选择。",11,false,"Muted"));
    }
    internal void Pin(string id,JsonElement view) {owner=id;Update(view);}
    internal void Reset() {owner=null;Empty();}
    internal void Update(JsonElement view)
    {
        var rows=view.Get("source");
        source=rows;
        if(owner==null) return;
        var row=rows.Get(owner);
        if(row.ValueKind!=JsonValueKind.Object) return;
        body.Children.Clear();
        var profile=new DockPanel{Margin=new Thickness(0,0,0,22)};
        var portrait=Portraits.Get(owner,row.Get("present").ValueKind==JsonValueKind.False);
        if(portrait!=null)
        {
            var frame=new Border{Width=58,Height=58,CornerRadius=new CornerRadius(14),ClipToBounds=true,
                BorderBrush=Theme.Resource("Line"),BorderThickness=new Thickness(1),Margin=new Thickness(0,0,13,0)};
            frame.Child=new Image{Source=portrait,Stretch=Stretch.UniformToFill};DockPanel.SetDock(frame,Dock.Left);profile.Children.Add(frame);
        }
        var name=new StackPanel(); name.Children.Add(Text(row.Text("name"),24,true));
        name.Children.Add(Text(row.Get("present").ValueKind switch{JsonValueKind.True=>"在场",JsonValueKind.False=>"已离场 · 累计保留",_=>"在场状态未知"},11,false,"Muted"));
        profile.Children.Add(name);body.Children.Add(profile);
        Section("伤害概览");
        Pair("有效伤害",Effective(row)); Pair("过量伤害",row.UiInt("overkill_damage"));
        Pair("治疗",row.UiInt("healing")); Pair("有效护盾",row.UiInt("shield_absorbed"));
        if(row.Bool("overkill_type_complete"))
        {
            var overkillTypes=row.Get("overkill_damage_by_type");
            var parts=new List<string>();
            foreach(var (key,label) in new[]{("physical","物理"),("arts","法术"),("elemental","元素"),("true","真实")})
            {
                var value=overkillTypes.UiInt(key);
                if(value!="0" && value!="—") parts.Add(label+" "+value);
            }
            if(parts.Count>0) body.Children.Add(Text(string.Join(" · ",parts),10,false,"Muted",new Thickness(0,2,0,0)));
        }
        body.Children.Add(Text(OverkillNote(row,view),11,false,"Muted",new Thickness(0,6,0,0)));
        if(row.Get("burst_unknown_reason").ValueKind==JsonValueKind.String)
            body.Children.Add(Text("元素爆条归属未知："+row.Text("burst_unknown_reason"),11,false,"Muted",new Thickness(0,4,0,0)));
        Section("独立接敌速率");
        var rates=view.Get("rates").Get(owner);
        var damageRate=rates.Get("damage");var healingRate=rates.Get("healing");
        Pair("DPS",RateText(row,damageRate,"engagement_damage"));Pair("HPS",RateText(row,healingRate,"engagement_healing"));
        Pair("伤害活跃秒",row.UiInt("active_damage"));Pair("治疗活跃秒",row.UiInt("active_healing"));
        // 事件不可用、未就绪或中断时，接敌列必须说明原因，而不是只留空值。
        var rateNote=damageRate.Text("note");
        if(rateNote.Length==0) rateNote=healingRate.Text("note");
        if(rateNote.Length>0) body.Children.Add(Text(rateNote,11,false,"Muted",new Thickness(0,6,0,0)));
        var rateReason=view.Text("rate_reason");
        if(rateReason.Length>0) body.Children.Add(Text(rateReason,11,false,"Muted",new Thickness(0,4,0,0)));
        var rateHint=view.Text("rate_hint");
        if(rateHint.Length>0) body.Children.Add(Text(rateHint,11,false,"Muted",new Thickness(0,4,0,0)));
        Section("伤害构成");
        var types=row.Get("damage_by_type");
        foreach(var (key,label,color) in new[]{("physical","物理","#E5484D"),("arts","法术","#3D8BFD"),("elemental","元素","#9B5CF6"),("true","真实","#FFFFFF")})
            Pair(label,types.UiInt(key),color);
        Section("观测覆盖");
        Pair("快照起点",row.UiInt("coverage_start"));
        Pair("事件起点",row.UiInt("shield_coverage_start"));Pair("事件终点",row.UiInt("event_coverage_end"));
        body.Children.Add(Text(row.Bool("counter_snapshot_complete") ? "累计快照完整" : "累计快照不完整",11,false,"Muted",new Thickness(0,10,0,0)));
        // 未建立事件覆盖、覆盖到某点后中断或单事件缺口必须给出具体原因。
        var eventReason=view.Text("rate_reason");
        string coverage=row.Bool("event_coverage_complete") ? "事件完整覆盖"
            : (eventReason.Length>0 ? eventReason : "事件覆盖不完整，原因未知")
              +(row.Get("event_coverage_end").ValueKind==JsonValueKind.Number
                ? "（已覆盖至 "+row.UiInt("event_coverage_end")+" 游戏秒）" : "")
              +" · 未覆盖区间保持未知，不补零";
        body.Children.Add(Text(coverage,11,false,"Muted"));
        if(row.Bool("shield_unknown"))
            body.Children.Add(Text("护盾事件不完整 · 伤害/接敌/过量继续",11,false,"Muted"));
        Section("归属来源");
        string sources=string.Join("\n",row.Get("sources").Items().Select(x=>x.Text()));
        body.Children.Add(Text(sources.Length>0 ? sources : "—",10,false,"Muted"));
    }
    private static string RateText(JsonElement row,JsonElement rate,string exact)
    {
        // 可靠零、未知与事件不可用由模型分类；这里只展示，不重新计算统计。
        if(rate.Text("state")=="value") return row.UiInt(exact);
        var text=rate.Text("text");
        return text.Length>0 ? text : row.UiInt(exact);
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
    private void Section(string title)
    {
        var row=new StackPanel{Orientation=Orientation.Horizontal,Margin=new Thickness(0,22,0,8)};
        row.Children.Add(new Border{Width=3,Height=14,CornerRadius=new CornerRadius(1.5),Background=Theme.Resource("Accent"),Margin=new Thickness(0,0,9,0),VerticalAlignment=VerticalAlignment.Center});
        row.Children.Add(Text(title,12,true,"Ink"));
        body.Children.Add(row);
    }
    private void Pair(string label,string value,string? color=null)
    {
        if(value=="0") return;
        var grid=new Grid{Margin=new Thickness(0,0,0,0)}; grid.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});grid.ColumnDefinitions.Add(new(){Width=GridLength.Auto});
        var name=Text(label,11,false,"Muted"); if(color!=null) {var stack=new StackPanel{Orientation=Orientation.Horizontal};stack.Children.Add(new Border{Width=7,Height=7,CornerRadius=new CornerRadius(2),Background=Theme.Brush(color),BorderBrush=Theme.Resource("Muted"),BorderThickness=new Thickness(.5),Margin=new Thickness(0,0,7,0)});stack.Children.Add(name);grid.Children.Add(stack);} else grid.Children.Add(name);
        var number=Text(value,13);number.FontFamily=new FontFamily("Cascadia Mono, Consolas");number.HorizontalAlignment=HorizontalAlignment.Right;number.Margin=new Thickness(12,0,0,0);number.ToolTip=value;Grid.SetColumn(number,1);grid.Children.Add(number);
        body.Children.Add(new Border{Child=grid,BorderBrush=Theme.Resource("Line"),BorderThickness=new Thickness(0,0,0,1),Padding=new Thickness(0,6,0,6)});
    }
    internal static TextBlock Text(string text,double size=13,bool bold=false,string token="Ink",Thickness? margin=null)
    {
        var block=new TextBlock{Text=text,FontSize=size,FontWeight=bold ? FontWeights.SemiBold : FontWeights.Normal,TextWrapping=TextWrapping.Wrap,Margin=margin??new Thickness(0)};block.SetResourceReference(TextBlock.ForegroundProperty,token);return block;
    }
}
