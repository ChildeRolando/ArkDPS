using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace ArkDPS.Desktop;

/// <summary>Offline input for the two-bundle Windows release acceptance.</summary>
internal sealed record UpdateFixture(string Archive, ReleaseVersion Version, string Receipt, string Scenario)
{
    internal string? LayeredArchive { get; private init; }

    internal static UpdateFixture Load(string path, string stateDirectory, PortableUpdateHost host)
    {
        using var input = JsonDocument.Parse(File.ReadAllBytes(path));
        var request = input.RootElement;
        string archive = request.Text("archive");
        string receipt = request.Text("receipt");
        string scenario = request.Text("scenario");
        string? layered = request.TryGetProperty("layered_archive", out var layeredElement) &&
            layeredElement.ValueKind == JsonValueKind.String ? layeredElement.GetString() : null;
        if (!Path.IsPathFullyQualified(archive) || !File.Exists(archive) ||
            !Path.IsPathFullyQualified(receipt) ||
            !ReleaseVersion.TryParse(request.Text("version"), out var version) ||
            scenario is not ("success" or "failure" or "relaunch" or "tampered" or "interrupted" or "old_exit_failure") ||
            layered is not null && (!Path.IsPathFullyQualified(layered) || !File.Exists(layered)))
            throw new InvalidDataException("Offline update fixture is invalid.");
        if (!Path.IsPathFullyQualified(stateDirectory) || !Path.IsPathFullyQualified(host.Root))
            throw new InvalidDataException("Offline update fixture requires absolute paths.");
        return new(archive, version, receipt, scenario) { LayeredArchive = layered };
    }

    internal ReleaseHandler CreateReleaseHandler() => new(this);

    internal static void InterruptIfRequested(string[] arguments, int candidateId)
    {
        int fixtureArgument = Array.IndexOf(arguments, "--verify-update");
        if (fixtureArgument < 0 || fixtureArgument + 1 >= arguments.Length ||
            !Array.Exists(arguments, argument => argument == "--offline"))
            return;
        using var request = JsonDocument.Parse(File.ReadAllBytes(arguments[fixtureArgument + 1]));
        if (request.RootElement.Text("scenario") != "interrupted") return;
        Write(request.RootElement.Text("receipt") + ".interrupted", new
        {
            helper_pid = Environment.ProcessId,
            candidate_pid = candidateId,
        });
        Environment.Exit(91);
    }

    internal void WriteResult(string outcome, ReleaseVersion version, int protocol)
    {
        Write(Receipt, new
        {
            outcome, version = version.ToString(), protocol, state_directory = App.StateDirectory,
            pid = Environment.ProcessId, executable = Environment.ProcessPath,
        });
    }

    internal void WriteFailure(Exception error) =>
        Write(Receipt, new { outcome = "fixture_error", error = error.ToString(), pid = Environment.ProcessId });

    private static void Write<T>(string receipt, T result)
    {
        string temporary = receipt + "." + Guid.NewGuid().ToString("N") + ".tmp";
        File.WriteAllBytes(temporary, JsonSerializer.SerializeToUtf8Bytes(result));
        File.Move(temporary, receipt, overwrite: true);
    }

    internal sealed class ReleaseHandler(UpdateFixture fixture) : HttpMessageHandler
    {
        private const int AssetId = 987654321;
        private const int LayeredAssetId = 987654322;
        private readonly List<AssetTransfer> downloads = [];

        internal void WriteDownloads() => Write(Path.ChangeExtension(fixture.Receipt, ".downloads.json"),
            downloads.Select(transfer => new { asset_id = transfer.Id, bytes = transfer.Bytes }));

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            if (request.RequestUri is not { Host: "api.github.com" } uri)
                return new HttpResponseMessage(HttpStatusCode.NotFound);
            if (uri.AbsolutePath == $"/repos/ChildeRolando/ArkDPS/releases/assets/{AssetId}")
                return OpenAsset(AssetId, fixture.Archive);
            if (uri.AbsolutePath == $"/repos/ChildeRolando/ArkDPS/releases/assets/{LayeredAssetId}" &&
                fixture.LayeredArchive is { } layeredArchive)
                return OpenAsset(LayeredAssetId, layeredArchive);
            if (uri.AbsolutePath != "/repos/ChildeRolando/ArkDPS/releases")
                return new HttpResponseMessage(HttpStatusCode.NotFound);

