using System;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

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

internal sealed record ReleaseUpdate(ReleaseVersion Version, long AssetId, long Size, string? Sha256)
{
    internal const long MaximumDownloadSize = 512L * 1024 * 1024;

    public bool CanDownload => AssetId > 0 && Size is > 0 and <= MaximumDownloadSize &&
        Sha256 is { } sha256 && IsSha256(sha256);

    internal static bool IsSha256(string text)
    {
        if (text.Length != 64) return false;
        foreach (char character in text)
            if (!Uri.IsHexDigit(character)) return false;
        return true;
    }
}

internal sealed record StagedUpdate(ReleaseUpdate Release, string Directory);
internal readonly record struct UpdateProgress(long Received, long Total);
internal sealed class UpdateRequiresManualDownloadException(string message, Exception? inner = null)
    : IOException(message, inner);

internal sealed class ReleaseUpdates(HttpClient http)
{
    private const string Repository = "ChildeRolando/ArkDPS";
    private static readonly HttpClient GitHub = new(new HttpClientHandler { AllowAutoRedirect = false });
    internal static readonly ReleaseUpdates Public = new(GitHub);
    private Task<ReleaseUpdate?>? pending;

    // The window calls this from the UI thread. A manual check can join the startup check.
    public Task<ReleaseUpdate?> Check(ReleaseVersion installed)
    {
        if (pending is { IsCompleted: false }) return pending;
        return pending = FindUpdate(installed);
    }

    internal static string DownloadPage(ReleaseVersion version) =>
        $"https://github.com/{Repository}/releases/tag/v{version}";

