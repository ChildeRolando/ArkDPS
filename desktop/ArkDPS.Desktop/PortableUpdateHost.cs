using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace ArkDPS.Desktop;

internal enum PortableLaunchKind { Selected, Probe, Activate, Recover }

internal sealed record PortableLaunch(
    PortableLaunchKind Kind, string[] Arguments, string? ReadyPipe = null, string? Message = null);

/// <summary>Selects a complete bundle without replacing a running executable or moving user state.</summary>
internal sealed class PortableUpdateHost(string root)
{
    internal const string ChildOption = "--arkdps-update-child";
    internal const string ApplyOption = "--arkdps-update-apply";
    private const string Executable = "ArkDPS.Desktop.exe";
    private const string ControlFolder = ".arkdps-update";
    private const byte Ready = 1;
    private const byte Proceed = 2;
    private static readonly TimeSpan HandoffTimeout = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan OldProcessTimeout = TimeSpan.FromSeconds(35);
    private static readonly TimeSpan StartTimeout = TimeSpan.FromSeconds(45);
    private static readonly TimeSpan ChildExitTimeout = TimeSpan.FromSeconds(25);
    internal string Root { get; } = Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
    internal string StateDirectory => Path.Combine(Root, "collector", "runtime");
    private string ControlDirectory => Path.Combine(Root, ControlFolder);
    private string SelectionFile => Path.Combine(ControlDirectory, "selection.json");

    private sealed record Selection(ReleaseUpdate? Current, ReleaseUpdate? Previous, string? FailedDigest);
    private sealed record Handoff(
        ReleaseUpdate Target, int ParentId, long ParentStart, string ReadyPipe, string[] Arguments);

    internal static PortableUpdateHost? Find(string directory)
    {
        var package = new DirectoryInfo(directory);
        if (!File.Exists(Path.Combine(package.FullName, "metadata", "release.json"))) return null;
        var control = package.Parent?.Parent;
        if (package.Parent?.Name == "versions" && control?.Name == ControlFolder && control.Parent is { } portable)
            return File.Exists(Path.Combine(portable.FullName, "metadata", "release.json")) ? new(portable.FullName) : null;
        return new(package.FullName);
    }

    internal static PortableLaunch ReadChild(string payload) => Decode<PortableLaunch>(payload);

    internal bool Redirect(string[] arguments)
    {
        if (Directory.Exists(ControlDirectory))
        {
            using var gate = TryLock();
            if (gate == null) return true;
            var selection = ReadSelection();
            foreach (var version in new[] { selection.Current, selection.Previous })
            {
                if (version is null || !version.CanDownload) continue;
                try
                {
                    string directory = UpdatePackage.BundleDirectory(Root, version);
                    UpdatePackage.VerifyStagedLaunch(version, directory);
                    using var process = Start(directory, ChildOption, new PortableLaunch(PortableLaunchKind.Selected, arguments));
                    return true;
                }
                catch (Exception error) when (IsUpdateError(error)) { Log(error); }
            }
        }
        if (!string.Equals(Path.TrimEndingDirectorySeparator(AppContext.BaseDirectory), Root,
                           StringComparison.OrdinalIgnoreCase))
        {
            using var process = Start(Root, ChildOption, new PortableLaunch(PortableLaunchKind.Selected, arguments));
            return true;
        }
        return false;
    }

    internal bool IsSuppressed(ReleaseUpdate target) =>
        target.Sha256 != null && ReadSelection().FailedDigest == target.Sha256;

    internal bool IsRetainedBundle(string bundle)
    {
        var selection = ReadSelectionFile();
        foreach (var version in new[] { selection.Current, selection.Previous })
            if (version != null && string.Equals(UpdatePackage.BundleDirectory(Root, version), bundle,
                    StringComparison.OrdinalIgnoreCase))
                return true;
        return false;
    }

