using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using ArkDPS.Desktop;

internal static class DesktopLogTests
{
    internal static void Run()
    {
        string directory = Directory.CreateTempSubdirectory("arkdps-log-tests-").FullName;
        try
        {
            File.WriteAllText(Path.Combine(directory, "layout.json"),
                """{"event_observation":false,"debug_mode":true,"theme":"people"}""");
            string connection = Path.Combine(directory, "connection.json");
            File.WriteAllText(connection,
                """{"platform":"native_pc","native_game_root":"C:/private-profile/game","serial":"127.0.0.1:5555"}""");
            string hash = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(connection)));
            File.WriteAllText(Path.Combine(directory, "log-environment.json"), JsonSerializer.Serialize(new
            {
                connection_sha256 = hash,
                environment = new { game_client_version = "3.8.01", emulator_version = "test", raw = "private-raw" }
            }));
            const string logName = "wpf-update.log";
            string path = Path.Combine(directory, logName);
            File.WriteAllText(path, "legacy contents");
            DesktopLog.Append(directory, logName, "event=failed");
            DesktopLog.Append(directory, logName, "event=recovered");
            string[] lines = File.ReadAllLines(path);
            Check(lines.Length == 3 && lines[1] == "event=failed" && lines[2] == "event=recovered",
                "the active log has one header followed by both entries");
            Check(File.ReadAllText(Directory.GetFiles(directory, "*.legacy-*").Single()) == "legacy contents",
                "previous log contents are preserved");
            using var document = JsonDocument.Parse(lines[0]);
            var header = document.RootElement.Get("data");
            Check(document.RootElement.Text("kind") == "log_header" && header.Bool("debug_mode") &&
                  header.Get("settings").Text("platform") == "native_pc" &&
                  !header.Get("settings").Bool("event_observation"), "saved settings and debug mode are reflected");
            Check(header.Get("environment").Text("game_client_version") == "3.8.01" &&
                  header.Get("environment").Text("provenance") == "last_observed", "matching observation is labeled");
            Check(!lines[0].Contains("private", StringComparison.Ordinal) &&
                  !lines[0].Contains("127.0.0.1:5555", StringComparison.Ordinal),
                "paths, endpoint and unrecognized metadata are excluded");
            File.WriteAllText(connection, """{"platform":"mumu"}""");
            using var changed = JsonDocument.Parse(DesktopLog.Header(directory, "worker"));
            Check(changed.RootElement.Get("data").Get("environment").Text("provenance") == "unobserved",
                "changing connection invalidates the prior observation");
            const string snapshot = "[bridge] utc op=snapshot id=1 phase=write event=sent";
            Check(!DesktopLog.KeepWorkerLine(snapshot, false) && DesktopLog.KeepWorkerLine(snapshot, true),
                "fast snapshot logs are gated by the debug mode");
            Check(DesktopLog.KeepWorkerLine(snapshot.Replace("sent", "slow"), false) &&
                  DesktopLog.KeepWorkerLine(snapshot.Replace("snapshot", "hello"), false) &&
                  DesktopLog.KeepWorkerLine("[environment] {}", false),
                "slow, handshake and observation records survive default filtering");
            Console.WriteLine("PASS_DESKTOP_LOG_CONTEXT 8 assertions (headless)");
        }
        finally { Directory.Delete(directory, recursive: true); }
    }

    private static void Check(bool result, string label)
    {
        if (!result) throw new InvalidOperationException(label);
    }
}
