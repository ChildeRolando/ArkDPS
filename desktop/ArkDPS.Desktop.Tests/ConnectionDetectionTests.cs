using System;
using System.Threading.Tasks;
using ArkDPS.Desktop;

internal static class ConnectionDetectionTests
{
    private static int passed;

    private static void Check(string name, bool condition)
    {
        if (!condition) throw new InvalidOperationException(name);
        passed++;
    }

    internal static void Run()
    {
        var scans = new ConnectionDetection();
        const string adb = @"C:\MuMu\adb.exe";
        var first = scans.Begin("mumu", " " + adb + " ");
        Check("request captures normalized source inputs", first.Platform == "mumu" && first.AdbPath == adb);
        Check("unchanged draft accepts result", scans.IsCurrent(first, "mumu", adb));
        Check("PC selection rejects MuMu result", !scans.IsCurrent(first, "native_pc", adb));
        Check("changed ADB rejects result", !scans.IsCurrent(first, "mumu", adb + ".other"));
        Check("empty ADB autodetection remains valid", scans.IsCurrent(scans.Begin("mumu", " "), "mumu", ""));

        var pending = scans.Begin("mumu", adb);
        scans.Invalidate(); // Settings changed away and back to the original values.
        Check("returning to original draft does not revive result", !scans.IsCurrent(pending, "mumu", adb));
        var older = scans.Begin("mumu", adb);
        var newer = scans.Begin("mumu", adb);
        Check("new request supersedes identical older request", !scans.IsCurrent(older, "mumu", adb) &&
            scans.IsCurrent(newer, "mumu", adb));
        scans.Invalidate(); // Successful Apply also invalidates scans for the prior connection.
        Check("applied settings reject pending scan", !scans.IsCurrent(newer, "mumu", adb));

        VerifyDelayedReplies().GetAwaiter().GetResult();
        Console.WriteLine($"PASS_CONNECTION_DETECTION {passed} assertions (headless, controlled replies only)");
    }

    private static async Task VerifyDelayedReplies()
    {
        var scans = new ConnectionDetection();
        string platform = "mumu", adb = "", note = "MuMu settings";
        int instances = 0;

        async Task Receive(Task<int> reply, ConnectionDetection.Request request)
        {
            int count = await reply.ConfigureAwait(false);
            if (!scans.IsCurrent(request, platform, adb)) return;
            instances = count;
            note = "scan completed";
        }

        var delayed = new TaskCompletionSource<int>(TaskCreationOptions.RunContinuationsAsynchronously);
        var receive = Receive(delayed.Task, scans.Begin(platform, adb));
        platform = "native_pc";
        scans.Invalidate();
        note = "PC settings";
        delayed.SetResult(3);
        await receive.ConfigureAwait(false);
        Check("late MuMu reply preserves current PC note and list", note == "PC settings" && instances == 0);

        platform = "mumu";
        scans.Invalidate();
        var current = new TaskCompletionSource<int>(TaskCreationOptions.RunContinuationsAsynchronously);
        receive = Receive(current.Task, scans.Begin(platform, adb));
        current.SetResult(2);
        await receive.ConfigureAwait(false);
        Check("new current MuMu reply can populate instances", note == "scan completed" && instances == 2);
    }
}
