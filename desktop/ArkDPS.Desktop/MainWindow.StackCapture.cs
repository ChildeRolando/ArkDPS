using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;

namespace ArkDPS.Desktop;

public partial class MainWindow
{
    private Process? stackCapture;
    private bool stackStopRequested;
    private string StackCaptureDirectory => Path.Combine(App.StateDirectory, "performance");

    private void RequestStackCaptureStop()
    {
        if (stackCapture == null || stackStopRequested) return;
        stackStopRequested = true;
        StackCaptureButton.IsEnabled = false;
        StackCaptureStatus.Text = "正在保存 ETL 文件…";
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
        StackCaptureButton.IsEnabled = false;
        StackCaptureStatus.Text = "正在启动 Windows 采样…";
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
                StackCaptureStatus.Text = "正在采样，可返回游戏。45 秒后自动保存，也可提前结束。";
                StackCaptureButton.Content = "结束并保存采样";
                StackCaptureButton.IsEnabled = true;
            }
            if (ready && await process.StandardOutput.ReadLineAsync() == "saving")
            {
                StackCaptureButton.IsEnabled = false;
                StackCaptureStatus.Text = "正在保存 ETL 文件…";
            }
            await process.WaitForExitAsync();
            string detail = (await errors).Trim();
            if (!ready || process.ExitCode != 0 || !File.Exists(file))
                throw new IOException(detail.Length > 0 ? detail : "采样进程未完成保存。");
            StackCaptureStatus.Text = "已保存 " + Path.GetFileName(file);
        }
        catch (Exception ex) { StackCaptureStatus.Text = "采样失败 · " + ex.Message; }
        finally
        {
            stackCapture = null;
            stackStopRequested = false;
            StackCaptureButton.Content = "开始线程调用栈采样";
            StackCaptureButton.IsEnabled = !closingRequested && !quitting;
        }
    }

    private void OpenStackCaptureDirectory(object sender, RoutedEventArgs e)
    {
        try
        {
            Directory.CreateDirectory(StackCaptureDirectory);
            Process.Start(new ProcessStartInfo(StackCaptureDirectory) { UseShellExecute = true })?.Dispose();
        }
        catch (Exception ex) { StackCaptureStatus.Text = "无法打开采样目录 · " + ex.Message; }
    }
}
