using System;
using System.Diagnostics;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace ArkDPS.Desktop;

public partial class MainWindow
{
    private ReleaseUpdate? availableUpdate;
    private StagedUpdate? stagedUpdate;
    private CancellationTokenSource? updateDownloadCancellation;
    private bool manualUpdateCheck;
    private bool startingUpdate;

    internal void InitializeReleaseUpdates(ReleaseVersion version, bool offline)
    {
        ReleaseSettings.Visibility = Visibility.Visible;
        InstalledVersion.Text = $"当前版本 v{version}";
        CheckUpdateButton.IsEnabled = !offline;
        if (App.UpdateLaunch?.Message is { } message) UpdateStatus.Text = message;
    }

    private async void CheckUpdateClick(object sender, RoutedEventArgs e)
    {
        if (App.InstalledVersion is ReleaseVersion version)
            await CheckForUpdates(ReleaseUpdates.Public, version, manual: true);
    }

    private async Task CheckForUpdates(ReleaseUpdates updates, ReleaseVersion version, bool manual)
    {
        if (startingUpdate || updateDownloadCancellation != null || stagedUpdate != null) return;
        if (manual)
        {
            if (manualUpdateCheck) return;
            manualUpdateCheck = true;
            CheckUpdateButton.IsEnabled = false;
            UpdateStatus.Text = "正在检查更新…";
        }
        try
        {
            var update = await updates.Check(version);
            if (quitting || closingRequested || stagedUpdate != null || updateDownloadCancellation != null) return;
            availableUpdate = update;
            UpdateDownload.Content = "发现新版本 · 查看下载";
            UpdateDownload.Visibility = update != null ? Visibility.Visible : Visibility.Collapsed;
            UpdateDownload.ToolTip = update is { } newer ? $"发现新版本 v{newer.Version}" : null;
            UpdateReleasePage.Visibility = UpdateDownload.Visibility;
            UpdateAction.Visibility = Visibility.Collapsed;
            if (update == null)
            {
                UpdateStatus.Text = manual ? "已是当前渠道的最新版本" : "";
                return;
            }
            if (App.Verification == null && App.UpdateHost is { } host && update.CanDownload)
            {
                if (manual || !host.IsSuppressed(update))
                    await DownloadUpdateAsync(updates, host, update);
                else
                    AllowUpdateRetry($"上次更新 v{update.Version} 未完成，当前版本可以继续使用。");
                return;
            }
            UpdateStatus.Text = $"发现新版本 v{update.Version}";
        }
        catch (Exception error) when (PortableUpdateHost.IsUpdateError(error))
        {
            if (manual && !quitting) UpdateStatus.Text = "暂时无法检查更新";
        }
        finally
        {
            if (manual)
            {
                manualUpdateCheck = false;
                if (!quitting && !closingRequested) CheckUpdateButton.IsEnabled = stagedUpdate == null;
            }
        }
    }

