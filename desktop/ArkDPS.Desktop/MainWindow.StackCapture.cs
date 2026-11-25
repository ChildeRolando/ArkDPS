using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Automation;

namespace ArkDPS.Desktop;

public partial class MainWindow
{
    private Process? stackCapture;
    private bool stackStopRequested;
    private string StackCaptureDirectory => Path.Combine(App.StateDirectory, "performance");

    private void SetStackCaptureStatus(string text)
    {
        StackCaptureStatus.Text = SideStackCaptureStatus.Text = text;
        SideStackCaptureStatus.ToolTip = text;
        SideStackCaptureStatus.Visibility = Visibility.Visible;
    }

    private void SetStackCaptureAction(bool capturing, bool enabled)
    {
        string action = capturing ? "结束并保存线程调用栈采样" : "开始线程调用栈采样";
        SideStackCaptureButton.Content = capturing ? "结束采样" : "栈采样";
        SideStackCaptureButton.ToolTip = action;
        AutomationProperties.SetName(SideStackCaptureButton, action);
        SideStackCaptureButton.IsEnabled = enabled;
    }

    private void RequestStackCaptureStop()
    {
        if (stackCapture == null || stackStopRequested) return;
        stackStopRequested = true;
        SetStackCaptureAction(capturing: true, enabled: false);
        SetStackCaptureStatus("正在保存 ETL 文件…");
        try { stackCapture.StandardInput.Close(); }
        catch (IOException) { } // The helper may already be finishing its timed recording.
    }

    private async void ToggleStackCapture(object sender, RoutedEventArgs e)
    {
        if (stackCapture != null)
        {
            RequestStackCaptureStop();
            return;
        }
        if (closingRequested || quitting) return;
        SetStackCaptureAction(capturing: false, enabled: false);
        SetStackCaptureStatus("正在启动 Windows 采样…");
        string file = Path.Combine(StackCaptureDirectory, $"ArkDPS-{DateTime.Now:yyyyMMdd-HHmmss}-{Guid.NewGuid():N}.etl");
        var start = new ProcessStartInfo(App.Python)
        {
            WorkingDirectory = App.Root, CreateNoWindow = true, UseShellExecute = false,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardErrorEncoding = Encoding.UTF8, StandardOutputEncoding = Encoding.UTF8
        };
        foreach (string arg in new[] { "-I", "-B", "-X", "utf8", Path.Combine(App.Root, "stack_capture.py"), file })
            start.ArgumentList.Add(arg);
        try
        {
            using var process = Process.Start(start) ?? throw new IOException("无法启动采样。");
            stackCapture = process;
            stackStopRequested = false;
            var errors = process.StandardError.ReadToEndAsync();
            bool ready = await process.StandardOutput.ReadLineAsync() == "ready";
            if (ready && !stackStopRequested)
            {
                SetStackCaptureStatus("正在采样 · 45 秒后自动保存，也可提前结束");
                SetStackCaptureAction(capturing: true, enabled: true);
            }
            if (ready && await process.StandardOutput.ReadLineAsync() == "saving")
            {
                SetStackCaptureAction(capturing: true, enabled: false);
                SetStackCaptureStatus("正在保存 ETL 文件…");
            }
            await process.WaitForExitAsync();
            string detail = (await errors).Trim();
            if (!ready || process.ExitCode != 0 || !File.Exists(file))
                throw new IOException(detail.Length > 0 ? detail : "采样进程未完成保存。");
            SetStackCaptureStatus("已保存 " + Path.GetFileName(file));
        }
        catch (Exception ex) { SetStackCaptureStatus("采样失败 · " + ex.Message); }
        finally
        {
            stackCapture = null;
            stackStopRequested = false;
            SetStackCaptureAction(capturing: false, enabled: !closingRequested && !quitting);
        }
    }

    private void OpenStackCaptureDirectory(object sender, RoutedEventArgs e)
    {
        try
        {
            Directory.CreateDirectory(StackCaptureDirectory);
            Process.Start(new ProcessStartInfo(StackCaptureDirectory) { UseShellExecute = true })?.Dispose();
        }
        catch (Exception ex) { SetStackCaptureStatus("无法打开采样目录 · " + ex.Message); }
    }
}
