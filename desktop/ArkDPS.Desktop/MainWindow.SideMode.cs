using System;
using System.Drawing;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;
using Forms = System.Windows.Forms;

namespace ArkDPS.Desktop;

public partial class MainWindow
{
    private const int SideWidth = 320;
    private readonly EmulatorAttachment SideAttachment = new();
    private readonly DispatcherTimer SideAttachmentTimer = new() { Interval = TimeSpan.FromMilliseconds(200) };
    private bool sideMode;
    private nint minimizedPlayer;
    private bool sideMinimizedWithPlayer;
    private (double MinWidth, double MinHeight, ResizeMode ResizeMode, bool Maximized) fullWindow;
    private string? lastAttachmentError;

    private async void ToggleSideMode(object sender, RoutedEventArgs e)
    {
        if (!initialized || closingRequested || quitting) return;
        SetSideMode(!sideMode);
        await SavePlacement();
    }

    private void SetSideMode(bool enabled)
    {
        if (sideMode == enabled) return;
        save.Stop();
        if (enabled)
        {
            RememberNormalGeometry();
            fullWindow = (MinWidth, MinHeight, ResizeMode, WindowState == WindowState.Maximized);
            sideMode = true;
            WindowState = WindowState.Normal;
            MinWidth = 0;
            MinHeight = 0;
            ResizeMode = ResizeMode.NoResize;
            LiveBody.Children.Remove(EnemySummary);
            SideEnemyHost.Content = EnemySummary;
            EnemySummary.Margin = new Thickness(0);
            TargetSummary.SetDocked(true);
            ContentRoot.Visibility = Visibility.Collapsed;
            SideRoot.Visibility = Visibility.Visible;
            SideTabs.SelectedIndex = route == "enemies" ? 1 : 0;
            RefreshSideRanking();
            ApplyShellLayout();
            UpdateSidePlacement(placeFallback: true);
            SideAttachmentTimer.Start();
            LeaveSideMode.Focus();
            return;
        }

        SideAttachmentTimer.Stop();
        SideAttachment.RaiseAlongsidePlayer(default, 0);
        minimizedPlayer = 0;
        if (sideMinimizedWithPlayer) RestoreSideWindow();
        SideEnemyHost.Content = null;
        LiveBody.Children.Insert(0, EnemySummary);
        TargetSummary.SetDocked(false);
        SideRoot.Visibility = Visibility.Collapsed;
        ContentRoot.Visibility = Visibility.Visible;
        MinWidth = fullWindow.MinWidth;
        MinHeight = fullWindow.MinHeight;
        ResizeMode = fullWindow.ResizeMode;
        if (normalGeometry is not null) Native.Restore(this, JsonSerializer.SerializeToElement(normalGeometry));
        if (fullWindow.Maximized) WindowState = WindowState.Maximized;
        sideMode = false;
        ApplyShellLayout();
        ArrangePanels();
        QueueWorkAreaLayout();
        EnterSideMode.Focus();
    }

    private void RefreshSideRanking()
    {
        var firstPanel = PanelHost.Children.OfType<StatisticPanel>().FirstOrDefault();
        SideFriendly.Update(DisplayedBattleId, firstPanel?.View ?? default);
    }

    private void UpdateSidePlacement(bool placeFallback = false, bool allowMinimize = true)
    {
        if (!sideMode || !IsVisible || closingRequested || WindowState == WindowState.Maximized)
        {
            SideAttachment.RaiseAlongsidePlayer(default, 0);
            return;
        }

        try
        {
            nint window = Native.Handle(this);
            var placement = SideAttachment.GetPlacement(State.Get("diagnostic"), window, SideWidth,
                State.Get("connection_config"));
            if (placement.State == EmulatorPlacementState.Minimized)
            {
                // A taskbar or tray restore remains usable during the same MuMu minimize.
                bool justMinimized = minimizedPlayer != placement.PlayerWindow;
                minimizedPlayer = placement.PlayerWindow;
                if (allowMinimize && justMinimized && WindowState == WindowState.Normal)
                {
                    sideMinimizedWithPlayer = true;
                    WindowState = WindowState.Minimized;
                }
                SetSideAttachmentStatus("MuMu 已最小化");
                SideAttachment.RaiseAlongsidePlayer(default, 0);
                return;
            }

            minimizedPlayer = 0;
            if (sideMinimizedWithPlayer) RestoreSideWindow();
            if (WindowState != WindowState.Normal) return;
            if (placement.Bounds is Rectangle bounds)
                Native.Place(this, bounds);
            else
                FitUnattachedSideWindow(window, placeFallback);

            SetSideAttachmentStatus(placement.State switch
            {
                EmulatorPlacementState.Attached => "",
                EmulatorPlacementState.NoRoom => "侧边空间不足，暂靠屏幕边缘",
                EmulatorPlacementState.Ambiguous => "请在设置中选择要吸附的 MuMu 实例",
                _ => "等待 MuMu 窗口"
            });
            SideAttachment.RaiseAlongsidePlayer(placement, window);
            lastAttachmentError = null;
        }
        catch (System.ComponentModel.Win32Exception error)
        {
            if (lastAttachmentError != error.Message) RecordUiError(error);
            lastAttachmentError = error.Message;
            SetSideAttachmentStatus("无法吸附 MuMu 窗口");
        }
    }

    private void RestoreSideWindow()
    {
        sideMinimizedWithPlayer = false;
        bool activate = ShowActivated;
        ShowActivated = false;
        try { WindowState = WindowState.Normal; }
        finally { ShowActivated = activate; }
    }

    private void FitUnattachedSideWindow(nint window, bool entering)
    {
        var work = Forms.Screen.FromHandle(window).WorkingArea;
        var current = Native.Bounds(this);
        if (!entering && work.Contains(new Rectangle(current[0], current[1], current[2], current[3]))) return;

        var dpi = VisualTreeHelper.GetDpi(this);
        int width = Math.Min(work.Width, (int)Math.Round(SideWidth * dpi.DpiScaleX));
        int height = Math.Min(work.Height, entering ? (int)Math.Round(720 * dpi.DpiScaleY) : current[3]);
        int top = entering ? work.Top : Math.Clamp(current[1], work.Top, work.Bottom - height);
        Native.Place(this, new Rectangle(work.Right - width, top, width, height));
    }

    private void SetSideAttachmentStatus(string text)
    {
        SideAttachmentStatus.Text = text;
        SideAttachmentStatus.Visibility = text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
    }
}