    private async Task DownloadUpdateAsync(ReleaseUpdates updates, PortableUpdateHost host, ReleaseUpdate target)
    {
        if (updateDownloadCancellation != null || stagedUpdate != null) return;
        using var cancellation = new CancellationTokenSource();
        updateDownloadCancellation = cancellation;
        CheckUpdateButton.IsEnabled = false;
        UpdateDownload.Visibility = Visibility.Collapsed;
        UpdateProgressBar.Visibility = Visibility.Visible;
        UpdateProgressBar.Value = 0;
        UpdateAction.Visibility = Visibility.Visible;
        UpdateAction.Content = "取消下载";
        UpdateAction.IsEnabled = true;
        UpdateStatus.Text = $"正在下载 v{target.Version}…";
        var progress = new Progress<UpdateProgress>(value =>
        {
            if (updateDownloadCancellation != cancellation || closingRequested) return;
            UpdateProgressBar.Value = value.Total > 0 ? 100.0 * value.Received / value.Total : 0;
            UpdateStatus.Text = value.Received == value.Total
                ? $"正在校验 v{target.Version}…"
                : string.Format(CultureInfo.CurrentCulture, "正在下载 v{0} · {1:0.0} / {2:0.0} MB",
                    target.Version, value.Received / 1_000_000.0, value.Total / 1_000_000.0);
            UpdateAction.Content = value.Received == value.Total ? "取消准备更新" : "取消下载";
        });
        try
        {
            stagedUpdate = await updates.StageAsync(target, host.Root, progress, cancellation.Token);
            UpdateStatus.Text = $"v{target.Version} 已就绪";
            UpdateAction.Content = "重启并更新";
            UpdateAction.IsEnabled = true;
            UpdateDownload.Content = "更新已就绪";
            UpdateDownload.Visibility = Visibility.Visible;
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
        {
            AllowUpdateRetry("已取消本次下载");
        }
        catch (UpdateRequiresManualDownloadException error)
        {
            host.Log(error);
            UpdateStatus.Text = $"v{target.Version} 需要从发布页手动更新。";
            UpdateAction.Visibility = Visibility.Collapsed;
            if (App.UpdateVerification != null) throw;
        }
        catch (Exception error) when (PortableUpdateHost.IsUpdateError(error))
        {
            host.Log(error);
            AllowUpdateRetry(error is System.IO.IOException && (error.HResult & 0xffff) is 39 or 112
                ? "磁盘空间不足。请释放空间后重试。"
                : $"无法准备 v{target.Version}。可以重试或查看发布页。");
            if (App.UpdateVerification != null) throw;
        }
        finally
        {
            updateDownloadCancellation = null;
            UpdateProgressBar.Visibility = Visibility.Collapsed;
            if (!closingRequested && !quitting && App.UpdateVerification == null)
                CheckUpdateButton.IsEnabled = stagedUpdate == null;
        }
    }

    private void AllowUpdateRetry(string message)
    {
        UpdateStatus.Text = message;
        UpdateAction.Content = "重试更新";
        UpdateAction.IsEnabled = true;
        UpdateAction.Visibility = Visibility.Visible;
    }

    private async void ShowUpdateClick(object sender, RoutedEventArgs e)
    {
        if (stagedUpdate != null)
            await Navigate("settings");
        else
            OpenReleaseDownload(sender, e);
    }

    private async void UpdateActionClick(object sender, RoutedEventArgs e)
    {
        if (updateDownloadCancellation is { } cancellation)
        {
            UpdateAction.IsEnabled = false;
            cancellation.Cancel();
        }
        else if (stagedUpdate != null)
            await ApplyStagedUpdateAsync(confirm: true);
        else if (App.UpdateHost is { } host && availableUpdate is { CanDownload: true } target)
            await DownloadUpdateAsync(ReleaseUpdates.Public, host, target);
    }

    private async Task ApplyStagedUpdateAsync(bool confirm)
    {
        if (startingUpdate || stagedUpdate is not { } update || App.UpdateHost is not { } host) return;
        if (confirm && MessageBox.Show(this,
            $"重启 ArkDPS 并更新到 v{update.Release.Version}？\n\n重启期间会暂停采集，进行中的战斗可能记录不完整。",
            "重启并更新", MessageBoxButton.OKCancel, MessageBoxImage.Question, MessageBoxResult.Cancel) != MessageBoxResult.OK)
            return;
        startingUpdate = true;
        UpdateAction.IsEnabled = false;
        CheckUpdateButton.IsEnabled = false;
        UpdateStatus.Text = "正在保存设置并准备重启…";
        try
        {
            await QuitForUpdate(() => host.StartHandoffAsync(update, App.LaunchArguments));
        }
        catch (Exception error) when (PortableUpdateHost.IsUpdateError(error) || error is BridgeCommandException)
        {
            host.Log(error);
            UpdateStatus.Text = $"暂时无法重启更新。{error.Message}";
            if (App.UpdateVerification != null) throw;
        }
        finally
        {
            if (!closingRequested && !quitting)
            {
                startingUpdate = false;
                UpdateAction.IsEnabled = true;
                CheckUpdateButton.IsEnabled = stagedUpdate == null;
            }
        }
    }

    private void OpenReleaseDownload(object sender, RoutedEventArgs e)
    {
        if (availableUpdate is not { } update) return;
        string page = ReleaseUpdates.DownloadPage(update.Version);
        try
        {
            Process.Start(new ProcessStartInfo(page) { UseShellExecute = true });
        }
        catch (System.ComponentModel.Win32Exception)
        {
            MessageBox.Show(this, "无法打开浏览器。\n" + page, "查看发布页");
        }
    }
}
