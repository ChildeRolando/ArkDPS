using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Win32;

namespace ArkDPS.Desktop;

internal static class DesktopLog
{
    private static readonly Encoding Utf8 = new UTF8Encoding(false);
    private static readonly object AppendLock = new();
    private static readonly string DesktopRun = Guid.NewGuid().ToString("N");
    private static readonly HashSet<string> ContextWritten = new(StringComparer.OrdinalIgnoreCase);
    private static readonly Lazy<object> SystemContext = new(ReadSystem);
    private static readonly Lazy<object> BuildContext = new(ReadBuild);
    private static readonly string[] EnvironmentFields =
        ["platform", "provider", "game_client_version", "game_executable_version", "emulator_name", "emulator_version",
         "game_client_expected", "version_status", "emulator_version_source", "observed_at_utc"];
    internal static string RunId => DesktopRun;

    internal static bool ReadDebugMode(string directory) => ReadJson(directory, "layout.json").Bool("debug_mode");

    internal static string Header(string directory, string source, string kind = "log_header", bool? debugMode = null,
        string? effectivePlatform = null, bool? eventsEnabled = null, bool connectionOverridden = false)
    {
        var layout = ReadJson(directory, "layout.json");
        byte[]? connectionBytes = ReadBytes(directory, "connection.json");
        var connection = Parse(connectionBytes);
        string platform = (effectivePlatform ?? connection.Text("platform")) == "native_pc" ? "native_pc" : "mumu";
        var settings = new
        {
            platform,
            auto_detect = connection.Get("auto_detect").ValueKind != JsonValueKind.False,
            adb_configured = connection.Text("adb_path").Length > 0,
            manual_target_configured = connection.Text("serial").Length > 0,
            native_game_root_configured = connection.Text("native_game_root").Length > 0,
            event_observation = eventsEnabled ?? layout.Get("event_observation").ValueKind != JsonValueKind.False,
            multiplayer_enabled = layout.Bool("multiplayer_enabled"),
            side_mode = layout.Bool("side_mode"),
            theme = layout.Text("theme") switch
            {
                Theme.People => Theme.People,
                Theme.Babel => Theme.Babel,
                _ => Theme.LoneTrail
            },
            number_units = layout.Text("number_units") == "chinese" ? "chinese" : "western",
            connection_overridden = connectionOverridden
        };
        return JsonSerializer.Serialize(new
        {
            kind,
            data = new
            {
                schema = 1,
                utc = DateTimeOffset.UtcNow,
                source,
                desktop_run_id = DesktopRun,
                build = BuildContext.Value,
                system = SystemContext.Value,
                settings,
                environment = ReadEnvironment(directory, connection, connectionBytes, platform, connectionOverridden),
                debug_mode = debugMode ?? layout.Bool("debug_mode")
            }
        });
    }

    internal static bool IsKeyBridgeOperation(string op) => op is "hello" or "connection_apply" or "debug_mode"
        or "quit" or "rescan" or "detect" or "export";

    internal static bool KeepWorkerLine(string line, bool debugMode)
    {
        if (debugMode || !line.StartsWith("[bridge]", StringComparison.Ordinal)) return true;
        int begin = line.IndexOf(" op=", StringComparison.Ordinal);
        if (begin < 0 || line.Contains(" event=slow", StringComparison.Ordinal)
            || line.Contains(" event=failed", StringComparison.Ordinal)
            || line.Contains(" error=", StringComparison.Ordinal))
            return true;
        begin += 4;
        int end = line.IndexOf(' ', begin);
        return end > begin && IsKeyBridgeOperation(line[begin..end]);
    }

