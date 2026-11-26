using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Interop;
using ArkDPS.Desktop;

internal static class BridgeStartupTests
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly FieldInfo BridgeField = typeof(MainWindow).GetField("bridge", Private)!;
    private static readonly MethodInfo Restore = typeof(MainWindow).GetMethod("RestoreWorkerSnapshot", Private)!;
    private static int passed;

    private const string Worker = """
        import json, sys, time
        from pathlib import Path
        mode, delay, marker = sys.argv[1], float(sys.argv[2]), Path(sys.argv[3])
        if mode == 'exit':
            raise SystemExit(7)
        marker.with_suffix('.ready').write_text('ready')
        time.sleep(delay)
        for line in sys.stdin:
            command = json.loads(line)
            op = command['op']
            marker.with_suffix('.received').write_text(op)
            if (mode == 'hang_hello' and op == 'hello') or (mode == 'hang_snapshot' and op == 'snapshot'):
                sys.stdin.read()
                marker.with_suffix('.eof').write_text('EOF received')
                break
            reply = dict(version=1, id=command['id'], ok=True,
                         kind={'hello':'hello', 'snapshot':'snapshot', 'quit':'bye'}.get(op, 'state'),
                         result=dict(transport='stdio-jsonl'),
                         state=dict(history=[], marker='synthetic startup'))
            print(json.dumps(reply), flush=True)
            if op == 'quit':
                break
        """;

    private static void Check(string label, bool condition)
    {
        if (!condition) throw new InvalidOperationException(label);
        passed++;
    }

    private static (string Root, string Marker, string[] Arguments) Fixture(string directory, string name,
        string mode = "normal", string delay = "0")
    {
        string root = Path.Combine(directory, name);
        Directory.CreateDirectory(root);
        File.WriteAllText(Path.Combine(root, "desktop_bridge.py"), Worker, new UTF8Encoding(false));
        string marker = Path.Combine(root, "worker");
        return (root, marker, new[] { mode, delay, marker });
    }

    private static Task<JsonElement> RestoreAsync(MainWindow window, CancellationToken stop = default) =>
        (Task<JsonElement>)Restore.Invoke(window, new object[] { stop })!;

    private static void SelectRecovery((string Root, string Marker, string[] Arguments) fixture)
    {
        App.Root = fixture.Root;
        App.StateDirectory = Path.Combine(fixture.Root, "runtime");
        App.BridgeArguments.Clear();
        App.BridgeArguments.AddRange(fixture.Arguments);
    }

    private static async Task WaitForFile(string path)
    {
        var deadline = Stopwatch.StartNew();
        while (!File.Exists(path))
        {
            if (deadline.Elapsed > TimeSpan.FromSeconds(5))
                throw new InvalidOperationException("Synthetic worker did not reach " + Path.GetFileName(path));
            await Task.Delay(10).ConfigureAwait(false);
        }
    }

    internal static void Run()
    {
        string directory = Directory.CreateTempSubdirectory("arkdps-startup-tests-").FullName;
        string oldRoot = App.Root, oldState = App.StateDirectory, oldPython = App.Python;
        string[] oldArguments = App.BridgeArguments.ToArray();
        var oldContext = SynchronizationContext.Current;
        if (Application.Current is null) { var app = new App(); app.InitializeComponent(); }
        var window = new MainWindow();
        try
        {
            // The test owns no dispatcher loop: its asynchronous pipe work must
            // not post back to a WPF context while this STA runner is blocked.
            SynchronizationContext.SetSynchronizationContext(null);
            App.Python = Environment.GetEnvironmentVariable("ARKDPS_TEST_PYTHON") ?? "python.exe";
            RunAsync(directory, window).GetAwaiter().GetResult();
            Check("startup tests never show a window or create a tray",
                new WindowInteropHelper(window).Handle == IntPtr.Zero &&
                typeof(MainWindow).GetField("tray", Private)!.GetValue(window) is null);
        }
        finally
        {
            if (BridgeField.GetValue(window) is BridgeClient remaining)
                remaining.DisposeAsync().AsTask().GetAwaiter().GetResult();
            BridgeField.SetValue(window, null);
            typeof(MainWindow).GetField("quitting", Private)!.SetValue(window, true);
            window.Close();
            SynchronizationContext.SetSynchronizationContext(oldContext);
            App.Root = oldRoot; App.StateDirectory = oldState; App.Python = oldPython;
            App.BridgeArguments.Clear(); App.BridgeArguments.AddRange(oldArguments);
            Directory.Delete(directory, recursive: true);
        }
        Console.WriteLine($"PASS_BRIDGE_STARTUP {passed} assertions (synthetic Python workers; no windows/input/game)");
    }

    private static async Task RunAsync(string directory, MainWindow window)
    {
        Check("worker startup has a distinct bounded thirty-second budget",
            BridgeClient.StartupTimeout == TimeSpan.FromSeconds(30));
        var initial = Fixture(directory, "initial-delayed", delay: "11");
        var recovery = Fixture(directory, "recovery-delayed", delay: "11");
        SelectRecovery(recovery);
        var clock = Stopwatch.StartNew();
        await using (var client = new BridgeClient(initial.Root, App.Python, initial.Arguments))
        {
            Task initialHello = client.ConnectAsync();
            Task<JsonElement> replacement = RestoreAsync(window);
            await Task.WhenAll(initialHello, replacement).ConfigureAwait(false);
            Check("initial hello accepts a real worker that starts after eleven seconds",
                client.Hello.Text("transport") == "stdio-jsonl" && clock.Elapsed.TotalSeconds >= 10);
            Check("replacement worker uses the same startup budget before its recovery snapshot",
                replacement.Result.Text("marker") == "synthetic startup");
            var snapshot = await client.RequestAsync("snapshot").ConfigureAwait(false);
            Check("initial startup remains usable for ordinary requests", snapshot.Text("kind") == "snapshot");
        }
        var recovered = (BridgeClient)BridgeField.GetValue(window)!;
        await recovered.DisposeAsync().ConfigureAwait(false);
        BridgeField.SetValue(window, null);
        Console.WriteLine($"BRIDGE_STARTUP_DELAYED initial+recovery accepted after {clock.Elapsed.TotalSeconds:F2}s");

        var hanging = Fixture(directory, "hello-override", "hang_hello");
        await using (var client = new BridgeClient(hanging.Root, App.Python, hanging.Arguments))
        {
            await WaitForFile(hanging.Marker + ".ready").ConfigureAwait(false);
            clock.Restart();
            bool timedOut = false;
            try { await client.ConnectAsync(TimeSpan.FromMilliseconds(200)).ConfigureAwait(false); }
            catch (BridgeTransportException error) when (error.InnerException is TimeoutException) { timedOut = true; }
            Check("explicit hello timeout override still bounds a hanging worker",
                timedOut && clock.Elapsed < TimeSpan.FromSeconds(2) && !client.CanSend);
        }
        Check("timed-out worker exits through owned stdin EOF", File.Exists(hanging.Marker + ".eof"));

        var exited = Fixture(directory, "early-exit", "exit");
        var failed = new BridgeClient(exited.Root, App.Python, exited.Arguments);
        clock.Restart();
        bool failedPromptly = false;
        try { await failed.ConnectAsync().ConfigureAwait(false); }
        catch (BridgeTransportException error)
        {
            failedPromptly = error.InnerException is not TimeoutException && clock.Elapsed < TimeSpan.FromSeconds(3);
        }
        try { await failed.DisposeAsync().ConfigureAwait(false); }
        catch (IOException) { } // The fixture deliberately returns process exit code 7.
        Check("worker exit does not wait for the startup deadline", failedPromptly);

        var normal = Fixture(directory, "ordinary-timeout", "hang_snapshot");
        await using (var client = new BridgeClient(normal.Root, App.Python, normal.Arguments))
        {
            await client.ConnectAsync().ConfigureAwait(false);
            using var guard = new CancellationTokenSource(TimeSpan.FromSeconds(15));
            clock.Restart();
            bool timedOut = false;
            try { await client.RequestAsync("snapshot", cancellationToken: guard.Token).ConfigureAwait(false); }
            catch (BridgeTransportException error) when (error.InnerException is TimeoutException) { timedOut = true; }
            Check("ordinary snapshot still times out at ten seconds instead of the startup budget",
                timedOut && clock.Elapsed >= TimeSpan.FromSeconds(9) && clock.Elapsed < TimeSpan.FromSeconds(14));
            Console.WriteLine($"BRIDGE_ORDINARY_TIMEOUT elapsed={clock.Elapsed.TotalSeconds:F2}s");
        }
        Check("ordinary timeout also closes only its owned worker through EOF", File.Exists(normal.Marker + ".eof"));

        var cancel = Fixture(directory, "recovery-cancel", "hang_hello");
        SelectRecovery(cancel);
        using var stop = new CancellationTokenSource();
        Task<JsonElement> cancelledRecovery = RestoreAsync(window, stop.Token);
        await WaitForFile(cancel.Marker + ".received").ConfigureAwait(false);
        clock.Restart();
        stop.CancelAfter(TimeSpan.FromMilliseconds(150));
        bool cancelled = false;
        try { await cancelledRecovery.ConfigureAwait(false); }
        catch (OperationCanceledException error) { cancelled = error.CancellationToken == stop.Token; }
        var cancelledClient = (BridgeClient)BridgeField.GetValue(window)!;
        Check("cancelling recovery interrupts the startup wait and preserves its cancellation token",
            cancelled && clock.Elapsed < TimeSpan.FromSeconds(2) && !cancelledClient.CanSend);
        bool rejected = false;
        try { await cancelledClient.RequestAsync("snapshot").ConfigureAwait(false); }
        catch (BridgeTransportException) { rejected = true; }
        Check("a cancelled hello cannot reuse a channel that may contain a late reply", rejected);
        await cancelledClient.DisposeAsync().ConfigureAwait(false);
        BridgeField.SetValue(window, null);
        Check("cancelled recovery disposes its owned worker through normal EOF",
            File.Exists(cancel.Marker + ".eof") &&
            File.ReadAllText(cancelledClient.LogPath).Contains("event=exited code=0", StringComparison.Ordinal));
        Console.WriteLine($"BRIDGE_RECOVERY_CANCEL cancelled+disposed after {clock.Elapsed.TotalSeconds:F2}s");
    }
}