            await using var stream = File.OpenRead(fixture.Archive);
            string digest = fixture.Scenario == "tampered" ? new string('0', 64)
                : Convert.ToHexString(await SHA256.HashDataAsync(stream, token)).ToLowerInvariant();
            var full = new
            {
                id = AssetId, size = stream.Length, name = $"ArkDPS-{fixture.Version}-win-x64.zip",
                state = "uploaded", digest = "sha256:" + digest,
            };
            string? layeredPath = fixture.LayeredArchive;
            object[] assets = layeredPath is null ? [full]
                :
            [
                full,
                await LayeredAsset(layeredPath, fixture.Scenario == "tampered", token),
            ];
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(JsonSerializer.Serialize(new[]
                {
                    new
                    {
                        tag_name = "v" + fixture.Version, draft = false, prerelease = fixture.Version.Candidate != 0,
                        assets,
                    },
                })),
            };
        }

        private HttpResponseMessage OpenAsset(int id, string path)
        {
            var transfer = new AssetTransfer(id);
            downloads.Add(transfer);
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StreamContent(new CountedArchive(path, transfer)),
            };
        }

        private sealed record AssetTransfer(int Id)
        {
            public long Bytes { get; set; }
        }

        private sealed class CountedArchive(string path, AssetTransfer transfer)
            : FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 4096, FileOptions.Asynchronous)
        {
            public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken token = default)
            {
                int count = await base.ReadAsync(buffer, token).ConfigureAwait(false);
                transfer.Bytes += count;
                return count;
            }
        }

        private static async Task<object> LayeredAsset(string path, bool tampered, CancellationToken token)
        {
            await using var stream = File.OpenRead(path);
            string digest = tampered ? new string('0', 64)
                : Convert.ToHexString(await SHA256.HashDataAsync(stream, token)).ToLowerInvariant();
            return new
            {
                id = LayeredAssetId, size = stream.Length, name = Path.GetFileName(path),
                state = "uploaded", digest = "sha256:" + digest,
            };
        }
    }
}

public partial class MainWindow
{
    private async Task VerifyPortableUpdateAsync(UpdateFixture fixture)
    {
        if (App.InstalledVersion is not { } installed || App.UpdateHost is not { } host || bridge == null)
            throw new InvalidOperationException("Offline update fixture was not started from an update-enabled bundle.");
        string? outcome = App.UpdateLaunch?.Kind switch
        {
            PortableLaunchKind.Activate => "updated",
            PortableLaunchKind.Recover => "rolled_back",
            _ when fixture.Scenario == "relaunch" => "selected",
            _ => null,
        };
        int protocol = (int)bridge.Hello.Number("version");
        if (outcome != null)
        {
            fixture.WriteResult(outcome, installed, protocol);
            await Quit();
            return;
        }

        var handler = fixture.CreateReleaseHandler();
        using var http = new HttpClient(handler);
        var updates = new ReleaseUpdates(http);
        availableUpdate = await updates.Check(installed);
        if (availableUpdate is not { CanDownload: true } target || target.Version != fixture.Version)
            throw new InvalidOperationException("Offline update fixture did not resolve the requested release.");
        try
        {
            try { await DownloadUpdateAsync(updates, host, target); }
            finally { handler.WriteDownloads(); }
        }
        catch (InvalidDataException) when (fixture.Scenario == "tampered" && bridge.CanSend)
        {
            fixture.WriteResult("rejected", installed, protocol);
            await Quit();
            return;
        }
        if (stagedUpdate == null || !bridge.CanSend)
            throw new InvalidOperationException("Offline update staging failed or interrupted the current worker.");
        await ApplyStagedUpdateAsync(confirm: false);
    }
}
