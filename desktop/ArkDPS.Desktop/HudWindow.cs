using System;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;

namespace ArkDPS.Desktop;

internal sealed class HudWindow : Window
{
    public string PanelId { get; }
    public bool Locked { get; private set; }
    public StatisticPanel Panel { get; }=new(){IsHud=true};
    internal event Action? PlacementChanged;
    internal event Action? ClosedByUser;
    internal bool SuppressClose;
    public HudWindow(string id,Action show)
    {
        Style=(Style)FindResource(typeof(Window));PanelId=id;Title="ArkDPS · HUD · "+id;Width=410;Height=430;MinWidth=350;MinHeight=280;
        WindowStyle=WindowStyle.None;AllowsTransparency=true;ResizeMode=ResizeMode.CanResizeWithGrip;
        ShowInTaskbar=false;Topmost=true;
        SetResourceReference(BackgroundProperty,"Card");
        Panel.Margin=new Thickness(0);Panel.CloseRequested=Close;
        Panel.TitleBar.MouseLeftButtonDown+=(_,e)=>{if(!Locked && e.OriginalSource is not Button){try{DragMove();}catch(InvalidOperationException){}}};
        Content=Panel;
        SourceInitialized+=(_,_)=>HwndSource.FromHwnd(Native.Handle(this))?.AddHook(Hook);
        LocationChanged+=(_,_)=>PlacementChanged?.Invoke();SizeChanged+=(_,_)=>PlacementChanged?.Invoke();
        Closed+=(_,_)=>{if(!SuppressClose) ClosedByUser?.Invoke();};
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
        Panel.Update(view);
    }
    public object Placement()
    {
        var bounds=Native.Bounds(this);return new {panel_id=PanelId,x=bounds[0],y=bounds[1],width=bounds[2],height=bounds[3],opacity=Opacity,locked=Locked};
    }
}