    internal static void Append(string directory, string name, string message, string? stateDirectory = null)
    {
        Directory.CreateDirectory(directory);
        string path = Path.Combine(directory, name);
        lock (AppendLock)
        {
            if (File.Exists(path) && new FileInfo(path).Length > 0 && !HasHeader(path))
            {
                var legacy = path + ".legacy-" + Guid.NewGuid().ToString("N");
                File.Move(path, legacy);
                ContextWritten.Remove(path);
            }
            using var writer = new StreamWriter(path, append: true, Utf8);
            if (writer.BaseStream.Length == 0)
                writer.WriteLine(Header(stateDirectory ?? directory, name));
            else if (!ContextWritten.Contains(path))
                writer.WriteLine(Header(stateDirectory ?? directory, name, "log_context"));
            writer.WriteLine(message);
            ContextWritten.Add(path);
        }
    }

    private static bool HasHeader(string path)
    {
        using var stream = File.OpenRead(path);
        using var reader = new StreamReader(stream, Utf8);
        var sample = new char[96];
        int count = reader.ReadBlock(sample, 0, sample.Length);
        return new string(sample, 0, count).StartsWith("{\"kind\":\"log_header\",", StringComparison.Ordinal);
    }

    private static Dictionary<string, object?> ReadEnvironment(string directory, JsonElement connection,
        byte[]? connectionBytes, string platform, bool overridden)
    {
        var result = new Dictionary<string, object?> { ["provenance"] = "unobserved", ["platform"] = platform,
            ["game_client_version"] = null, ["game_executable_version"] = null,
            ["emulator_name"] = null, ["emulator_version"] = null };
        if (overridden) return result;
        var cached = ReadJson(directory, "log-environment.json");
        var environment = cached.Get("environment");
        if (environment.ValueKind == JsonValueKind.Object &&
            string.Equals(cached.Text("connection_sha256"), Convert.ToHexString(SHA256.HashData(connectionBytes ?? [])),
                StringComparison.OrdinalIgnoreCase))
        {
            result["provenance"] = "last_observed";
            foreach (string field in EnvironmentFields)
                if (SafeText(environment.Get(field)) is string value) result[field] = value;
        }
        else if (platform == "native_pc" && LocalPath(connection.Text("native_game_root")) is string root &&
            ExecutableVersion(Path.Combine(root, "Arknights.exe")) is string version)
        {
            result["provenance"] = "local_installation";
            result["game_executable_version"] = version;
        }
        else if (platform == "mumu" && ReadMumuVersion(connection.Text("adb_path")) is string emulatorVersion)
        {
            result["provenance"] = "local_installation";
            result["emulator_name"] = "MuMu";
            result["emulator_version"] = emulatorVersion;
            result["emulator_version_source"] = "executable_file_version";
        }
        return result;
    }

    private static string? LocalPath(string path)
    {
        if (path.Length < 3 || path[1] != ':' || !Path.IsPathFullyQualified(path)) return null;
        try
        {
            string fullPath = Path.GetFullPath(path);
            return new DriveInfo(fullPath[..3]).DriveType is DriveType.Fixed or DriveType.Removable ? fullPath : null;
        }
        catch (Exception error) when (error is IOException or ArgumentException or UnauthorizedAccessException)
        { return null; }
    }

