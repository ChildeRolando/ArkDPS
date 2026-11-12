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
    public StatisticPanel Panel { get; }=new(){IsHud=true};
    internal event Action? PlacementChanged;
    internal event Action? ClosedByUser;
    internal bool SuppressClose;
    private const double StandardMinimumWidth=612;
    private bool? loneTrailSkin;
    private bool movingOrSizing, fitQueued;
    private int? restoredWidthPixels;
    public HudWindow(string id,Action show)
    {
        Style=(Style)FindResource(typeof(Window));PanelId=id;Title="ArkDPS · HUD · "+id;
        Width=680;MinWidth=StandardMinimumWidth;SizeToContent=SizeToContent.Height;
        WindowStyle=WindowStyle.None;AllowsTransparency=true;ResizeMode=ResizeMode.CanResizeWithGrip;
        ShowInTaskbar=false;Topmost=true;
        Background=Brushes.Transparent;
        Panel.MinWidth=0;
        Panel.PanelFrame.Margin=new Thickness(0);
        Panel.PanelFrame.BorderThickness=new Thickness(0);
        Panel.LegendRow.Visibility=Visibility.Collapsed;
        Panel.TitleRegion.Height=52;
        Panel.TitleText.Orientation=Orientation.Horizontal;
        Panel.Heading.FontSize=28;
        Panel.Heading.SetResourceReference(TextBlock.FontFamilyProperty,"InterfaceFont");
        Panel.PanelKind.FontSize=17;
        Panel.PanelKind.SetResourceReference(TextBlock.FontFamilyProperty,"InterfaceFont");
        Panel.PanelKind.Margin=new Thickness(18,0,0,0);
        Panel.PanelKind.VerticalAlignment=VerticalAlignment.Center;
        Panel.HudClose.MinWidth=44;
        Panel.HudClose.MinHeight=44;
        Panel.HudClose.Margin=new Thickness(0);
        Panel.TitleAccent.Height=23;
        Panel.LoneTrailHudMark.Height=23;
        Panel.LoneTrailHudMark.Width=14;
        Panel.Plot.MinHeight=0;
        Panel.Rows.MinHeight=StatisticPanel.HudRowHeight;
        Panel.Rows.MaxHeight=StatisticPanel.HudRowHeight*StatisticPanel.HudVisibleRows;
        Panel.Rows.Padding=new Thickness(0);
        Panel.Rows.ItemContainerStyle=(Style)Panel.FindResource("HudRankedRow");
        Panel.Rows.Template=(ControlTemplate)Panel.FindResource("HudRowsTemplate");
        Panel.Empty.FontSize=18;
        Panel.Empty.MaxWidth=440;
        Panel.Empty.Margin=new Thickness(0);
        Panel.CloseRequested=Close;
        Panel.TitleRegion.PreviewMouseLeftButtonDown+=DragTitle;
        ApplySkin();
        Content=Panel;
        SourceInitialized+=(_,_)=>HwndSource.FromHwnd(Native.Handle(this))?.AddHook(Hook);
        LocationChanged+=(_,_)=>PlacementChanged?.Invoke();
        SizeChanged+=(_,_)=>{QueueWorkAreaFit();PlacementChanged?.Invoke();};
        DpiChanged+=(_,_)=>QueueWorkAreaFit();
        Loaded+=(_,_)=>QueueWorkAreaFit();
        Closed+=(_,_)=>{if(!SuppressClose) ClosedByUser?.Invoke();};
    }
    private void ApplySkin()
    {
        bool loneTrail=Theme.Key=="lone-trail";
        if(loneTrailSkin==loneTrail) return;
        loneTrailSkin=loneTrail;

        Panel.PanelFrame.SetResourceReference(Border.BackgroundProperty,loneTrail ? "Console" : "Card");
        Panel.TitleBar.Margin=new Thickness(12,4,6,4);
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
            MinWidth=Math.Min(StandardMinimumWidth,MaxWidth);
            MaxHeight=work.Height/dpi.DpiScaleY;
            double preferredWidth=restoredWidthPixels is int saved ? saved/dpi.DpiScaleX : Width;
            restoredWidthPixels=null;
            Width=Math.Clamp(preferredWidth,MinWidth,MaxWidth);
            // The native resize grip switches WPF to manual sizing. Keep the
            // chosen width and reapply the list-driven height after the drag.
            SizeToContent=SizeToContent.Height;
            Native.KeepInWorkingArea(this,work);
        },DispatcherPriority.Loaded);
    }

    internal void RestorePlacement(JsonElement placement)
    {
        restoredWidthPixels=(int)placement.Number("width");
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
