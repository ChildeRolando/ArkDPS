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
        BorderThickness=new Thickness(1); CornerRadius=new CornerRadius(3); Padding=new Thickness(18);
        Child=body; Empty();
    }
    internal void Empty()
    {
        body.Children.Clear();
        body.Children.Add(Text("干员明细",19,true));
        body.Children.Add(Text("点击排行中的干员，固定查看累计、独立接敌区间与数据来源。",12,false,"Muted",new Thickness(0,14,0,8)));
        body.Children.Add(Text("近期输出归零后保留固定条目；新战局清除选择。",11,false,"Muted"));
    }
    internal void Pin(string id,JsonElement rows) {owner=id;Update(rows);}
    internal void Reset() {owner=null;Empty();}
    internal void Update(JsonElement rows)
    {
        source=rows;
        if(owner==null) return;
        var row=source.Get(owner);
        if(row.ValueKind!=JsonValueKind.Object) return;
        body.Children.Clear();
        var profile=new DockPanel{Margin=new Thickness(0,0,0,20)};
        var portrait=Portraits.Get(owner,row.Get("present").ValueKind==JsonValueKind.False);
        if(portrait!=null) {var image=new Image{Source=portrait,Width=52,Height=52,Margin=new Thickness(0,0,12,0)};DockPanel.SetDock(image,Dock.Left);profile.Children.Add(image);}
        var name=new StackPanel(); name.Children.Add(Text(row.Text("name"),23,true));
        name.Children.Add(Text(row.Get("present").ValueKind switch{JsonValueKind.True=>"在场",JsonValueKind.False=>"已离场 · 累计保留",_=>"在场状态未知"},11,false,"Muted"));
        profile.Children.Add(name);body.Children.Add(profile);
        Section("累计贡献");
        Pair("伤害 · 含过量",row.Ui("damage")); Pair("治疗 · 含过量",row.Ui("healing")); Pair("有效护盾 · 已观察",row.Ui("shield_absorbed"));
        Pair("过量伤害 · 事件派生",row.Ui("overkill_damage"));
        if(row.Bool("overkill_type_complete"))
        {
            var overkillTypes=row.Get("overkill_damage_by_type");
            body.Children.Add(Text("物理 "+overkillTypes.Ui("physical")+" · 法术 "+overkillTypes.Ui("arts")+" · 元素 "+overkillTypes.Ui("elemental")+" · 真实 "+overkillTypes.Ui("true"),10,false,"Muted",new Thickness(0,2,0,0)));
        }
        body.Children.Add(Text(OverkillNote(row),11,false,"Muted",new Thickness(0,6,0,0)));
        Section("独立接敌速率");
        Pair("DPS",row.Ui("engagement_damage"));Pair("HPS",row.Ui("engagement_healing"));
        Pair("伤害活跃秒",row.Ui("active_damage"));Pair("治疗活跃秒",row.Ui("active_healing"));
        Section("伤害构成 · 精确原值");
        var types=row.Get("damage_by_type");
        foreach(var (key,label,color) in new[]{("physical","物理","#E5484D"),("arts","法术","#3D8BFD"),("elemental","元素","#9B5CF6"),("true","真实","#FFFFFF")})
            Pair(label,types.Ui(key),color);
        Section("观测覆盖");
        Pair("快照起点",row.Ui("coverage_start"));
        Pair("事件起点",row.Ui("shield_coverage_start"));Pair("事件终点",row.Ui("event_coverage_end"));
        body.Children.Add(Text(row.Bool("counter_snapshot_complete") ? "累计快照完整" : "累计快照不完整",11,false,"Muted",new Thickness(0,10,0,0)));
        body.Children.Add(Text(row.Bool("event_coverage_complete") ? "事件完整覆盖" : "事件部分覆盖 · 未知不记零",11,false,"Muted"));
        if(row.Bool("shield_unknown"))
            body.Children.Add(Text("护盾事件不完整 · 伤害/接敌/过量继续",11,false,"Muted"));
        Section("归属来源");
        string sources=string.Join("\n",row.Get("sources").Items().Select(x=>x.Text()));
        body.Children.Add(Text(sources.Length>0 ? sources : "—",10,false,"Muted"));
    }
    private static string OverkillNote(JsonElement row)
    {
        if(row.Bool("overkill_coverage_complete") && row.Get("overkill_damage").ValueKind==JsonValueKind.Number)
            return "总伤害仍含过量；过量只用于查看，不改变伤害排行、柱长或占比，并已排除护盾/抵挡吸收。";
        if(!row.Bool("event_coverage_complete")) return "总伤害仍含过量；事件覆盖不完整，过量未知（不记零）。";
        return "总伤害仍含过量；目标或实际扣血证据不足，过量未知（不记零）。";
    }
    private void Section(string title)
    {
        var text=Text(title,12,true,"Ink",new Thickness(0,20,0,9));body.Children.Add(text);
    }
    private void Pair(string label,string value,string? color=null)
    {
        var grid=new Grid{Margin=new Thickness(0,4,0,4)}; grid.ColumnDefinitions.Add(new(){Width=new GridLength(1,GridUnitType.Star)});grid.ColumnDefinitions.Add(new(){Width=GridLength.Auto});
        var name=Text(label,11,false,"Muted"); if(color!=null) {var stack=new StackPanel{Orientation=Orientation.Horizontal};stack.Children.Add(new Border{Width=6,Height=6,Background=Theme.Brush(color),BorderBrush=Theme.Resource("Muted"),BorderThickness=new Thickness(.5),Margin=new Thickness(0,0,6,0)});stack.Children.Add(name);grid.Children.Add(stack);} else grid.Children.Add(name);
        var number=Text(value,13);number.FontFamily=new FontFamily("Bahnschrift");number.HorizontalAlignment=HorizontalAlignment.Right;number.Margin=new Thickness(12,0,0,0);number.ToolTip=value;Grid.SetColumn(number,1);grid.Children.Add(number);body.Children.Add(grid);
    }
    internal static TextBlock Text(string text,double size=13,bool bold=false,string token="Ink",Thickness? margin=null)
    {
        var block=new TextBlock{Text=text,FontSize=size,FontWeight=bold ? FontWeights.SemiBold : FontWeights.Normal,TextWrapping=TextWrapping.Wrap,Margin=margin??new Thickness(0)};block.SetResourceReference(TextBlock.ForegroundProperty,token);return block;
    }
}