    /// <summary>An installed bundle that can donate the shared update layer:
    /// the recorded current or previous version, never the update target
    /// itself. Null when the selection is unreadable or names no directory.</summary>
    internal string? DonorBundle(string target)
    {
        var selection = ReadSelection();
        foreach (var version in new[] { selection.Current, selection.Previous })
        {
            if (version is null || !version.CanDownload) continue;
            try
            {
                string directory = UpdatePackage.BundleDirectory(Root, version);
                if (!string.Equals(directory, target, StringComparison.OrdinalIgnoreCase) &&
                    Directory.Exists(directory))
                    return directory;
            }
            catch (Exception error) when (IsUpdateError(error)) { }
        }
        return null;
    }

    internal async Task StartHandoffAsync(StagedUpdate update, string[] arguments)
    {
        using var server = NewPipe(out var pipe);
        using var current = Process.GetCurrentProcess();
        var handoff = new Handoff(update.Release, current.Id, StartedAt(current), pipe, arguments);
        string source = Path.TrimEndingDirectorySeparator(AppContext.BaseDirectory);
        using var helper = Start(source, ApplyOption, handoff);
        using var deadline = new CancellationTokenSource(HandoffTimeout);
        try
        {
            await AcceptAsync(server, deadline.Token);
            await SendAsync(server, Proceed, deadline.Token);
        }
        catch (Exception error) when (error is IOException or OperationCanceledException)
        {
            throw new IOException("更新进程未能接管，ArkDPS 将继续运行。", error);
        }
    }

