using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace ArkDPS.Desktop;

internal enum EmulatorPlacementState { Attached, NoPlayer, Minimized, NoRoom, Ambiguous }

internal readonly record struct EmulatorPlacement(EmulatorPlacementState State, Rectangle? Bounds = null)
{
    internal nint PlayerWindow { get; init; }
}

internal sealed class EmulatorAttachment
{
    private static readonly string[] PlayerProcesses = { "MuMuNxMain", "MuMuNxDevice", "MuMuPlayer", "NemuPlayer" };
    private const int MinimumWidth = 280;
    private const int MinimumVisibleHeight = 160;
    private const int DefaultDpi = 96;
    private const uint ExtendedFrameBounds = 9;
    private const uint Cloaked = 14;
    private const uint WindowOwner = 4;
    private const uint RootAncestor = 2;
    private const uint NearestMonitor = 2;

    private record struct PlayerWindow(nint Handle, uint ProcessId);
    private record struct ReportedEndpoint(string Host, int Port, nint MainWindow);
    private record struct PlayerEndpoint(string Host, int Port, PlayerWindow Window);
    private PlayerWindow[] players = Array.Empty<PlayerWindow>();
    private HashSet<uint> playerProcessIds = new();
    private PlayerEndpoint[]? knownEndpoints;
    private Task<PlayerEndpoint[]?>? lookup;
    private long scanAfter;
    private long lookupAfter;
    private string lastSerial = "";
    private string rememberedAlias = "";
    private bool completedLookup;
    private nint foregroundPlayer;

    internal nint FixtureWindow { get; set; }

    internal void RaiseAlongsidePlayer(EmulatorPlacement placement, nint sideWindow)
    {
        nint player = placement.State == EmulatorPlacementState.Attached ? placement.PlayerWindow : nint.Zero;
        if (player != nint.Zero && GetForegroundWindow() == player)
        {
            const uint noMoveSizeOrActivate = 0x0013;
            if (foregroundPlayer != player &&
                SetWindowPos(sideWindow, nint.Zero, 0, 0, 0, 0, noMoveSizeOrActivate))
                foregroundPlayer = player;
        }
        else foregroundPlayer = nint.Zero;
    }

    internal EmulatorPlacement GetPlacement(JsonElement diagnostics, nint ownWindow, int width,
        JsonElement connection = default)
    {
        if (App.Verification != null || App.BridgeArguments.Contains("--offline"))
            return PlaceBeside(FixtureWindow, width);

        string serial = ResolveSerial(diagnostics, connection);
        if (serial.Length == 0 && diagnostics.Text("status") == "needs_selection")
            return new(EmulatorPlacementState.Ambiguous);
        bool hasEndpoint = TryEndpoint(serial, out string host, out int port);
        if (!string.Equals(serial, lastSerial, StringComparison.OrdinalIgnoreCase))
        {
            lastSerial = serial;
            rememberedAlias = "";
            completedLookup = false;
            lookupAfter = 0;
        }
        if (!hasEndpoint && serial.Length > 0)
        {
            var aliases = diagnostics.Get("instances").Items()
                .Select(instance => instance.Get("serials").Items().Select(item => item.Text()).ToArray())
                .FirstOrDefault(items => items.Contains(serial, StringComparer.OrdinalIgnoreCase));
            if (aliases?.FirstOrDefault(alias => TryEndpoint(alias, out _, out _)) is string endpointAlias)
                rememberedAlias = endpointAlias;
            hasEndpoint = TryEndpoint(rememberedAlias, out host, out port);
        }
        if (!hasEndpoint && serial.Length > 0 && !serial.StartsWith("emulator-", StringComparison.OrdinalIgnoreCase))
            return new(EmulatorPlacementState.NoPlayer); // USB has no companion window.

        if (lookup is { IsCompleted: true })
        {
            knownEndpoints = lookup.GetAwaiter().GetResult() ?? knownEndpoints;
            completedLookup = true;
            lookup = null;
        }
        PlayerWindow? exact = null;
        bool ambiguous = false;
        if (hasEndpoint && knownEndpoints != null)
        {
            var matches = knownEndpoints.Where(item => item.Port == port && HostsMatch(item.Host, host))
                .Select(item => item.Window).Where(window => window.Handle != ownWindow && IsAvailable(window))
                .Distinct().ToArray();
            if (matches.Length == 1) exact = matches[0];
            ambiguous = matches.Length > 1;
        }

        long now = Environment.TickCount64;
        if (now >= scanAfter && (exact == null || now >= lookupAfter))
        {
            players = FindPlayers(ownWindow, out playerProcessIds);
            scanAfter = now + 1_000;
        }
        var available = players.Where(IsAvailable).ToArray();
        if (hasEndpoint)
        {
            // MuMu maps its window handles to ADB ports. Query when the
            // identity needs resolving. Geometry updates never wait on it.
            if (playerProcessIds.Count > 0 && lookup == null && now >= lookupAfter)
            {
                var processIds = playerProcessIds;
                lookup = Task.Run(() => QueryEndpoints(processIds));
                lookupAfter = now + (exact != null ? 15_000 : 4_000);
            }

            if (exact is PlayerWindow selected)
                return PlaceBeside(selected.Handle, width);
            if (ambiguous)
                return new(EmulatorPlacementState.Ambiguous);
            if (available.Length == 1 && (!completedLookup || knownEndpoints != null || !IsLoopback(host)))
                return new(EmulatorPlacementState.NoPlayer);
        }

        return available.Length switch
        {
            1 => PlaceBeside(available[0].Handle, width),
            0 => new(EmulatorPlacementState.NoPlayer),
            _ => new(EmulatorPlacementState.Ambiguous),
        };
    }

