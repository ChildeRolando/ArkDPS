using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;

namespace ArkDPS.Desktop;

public partial class MainWindow
{
    private bool updateShutdownPreparing;

    private async Task QuitForUpdate(Func<Task> startHelper)
    {
        if (closingRequested || quitting || updateShutdownPreparing)
            throw new InvalidOperationException("ArkDPS 正在准备退出。");
        if (!initialized || workerUnavailable || recoveryTask is { IsCompleted: false } ||
            bridge is not { CanSend: true } source)
            throw new IOException("采集进程尚未就绪。恢复连接后可以重试更新。");

        bool pollWasEnabled = poll.IsEnabled;
        bool saveWasEnabled = save.IsEnabled;
        bool wasEnabled = IsEnabled;
        updateShutdownPreparing = true;
        IsEnabled = false;
        poll.Stop();
        save.Stop();
        try
        {
            var waitingSince = Stopwatch.GetTimestamp();
            var timeout = TimeSpan.FromSeconds(5);
            while (commands > 0 || polling)
            {
                if (Stopwatch.GetElapsedTime(waitingSince) >= timeout)
                    throw new TimeoutException("现有操作尚未完成，请稍后重试更新。");
                await Task.Delay(50);
            }

            if (workerUnavailable || recoveryTask is { IsCompleted: false } || bridge != source || !source.CanSend)
                throw new IOException("采集进程尚未就绪。恢复连接后可以重试更新。");

            await source.RequestAsync("placement", PlacementFields(), timeout);
            await startHelper();
            await Quit(code: App.UpdateVerification?.Scenario == "old_exit_failure" ? 1 : 0, forUpdate: true);
        }
        catch (BridgeTransportException ex)
        {
            ReportTransportFailure(source, ex);
            throw;
        }
        finally
        {
            updateShutdownPreparing = false;
            if (!closingRequested)
            {
                IsEnabled = wasEnabled;
                if (pollWasEnabled && !workerUnavailable) poll.Start();
                if (saveWasEnabled) ScheduleSave();
            }
        }
    }
}
