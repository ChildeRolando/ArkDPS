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
        SetResourceReference(BackgroundProperty,"Card");
        SetResourceReference(BorderBrushProperty,"Line");
        SetResourceReference(CornerRadiusProperty,"CardRadius");
        BorderThickness=new Thickness(1); Padding=new Thickness(14);
        Child=body; Empty();
    }
    internal void Empty()
    {
        body.Children.Clear();
        body.Children.Add(Text("OPERATOR / FIELD REPORT",10,true,"Accent"));
        body.Children.Add(Text("干员详情",22,true,"Ink",new Thickness(0,2,0,8)));
        body.Children.Add(Text("点击排行中的干员，固定查看有效伤害、过量拆分与属性构成。",12,false,"Muted"));
        body.Children.Add(Text("近期输出归零后保留固定条目；新战局清除选择。",11,false,"Muted",new Thickness(0,6,0,0)));
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
        var entries=view.Get("entries").Items().ToArray();
        var entry=entries.FirstOrDefault(x=>x.Text("owner_id")==owner);
        int index=Array.FindIndex(entries,x=>x.Text("owner_id")==owner);
        string rank=index<0 ? "—" : (index+1).ToString("00");
        string share=entry.ValueKind==JsonValueKind.Object ? entry.Text("share_display") : "—";
        string shareLabel=view.Get("config").Text("ranking")=="recent" ? "近期贡献" : "本局贡献";
        string metric=view.Text("metric_label");
        string selected=entry.ValueKind==JsonValueKind.Object ? entry.Text("display_value") : "—";
        body.Children.Clear();

        var top=new Grid{Margin=new Thickness(0,0,0,10)};
        top.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});
        top.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(1,GridUnitType.Star)});
        top.ColumnDefinitions.Add(new ColumnDefinition{Width=GridLength.Auto});
        var rankBox=new Border{Background=Theme.Resource("Inset"),BorderBrush=Theme.Resource("Accent"),BorderThickness=new Thickness(1),
            CornerRadius=new CornerRadius(2),Padding=new Thickness(6,4,6,4),Margin=new Thickness(0,0,8,0),VerticalAlignment=VerticalAlignment.Center};
        var rankStack=new StackPanel();rankStack.Children.Add(Text("RANK",9,true,"Muted"));rankStack.Children.Add(Mono(rank,16,true,"Accent"));
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
        copy.Children.Add(Text(row.Get("present").ValueKind switch{JsonValueKind.True=>"在场",JsonValueKind.False=>"已离场",_=>"在场状态未知"},8,true,"Good"));
        copy.Children.Add(Text(row.Text("name"),18,true,"Ink"));
        identity.Children.Add(copy);Grid.SetColumn(identity,1);top.Children.Add(identity);

        var shareStack=new StackPanel{HorizontalAlignment=HorizontalAlignment.Right,VerticalAlignment=VerticalAlignment.Center};
        shareStack.Children.Add(Mono(share,20,true,"Accent"));
        shareStack.Children.Add(Text(shareLabel,9,false,"Muted"));
        Grid.SetColumn(shareStack,2);top.Children.Add(shareStack);
        body.Children.Add(top);

        var hero=new Border{Background=Theme.Resource("Inset"),BorderBrush=Theme.Resource("Accent"),BorderThickness=new Thickness(3,1,1,1),Padding=new Thickness(10,8,10,8),Margin=new Thickness(0,0,0,10)};
        var heroDock=new DockPanel();
        var heroValue=Mono(selected,28,true,"Accent");heroValue.HorizontalAlignment=HorizontalAlignment.Right;
        DockPanel.SetDock(heroValue,Dock.Right);heroDock.Children.Add(heroValue);
        heroDock.Children.Add(Text(entry.ValueKind==JsonValueKind.Object?metric:"本局指标",11,true,"Muted"));
        hero.Child=heroDock;body.Children.Add(hero);

        var stats=new WrapPanel{Margin=new Thickness(0,0,0,4)};
        AddStat(stats,"有效伤害",Effective(row));
        AddStat(stats,"过量伤害",row.UiInt("overkill_damage"));
        AddStat(stats,"治疗",row.UiInt("healing"));
        AddStat(stats,"有效护盾",row.UiInt("shield_absorbed"));
        if(stats.Children.Count>0) body.Children.Add(stats);
        body.Children.Add(Text(OverkillNote(row,view),10,false,"Muted",new Thickness(0,4,0,2)));
        if(row.Get("burst_unknown_reason").ValueKind==JsonValueKind.String)
            body.Children.Add(Text("元素爆条归属未知："+row.Text("burst_unknown_reason"),10,false,"Muted",new Thickness(0,2,0,0)));

        Section("被减伤率");
        Pair("被减伤率",Mitigation(row));
        var mitigationNote=MitigationNote(row,view);
        if(mitigationNote.Length>0) body.Children.Add(Text(mitigationNote,10,false,"Muted",new Thickness(0,5,0,0)));

        Section("伤害构成");
        if(entry.ValueKind==JsonValueKind.Object)
        {
            var bar=new ContributionBar{Data=entry,Height=9,Margin=new Thickness(0,0,0,6)};
            body.Children.Add(bar);
            var types=row.Get("damage_by_type");
            double total=new[]{"physical","arts","elemental","true"}.Sum(k=>types.TryGetProperty(k,out var v)&&v.ValueKind==JsonValueKind.Number?v.GetDouble():0);
            var mix=new WrapPanel();
            foreach(var (key,label,token) in new[]{("physical","物理","Physical"),("arts","法术","Arts"),("elemental","元素","Elemental"),("true","真实","True")})
            {
                double amount=types.TryGetProperty(key,out var v)&&v.ValueKind==JsonValueKind.Number?v.GetDouble():0;
                if(amount<=0) continue;
                string fraction=total>0 ? Math.Round(amount/total*100).ToString("0")+"%" : "—";
                var card=new Border{Background=Theme.Resource("Inset"),BorderBrush=Theme.Resource(token),BorderThickness=new Thickness(1,2,1,1),Padding=new Thickness(6,5,6,5),Margin=new Thickness(0,0,4,4),MinWidth=66};
                var stack=new StackPanel();stack.Children.Add(Text(label,9,false,"Muted"));stack.Children.Add(Mono(fraction,13,true,"Ink"));stack.Children.Add(Text(Json.UiIntText(amount.ToString(System.Globalization.CultureInfo.InvariantCulture)),9,false,"Muted"));
                card.Child=stack;mix.Children.Add(card);
            }
            body.Children.Add(mix);
        }

        Section("独立接敌速率");
        var rates=view.Get("rates").Get(owner);
        var damageRate=rates.Get("damage");var healingRate=rates.Get("healing");
        var rateStats=new WrapPanel();
        AddStat(rateStats,"DPS",RateText(row,damageRate,"engagement_damage"));
        AddStat(rateStats,"HPS",RateText(row,healingRate,"engagement_healing"));
        AddStat(rateStats,"伤害活跃秒",row.UiInt("active_damage"));
        AddStat(rateStats,"治疗活跃秒",row.UiInt("active_healing"));
        body.Children.Add(rateStats);
        var rateNote=damageRate.Text("note");if(rateNote.Length==0) rateNote=healingRate.Text("note");
        if(rateNote.Length>0) body.Children.Add(Text(rateNote,10,false,"Muted",new Thickness(0,5,0,0)));
        var rateReason=view.Text("rate_reason");if(rateReason.Length>0) body.Children.Add(Text(rateReason,10,false,"Muted",new Thickness(0,3,0,0)));
        var rateHint=view.Text("rate_hint");if(rateHint.Length>0) body.Children.Add(Text(rateHint,10,false,"Muted",new Thickness(0,3,0,0)));

        if(!row.Bool("event_coverage_complete"))
            body.Children.Add(Text("事件覆盖不完整：未建立事件覆盖的区间保持未知，不补零",10,false,"Muted",new Thickness(0,5,0,0)));

        Section("归属来源");
        string sources=string.Join("\n",row.Get("sources").Items().Select(x=>x.Text()));
        body.Children.Add(Text(sources.Length>0 ? sources : "—",10,false,"Muted"));
    }
    private static string RateText(JsonElement row,JsonElement rate,string exact)
    {
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
    private static string Mitigation(JsonElement row)
    {
        var value=row.Get("mitigation_rate");
        if(value.ValueKind!=JsonValueKind.Number) return "—";
        double number=value.GetDouble();
        return double.IsFinite(number) ? Math.Round(number,0).ToString("0")+"%" : "—";
    }
    private static string MitigationNote(JsonElement row,JsonElement view)
    {
        if(row.Get("mitigation_rate").ValueKind==JsonValueKind.Number) return "被减伤率 = 1 − 实际伤害 / 公式前伤害";
        string reason=row.Text("mitigation_unknown_reason");
        if(reason.Length>0) return "被减伤未知："+reason;
        return "被减伤未知：尚未采集公式前伤害；不推测、不补零。";
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
    private void AddStat(Panel host,string label,string value)
    {
        if(value=="0") return;
        var border=new Border{Background=Theme.Resource("Inset"),BorderBrush=Theme.Resource("Edge"),BorderThickness=new Thickness(1,2,1,1),Padding=new Thickness(7,5,7,5),Margin=new Thickness(0,0,4,4),MinWidth=92};
        var stack=new StackPanel();stack.Children.Add(Text(label,9,false,"Muted"));stack.Children.Add(Mono(value,13,true,"Ink"));
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
        var block=Text(text,size,bold,token);block.FontFamily=new FontFamily("Cascadia Mono, Consolas");return block;
    }
}