    private async Task<ReleaseUpdate?> FindUpdate(ReleaseVersion installed)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        ReleaseUpdate? best = null;
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
                    version.CompareTo(best?.Version ?? installed) <= 0)
                    continue;
                var assets = release.Get("assets");
                if (assets.ValueKind != JsonValueKind.Array) continue;
                foreach (var asset in assets.EnumerateArray())
                {
                    if (asset.Text("name") == $"ArkDPS-{version}-win-x64.zip" && asset.Text("state") == "uploaded")
                    {
                        string digest = asset.Text("digest");
                        string? sha256 = digest.StartsWith("sha256:", StringComparison.Ordinal) &&
                            ReleaseUpdate.IsSha256(digest[7..]) ? digest[7..].ToLowerInvariant() : null;
                        best = new(version, ReadLong(asset.Get("id")), ReadLong(asset.Get("size")), sha256);
                        break;
                    }
                }
            }
            if (document.RootElement.GetArrayLength() < 100) return best;
        }
    }

    private static long ReadLong(JsonElement value) =>
        value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out long number) ? number : 0;

    public Task<StagedUpdate> StageAsync(ReleaseUpdate update, string portableRoot,
        IProgress<UpdateProgress>? progress, CancellationToken token) =>
        Task.Run(() => Stage(update, portableRoot, progress, token), token);

    private async Task<StagedUpdate> Stage(ReleaseUpdate update, string portableRoot,
        IProgress<UpdateProgress>? progress, CancellationToken token)
    {
        string bundle;
        try { bundle = UpdatePackage.BundleDirectory(portableRoot, update); }
        catch (InvalidDataException error)
        {
            throw new UpdateRequiresManualDownloadException("无法安全使用更新目录，请从发行页手动下载。", error);
        }
        string versions = Path.GetDirectoryName(bundle)!;
        UpdatePackage.CreateVersionsDirectory(portableRoot);
        string updateRoot = Path.GetDirectoryName(versions)!;

        try
        {
            return await StageWithLease(update, bundle, versions, progress, token).ConfigureAwait(false);
        }
        catch (DamagedStageException)
        {
            // Activation also takes apply.lock before stage.lock when pruning a candidate.
            using var apply = OpenLease(updateRoot, "apply.lock");
            using var stage = OpenLease(updateRoot, "stage.lock");
            token.ThrowIfCancellationRequested();
            if (Directory.Exists(bundle))
            {
                try { UpdatePackage.VerifyStaged(update, bundle, token); }
                catch (Exception error) when (IsDamagedStage(error))
                {
                    if (new PortableUpdateHost(portableRoot).IsRetainedBundle(bundle))
                        throw new UpdateRequiresManualDownloadException("正在使用或保留的版本已损坏，请从发行页手动下载。", error);
                    RequireSafeReceipt(bundle + ".receipt");
                    Directory.Move(bundle, NewStage(versions));
                }
            }
        }

        return await StageWithLease(update, bundle, versions, progress, token).ConfigureAwait(false);
    }

    private sealed class DamagedStageException(Exception inner) : IOException("更新缓存损坏。", inner);

    private static bool IsDamagedStage(Exception error) => error is
        InvalidDataException or JsonException or FileNotFoundException or DirectoryNotFoundException or
        UpdateRequiresManualDownloadException;

    private static void RequireSafeReceipt(string path)
    {
        try { UpdatePackage.RequirePlainFileIfPresent(path); }
        catch (InvalidDataException error)
        {
            throw new UpdateRequiresManualDownloadException("无法安全使用更新记录，请从发行页手动下载。", error);
        }
    }

    private static FileStream OpenLease(string updateRoot, string name)
    {
        string gate = Path.Combine(updateRoot, name);
        UpdatePackage.RequirePlainFileIfPresent(gate);
        return new FileStream(gate, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
    }

    private async Task<StagedUpdate> StageWithLease(ReleaseUpdate update, string bundle, string versions,
        IProgress<UpdateProgress>? progress, CancellationToken token)
    {
        using var lease = OpenLease(Path.GetDirectoryName(versions)!, "stage.lock");
        RemoveAbandonedStages(versions);

        string receipt = bundle + ".receipt";
        if (Directory.Exists(bundle))
        {
            try { UpdatePackage.VerifyStaged(update, bundle, token); }
            catch (Exception error) when (IsDamagedStage(error)) { throw new DamagedStageException(error); }
            progress?.Report(new(update.Size, update.Size));
            return new(update, bundle);
        }
        RequireSafeReceipt(receipt);

        string staging = NewStage(versions);
        Directory.CreateDirectory(staging);
        try
        {
            string archive = Path.Combine(staging, "download.zip");
            await Download(update, archive, progress, token).ConfigureAwait(false);
            string extracted = Path.Combine(staging, "bundle");
            UpdatePackage.Extract(archive, extracted, token);
            string manifestHash = UpdatePackage.VerifyDirectory(extracted, update.Version, token);
            string pendingReceipt = Path.Combine(staging, "verified.receipt");
            using (var file = new FileStream(pendingReceipt, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                file.Write(System.Text.Encoding.ASCII.GetBytes(UpdatePackage.ReceiptContents(update, manifestHash)));
                file.Flush(flushToDisk: true);
            }
            token.ThrowIfCancellationRequested();
            File.Move(pendingReceipt, receipt, overwrite: true);
            Directory.Move(extracted, bundle);
            return new(update, bundle);
        }
        finally
        {
            TryDeleteStage(staging);
        }
    }

    private static string NewStage(string versions) => Path.Combine(versions, ".stage-" + Guid.NewGuid().ToString("N"));

    private static void RemoveAbandonedStages(string versions)
    {
        foreach (string path in Directory.EnumerateDirectories(versions, ".stage-*"))
        {
            string name = Path.GetFileName(path);
            if (name.StartsWith(".stage-", StringComparison.Ordinal) && Guid.TryParseExact(name[7..], "N", out _))
                TryDeleteStage(path);
        }
    }

    private static void TryDeleteStage(string path)
    {
        try
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0)
                Directory.Delete(path, recursive: true);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private async Task Download(ReleaseUpdate update, string destination,
        IProgress<UpdateProgress>? progress, CancellationToken token)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(TimeSpan.FromMinutes(15));
        using var response = await OpenAsset(update, timeout.Token).ConfigureAwait(false);
        if (response.Content.Headers.ContentLength is long length && length != update.Size)
            throw new InvalidDataException("更新包大小与 GitHub 记录不一致。");

        await using var source = await response.Content.ReadAsStreamAsync(timeout.Token).ConfigureAwait(false);
        await using var file = new FileStream(destination, FileMode.CreateNew, FileAccess.Write,
            FileShare.None, 128 * 1024, FileOptions.Asynchronous | FileOptions.SequentialScan);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        byte[] buffer = new byte[128 * 1024];
        long received = 0;
        long lastReport = Environment.TickCount64;
        progress?.Report(new(received, update.Size));
        while (true)
        {
            int count = await source.ReadAsync(buffer, timeout.Token).ConfigureAwait(false);
            if (count == 0) break;
            received += count;
            if (received > update.Size)
                throw new InvalidDataException("更新包大小超过 GitHub 记录。");
            hash.AppendData(buffer, 0, count);
            await file.WriteAsync(buffer.AsMemory(0, count), timeout.Token).ConfigureAwait(false);
            if (received == update.Size || Environment.TickCount64 - lastReport >= 100)
            {
                progress?.Report(new(received, update.Size));
                lastReport = Environment.TickCount64;
            }
        }
        if (received != update.Size || !string.Equals(Convert.ToHexString(hash.GetHashAndReset()),
                update.Sha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("更新包 SHA-256 校验失败。");
    }

    private async Task<HttpResponseMessage> OpenAsset(ReleaseUpdate update, CancellationToken token)
    {
        var endpoint = new Uri($"https://api.github.com/repos/{Repository}/releases/assets/{update.AssetId.ToString(CultureInfo.InvariantCulture)}");
        Uri uri = endpoint;
        for (int redirects = 0; redirects <= 3; redirects++)
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, uri);
            request.Headers.UserAgent.ParseAdd("ArkDPS/" + update.Version);
            request.Headers.Accept.ParseAdd("application/octet-stream");
            var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token).ConfigureAwait(false);
            if (response.StatusCode == HttpStatusCode.OK) return response;
            using (response)
            {
                if (response.StatusCode is not (HttpStatusCode.MovedPermanently or HttpStatusCode.Redirect or
                    HttpStatusCode.SeeOther or HttpStatusCode.TemporaryRedirect or HttpStatusCode.PermanentRedirect))
                    throw new HttpRequestException("无法下载更新包。", null, response.StatusCode);

                if (response.Headers.Location is not Uri location ||
                    !Uri.TryCreate(uri, location, out var target) || !IsAssetRedirect(target))
                    throw new HttpRequestException("GitHub 更新包下载地址无效。");
                uri = target;
            }
        }
        throw new HttpRequestException("GitHub 更新包下载重定向次数过多。");
    }

    private static bool IsAssetRedirect(Uri target) =>
        target.Scheme == Uri.UriSchemeHttps && target.IsDefaultPort && target.UserInfo.Length == 0 &&
        target.Fragment.Length == 0 && target.Host is "release-assets.githubusercontent.com" or "objects.githubusercontent.com";
}