    private static string ResolveSerial(JsonElement diagnostics, JsonElement connection)
    {
        string selected = diagnostics.Text("selected_serial");
        if (selected.Length > 0 || diagnostics.Text("status") == "needs_selection")
            return selected;
        int argument = App.BridgeArguments.LastIndexOf("--serial");
        return argument >= 0 && argument + 1 < App.BridgeArguments.Count
            ? App.BridgeArguments[argument + 1] : connection.Text("serial");
    }

    private static bool TryEndpoint(string serial, out string host, out int port)
    {
        host = "";
        port = 0;
        int separator = serial.LastIndexOf(':');
        if (separator <= 0 || !int.TryParse(serial.AsSpan(separator + 1), NumberStyles.None,
                CultureInfo.InvariantCulture, out port) || port is <= 0 or > 65535)
            return false;
        host = serial[..separator].Trim('[', ']');
        return true;
    }

    private static bool HostsMatch(string first, string second) =>
        string.Equals(first, second, StringComparison.OrdinalIgnoreCase) ||
        (IsLoopback(first) && IsLoopback(second));

    private static bool IsLoopback(string host) => host.Equals("localhost", StringComparison.OrdinalIgnoreCase)
        || (IPAddress.TryParse(host, out var address) && IPAddress.IsLoopback(address));

    private static bool IsPlayerProcess(uint processId)
    {
        try
        {
            using var process = Process.GetProcessById((int)processId);
            return PlayerProcesses.Contains(process.ProcessName, StringComparer.OrdinalIgnoreCase);
        }
        catch (Exception error) when (error is Win32Exception or ArgumentException or InvalidOperationException)
        {
            return false;
        }
    }

    private static PlayerWindow[] FindPlayers(nint ownWindow, out HashSet<uint> processIds)
    {
        var found = new List<PlayerWindow>();
        var owners = new Dictionary<uint, bool>();
        var title = new StringBuilder(256);
        EnumWindows((handle, _) =>
        {
            if (handle == ownWindow || GetWindow(handle, WindowOwner) != nint.Zero)
                return true;
            if (GetWindowText(handle, title, title.Capacity) == 0)
                return true;
            GetWindowThreadProcessId(handle, out uint id);
            if (!owners.TryGetValue(id, out bool isPlayer))
                owners[id] = isPlayer = IsPlayerProcess(id);
            if (!isPlayer || !IsWindowVisible(handle)) return true;

            string text = title.ToString();
            if (text.Contains("多开") || text.Contains("设置") ||
                text.Contains("Multi-instance", StringComparison.OrdinalIgnoreCase) ||
                text.Contains("Settings", StringComparison.OrdinalIgnoreCase))
                return true;
            if (!IsIconic(handle) && (!TryBounds(handle, out var bounds) || bounds.Width < 250 || bounds.Height < 180))
                return true;
            found.Add(new(handle, id));
            return true;
        }, nint.Zero);
        processIds = owners.Where(pair => pair.Value).Select(pair => pair.Key).ToHashSet();
        return found.ToArray();
    }

