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
    private readonly TextBlock state;
    internal event Action? PlacementChanged;
    internal event Action? ClosedByUser;
    internal bool SuppressClose;
    public HudWindow(string id,Action show)
    {
        Style=(Style)FindResource(typeof(Window));PanelId=id;Title="ArkDPS · HUD · "+id;Width=410;Height=430;MinWidth=350;MinHeight=280;
        WindowStyle=WindowStyle.None;AllowsTransparency=true;ResizeMode=ResizeMode.CanResizeWithGrip;
        ShowInTaskbar=false;Topmost=true;
        var dock=new DockPanel();dock.SetResourceReference(System.Windows.Controls.Panel.BackgroundProperty,"Card");SetResourceReference(BackgroundProperty,"Card");
        var header=new DockPanel{LastChildFill=true,Margin=new Thickness(12,8,8,8)};
        var close=new Button{Content="关闭",Padding=new Thickness(8,3,8,3)};close.Click+=(_,_)=>Close();DockPanel.SetDock(close,Dock.Right);header.Children.Add(close);
        var gui=new Button{Content="主界面",Padding=new Thickness(8,3,8,3)};gui.Click+=(_,_)=>show();DockPanel.SetDock(gui,Dock.Right);header.Children.Add(gui);
        header.Children.Add(Inspector.Text("ArkDPS / HUD",14,true));
        header.MouseLeftButtonDown+=(_,e)=>{if(!Locked && e.OriginalSource is not Button){try{DragMove();}catch(InvalidOperationException){}}};
        DockPanel.SetDock(header,Dock.Top);dock.Children.Add(header);
        state=Inspector.Text("等待战斗数据",10,false,"Muted",new Thickness(12,0,18,10));DockPanel.SetDock(state,Dock.Bottom);dock.Children.Add(state);
        state.MaxHeight=42;Panel.Margin=new Thickness(0);dock.Children.Add(Panel);
        Content=dock;
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
        Panel.Update(view);state.Text=(Locked ? "已锁定 · 点击穿透 · " : "解锁 · 可拖动 / 调整大小 · ")+status;
    }
    public object Placement()
    {
        var bounds=Native.Bounds(this);return new {panel_id=PanelId,x=bounds[0],y=bounds[1],width=bounds[2],height=bounds[3],opacity=Opacity,locked=Locked};
    }
}
