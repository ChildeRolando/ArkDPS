using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;

namespace ArkDPS.Desktop;

public partial class HudLayoutEditor : Window
{
    private sealed record Field(string Key,string Label,int Minimum,int Maximum,
        Func<HudLayout,double> Read,Func<HudLayout,double,HudLayout> Write);

    private static readonly (string Label,Field[] Fields)[] Groups=
    {
        ("字体",new Field[]
        {
            new("TitleFont","标题字号",6,64,x=>x.TitleFont,(x,v)=>x with {TitleFont=v}),
            new("NameFont","干员字号",6,64,x=>x.NameFont,(x,v)=>x with {NameFont=v}),
            new("NumberFont","数字字号",6,64,x=>x.NumberFont,(x,v)=>x with {NumberFont=v}),
        }),
        ("尺寸与间距",new Field[]
        {
            new("Width","HUD 宽度",240,2000,x=>x.Width,(x,v)=>x with {Width=v}),
            new("TitleHeight","标题区高度",20,160,x=>x.TitleHeight,(x,v)=>x with {TitleHeight=v}),
            new("RowHeight","干员行高度",16,160,x=>x.RowHeight,(x,v)=>x with {RowHeight=v}),
            new("PortraitSize","头像大小",0,128,x=>x.PortraitSize,(x,v)=>x with {PortraitSize=v}),
            new("Padding","边缘留白",0,48,x=>x.Padding,(x,v)=>x with {Padding=v}),
            new("RowGap","行距",0,48,x=>x.RowGap,(x,v)=>x with {RowGap=v}),
            new("ColumnGap","列距",0,48,x=>x.ColumnGap,(x,v)=>x with {ColumnGap=v}),
        }),
        ("列宽",new Field[]
        {
            new("RankWidth","名次",0,600,x=>x.RankWidth,(x,v)=>x with {RankWidth=v}),
            new("PortraitWidth","头像",0,600,x=>x.PortraitWidth,(x,v)=>x with {PortraitWidth=v}),
            new("NameWidth","干员名",0,600,x=>x.NameWidth,(x,v)=>x with {NameWidth=v}),
            new("BarWidth","贡献条",0,600,x=>x.BarWidth,(x,v)=>x with {BarWidth=v}),
            new("TotalWidth","总量",0,600,x=>x.TotalWidth,(x,v)=>x with {TotalWidth=v}),
            new("RateWidth","每秒速率",0,600,x=>x.RateWidth,(x,v)=>x with {RateWidth=v}),
            new("ShareWidth","占比",0,600,x=>x.ShareWidth,(x,v)=>x with {ShareWidth=v}),
        }),
    };

    private readonly StatisticPanel preview=new();
    private readonly List<(Field Field,Slider Slider,TextBox Box)> inputs=new();
    private readonly HashSet<Field> invalid=new();
    private readonly Func<HudLayout?,Task<bool>> save;
    private HudLayout draft;
    private bool updating,reset,saving;

    internal HudLayoutEditor(string title,JsonElement view,HudLayout initial,Func<HudLayout?,Task<bool>> save)
    {
        this.save=save;
        draft=initial;
        InitializeComponent();
        SourceTitle.Text=title;
        preview.ConfigureHud();
        KeyboardNavigation.SetTabNavigation(preview,KeyboardNavigationMode.None);
        PreviewSurface.Child=preview;
        PreviewSurface.SizeChanged+=(_,_)=>UpdatePreviewSize();
        for(int index=0;index<Groups.Length;index++)
        {
            var group=Groups[index];
            var panel=new StackPanel();
            var card=new Border
            {
                Child=panel,Padding=new Thickness(12,8,12,8),
                Margin=new Thickness(0,0,index==Groups.Length-1 ? 0 : 10,0),
                Style=(Style)FindResource("SurfaceCard"),
            };
            Fields.Children.Add(card);
            panel.Children.Add(Inspector.Text(group.Label,14,true,"Ink",new Thickness(0,0,0,5)));
            if(group.Label=="列宽")
                panel.Children.Add(Inspector.Text("设为 0 可隐藏该列，其他列按可用宽度分配。",12,false,"Muted",
                    new Thickness(0,0,0,4)));
            foreach(var field in group.Fields) AddField(panel,field);
        }
        if(!view.Get("entries").Items().Any())
        {
            PreviewLabel.Text="示例预览";
            view=SampleView(view.Get("config"));
        }
        preview.Update(view);
        SetDraft(initial);
    }