    private static bool IsAvailable(PlayerWindow window) =>
        window.Handle != nint.Zero && IsWindowVisible(window.Handle) &&
        GetWindowThreadProcessId(window.Handle, out uint pid) != 0 && pid == window.ProcessId &&
        GetAncestor(window.Handle, RootAncestor) == window.Handle;

    private static async Task<PlayerEndpoint[]?> QueryEndpoints(HashSet<uint> processIds)
    {
        var directories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (uint processId in processIds)
        {
            try
            {
                using var process = Process.GetProcessById((int)processId);
                string? directory = PlayerInstallation(process);
                if (directory != null) directories.Add(directory);
            }
            catch (Exception error) when (error is Win32Exception or ArgumentException or InvalidOperationException)
            {
                // The player can exit during enumeration or run at higher privilege.
            }
        }

        var results = await Task.WhenAll(directories.Select(QueryDirectory)).ConfigureAwait(false);
        return results.Any(result => result != null)
            ? results.OfType<PlayerEndpoint[]>().SelectMany(result => result).Distinct().ToArray()
            : null;
    }

    private static string? PlayerInstallation(Process process)
    {
        if (!PlayerProcesses.Contains(process.ProcessName, StringComparer.OrdinalIgnoreCase)) return null;
        string? directory = Path.GetDirectoryName(process.MainModule?.FileName);
        if (directory == null) return null;
        if (Path.GetFileName(directory).Equals("nx_main", StringComparison.OrdinalIgnoreCase))
            return Path.GetDirectoryName(directory);
        // Newer MuMu uses a distinct player shell inside nx_device/<version>/shell.
        string? parent = Path.GetDirectoryName(directory);
        string? device = parent == null ? null : Path.GetDirectoryName(parent);
        if (device != null && Path.GetFileName(device).Equals("nx_device", StringComparison.OrdinalIgnoreCase))
            return Path.GetDirectoryName(device);
        return Path.GetFileName(directory).Equals("shell", StringComparison.OrdinalIgnoreCase)
            ? parent : directory;
    }

    private static async Task<PlayerEndpoint[]?> QueryDirectory(string installation)
    {
        foreach (string folder in new[] { "nx_main", "shell", "" })
        {
            foreach (string name in new[] { "mumu-cli.exe", "MuMuManager.exe" })
            {
                string path = Path.Combine(installation, folder, name);
                if (File.Exists(path) && await QueryTool(path, installation).ConfigureAwait(false) is PlayerEndpoint[] result)
                    return result;
            }
        }
        return null;
    }

    private static async Task<PlayerEndpoint[]?> QueryTool(string tool, string installation)
    {
        var start = new ProcessStartInfo(tool)
        {
            WorkingDirectory = Path.GetDirectoryName(tool)!,
            CreateNoWindow = true,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            StandardOutputEncoding = Encoding.UTF8,
        };
        foreach (string argument in new[] { "info", "-v", "all" }) start.ArgumentList.Add(argument);
        try
        {
            using var process = Process.Start(start);
            if (process == null) return null;
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            try
            {
                string output = await process.StandardOutput.ReadToEndAsync(timeout.Token).ConfigureAwait(false);
                await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
                if (process.ExitCode != 0) return null;
                return ReadEndpoints(output)?.Select(endpoint => new PlayerEndpoint(endpoint.Host, endpoint.Port,
                        new PlayerWindow(endpoint.MainWindow, ValidateWindowOwner(endpoint.MainWindow, installation))))
                    .Where(endpoint => endpoint.Window.ProcessId != 0).ToArray();
            }
            catch (OperationCanceledException)
            {
                if (!process.HasExited) process.Kill();
                return null;
            }
        }
        catch (Exception error) when (error is Win32Exception or InvalidOperationException or IOException or JsonException)
        {
            return null;
        }
    }