    private static string? ExecutableVersion(string path)
    {
        try
        {
            if (!File.Exists(path)) return null;
            var info = FileVersionInfo.GetVersionInfo(path);
            return SafeText(info.FileVersion ?? "") ?? SafeText(info.ProductVersion ?? "");
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
        { return null; }
    }

    private static string? ReadMumuVersion(string adbPath)
    {
        if (LocalPath(adbPath) is not string local) return null;
        var folder = Directory.GetParent(local);
        if (folder?.Parent is not { } root ||
            !string.Equals(folder.Name, "nx_main", StringComparison.OrdinalIgnoreCase) &&
            !string.Equals(folder.Name, "shell", StringComparison.OrdinalIgnoreCase)) return null;
        if (string.Equals(root.Parent?.Name, "nx_device", StringComparison.OrdinalIgnoreCase))
            root = root.Parent?.Parent ?? root;
        foreach (string candidate in new[] { "nx_main/MuMuNxMain.exe", "shell/MuMuPlayer.exe", "MuMuPlayer.exe" })
            if (ExecutableVersion(Path.Combine(root.FullName, candidate)) is string version) return version;
        return null;
    }

    private static object ReadBuild()
    {
        var release = ReadJson(AppContext.BaseDirectory, "metadata/release.json");
        return new
        {
            version = SafeText(release.Get("version")) ?? typeof(App).Assembly.GetName().Version?.ToString(),
            head = SafeText(release.Get("head")),
            protocol = BridgeClient.Version
        };
    }

    private static object ReadSystem()
    {
        const string windowsKey = @"SOFTWARE\Microsoft\Windows NT\CurrentVersion";
        const string runtimes = @"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64";
        var memory = new MemoryStatus { Length = (uint)Marshal.SizeOf<MemoryStatus>() };
        const ulong mib = 1024UL * 1024;
        return new
        {
            os = "Windows",
            os_version = RuntimeInformation.OSDescription,
            os_build = Environment.OSVersion.Version.Build,
            architecture = RuntimeInformation.OSArchitecture.ToString().ToLowerInvariant(),
            windows_display_version = ReadRegistry(windowsKey, "DisplayVersion") as string,
            windows_update_build = ReadRegistry(windowsKey, "UBR") as int?,
            cpu = ReadRegistry(@"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") is string cpu
                ? SafeText(cpu.Trim()) : null,
            logical_cores = Environment.ProcessorCount,
            memory_mb = GlobalMemoryStatusEx(ref memory) ? (ulong?)(memory.TotalPhysical / mib) : null,
            gpus = ReadGraphicsAdapters(),
            vc_runtime_x64_version = ReadRegistry(runtimes, "Version") as string,
            vc_runtime_x64_installed = ReadRegistry(runtimes, "Installed") is int installed ? (bool?)(installed != 0) : null
        };
    }

    private static string[] ReadGraphicsAdapters()
    {
        var names = new HashSet<string>(StringComparer.Ordinal);
        try
        {
            using var adapters = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}");
            if (adapters is not null)
                foreach (string name in adapters.GetSubKeyNames())
                {
                    if (names.Count == 8) break;
                    if (name.Length != 4 || !int.TryParse(name, out _)) continue;
                    using var item = adapters.OpenSubKey(name);
                    if (item?.GetValue("DriverDesc") is string text && SafeText(text) is string safe)
                        names.Add(safe);
                }
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or SecurityException) { }
        return [.. names];
    }

    private static object? ReadRegistry(string path, string name)
    {
        try { using var key = Registry.LocalMachine.OpenSubKey(path); return key?.GetValue(name); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or SecurityException) { return null; }
    }

    private static string? SafeText(JsonElement value) =>
        value.ValueKind == JsonValueKind.String && value.GetString() is string text ? SafeText(text) : null;

    private static string? SafeText(string value)
    {
        if (value.Length is 0 or > 160) return null;
        foreach (char c in value)
            if (char.IsControl(c) || c is '\\' or '/' or '@') return null;
        return value;
    }

    private static JsonElement ReadJson(string directory, string relative) => Parse(ReadBytes(directory, relative));

    private static JsonElement Parse(byte[]? contents)
    {
        if (contents is null) return default;
        try { using var document = JsonDocument.Parse(contents); return document.RootElement.Clone(); }
        catch (JsonException) { return default; }
    }

    private static byte[]? ReadBytes(string directory, string relative)
    {
        try
        {
            using var stream = new FileStream(Path.Combine(directory, relative), FileMode.Open, FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);
            if (stream.Length > 1024 * 1024) return null;
            byte[] result = new byte[stream.Length];
            stream.ReadExactly(result);
            return result;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
        { return null; }
    }

    [DllImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx(ref MemoryStatus status);

    [StructLayout(LayoutKind.Sequential)]
    private struct MemoryStatus
    {
        internal uint Length, Load;
        internal ulong TotalPhysical, AvailablePhysical, TotalPageFile, AvailablePageFile, TotalVirtual,
            AvailableVirtual, AvailableExtendedVirtual;
    }
}
