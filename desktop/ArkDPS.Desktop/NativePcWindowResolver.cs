using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;

namespace ArkDPS.Desktop;

internal enum NativePcWindowState { Unavailable, Available, Minimized, Ambiguous }

internal readonly record struct NativePcWindowSelection(NativePcWindowState State, nint Handle = default);

internal readonly record struct NativePcWindowCandidate(nint Handle, uint ProcessId, bool Visible,
    bool Ownerless, bool RootWindow, bool Cloaked, bool ToolWindow, bool Minimized,
    int ClientWidth, int ClientHeight, int RestoreWidth = 0, int RestoreHeight = 0);

/// <summary>Resolves only the process identity already verified by the collection worker.</summary>
internal sealed class NativePcWindowResolver
{
    private readonly Func<uint, long?> readCreatedFileTime;
    private readonly Func<uint, NativePcWindowCandidate[]> readWindows;

    internal NativePcWindowResolver() : this(ReadCreatedFileTime, ReadWindows) { }

    // The seam runs the real selection/identity checks with recorded window metadata,
    // without opening a process, creating a window or loading WPF in offline tests.
    internal NativePcWindowResolver(Func<uint, long?> readCreatedFileTime,
        Func<uint, NativePcWindowCandidate[]> readWindows)
    {
        this.readCreatedFileTime = readCreatedFileTime;
        this.readWindows = readWindows;
    }

    internal NativePcWindowSelection Resolve(JsonElement diagnostics, JsonElement connection, nint ownWindow)
    {
        try
        {
            // During a source/settings switch, an old worker diagnostic must not
            // attach the desktop to its previous source or installation.
            if (Text(connection, "platform") != "native_pc" || Text(diagnostics, "platform") != "native_pc" ||
                Text(diagnostics, "status") != "ready_unique" ||
                Property(diagnostics, "connection_ok").ValueKind != JsonValueKind.True ||
                !SameRoot(Text(connection, "native_game_root"), Text(diagnostics, "native_game_root")))
                return default;

            var pidValue = Property(diagnostics, "process_pid");
            var createdValue = Property(diagnostics, "process_created_filetime");
            if (pidValue.ValueKind != JsonValueKind.Number || !pidValue.TryGetUInt32(out uint pid) ||
                pid is 0 or > int.MaxValue || createdValue.ValueKind != JsonValueKind.Number ||
                !createdValue.TryGetInt64(out long created) || created <= 0)
                return default;

            // Parse FILETIME as Int64 directly. A double cannot preserve all bits
            // of the worker's process-instance identity.
            if (readCreatedFileTime(pid) != created) return default;
            var candidates = readWindows(pid).Where(window => window.Handle != nint.Zero &&
                window.Handle != ownWindow && window.ProcessId == pid && window.Visible &&
                window.Ownerless && window.RootWindow && !window.Cloaked && !window.ToolWindow &&
                (window.Minimized ? window.RestoreWidth >= 250 && window.RestoreHeight >= 180
                    : window.ClientWidth >= 250 && window.ClientHeight >= 180))
                .DistinctBy(window => window.Handle).ToArray();

            // Enumeration can race exit/PID reuse. Query again on every call,
            // including the ambiguous/empty result; no PID/HWND is cached.
            if (readCreatedFileTime(pid) != created) return default;
            return candidates.Length switch
            {
                1 => new(candidates[0].Minimized ? NativePcWindowState.Minimized : NativePcWindowState.Available,
                    candidates[0].Handle),
                > 1 => new(NativePcWindowState.Ambiguous),
                _ => default,
            };
        }
        catch (Exception error) when (error is Win32Exception or ArgumentException or
            InvalidOperationException or IOException or UnauthorizedAccessException or NotSupportedException)
        {
            return default;
        }
    }

    private static JsonElement Property(JsonElement value, string name) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var result) ? result : default;

    private static string Text(JsonElement value, string name) =>
        Property(value, name) is var item && item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : "";

    private static bool SameRoot(string first, string second) =>
        Path.IsPathFullyQualified(first) && Path.IsPathFullyQualified(second) &&
        string.Equals(Path.TrimEndingDirectorySeparator(Path.GetFullPath(first)),
            Path.TrimEndingDirectorySeparator(Path.GetFullPath(second)), StringComparison.OrdinalIgnoreCase);

    private static long? ReadCreatedFileTime(uint pid)
    {
        using var process = Process.GetProcessById(checked((int)pid));
        if (process.HasExited) return null;
        long created = process.StartTime.ToFileTimeUtc();
        return process.HasExited ? null : created;
    }

    private static NativePcWindowCandidate[] ReadWindows(uint pid)
    {
        var windows = new List<NativePcWindowCandidate>();
        if (!EnumWindows((window, _) =>
        {
            if (GetWindowThreadProcessId(window, out uint ownerPid) == 0 || ownerPid != pid) return true;
            bool hasClient = GetClientRect(window, out var client);
            bool hasCloak = DwmGetWindowAttribute(window, 14, out int cloaked, sizeof(int)) == 0;
            bool minimized = IsIconic(window);
            var placement = new WindowPlacement { Length = (uint)Marshal.SizeOf<WindowPlacement>() };
            // Iconic windows can report a zero/tiny client rect. Their normal
            // restore size identifies a usable game window without restoring it.
            bool hasRestore = minimized && GetWindowPlacement(window, ref placement);
            windows.Add(new(window, ownerPid, IsWindowVisible(window), GetWindow(window, 4) == nint.Zero,
                GetAncestor(window, 2) == window, !hasCloak || cloaked != 0,
                (GetWindowLong(window, -20) & 0x00000080) != 0, minimized,
                hasClient ? client.Right - client.Left : 0, hasClient ? client.Bottom - client.Top : 0,
                hasRestore ? placement.NormalPosition.Right - placement.NormalPosition.Left : 0,
                hasRestore ? placement.NormalPosition.Bottom - placement.NormalPosition.Top : 0));
            return true;
        }, nint.Zero))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return windows.ToArray();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WindowRect { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    private struct WindowPoint { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct WindowPlacement
    {
        public uint Length, Flags, ShowCommand;
        public WindowPoint MinPosition, MaxPosition;
        public WindowRect NormalPosition;
    }

    private delegate bool EnumWindowsCallback(nint window, nint data);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool EnumWindows(EnumWindowsCallback callback, nint data);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint window, out uint processId);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(nint window);
    [DllImport("user32.dll")] private static extern bool IsIconic(nint window);
    [DllImport("user32.dll")] private static extern nint GetWindow(nint window, uint command);
    [DllImport("user32.dll")] private static extern nint GetAncestor(nint window, uint flags);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")] private static extern int GetWindowLong(nint window, int index);
    [DllImport("user32.dll")] private static extern bool GetClientRect(nint window, out WindowRect bounds);
    [DllImport("user32.dll")] private static extern bool GetWindowPlacement(nint window, ref WindowPlacement placement);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(nint window, uint attribute, out int value, int size);
}
