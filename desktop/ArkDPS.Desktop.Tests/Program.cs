using System;
using System.ComponentModel;
using System.Text.Json;
using ArkDPS.Desktop;

// Headless tests inject ordinary query results and instantiate WPF controls only.
// They never query a real target, show a window, run the app or send input.
internal static class Program
{
    private const uint Pid = 24804;
    private const long Birth = 134397034780587831;
    private const string Root = @"C:\Games\Arknights";
    private static int passed;

    private static JsonElement Config(string platform = "native_pc", string? root = Root) =>
        JsonSerializer.SerializeToElement(new { platform, native_game_root = root });

    private static JsonElement Diagnostic(string platform = "native_pc", string status = "ready_unique",
        bool connected = true, string? root = Root, long? birth = Birth, uint pid = Pid) =>
        JsonSerializer.SerializeToElement(new { platform, status, connection_ok = connected,
            native_game_root = root, process_pid = pid, process_created_filetime = birth });

    private static NativePcWindowCandidate Window(long handle = 100) =>
        new(new nint(handle), Pid, true, true, true, false, false, false, 1280, 720, 1280, 720);

    private static void Check(string name, bool condition)
    {
        if (!condition) throw new InvalidOperationException(name);
        passed++;
    }

    [STAThread]
    private static void Main()
    {
        var normal = new NativePcWindowResolver(_ => Birth, _ => new[] { Window() });
        Check("unique suitable window", normal.Resolve(Diagnostic(), Config(), 0) is
            { State: NativePcWindowState.Available, Handle: 100 });
        Check("case and trailing root separator", normal.Resolve(Diagnostic(root: Root + "\\"),
            Config(root: Root.ToLowerInvariant()), 0).State == NativePcWindowState.Available);

        int queries = 0;
        var guarded = new NativePcWindowResolver(_ => { queries++; return Birth; },
            _ => throw new InvalidOperationException("window query must not run"));
        foreach (var item in new[] {
            (Diagnostic(), Config("mumu")),
            (Diagnostic(platform: "mumu"), Config()),
            (Diagnostic(status: "error"), Config()),
            (Diagnostic(connected: false), Config()),
            (Diagnostic(root: Root + "-other"), Config()),
            (Diagnostic(root: null), Config()),
            (Diagnostic(), Config(root: "relative")),
            (Diagnostic(birth: null), Config()),
            (Diagnostic(birth: 0), Config()),
            (Diagnostic(pid: 0), Config()),
            (Diagnostic(pid: uint.MaxValue), Config()),
        })
            Check("invalid source/config/identity rejected", guarded.Resolve(item.Item1, item.Item2, 0).State ==
                NativePcWindowState.Unavailable);
        Check("invalid diagnostics do not query", queries == 0);

        var reused = new NativePcWindowResolver(_ => Birth + 1,
            _ => throw new InvalidOperationException("reused PID must not enumerate"));
        Check("adjacent Int64 birth mismatch", reused.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Unavailable);
        int raceQueries = 0;
        var race = new NativePcWindowResolver(_ => ++raceQueries == 1 ? Birth : Birth + 1,
            _ => new[] { Window() });
        Check("PID reuse during enumeration", race.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Unavailable);
        Check("birth checked before and after enumeration", raceQueries == 2);
        var exited = new NativePcWindowResolver(_ => null, _ => new[] { Window() });
        Check("process exit", exited.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Unavailable);
        var denied = new NativePcWindowResolver(_ => throw new Win32Exception(5), _ => new[] { Window() });
        Check("query denied", denied.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Unavailable);

        foreach (var item in new[] {
            Window() with { Handle = 0 },
            Window() with { ProcessId = Pid + 1 },
            Window() with { Visible = false },
            Window() with { Ownerless = false },
            Window() with { RootWindow = false },
            Window() with { Cloaked = true },
            Window() with { ToolWindow = true },
            Window() with { Minimized = true, RestoreWidth = 0, RestoreHeight = 0 },
            Window() with { Minimized = true, RestoreWidth = 249 },
            Window() with { Minimized = true, RestoreHeight = 179 },
            Window() with { ClientWidth = 249 },
            Window() with { ClientHeight = 179 },
        })
        {
            var unsuitable = new NativePcWindowResolver(_ => Birth, _ => new[] { item });
            Check("unsuitable window rejected", unsuitable.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Unavailable);
        }
        Check("own window excluded", normal.Resolve(Diagnostic(), Config(), 100).State == NativePcWindowState.Unavailable);
        var duplicate = new NativePcWindowResolver(_ => Birth, _ => new[] { Window(), Window() });
        Check("same handle is one candidate", duplicate.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Available);
        var ambiguous = new NativePcWindowResolver(_ => Birth, _ => new[] { Window(), Window(101) });
        Check("multiple suitable windows rejected", ambiguous.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Ambiguous);
        var iconic = Window() with { Minimized = true, ClientWidth = 0, ClientHeight = 0 };
        var minimizing = new NativePcWindowResolver(_ => Birth, _ => new[] { iconic });
        Check("minimized window retains verified handle via restore bounds", minimizing.Resolve(Diagnostic(), Config(), 0) is
            { State: NativePcWindowState.Minimized, Handle: 100 });
        var mixed = new NativePcWindowResolver(_ => Birth, _ => new[] { iconic, Window(101) });
        Check("minimized and normal candidates stay ambiguous", mixed.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Ambiguous);
        var twoIconic = new NativePcWindowResolver(_ => Birth, _ => new[] { iconic, iconic with { Handle = 101 } });
        Check("multiple minimized candidates stay ambiguous", twoIconic.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Ambiguous);
        var reusedIconic = new NativePcWindowResolver(_ => Birth + 1, _ => new[] { iconic });
        Check("minimized PID reuse rejected", reusedIconic.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Unavailable);

        bool minimized = false;
        long? currentBirth = Birth;
        var changing = new NativePcWindowResolver(_ => currentBirth,
            _ => new[] { Window() with { Minimized = minimized } });
        Check("initial attachment", changing.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Available);
        minimized = true;
        Check("minimize retains identity next call", changing.Resolve(Diagnostic(), Config(), 0) is
            { State: NativePcWindowState.Minimized, Handle: 100 });
        minimized = false;
        currentBirth = Birth + 1;
        Check("cached PID never reused", changing.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Unavailable);
        currentBirth = Birth;
        Check("restored identity resolves again", changing.Resolve(Diagnostic(), Config(), 0).State == NativePcWindowState.Available);
        Console.WriteLine($"PASS_NATIVE_PC_WINDOW_RESOLVER {passed} assertions (headless, injected metadata only)");
        LiveTargetSummaryTests.Run();
        ConnectionDetectionTests.Run();
        HudStatusNoticeTests.Run();
        ScreenshotCategoryTests.Run();
    }
}
