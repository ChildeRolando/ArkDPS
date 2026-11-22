using System;
using System.IO;
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
    internal static UpdateFixture Load(string path, string stateDirectory, PortableUpdateHost host)
    {
        using var input = JsonDocument.Parse(File.ReadAllBytes(path));
        var request = input.RootElement;
        string archive = request.Text("archive");
        string receipt = request.Text("receipt");
        string scenario = request.Text("scenario");
        if (!Path.IsPathFullyQualified(archive) || !File.Exists(archive) ||
            !Path.IsPathFullyQualified(receipt) ||
            !ReleaseVersion.TryParse(request.Text("version"), out var version) ||
            scenario is not ("success" or "failure" or "relaunch" or "tampered" or "interrupted" or "old_exit_failure"))
            throw new InvalidDataException("Offline update fixture is invalid.");
        if (!Path.IsPathFullyQualified(stateDirectory) || !Path.IsPathFullyQualified(host.Root))
            throw new InvalidDataException("Offline update fixture requires absolute paths.");
        return new(archive, version, receipt, scenario);
    }

    internal HttpClient CreateReleaseClient() => new(new ReleaseHandler(this));

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

    private sealed class ReleaseHandler(UpdateFixture fixture) : HttpMessageHandler
    {
        private const int AssetId = 987654321;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            if (request.RequestUri is not { Host: "api.github.com" } uri)
                return new HttpResponseMessage(HttpStatusCode.NotFound);
            if (uri.AbsolutePath == $"/repos/ChildeRolando/ArkDPS/releases/assets/{AssetId}")
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StreamContent(File.OpenRead(fixture.Archive)) };
            if (uri.AbsolutePath != "/repos/ChildeRolando/ArkDPS/releases")
                return new HttpResponseMessage(HttpStatusCode.NotFound);

            await using var stream = File.OpenRead(fixture.Archive);
            string digest = fixture.Scenario == "tampered" ? new string('0', 64)
                : Convert.ToHexString(await SHA256.HashDataAsync(stream, token)).ToLowerInvariant();
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(JsonSerializer.Serialize(new[]
                {
                    new
                    {
                        tag_name = "v" + fixture.Version, draft = false, prerelease = fixture.Version.Candidate != 0,
                        assets = new[]
                        {
                            new
                            {
                                id = AssetId, size = stream.Length, name = $"ArkDPS-{fixture.Version}-win-x64.zip",
                                state = "uploaded", digest = "sha256:" + digest,
                            },
                        },
                    },
                })),
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

        using var http = fixture.CreateReleaseClient();
        var updates = new ReleaseUpdates(http);
        availableUpdate = await updates.Check(installed);
        if (availableUpdate is not { CanDownload: true } target || target.Version != fixture.Version)
            throw new InvalidOperationException("Offline update fixture did not resolve the requested release.");
        try
        {
            await DownloadUpdateAsync(updates, host, target);
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