    private static ReportedEndpoint[]? ReadEndpoints(string output)
    {
        using var document = JsonDocument.Parse(output.Trim().TrimStart('\uFEFF'));
        JsonElement root = document.RootElement;
        foreach (string wrapper in new[] { "data", "players" })
        {
            var wrapped = root.Get(wrapper);
            if (wrapped.ValueKind is JsonValueKind.Array or JsonValueKind.Object) root = wrapped;
        }
        IEnumerable<JsonElement> instances = root.ValueKind switch
        {
            JsonValueKind.Array => root.Items(),
            JsonValueKind.Object when root.Get("index").ValueKind != JsonValueKind.Undefined => new[] { root },
            JsonValueKind.Object => root.EnumerateObject().Select(item => item.Value),
            _ => Array.Empty<JsonElement>(),
        };
        var players = new List<ReportedEndpoint>();
        bool recognized = false;
        foreach (JsonElement instance in instances)
        {
            if (instance.Get("index").ValueKind == JsonValueKind.Undefined) continue;
            recognized = true;
            if (!int.TryParse(instance.Text("adb_port"), NumberStyles.None, CultureInfo.InvariantCulture, out int port) ||
                port is <= 0 or > 65535 || !TryWindowHandle(instance.Get("main_wnd"), out nint window))
                continue;

            string host = instance.Text("adb_host_ip");
            players.Add(new(host.Length > 0 ? host : IPAddress.Loopback.ToString(), port, window));
        }
        return recognized ? players.ToArray() : null;
    }

    private static uint ValidateWindowOwner(nint window, string installation)
    {
        // The CLI's PID describes the instance process. Validate the actual
        // top-level window owner to guard against unrelated or recycled HWNDs.
        if (GetWindowThreadProcessId(window, out uint pid) == 0 ||
            GetAncestor(window, RootAncestor) != window) return 0;
        try
        {
            using var process = Process.GetProcessById((int)pid);
            return string.Equals(PlayerInstallation(process), installation, StringComparison.OrdinalIgnoreCase)
                ? pid : 0;
        }
        catch (Exception error) when (error is Win32Exception or ArgumentException or InvalidOperationException)
        {
            return 0;
        }
    }

    private static bool TryWindowHandle(JsonElement value, out nint handle)
    {
        var text = value.Text().AsSpan();
        if (text.StartsWith("0x", StringComparison.OrdinalIgnoreCase)) text = text[2..];
        NumberStyles style = value.ValueKind == JsonValueKind.Number ? NumberStyles.None : NumberStyles.HexNumber;
        if (long.TryParse(text, style, CultureInfo.InvariantCulture, out long address) && address is > 0 and <= uint.MaxValue)
        {
            handle = new nint(address);
            return true;
        }
        handle = nint.Zero;
        return false;
    }

    private static EmulatorPlacement PlaceBeside(nint handle, int width) =>
        MeasurePlacement(handle, width) with { PlayerWindow = handle };

    private static EmulatorPlacement MeasurePlacement(nint handle, int width)
    {
        if (handle == nint.Zero || !IsWindowVisible(handle) ||
            (DwmGetWindowAttribute(handle, Cloaked, out int cloaked, sizeof(int)) == 0 && cloaked != 0))
            return new(EmulatorPlacementState.NoPlayer);
        if (IsIconic(handle))
            return new(EmulatorPlacementState.Minimized);
        if (!TryBounds(handle, out var player))
            return new(EmulatorPlacementState.NoPlayer);

        var current = Screen.FromRectangle(player);
        var placement = CalculatePlacement(player, current.WorkingArea, MonitorScale(current.Bounds), width);
        return placement.State == EmulatorPlacementState.Attached
            ? placement : PlaceOnAdjacentMonitor(player, current, width) ?? placement;
    }

    internal static EmulatorPlacement CalculatePlacement(Rectangle player, Rectangle work, double scale, int width)
    {
        int desired = (int)Math.Round(width * scale);
        int minimum = (int)Math.Round(Math.Min(width, MinimumWidth) * scale);
        int top = Math.Max(player.Top, work.Top);
        int bottom = Math.Min(player.Bottom, work.Bottom);
        int leftRoom = Math.Clamp(player.Left, work.Left, work.Right) - work.Left;
        int rightRoom = work.Right - Math.Clamp(player.Right, work.Left, work.Right);

        int minimumHeight = Math.Min(player.Height, (int)Math.Round(MinimumVisibleHeight * scale));
        if (bottom - top >= minimumHeight)
        {
            if (rightRoom >= desired && player.Right >= work.Left)
                return new(EmulatorPlacementState.Attached, Rectangle.FromLTRB(player.Right, top, player.Right + desired, bottom));
            if (leftRoom >= desired && player.Left <= work.Right)
                return new(EmulatorPlacementState.Attached, Rectangle.FromLTRB(player.Left - desired, top, player.Left, bottom));

            if (rightRoom >= minimum && rightRoom >= leftRoom && player.Right >= work.Left)
                return new(EmulatorPlacementState.Attached, Rectangle.FromLTRB(player.Right, top, work.Right, bottom));
            if (leftRoom >= minimum && player.Left <= work.Right)
                return new(EmulatorPlacementState.Attached, Rectangle.FromLTRB(work.Left, top, player.Left, bottom));
        }

        int fallbackWidth = Math.Min(desired, work.Width);
        int fallbackHeight = Math.Min(player.Height, work.Height);
        int fallbackX = leftRoom > rightRoom ? work.Left : work.Right - fallbackWidth;
        int fallbackY = Math.Clamp(player.Top, work.Top, work.Bottom - fallbackHeight);
        return new(EmulatorPlacementState.NoRoom, new Rectangle(fallbackX, fallbackY, fallbackWidth, fallbackHeight));
    }