    internal async Task ApplyAsync(string payload)
    {
        var request = Decode<Handoff>(payload);
        using var gate = TryLock();
        if (gate == null) return;
        using var parent = Process.GetProcessById(request.ParentId);
        _ = parent.Handle;
        if (parent.HasExited || StartedAt(parent) != request.ParentStart) return;

        using (var deadline = new CancellationTokenSource(HandoffTimeout))
        using (var client = new NamedPipeClientStream(".", request.ReadyPipe, PipeDirection.InOut, PipeOptions.Asynchronous))
        {
            await client.ConnectAsync(deadline.Token);
            await SendAsync(client, Ready, deadline.Token);
            await ExpectAsync(client, Proceed, deadline.Token);
        }

        string source = Path.TrimEndingDirectorySeparator(AppContext.BaseDirectory);
        var prior = SelectionForSource(ReadSelection(), source);
        WriteUpdateEvent($"event=applying target={request.Target.Version}");
        bool selectionChanged = false;
        string? scratch = null;
        try
        {
            await WaitForExitAsync(parent, OldProcessTimeout);
            if (parent.ExitCode != 0) throw new IOException("旧版采集器未确认正常退出。");
            string bundle = UpdatePackage.BundleDirectory(Root, request.Target);
            UpdatePackage.VerifyStaged(request.Target, bundle);
            scratch = Path.Combine(ControlDirectory, "probe-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(scratch);
            await StartCheckedAsync(bundle, PortableLaunchKind.Probe,
                ["--offline", "--state-dir", scratch], null, waitForExit: true);
            TryDeleteDirectory(scratch);
            scratch = null;

            await StartCheckedAsync(bundle, PortableLaunchKind.Activate, request.Arguments, child =>
            {
                UpdateFixture.InterruptIfRequested(request.Arguments, child.Id);
                WriteSelection(new Selection(request.Target, prior.Current, null));
                selectionChanged = true;
            }, waitForExit: false);
            WriteUpdateEvent($"event=activated target={request.Target.Version}");
            PruneVersions(request.Target, prior.Current);
        }
        catch (Exception error) when (IsUpdateError(error))
        {
            Log(error);
            if (!parent.HasExited) return;
            try
            {
                WriteSelection(prior with { FailedDigest = request.Target.Sha256 });
            }
            catch (Exception writeError) when (IsUpdateError(writeError))
            {
                Log(writeError);
                if (selectionChanged) throw;
            }
            using var recovery = Start(source, ChildOption, new PortableLaunch(
                PortableLaunchKind.Recover, request.Arguments, Message: "更新未完成，已重新打开上一版本。"));
            WriteUpdateEvent($"event=rolled_back target={request.Target.Version}");
        }
        finally
        {
            if (scratch != null) TryDeleteDirectory(scratch);
        }
    }

    private Selection SelectionForSource(Selection selection, string runningBundle)
    {
        bool IsRunning(ReleaseUpdate? release)
        {
            if (release == null || !release.CanDownload) return false;
            try
            {
                return string.Equals(UpdatePackage.BundleDirectory(Root, release), runningBundle,
                    StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception error) when (IsUpdateError(error)) { return false; }
        }
        if (IsRunning(selection.Current)) return selection;
        if (IsRunning(selection.Previous)) return selection with { Current = selection.Previous, Previous = null };
        return new(null, null, selection.FailedDigest);
    }

    internal static async Task ReportReadyAsync(PortableLaunch launch)
    {
        if (launch.ReadyPipe == null) return;
        using var deadline = new CancellationTokenSource(StartTimeout);
        using var client = new NamedPipeClientStream(".", launch.ReadyPipe, PipeDirection.InOut, PipeOptions.Asynchronous);
        await client.ConnectAsync(deadline.Token);
        await SendAsync(client, Ready, deadline.Token);
        await ExpectAsync(client, Proceed, deadline.Token);
    }

    private static async Task StartCheckedAsync(
        string directory, PortableLaunchKind kind, string[] arguments, Action<Process>? commit, bool waitForExit)
    {
        using var server = NewPipe(out var pipe);
        using var child = Start(directory, ChildOption, new PortableLaunch(kind, arguments, pipe));
        try
        {
            using var deadline = new CancellationTokenSource(StartTimeout);
            var ready = AcceptAsync(server, deadline.Token);
            var exited = child.WaitForExitAsync(deadline.Token);
            var first = await Task.WhenAny(ready, exited);
            if (first == exited && child.HasExited) throw new IOException($"候选版本未就绪，退出码 {child.ExitCode}。");
            await ready;
            commit?.Invoke(child);
            await SendAsync(server, Proceed, deadline.Token);
            if (waitForExit)
            {
                await WaitForExitAsync(child, ChildExitTimeout);
                if (child.ExitCode != 0) throw new IOException($"候选版本预检未正常退出，退出码 {child.ExitCode}。");
            }
        }
        catch
        {
            // Closing the pipe releases a child that is waiting for permission to finish startup.
            server.Dispose();
            if (!child.HasExited)
            {
                try { await WaitForExitAsync(child, ChildExitTimeout); }
                catch (OperationCanceledException)
                {
                    child.Kill();
                    await WaitForExitAsync(child, ChildExitTimeout);
                }
            }
            throw;
        }
    }

    private FileStream? TryLock()
    {
        Directory.CreateDirectory(ControlDirectory);
        if ((File.GetAttributes(ControlDirectory) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("更新目录不能是重解析点。");
        string path = Path.Combine(ControlDirectory, "apply.lock");
        UpdatePackage.RequirePlainFileIfPresent(path);
        try { return new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException error) when ((error.HResult & 0xffff) is 32 or 33) { return null; }
    }

    private Selection ReadSelection()
    {
        try
        {
            return ReadSelectionFile();
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException or InvalidDataException)
        {
            UpdatePackage.RequirePlainFileIfPresent(SelectionFile);
            return new(null, null, null);
        }
    }

    private Selection ReadSelectionFile()
    {
        UpdatePackage.RequirePlainFileIfPresent(SelectionFile);
        try
        {
            return JsonSerializer.Deserialize<Selection>(File.ReadAllBytes(SelectionFile))
                ?? throw new InvalidDataException("更新版本记录无效。");
        }
        catch (FileNotFoundException) { return new(null, null, null); }
        catch (DirectoryNotFoundException) { return new(null, null, null); }
    }

    private void WriteSelection(Selection selection)
    {
        string temp = Path.Combine(ControlDirectory, "selection-" + Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            using (var file = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                JsonSerializer.Serialize(file, selection);
                file.Flush(flushToDisk: true);
            }
            File.Move(temp, SelectionFile, overwrite: true);
        }
        finally { File.Delete(temp); }
    }

    private void PruneVersions(ReleaseUpdate target, ReleaseUpdate? previous)
    {
        string active = UpdatePackage.BundleDirectory(Root, target);
        string? retained = previous == null ? null : UpdatePackage.BundleDirectory(Root, previous);
        string versions = Path.GetDirectoryName(active)!;
        try
        {
            string gate = Path.Combine(ControlDirectory, "stage.lock");
            UpdatePackage.RequirePlainFileIfPresent(gate);
            using var lease = new FileStream(gate, FileMode.OpenOrCreate, FileAccess.Write, FileShare.None);
            foreach (string version in Directory.EnumerateDirectories(versions))
            {
                string name = Path.GetFileName(version);
                int digestOffset = name.LastIndexOf('-') + 1;
                if (digestOffset <= 0 || name.Length - digestOffset != 16 ||
                    !ReleaseVersion.TryParse(name[..(digestOffset - 1)], out var parsed) ||
                    parsed.CompareTo(target.Version) >= 0 ||
                    !UpdatePackage.IsSha256Prefix(name[digestOffset..]))
                    continue;
                if (!string.Equals(version, active, StringComparison.OrdinalIgnoreCase) &&
                    !string.Equals(version, retained, StringComparison.OrdinalIgnoreCase))
                {
                    TryDeleteDirectory(version);
                    if (!Directory.Exists(version)) File.Delete(version + ".receipt");
                }
            }
        }
        catch (Exception error) when (IsUpdateError(error)) { Log(error); }
    }

    private void TryDeleteDirectory(string directory)
    {
        try
        {
            if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) == 0)
                Directory.Delete(directory, recursive: true);
        }
        catch (Exception error) when (IsUpdateError(error)) { Log(error); }
    }

    internal void Log(Exception error) => WriteUpdateEvent(error.ToString());

    private void WriteUpdateEvent(string message)
    {
        try
        {
            DesktopLog.Append(StateDirectory, "wpf-update.log", $"{DateTimeOffset.UtcNow:O} {message}");
        }
        catch (Exception failure) when (failure is IOException or UnauthorizedAccessException) { }
    }

    internal static bool IsUpdateError(Exception error) => error is
        IOException or UnauthorizedAccessException or InvalidDataException or JsonException or
        ArgumentException or InvalidOperationException or Win32Exception or OperationCanceledException or TimeoutException or
        System.Net.Http.HttpRequestException;

    private static NamedPipeServerStream NewPipe(out string name)
    {
        name = "ArkDPS-update-" + Guid.NewGuid().ToString("N");
        return new NamedPipeServerStream(name, PipeDirection.InOut, 1, PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
    }

    private static async Task AcceptAsync(NamedPipeServerStream pipe, CancellationToken token)
    {
        await pipe.WaitForConnectionAsync(token);
        await ExpectAsync(pipe, Ready, token);
    }

    private static async Task ExpectAsync(Stream pipe, byte expected, CancellationToken token)
    {
        byte[] content = new byte[1];
        await pipe.ReadExactlyAsync(content, token);
        if (content[0] != expected) throw new IOException("更新进程握手失败。");
    }

    private static Task SendAsync(Stream pipe, byte value, CancellationToken token) =>
        pipe.WriteAsync(new byte[] { value }, token).AsTask();

    private static async Task WaitForExitAsync(Process process, TimeSpan timeout)
    {
        using var deadline = new CancellationTokenSource(timeout);
        await process.WaitForExitAsync(deadline.Token);
    }

    private static long StartedAt(Process process) => process.StartTime.ToUniversalTime().Ticks;

    private static Process Start<T>(string directory, string option, T request)
    {
        var start = new ProcessStartInfo(Path.Combine(directory, Executable))
        {
            // Let Windows apply the target executable's manifest. The returned
            // Process still belongs to the existing ready/exit/rollback protocol.
            WorkingDirectory = directory, UseShellExecute = true,
        };
        start.ArgumentList.Add(option);
        start.ArgumentList.Add(Convert.ToBase64String(JsonSerializer.SerializeToUtf8Bytes(request)));
        return Process.Start(start) ?? throw new IOException("无法启动 ArkDPS 更新进程。");
    }

    private static T Decode<T>(string data) where T : class =>
        JsonSerializer.Deserialize<T>(Convert.FromBase64String(data)) ?? throw new IOException("更新进程参数无效。");
}
