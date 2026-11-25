using System;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Interop;
using ArkDPS.Desktop;

internal static class GameFpsSidebarTests
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    internal static void Run()
    {
        var view = new MainWindow();
        var update = typeof(MainWindow).GetMethod("UpdateGameFpsStatus", Private)!;
        var expire = typeof(MainWindow).GetMethod("ExpireGameFpsStatus", Private)!;
        var action = typeof(MainWindow).GetMethod("SetStackCaptureAction", Private)!;
        var captureStatus = typeof(MainWindow).GetMethod("SetStackCaptureStatus", Private)!;

        void Publish(string status, double? fps, int? sequence, string source = "dxgi_present",
            long created = 123456789, string? endUtc = null, string? logError = null, string? observedUtc = null)
        {
            // FPS still renders when there is no battle ID, enemy state or statistics.
            var state = JsonSerializer.SerializeToElement(new
            {
                status = "waiting",
                game_fps = new { status, fps, source, sample_seq = sequence, pid = 6789,
                    process_created_filetime = created, window_end_utc = endUtc, log_error = logError,
                    observed_at_utc = observedUtc }
            });
            update.Invoke(view, new object[] { state });
        }

        bool Shows(string text) => view.MainGameFpsStatus.Text == text && view.SideGameFpsStatus.Text == text;

        try
        {
            Publish("warming", null, 1);
            Check(Shows("FPS —"), "warming does not imply zero FPS");
            Publish("ok", 0, 2);
            Check(Shows("FPS 0"), "a complete frame window with zero presents shows zero FPS");
            Publish("ok", 60, 2);
            Check(Shows("FPS 0"), "a repeated window does not refresh the display");
            expire.Invoke(view, new object?[] { null, EventArgs.Empty });
            Publish("ok", 0, 2);
            Check(Shows("FPS —"), "an expired window cannot reappear through a repeated reply");
            Publish("ok", 60, 2, created: 123456790);
            Check(Shows("FPS 60"), "the same PID and sequence can begin a fresh window after a restart");
            Publish("ok", 60, 6, endUtc: "2020-01-01T00:00:00Z");
            Check(Shows("FPS —"), "a late reply cannot start a freshness timer for an old window");
            Publish("ok", 60, 7, endUtc: DateTimeOffset.UtcNow.AddSeconds(-6).ToString("O"),
                observedUtc: DateTimeOffset.UtcNow.ToString("O"));
            Check(Shows("FPS 60"), "a fresh publication can carry an earlier native DXGI window");
            Publish("ok", 60, null);
            Check(Shows("FPS —"), "a reading without a window identity remains unknown");
            Publish("ok", 1.18, 3, "surfaceflinger");
            Check(Shows("FPS 1.2") && (view.SideGameFpsStatus.ToolTip as string)?.Contains("SurfaceFlinger") == true,
                "an independent new frame window reports its actual rate and source");
            Publish("ok", 99, 3, "surfaceflinger", logError: "access_denied");
            Check(Shows("FPS 1.2") && (view.MainGameFpsStatus.ToolTip as string)?.Contains("帧率日志写入失败") == true,
                "a journal warning appears without changing or refreshing a repeated reading");
            Publish("lost", 60, 4);
            Check(Shows("FPS —"), "a frame observation gap cannot retain the prior value");
            Publish("ok", 59.4, 5);

            view.SideRoot.Visibility = Visibility.Visible;
            view.ContentRoot.Visibility = Visibility.Collapsed;
            void ArrangeSide()
            {
                var area = new Size(320, 270);
                view.SideRoot.Measure(area);
                view.SideRoot.Arrange(new Rect(area));
                view.SideRoot.UpdateLayout();
            }
            foreach (string theme in new[] { "lone-trail", "people", "babel" })
            {
                Theme.Apply(theme);
                foreach (bool recording in new[] { false, true })
                {
                    action.Invoke(view, new object[] { recording, true });
                    ArrangeSide();
                    var sampling = view.SideStackCaptureButton;
                    var restore = view.LeaveSideMode;
                    double left = sampling.TranslatePoint(new Point(), view.SideToolbar).X;
                    double right = restore.TranslatePoint(new Point(), view.SideToolbar).X;
                    Check(sampling.ActualWidth > 0 && left >= 0 && left + sampling.ActualWidth <= right &&
                          right + restore.ActualWidth <= view.SideToolbar.ActualWidth,
                        theme + " capture fits immediately left of restore at 320 DIPs");
                    Check(Grid.GetColumn(sampling) + 1 == Grid.GetColumn(restore) && sampling.Focusable,
                        "the sample action retains its toolbar order and keyboard focus");
                    Check(AutomationProperties.GetName(sampling).StartsWith(recording ? "结束并保存" : "开始"),
                        "the capture button announces its current action");
                }
            }

            captureStatus.Invoke(view, new object[] { "正在保存 ETL 文件…" });
            action.Invoke(view, new object[] { true, false });
            Check(!view.SideStackCaptureButton.IsEnabled && view.SideStackCaptureStatus.Text == view.StackCaptureStatus.Text,
                "saving disables the same capture button and mirrors status across the two views");
            Check(new WindowInteropHelper(view).Handle == IntPtr.Zero, "headless FPS review creates no HWND");

            string? evidencePath = Environment.GetEnvironmentVariable("ARKDPS_FPS_VIEW_EVIDENCE_DIR");
            if (!string.IsNullOrWhiteSpace(evidencePath))
            {
                Theme.Apply("lone-trail");
                Directory.CreateDirectory(evidencePath);
                ArrangeSide();
                File.WriteAllBytes(Path.Combine(evidencePath, "side-fps-saving.png"), Screenshot.Capture(view.SideRoot));
                view.SideStackCaptureStatus.Visibility = Visibility.Collapsed;
                action.Invoke(view, new object[] { false, true });
                ArrangeSide();
                File.WriteAllBytes(Path.Combine(evidencePath, "side-fps-320.png"), Screenshot.Capture(view.SideRoot));

                view.ContentRoot.Visibility = Visibility.Visible;
                view.SideRoot.Visibility = Visibility.Collapsed;
                var frame = view.LiveOverviewFrame;
                var size = new Size(900, 304);
                frame.Measure(size);
                frame.Arrange(new Rect(size));
                frame.UpdateLayout();
                File.WriteAllBytes(Path.Combine(evidencePath, "main-fps-status.png"), Screenshot.Capture(frame));
            }
        }
        finally
        {
            typeof(MainWindow).GetMethod("StopGameFpsStatus", Private)!.Invoke(view, null);
            typeof(MainWindow).GetField("quitting", Private)!.SetValue(view, true);
            view.Close();
            ((IDisposable)typeof(MainWindow).GetField("recoveryCancellation", Private)!.GetValue(view)!).Dispose();
            Theme.Apply("lone-trail");
        }
        Console.WriteLine("PASS_GAME_FPS_SIDEBAR (offscreen layout and injected samples only)");
    }
}