    private void AddField(Panel panel,Field field)
    {
        var row=new Grid{Margin=new Thickness(0,0,0,2)};
        row.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(91)});
        row.ColumnDefinitions.Add(new ColumnDefinition());
        row.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(60)});
        var label=Inspector.Text(field.Label,13);
        label.VerticalAlignment=VerticalAlignment.Center;
        row.Children.Add(label);

        var slider=new Slider
        {
            Minimum=field.Minimum,Maximum=field.Maximum,SmallChange=1,LargeChange=8,TickFrequency=1,
            IsSnapToTickEnabled=true,VerticalAlignment=VerticalAlignment.Center,
            Margin=new Thickness(0,0,9,0),
        };
        slider.SetResourceReference(ForegroundProperty,"Accent");
        AutomationProperties.SetName(slider,field.Label+"滑块");
        Grid.SetColumn(slider,1);
        row.Children.Add(slider);

        var box=new TextBox{MinHeight=25,Padding=new Thickness(5,3,5,3),TextAlignment=TextAlignment.Right,
            MaxLength=9,VerticalAlignment=VerticalAlignment.Center};
        AutomationProperties.SetName(box,field.Label);
        AutomationProperties.SetAutomationId(box,"HudLayout"+field.Key);
        Grid.SetColumn(box,2);
        row.Children.Add(box);
        inputs.Add((field,slider,box));
        panel.Children.Add(row);

        slider.ValueChanged+=(_,_) =>
        {
            if(updating) return;
            updating=true;
            box.Text=slider.Value.ToString("0.#",CultureInfo.CurrentCulture);
            updating=false;
            SetField(field,box,slider.Value);
        };
        box.TextChanged+=(_,_) =>
        {
            if(updating) return;
            if(double.TryParse(box.Text,NumberStyles.AllowDecimalPoint,CultureInfo.CurrentCulture,out double value)
                && double.IsFinite(value) && value>=field.Minimum && value<=field.Maximum)
            {
                updating=true;
                slider.Value=value;
                updating=false;
                SetField(field,box,value);
            }
            else
            {
                invalid.Add(field);
                box.SetResourceReference(BorderBrushProperty,"Danger");
                UpdateSaveState();
            }
        };
    }

    private void SetField(Field field,TextBox box,double value)
    {
        invalid.Remove(field);
        box.SetResourceReference(BorderBrushProperty,"Edge");
        reset=false;
        draft=field.Write(draft,value);
        RefreshPreview();
        UpdateSaveState();
    }

    private void SetDraft(HudLayout layout)
    {
        draft=layout;
        updating=true;
        invalid.Clear();
        foreach(var (field,slider,box) in inputs)
        {
            double value=field.Read(draft);
            slider.Value=value;
            box.Text=value.ToString("0.##",CultureInfo.CurrentCulture);
            box.SetResourceReference(BorderBrushProperty,"Edge");
        }
        updating=false;
        RefreshPreview();
        UpdateSaveState();
    }

    private void RefreshPreview()
    {
        PreviewSurface.Width=draft.Width;
        preview.ApplyHudLayout(draft);
    }

    private void UpdatePreviewSize()
    {
        PreviewSize.Text=FormattableString.Invariant($"{PreviewSurface.ActualWidth:0.#} × {PreviewSurface.ActualHeight:0.#}");
    }

    private void UpdateSaveState()
    {
        SaveButton.IsEnabled=!saving && invalid.Count==0;
        var field=Groups.SelectMany(group=>group.Fields).FirstOrDefault(invalid.Contains);
        StatusText.Text=field is null ? "" : $"{field.Label}请输入 {field.Minimum}–{field.Maximum} 之间的数值";
    }

    private void ResetClick(object sender,RoutedEventArgs e)
    {
        reset=true;
        SetDraft(HudLayout.Default);
    }

    private async void SaveClick(object sender,RoutedEventArgs e)
    {
        if(saving || invalid.Count>0) return;
        saving=true;
        Fields.IsEnabled=ResetButton.IsEnabled=CancelButton.IsEnabled=SaveButton.IsEnabled=false;
        try
        {
            bool saved=await save(reset ? null : draft);
            saving=false;
            if(saved) Close();
            else
            {
                UpdateSaveState();
                StatusText.Text="布局尚未保存";
            }
        }
        finally
        {
            saving=false;
            Fields.IsEnabled=ResetButton.IsEnabled=CancelButton.IsEnabled=true;
        }
    }

    protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
    {
        if(saving) e.Cancel=true;
        base.OnClosing(e);
    }

    private static JsonElement SampleView(JsonElement config)
    {
        var operators=new[]
        {
            ("char_002_amiya","阿米娅",109500,1200),("char_017_huang","煌",100400,1080),
            ("char_003_kalts","凯尔希",91200,972),("char_010_chen","陈",82100,875),
            ("char_248_mgllan","麦哲伦",73000,778),
        };
        int sum=operators.Sum(item=>item.Item3);
        return JsonSerializer.SerializeToElement(new
        {
            config,share_scope="team",share_known=true,
            entries=operators.Select(item=>new
            {
                owner_id=item.Item1,name=item.Item2,
                display_value=Json.CompactAmount(item.Item3),
                rate_text=Json.CompactAmount(item.Item4)+"/s",
                share_display=FormattableString.Invariant($"{(double)item.Item3/sum:P1}"),
                bar_ratio=(double)item.Item3/operators[0].Item3,bar_color="#58C7E5",
            }),
        });
    }
}
