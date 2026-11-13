using System;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
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
    internal Func<HudLayout,Task<bool>>? SaveScale { get; set; }
    internal bool SuppressClose;
    private const string ScaleHint="拖动以等比例缩放 HUD";
    private readonly Thumb scaleGrip;
    private readonly ScaleTransform contentScale=new(1,1);
    internal HudLayout LayoutProfile { get; private set; }=HudLayout.Default;
    internal double BaseWidth { get; private set; }=HudLayout.Default.Width;
    internal double DisplayScale => contentScale.ScaleX;
    private HudLayout? dragStart;
    private System.Drawing.Point dragPointer;
    private Vector dragExtent;
    private bool savingScale;
    private bool movingOrSizing, fitQueued;
    private int? restoredWidthPixels;
    public HudWindow(string id)
    {
        Style=(Style)FindResource(typeof(Window));PanelId=id;Title="ArkDPS · HUD · "+id;
        Width=BaseWidth;SizeToContent=SizeToContent.Height;
        WindowStyle=WindowStyle.None;AllowsTransparency=true;ResizeMode=ResizeMode.NoResize;
        ShowInTaskbar=false;Topmost=true;
        Background=Brushes.Transparent;
        Panel.ConfigureHud(Close);
        Panel.TitleRegion.PreviewMouseLeftButtonDown+=DragTitle;
        Panel.LayoutTransform=contentScale;
        scaleGrip=CreateScaleGrip();
        scaleGrip.DragStarted+=BeginScale;
        scaleGrip.DragDelta+=DragScale;
        scaleGrip.DragCompleted+=CompleteScale;
        var surface=new Grid();
        surface.Children.Add(Panel);
        surface.Children.Add(scaleGrip);
        Content=surface;
        SourceInitialized+=(_,_)=>HwndSource.FromHwnd(Native.Handle(this))?.AddHook(Hook);
        LocationChanged+=(_,_)=>PlacementChanged?.Invoke();
        SizeChanged+=(_,_)=>{QueueWorkAreaFit();if(dragStart==null) PlacementChanged?.Invoke();};
        DpiChanged+=(_,_)=>QueueWorkAreaFit();
        Loaded+=(_,_)=>QueueWorkAreaFit();
        Closed+=(_,_)=>{if(!SuppressClose) ClosedByUser?.Invoke();};
    }
    internal void ApplyHudLayout(HudLayout style)
    {
        LayoutProfile=style;
        BaseWidth=style.Width;
        restoredWidthPixels=null;
        Panel.ApplyHudLayout(style);
        if(!IsVisible)
        {
            contentScale.ScaleX=contentScale.ScaleY=style.Scale;
            Width=BaseWidth*style.Scale;
        }
        QueueWorkAreaFit();
    }

    private static Thumb CreateScaleGrip()
    {
        var grip=new Thumb
        {
            Width=20, Height=20, HorizontalAlignment=HorizontalAlignment.Right,
            VerticalAlignment=VerticalAlignment.Bottom, Cursor=Cursors.SizeNWSE,
            Focusable=false, ToolTip=ScaleHint
        };
        AutomationProperties.SetName(grip,"整体缩放");
        AutomationProperties.SetAutomationId(grip,"HudScaleGrip");
        var surface=new FrameworkElementFactory(typeof(Grid));
        surface.SetValue(System.Windows.Controls.Panel.BackgroundProperty,Brushes.Transparent);
        var glyph=new FrameworkElementFactory(typeof(System.Windows.Shapes.Path));
        glyph.SetValue(System.Windows.Shapes.Path.DataProperty,Geometry.Parse("M 2,9 L 9,2 M 6,9 L 9,6"));
        glyph.SetValue(System.Windows.Shapes.Shape.StrokeThicknessProperty,1.5);
        glyph.SetResourceReference(System.Windows.Shapes.Shape.StrokeProperty,"ConsoleMuted");
        glyph.SetValue(HorizontalAlignmentProperty,HorizontalAlignment.Right);
        glyph.SetValue(VerticalAlignmentProperty,VerticalAlignment.Bottom);
        glyph.SetValue(MarginProperty,new Thickness(0,0,3,3));
        glyph.SetValue(IsHitTestVisibleProperty,false);
        surface.AppendChild(glyph);
        grip.Template=new ControlTemplate(typeof(Thumb)){VisualTree=surface};
        return grip;
    }

    private void BeginScale(object sender,DragStartedEventArgs e)
    {
        if(Locked || savingScale)
        {
            scaleGrip.CancelDrag();
            return;
        }
        scaleGrip.ToolTip=ScaleHint;
        dragStart=LayoutProfile with {Width=BaseWidth};
        dragPointer=System.Windows.Forms.Cursor.Position;
        var bounds=Native.Bounds(this);
        dragExtent=new Vector(bounds[2],bounds[3]);
    }

    private void DragScale(object sender,DragDeltaEventArgs e)
    {
        if(dragStart is not { } initial) return;
        var pointer=System.Windows.Forms.Cursor.Position;
        var delta=new Vector(pointer.X-dragPointer.X,pointer.Y-dragPointer.Y);
        // Screen coordinates remain stable as the captured corner moves.
        double ratio=1+Vector.Multiply(delta,dragExtent)/dragExtent.LengthSquared;
        double value=Math.Clamp(Math.Round(initial.Scale*ratio,4),HudLayout.MinimumScale,HudLayout.MaximumScale);
        if(value==LayoutProfile.Scale) return;
        LayoutProfile=initial with {Scale=value};
        QueueWorkAreaFit();
    }

    private async void CompleteScale(object sender,DragCompletedEventArgs e)
    {
        if(dragStart is not { } initial) return;
        dragStart=null;
        if(e.Canceled)
            LayoutProfile=LayoutProfile with {Scale=initial.Scale};
        QueueWorkAreaFit();
        if(LayoutProfile.Scale==initial.Scale || SaveScale is not { } save) return;
        var changed=LayoutProfile;
        savingScale=true;
        try
        {
            if(!await save(changed) && LayoutProfile==changed)
            {
                ApplyHudLayout(initial);
                scaleGrip.ToolTip="缩放未保存，请检查采集连接后重试";
            }
        }
        finally
        {
            savingScale=false;
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
        if(message==wmNcHitTest && (Locked || Opacity==0)){handled=true;return new IntPtr(-1);} // HTTRANSPARENT
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
            if(restoredWidthPixels is int saved)
            {
                BaseWidth=Math.Clamp(saved/dpi.DpiScaleX,240,2000);
                restoredWidthPixels=null;
            }
            MaxWidth=work.Width/dpi.DpiScaleX;
            MaxHeight=work.Height/dpi.DpiScaleY;
            double scale=Math.Min(LayoutProfile.Scale,MaxWidth/BaseWidth);
            contentScale.ScaleX=contentScale.ScaleY=scale;
            double noticeHeight=Panel.RateNoticePanel.Visibility==Visibility.Visible
                ? Panel.RateNoticePanel.ActualHeight+Panel.RateNoticePanel.Margin.Bottom : 0;
            Panel.LimitHudViewport(MaxHeight/scale-LayoutProfile.TitleHeight-noticeHeight);
            Width=BaseWidth*scale;
            if(dragStart==null) Native.KeepInWorkingArea(this,work);
        },DispatcherPriority.Loaded);
    }

    internal void RestorePlacement(JsonElement placement,bool hasLayoutProfile)
    {
        // Older HUDs without a profile stored their adjustable width in
        // the window rectangle. A profile supplies its own unscaled width.
        if(!hasLayoutProfile)
            restoredWidthPixels=(int)placement.Number("width");
        Native.Restore(this,placement,restoreSize:false);
        SetOpacity(placement.Number("opacity",.9));
        SetLocked(placement.Bool("locked"));
        QueueWorkAreaFit();
    }
    public void SetLocked(bool value)
    {
        Native.Lock(this,value || Opacity==0);
        Locked=value;
        if(value) scaleGrip.CancelDrag();
        scaleGrip.Visibility=value ? Visibility.Collapsed : Visibility.Visible;
        PlacementChanged?.Invoke();
    }
    public void SetOpacity(double value)
    {
        value=Math.Clamp(value,0,1);
        bool updateInput=!Locked && (Opacity==0)!=(value==0);
        Opacity=value;
        if(updateInput) Native.Lock(this,value==0);
        PlacementChanged?.Invoke();
    }
    public void Update(JsonElement view,string status)=>Panel.Update(view);
    public object Placement()
    {
        var bounds=Native.Bounds(this);return new {panel_id=PanelId,x=bounds[0],y=bounds[1],width=bounds[2],height=bounds[3],opacity=Opacity,locked=Locked};
    }
}
