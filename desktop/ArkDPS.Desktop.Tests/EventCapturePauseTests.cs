using System;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Interop;
using ArkDPS.Desktop;

internal static class EventCapturePauseTests
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private static int passed;

    private static void Check(string name, bool condition)
    {
        if (!condition) throw new InvalidOperationException(name);
        passed++;
    }

    internal static void Run()
    {
        if (Application.Current is null) { var app = new App(); app.InitializeComponent(); }
        var window = new MainWindow();
        var update = typeof(MainWindow).GetMethod("UpdateEventCaptureControl", Private)!;
        var failure = typeof(MainWindow).GetMethod("SetEventCaptureRequestError", Private)!;
        var request = typeof(MainWindow).GetMethod("RequestEventCaptureToggle", Private)!;
        var fps = typeof(MainWindow).GetMethod("UpdateGameFpsStatus", Private)!;
        var pending = typeof(MainWindow).GetField("eventCaptureCommandPending", Private)!;
        var requested = typeof(MainWindow).GetField("eventCaptureRequestedPause", Private)!;
        var unavailable = typeof(MainWindow).GetField("workerUnavailable", Private)!;
        var localError = typeof(MainWindow).GetField("eventCaptureRequestError", Private)!;
        string? evidence = Environment.GetEnvironmentVariable("ARKDPS_EVENT_CAPTURE_EVIDENCE_DIR");
        if (!string.IsNullOrWhiteSpace(evidence)) Directory.CreateDirectory(evidence);
        string Status() => AutomationProperties.GetItemStatus(window.SideEventCaptureButton);

        void Publish(string state = "running", bool paused = false, bool wanted = false,
            bool supported = true, string reason = "", int revision = 1)
        {
            window.State = JsonSerializer.SerializeToElement(new
            {
                battle_id = "owned-event-pause", totals = new { damage = 12345 },
                event_capture_control = new { supported, requested_paused = wanted, paused, state, revision, reason },
            });
            update.Invoke(window, null);
        }

        void Arrange()
        {
            var area = new Size(320, 420);
            window.SideRoot.Measure(area);
            window.SideRoot.Arrange(new Rect(area));
            window.SideRoot.UpdateLayout();
        }

        void Capture(string skin, string state)
        {
            Arrange();
            var button = window.SideEventCaptureButton;
            var fpsText = window.SideGameFpsStatus;
            Point buttonPoint = button.TranslatePoint(new Point(), window.SideStatusBar);
            Point fpsPoint = fpsText.TranslatePoint(new Point(), window.SideStatusBar);
            Check(skin + state + " event action shares the existing FPS row at the bottom",
                ReferenceEquals(button.Parent, fpsText.Parent) && !window.SideToolbar.IsAncestorOf(button) &&
                Math.Abs(buttonPoint.Y + button.ActualHeight / 2 - fpsPoint.Y - fpsText.ActualHeight / 2) < 1 &&
                button.ActualWidth >= 82 && button.ActualHeight >= 30);
            Check(skin + state + " action and FPS fit the 320-DIP sidebar without overlap",
                fpsPoint.X >= 0 && fpsPoint.X + fpsText.ActualWidth <= buttonPoint.X &&
                buttonPoint.X + button.ActualWidth <= window.SideStatusBar.ActualWidth &&
                buttonPoint.Y + button.ActualHeight <= window.SideStatusBar.ActualHeight);
            if (!string.IsNullOrWhiteSpace(evidence))
                File.WriteAllBytes(Path.Combine(evidence, skin + "-event-" + state + ".png"),
                    Screenshot.Capture(window.SideRoot));
        }

        try
        {
            window.SideRoot.Visibility = Visibility.Visible;
            window.ContentRoot.Visibility = Visibility.Collapsed;
            window.Status.Text = "正在统计";
            window.SideDataStatus.GetBindingExpression(TextBlock.TextProperty)!.UpdateTarget();
            foreach (string skin in new[] { "lone-trail", "people", "babel" })
            {
                Theme.Apply(skin);
                localError.SetValue(window, "");
                Publish();
                Check(skin + " running offers a keyboard-accessible pause action",
                    window.SideEventCaptureButton.Content as string == "暂停事件" && window.SideEventCaptureButton.IsEnabled &&
                    window.SideEventCaptureButton.Focusable && window.SideEventCaptureButton.IsTabStop &&
                    AutomationProperties.GetName(window.SideEventCaptureButton) == "暂停逐次事件采集");
                Check(skin + " scope and the unrecoverable event gap are explained",
                    AutomationProperties.GetHelpText(window.SideEventCaptureButton).Contains("累计快照与FPS继续") &&
                    AutomationProperties.GetHelpText(window.SideEventCaptureButton).Contains("无法补采"));
                Capture(skin, "running");

                pending.SetValue(window, true); requested.SetValue(window, true);
                update.Invoke(window, null);
                Check(skin + " local request pending never claims producer pause confirmation",
                    window.SideEventCaptureButton.Content as string == "正在暂停…" && !window.SideEventCaptureButton.IsEnabled &&
                    Status() == "等待采集端确认" && !window.State.Get("event_capture_control").Bool("paused"));
                pending.SetValue(window, false);
                Publish("pausing", wanted: true);
                Check(skin + " producer pausing remains disabled until confirmation",
                    window.SideEventCaptureButton.Content as string == "正在暂停…" && !window.SideEventCaptureButton.IsEnabled);
                Capture(skin, "pausing");

                Publish("paused", paused: true, wanted: true);
                Check(skin + " only confirmed pause offers resume",
                    window.SideEventCaptureButton.Content as string == "继续事件" && window.SideEventCaptureButton.IsEnabled &&
                    Status() == "事件已暂停 · 快照与FPS继续");
                fps.Invoke(window, new object[] { JsonSerializer.SerializeToElement(new
                {
                    game_fps = new { status = "ok", fps = 45, source = "dxgi_present", sample_seq = skin.Length,
                        pid = 1234, process_created_filetime = skin.Length, observed_at_utc = DateTimeOffset.UtcNow.ToString("O") }
                }) });
                Check(skin + " paused event controls preserve statistics and independent FPS updates",
                    window.State.Get("totals").Number("damage") == 12345 && window.SideGameFpsStatus.Text == "FPS 45");
                Capture(skin, "paused");

                Publish("resuming", paused: true);
                Check(skin + " resuming uses the requested action without inventing running state",
                    window.SideEventCaptureButton.Content as string == "正在继续…" && !window.SideEventCaptureButton.IsEnabled &&
                    window.State.Get("event_capture_control").Bool("paused"));
                Capture(skin, "resuming");

                Publish("error", reason: "采集端拒绝暂停", wanted: true);
                Check(skin + " failed pause preserves actual running state and exposes retry",
                    window.SideEventCaptureButton.Content as string == "重试暂停" && window.SideEventCaptureButton.IsEnabled &&
                    Status().Contains("采集端拒绝暂停") && !Status().Contains("事件已暂停"));
                Capture(skin, "error");
                Publish("error", paused: true, reason: "采集端拒绝恢复");
                Check(skin + " failed resume preserves the confirmed paused action",
                    window.SideEventCaptureButton.Content as string == "重试继续" && window.SideEventCaptureButton.IsEnabled &&
                    Status().Contains("采集端拒绝恢复"));

                Publish("unavailable", supported: false, reason: "当前后端不支持暂停");
                Check(skin + " unsupported control explains its disabled state inline",
                    !window.SideEventCaptureButton.IsEnabled && Status().Contains("当前后端不支持暂停") &&
                    ToolTipService.GetShowOnDisabled(window.SideEventCaptureButton));
                Publish("unavailable", reason: "等待观察者连接");
                Check(skin + " temporary unavailable state retains its specific reason",
                    !window.SideEventCaptureButton.IsEnabled && Status().Contains("等待观察者连接"));
                Publish();
                unavailable.SetValue(window, true); update.Invoke(window, null);
                Check(skin + " cached producer state cannot enable pause while disconnected",
                    !window.SideEventCaptureButton.IsEnabled && Status().Contains("重新连接"));
                unavailable.SetValue(window, false);
            }

            Publish();
            failure.Invoke(window, new object[] { "worker 拒绝命令" });
            Check("command rejection stays inline without changing the acknowledged producer state",
                window.SideEventCaptureButton.Content as string == "重试暂停" && Status().Contains("worker 拒绝命令") &&
                !window.State.Get("event_capture_control").Bool("paused"));
            Publish(revision: 2);
            Check("a newer producer revision replaces an old request error", !Status().Contains("worker 拒绝命令"));
            string original = window.State.GetRawText();
            ((Task)request.Invoke(window, null)!).GetAwaiter().GetResult();
            Check("a click without a live bridge cannot optimistically pause or start a worker",
                window.State.GetRawText() == original && Status().Contains("采集连接不可用") &&
                typeof(MainWindow).GetField("bridge", Private)!.GetValue(window) is null);
            window.State = JsonSerializer.SerializeToElement(new { }); update.Invoke(window, null);
            Check("older workers without the optional control remain safely unavailable", !window.SideEventCaptureButton.IsEnabled);
            Check("event pause control and render checks never create HWND or tray",
                new WindowInteropHelper(window).Handle == IntPtr.Zero &&
                typeof(MainWindow).GetField("tray", Private)!.GetValue(window) is null);
        }
        finally
        {
            typeof(MainWindow).GetMethod("StopGameFpsStatus", Private)!.Invoke(window, null);
            typeof(MainWindow).GetField("quitting", Private)!.SetValue(window, true);
            window.Close();
            ((IDisposable)typeof(MainWindow).GetField("recoveryCancellation", Private)!.GetValue(window)!).Dispose();
            Theme.Apply("lone-trail");
        }
        Console.WriteLine($"PASS_EVENT_CAPTURE_PAUSE {passed} assertions (producer states and offscreen FPS footer; no windows/input/targets)");
    }
}