    private static EmulatorPlacement? PlaceOnAdjacentMonitor(Rectangle player, Screen current, int width)
    {
        var work = current.WorkingArea;
        int top = Math.Max(player.Top, work.Top);
        int bottom = Math.Min(player.Bottom, work.Bottom);
        int minimumHeight = Math.Min(player.Height, (int)Math.Round(MinimumVisibleHeight * MonitorScale(current.Bounds)));
        if (bottom - top < minimumHeight) return null;
        var screens = Screen.AllScreens;
        foreach (bool right in new[] { true, false })
        {
            int edge = right ? player.Right : player.Left;
            int monitorEdge = right ? current.Bounds.Right : current.Bounds.Left;
            // A maximized Win32 window can leave a few physical border pixels.
            if (Math.Abs(edge - monitorEdge) > 8) continue;
            foreach (var screen in screens)
            {
                if (screen.DeviceName == current.DeviceName) continue;
                var adjacent = screen.WorkingArea;
                if ((right ? adjacent.Left : adjacent.Right) != monitorEdge ||
                    top < adjacent.Top || bottom > adjacent.Bottom) continue;
                int adjacentWidth = (int)Math.Round(width * MonitorScale(screen.Bounds));
                if (adjacent.Width >= adjacentWidth)
                    return new(EmulatorPlacementState.Attached,
                        new Rectangle(right ? monitorEdge : monitorEdge - adjacentWidth, top, adjacentWidth, bottom - top));
            }
        }
        return null;
    }

    private static double MonitorScale(Rectangle screen)
    {
        var bounds = new WindowRect { Left = screen.Left, Top = screen.Top, Right = screen.Right, Bottom = screen.Bottom };
        nint monitor = MonitorFromRect(ref bounds, NearestMonitor);
        // The desktop manifest makes this process PerMonitorV2. Read the
        // target monitor DPI even when the player itself is system-DPI aware.
        return GetDpiForMonitor(monitor, 0, out uint x, out _) == 0 ? (double)x / DefaultDpi : 1;
    }

    private static bool TryBounds(nint handle, out Rectangle bounds)
    {
        var result = DwmGetWindowAttribute(handle, ExtendedFrameBounds, out WindowRect rect, Marshal.SizeOf<WindowRect>()) == 0
            && rect.Right > rect.Left && rect.Bottom > rect.Top;
        if (!result) result = GetWindowRect(handle, out rect) && rect.Right > rect.Left && rect.Bottom > rect.Top;
        bounds = Rectangle.FromLTRB(rect.Left, rect.Top, rect.Right, rect.Bottom);
        return result;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WindowRect { public int Left, Top, Right, Bottom; }

    private delegate bool EnumWindowsCallback(nint window, nint data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsCallback callback, nint data);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint window, out uint processId);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(nint window);
    [DllImport("user32.dll")] private static extern bool IsIconic(nint window);
    [DllImport("user32.dll")] private static extern nint GetAncestor(nint window, uint flags);
    [DllImport("user32.dll")] private static extern nint GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool SetWindowPos(nint window, nint after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] private static extern nint GetWindow(nint window, uint command);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(nint window, StringBuilder text, int maxCount);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(nint window, out WindowRect bounds);
    [DllImport("user32.dll")] private static extern nint MonitorFromRect(ref WindowRect bounds, uint flags);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(nint window, uint attribute, out WindowRect value, int size);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(nint window, uint attribute, out int value, int size);
    [DllImport("shcore.dll")] private static extern int GetDpiForMonitor(nint monitor, int type, out uint dpiX, out uint dpiY);
}
