using System;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;

namespace ArkDPS.Desktop;

internal sealed class HudWindow : Window
{
    public string PanelId { get; }
    public bool Locked { get; private set; }
    public StatisticPanel Panel { get; }=new();
    internal event Action? PlacementChanged;
    internal event Action? ClosedByUser;
    internal bool SuppressClose;
    private const double StandardMinimumWidth=548;
    internal HudLayout LayoutProfile { get; private set; }=HudLayout.Default;
    private double ProfileMinimumWidth => Math.Min(StandardMinimumWidth,LayoutProfile.Width);
    private double? preferredWidthDips;
    private bool movingOrSizing, fitQueued;
    private int? restoredWidthPixels;
    public HudWindow(string id,Action show)
    {
        Style=(Style)FindResource(typeof(Window));PanelId=id;Title="ArkDPS · HUD · "+id;
        Width=LayoutProfile.Width;MinWidth=ProfileMinimumWidth;SizeToContent=SizeToContent.Height;
        WindowStyle=WindowStyle.None;AllowsTransparency=true;ResizeMode=ResizeMode.CanResizeWithGrip;
        ShowInTaskbar=false;Topmost=true;
        Background=Brushes.Transparent;
        Panel.ConfigureHud(Close);
        Panel.TitleRegion.PreviewMouseLeftButtonDown+=DragTitle;
        Content=Panel;
        SourceInitialized+=(_,_)=>HwndSource.FromHwnd(Native.Handle(this))?.AddHook(Hook);
        LocationChanged+=(_,_)=>PlacementChanged?.Invoke();
        SizeChanged+=(_,_)=>{QueueWorkAreaFit();PlacementChanged?.Invoke();};
        DpiChanged+=(_,_)=>QueueWorkAreaFit();
        Loaded+=(_,_)=>QueueWorkAreaFit();
        Closed+=(_,_)=>{if(!SuppressClose) ClosedByUser?.Invoke();};
    }
    internal void ApplyHudLayout(HudLayout style,bool applyWidth=true)
    {
        LayoutProfile=style;
        Panel.ApplyHudLayout(style);
        if(applyWidth)
        {
            preferredWidthDips=style.Width;
            restoredWidthPixels=null;
        }
        if(!IsVisible && applyWidth)
        {
            MinWidth=ProfileMinimumWidth;
            Width=style.Width;
        }
        QueueWorkAreaFit();
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
        const int wmEnterSizeMove=0x0231,wmExitSizeMove=0x0232,wmNcHitTest=0x0084;
        if(message==wmNcHitTest && Locked){handled=true;return new IntPtr(-1);} // HTTRANSPARENT
        if(message==wmEnterSizeMove) movingOrSizing=true;
        if(message==wmExitSizeMove)
        {
            movingOrSizing=false;
            QueueWorkAreaFit();
        }
        return IntPtr.Zero;
    }

    private void QueueWorkAreaFit()
    {
        if(fitQueued || movingOrSizing || !IsVisible) return;
        fitQueued=true;
        _=Dispatcher.InvokeAsync(()=>
        {
            fitQueued=false;
            if(movingOrSizing || !IsVisible) return;
            var work=System.Windows.Forms.Screen.FromHandle(Native.Handle(this)).WorkingArea;
            var dpi=VisualTreeHelper.GetDpi(this);
            MaxWidth=work.Width/dpi.DpiScaleX;
            MinWidth=Math.Min(ProfileMinimumWidth,MaxWidth);
            MaxHeight=work.Height/dpi.DpiScaleY;
            double noticeHeight=Panel.RateNoticePanel.Visibility==Visibility.Visible
                ? Panel.RateNoticePanel.ActualHeight+Panel.RateNoticePanel.Margin.Bottom : 0;
            Panel.LimitHudViewport(MaxHeight-LayoutProfile.TitleHeight-noticeHeight);
            double preferredWidth=preferredWidthDips??(restoredWidthPixels is int saved ? saved/dpi.DpiScaleX : Width);
            preferredWidthDips=null;
            restoredWidthPixels=null;
            Width=Math.Clamp(preferredWidth,MinWidth,MaxWidth);
            // The native resize grip switches WPF to manual sizing. Keep the
            // chosen width and reapply the list-driven height after the drag.
            SizeToContent=SizeToContent.Height;
            Native.KeepInWorkingArea(this,work);
        },DispatcherPriority.Loaded);
    }

    internal void RestorePlacement(JsonElement placement,bool restoreWidth=true)
    {
        preferredWidthDips=restoreWidth ? null : LayoutProfile.Width;
        restoredWidthPixels=restoreWidth ? (int)placement.Number("width") : null;
        Native.Restore(this,placement,restoreHeight:false);
        SetOpacity(placement.Number("opacity",.9));
        SetLocked(placement.Bool("locked"));
        QueueWorkAreaFit();
    }
    public void SetLocked(bool value)
    {
        Native.Lock(this,value); Locked=value;PlacementChanged?.Invoke();
    }
    public void SetOpacity(double value)
    {
        Opacity=Math.Clamp(value,.7,1);PlacementChanged?.Invoke();
    }
    public void Update(JsonElement view,string status)=>Panel.Update(view);
    public object Placement()
    {
        var bounds=Native.Bounds(this);return new {panel_id=PanelId,x=bounds[0],y=bounds[1],width=bounds[2],height=bounds[3],opacity=Opacity,locked=Locked};
    }
}
