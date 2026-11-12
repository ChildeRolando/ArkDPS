using System;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;

namespace ArkDPS.Desktop;

internal sealed class HudWindow : Window
{
    public string PanelId { get; }
    public bool Locked { get; private set; }
    public StatisticPanel Panel { get; }=new(){IsHud=true};
    internal event Action? PlacementChanged;
    internal event Action? ClosedByUser;
    internal bool SuppressClose;
    private bool? loneTrailSkin;
    public HudWindow(string id,Action show)
    {
        Style=(Style)FindResource(typeof(Window));PanelId=id;Title="ArkDPS · HUD · "+id;Width=410;Height=430;MinWidth=350;MinHeight=280;
        WindowStyle=WindowStyle.None;AllowsTransparency=true;ResizeMode=ResizeMode.CanResizeWithGrip;
        ShowInTaskbar=false;Topmost=true;
        Background=Brushes.Transparent;
        Panel.MinWidth=0;
        Panel.PanelFrame.Margin=new Thickness(0);
        Panel.PanelFrame.BorderThickness=new Thickness(0);
        Panel.LegendRow.Visibility=Visibility.Collapsed;
        Panel.CloseRequested=Close;
        Panel.TitleRegion.PreviewMouseLeftButtonDown+=DragTitle;
        ApplySkin();
        Content=Panel;
        SourceInitialized+=(_,_)=>HwndSource.FromHwnd(Native.Handle(this))?.AddHook(Hook);
        LocationChanged+=(_,_)=>PlacementChanged?.Invoke();SizeChanged+=(_,_)=>PlacementChanged?.Invoke();
        Closed+=(_,_)=>{if(!SuppressClose) ClosedByUser?.Invoke();};
    }
    private void ApplySkin()
    {
        bool loneTrail=Theme.Key=="lone-trail";
        if(loneTrailSkin==loneTrail) return;
        loneTrailSkin=loneTrail;

        Panel.PanelFrame.SetResourceReference(Border.BackgroundProperty,loneTrail ? "Console" : "Card");
        Panel.TitleBar.Margin=loneTrail ? new Thickness(10,3,4,3) : new Thickness(14,10,12,8);
        Panel.TitleAccent.Visibility=loneTrail ? Visibility.Collapsed : Visibility.Visible;
        Panel.LoneTrailHudMark.Visibility=loneTrail ? Visibility.Visible : Visibility.Collapsed;
        Panel.TitleRegion.BorderThickness=loneTrail ? new Thickness(0,0,0,1) : new Thickness(0);
        if(loneTrail)
        {
            Panel.PanelFrame.CornerRadius=new CornerRadius(0);
            Panel.TitleRegion.SetResourceReference(Border.BackgroundProperty,"ConsoleCard");
            Panel.TitleRegion.SetResourceReference(Border.BorderBrushProperty,"ConsoleLine");
            Panel.HudClose.SetResourceReference(StyleProperty,"Quiet");
            Panel.HudClose.BorderBrush=Brushes.Transparent;
        }
        else
        {
            Panel.PanelFrame.SetResourceReference(Border.CornerRadiusProperty,"CardRadius");
            Panel.TitleRegion.Background=Brushes.Transparent;
            Panel.TitleRegion.ClearValue(Border.BorderBrushProperty);
            Panel.HudClose.ClearValue(StyleProperty);
            Panel.HudClose.SetResourceReference(Control.BorderBrushProperty,"Line");
        }
    }

    internal bool IsTitleDragSource(DependencyObject? source)
    {
        if(Locked) return false;
        for(var current=source;current!=null;current=current switch
            {
                Visual visual=>VisualTreeHelper.GetParent(visual),
                FrameworkContentElement text=>text.Parent,
                _=>null
            })
        {
            if(current is ButtonBase) return false;
            if(ReferenceEquals(current,Panel.TitleRegion)) return true;
        }
        return false;
    }

    private void DragTitle(object sender,MouseButtonEventArgs e)
    {
        if(e.ButtonState!=MouseButtonState.Pressed || !IsTitleDragSource(e.OriginalSource as DependencyObject)) return;
        e.Handled=true;
        try { DragMove(); }
        catch(InvalidOperationException) { }
    }

    private IntPtr Hook(IntPtr hwnd,int message,IntPtr wParam,IntPtr lParam,ref bool handled)
    {
        if(message==0x84 && Locked){handled=true;return new IntPtr(-1);} // HTTRANSPARENT
        return IntPtr.Zero;
    }
    public void SetLocked(bool value)
    {
        Native.Lock(this,value); Locked=value;PlacementChanged?.Invoke();
    }
    public void SetOpacity(double value)
    {
        Opacity=Math.Clamp(value,.7,1);PlacementChanged?.Invoke();
    }
    public void Update(JsonElement view,string status)
    {
        ApplySkin();
        Panel.Update(view);
    }
    public object Placement()
    {
        var bounds=Native.Bounds(this);return new {panel_id=PanelId,x=bounds[0],y=bounds[1],width=bounds[2],height=bounds[3],opacity=Opacity,locked=Locked};
    }
}
