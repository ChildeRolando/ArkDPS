using System;
using System.Diagnostics;
using System.Globalization;
using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace ArkDPS.Desktop;

internal readonly record struct ReleaseVersion(int Major, int Minor, int Patch, int Candidate)
    : IComparable<ReleaseVersion>
{
    private static readonly Regex Pattern = new(
        @"\A(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.([1-9][0-9]*))?\z");

    public static bool TryParse(string text, out ReleaseVersion version)
    {
        version = default;
        Match match = Pattern.Match(text);
        int candidate = 0;
        if (!match.Success ||
            !int.TryParse(match.Groups[1].Value, out int major) ||
            !int.TryParse(match.Groups[2].Value, out int minor) ||
            !int.TryParse(match.Groups[3].Value, out int patch) ||
            (match.Groups[4].Success && !int.TryParse(match.Groups[4].Value, out candidate)))
            return false;
        version = new(major, minor, patch, candidate);
        return true;
    }

    public int CompareTo(ReleaseVersion other) =>
        (Major, Minor, Patch, Candidate == 0, Candidate)
        .CompareTo((other.Major, other.Minor, other.Patch, other.Candidate == 0, other.Candidate));

    public override string ToString() => Candidate == 0
        ? FormattableString.Invariant($"{Major}.{Minor}.{Patch}")
        : FormattableString.Invariant($"{Major}.{Minor}.{Patch}-rc.{Candidate}");
}

internal sealed class ReleaseUpdates(HttpClient http)
{
    private const string Repository = "ChildeRolando/ArkDPS";
    private static readonly HttpClient GitHub = new(new HttpClientHandler { AllowAutoRedirect = false });
    internal static readonly ReleaseUpdates Public = new(GitHub);
    private Task<ReleaseVersion?>? pending;

    // The window calls this from the UI thread. A manual check can join the startup check.
    public Task<ReleaseVersion?> Check(ReleaseVersion installed)
    {
        if (pending is { IsCompleted: false }) return pending;
        return pending = FindUpdate(installed);
    }

    internal static string DownloadPage(ReleaseVersion version) =>
        $"https://github.com/{Repository}/releases/tag/v{version}";

    private async Task<ReleaseVersion?> FindUpdate(ReleaseVersion installed)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        ReleaseVersion? best = null;
        for (int page = 1; ; page++)
        {
            using var request = new HttpRequestMessage(HttpMethod.Get,
                $"https://api.github.com/repos/{Repository}/releases?per_page=100&page={page.ToString(CultureInfo.InvariantCulture)}");
            request.Headers.UserAgent.ParseAdd("ArkDPS/" + installed);
            request.Headers.Accept.ParseAdd("application/vnd.github+json");
            using var response = await http.SendAsync(request, timeout.Token).ConfigureAwait(false);
            response.EnsureSuccessStatusCode();
            using var stream = await response.Content.ReadAsStreamAsync(timeout.Token).ConfigureAwait(false);
            using var document = await JsonDocument.ParseAsync(stream, cancellationToken: timeout.Token).ConfigureAwait(false);
            if (document.RootElement.ValueKind != JsonValueKind.Array)
                throw new JsonException("Expected a GitHub Release list.");

            foreach (JsonElement release in document.RootElement.EnumerateArray())
            {
                string tag = release.Text("tag_name");
                if (!tag.StartsWith('v') || !ReleaseVersion.TryParse(tag[1..], out var version) ||
                    release.Get("draft").ValueKind != JsonValueKind.False ||
                    release.Get("prerelease").ValueKind != (version.Candidate == 0 ? JsonValueKind.False : JsonValueKind.True) ||
                    (installed.Candidate == 0 && version.Candidate != 0) ||
                    version.CompareTo(best ?? installed) <= 0)
                    continue;
                var assets = release.Get("assets");
                if (assets.ValueKind != JsonValueKind.Array) continue;
                foreach (var asset in assets.EnumerateArray())
                {
                    if (asset.Text("name") == $"ArkDPS-{version}-win-x64.zip" && asset.Text("state") == "uploaded")
                    {
                        best = version;
                        break;
                    }
                }
            }
            if (document.RootElement.GetArrayLength() < 100) return best;
        }
    }
}

public partial class MainWindow
{
    private ReleaseVersion? availableUpdate;
    private bool manualUpdateCheck;

    internal void InitializeReleaseUpdates(ReleaseVersion version, bool offline)
    {
        ReleaseSettings.Visibility = Visibility.Visible;
        InstalledVersion.Text = $"当前版本 v{version}";
        CheckUpdateButton.IsEnabled = !offline;
        if (!offline) _ = CheckForUpdates(ReleaseUpdates.Public, version, manual: false);
    }

    private async void CheckUpdateClick(object sender, RoutedEventArgs e)
    {
        if (App.InstalledVersion is ReleaseVersion version)
            await CheckForUpdates(ReleaseUpdates.Public, version, manual: true);
    }

    private async Task CheckForUpdates(ReleaseUpdates updates, ReleaseVersion version, bool manual)
    {
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
            if (quitting) return;
            availableUpdate = update;
            UpdateDownload.Visibility = update.HasValue ? Visibility.Visible : Visibility.Collapsed;
            UpdateDownload.ToolTip = update is { } newer ? $"发现新版本 v{newer}" : null;
            UpdateStatus.Text = update is { } found ? $"发现新版本 v{found}" :
                manual ? "已是当前渠道的最新版本" : "";
        }
        catch (Exception error) when (error is HttpRequestException or OperationCanceledException or JsonException or System.IO.IOException)
        {
            if (manual && !quitting) UpdateStatus.Text = "暂时无法检查更新";
        }
        finally
        {
            if (manual)
            {
                manualUpdateCheck = false;
                if (!quitting) CheckUpdateButton.IsEnabled = true;
            }
        }
    }

    private void OpenReleaseDownload(object sender, RoutedEventArgs e)
    {
        if (availableUpdate is not ReleaseVersion update) return;
        try
        {
            Process.Start(new ProcessStartInfo(ReleaseUpdates.DownloadPage(update)) { UseShellExecute = true });
        }
        catch (System.ComponentModel.Win32Exception)
        {
            MessageBox.Show(this, "无法打开浏览器。\n" + ReleaseUpdates.DownloadPage(update), "查看下载");
        }
    }
}
