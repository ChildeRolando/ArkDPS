using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;

namespace ArkDPS.Desktop;

public partial class App : System.Windows.Application
{
    internal static string Root = "";
    internal static string Python = "python.exe";
    internal static string? Verification;
    internal static readonly List<string> BridgeArguments = new();
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        try
        {
            for (int i = 0; i < e.Args.Length; i++)
            {
                string arg = e.Args[i];
                switch (arg)
                {
                    case "--repo": Root = e.Args[++i]; break;
                    case "--python": Python = e.Args[++i]; break;
                    case "--verify": Verification = e.Args[++i]; break;
                    case "--offline": case "--no-events": BridgeArguments.Add(arg); break;
                    case "--fixture": case "--state-dir": case "--adb": case "--serial": case "--frida-remote":
                        BridgeArguments.Add(arg); BridgeArguments.Add(e.Args[++i]); break;
                    default: throw new ArgumentException("未知参数：" + arg);
                }
            }
            string packagedRoot=Path.Combine(AppContext.BaseDirectory,"collector");
            if(Root.Length==0 && File.Exists(Path.Combine(packagedRoot,"desktop_bridge.py"))) Root=packagedRoot;
            string packagedPython=Path.Combine(AppContext.BaseDirectory,"python","python.exe");
            if(Python=="python.exe" && File.Exists(packagedPython)) Python=packagedPython;
            if (Root.Length == 0)
            {
                var directory = new DirectoryInfo(AppContext.BaseDirectory);
                while (directory != null && !File.Exists(Path.Combine(directory.FullName, "desktop_bridge.py"))) directory = directory.Parent;
                Root = directory?.FullName ?? throw new IOException("找不到采集器；请使用 start.cmd 启动。");
            }
            if (BridgeArguments.Contains("--offline") && !BridgeArguments.Contains("--state-dir"))
                throw new ArgumentException("离线验收必须指定独立 --state-dir，不能覆盖产品设置。");
            if (Verification != null && !BridgeArguments.Contains("--offline"))
                throw new ArgumentException("--verify 必须使用 --offline");
            string release=Path.Combine(AppContext.BaseDirectory,"release.json");
            if(File.Exists(release))
            {
                using var manifest=System.Text.Json.JsonDocument.Parse(File.ReadAllText(release));
                string worker=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(Path.Combine(Root,"desktop_bridge.py")))).ToLowerInvariant();
                if(manifest.RootElement.Number("protocol")!=BridgeClient.Version || manifest.RootElement.Text("collector_sha256")!=worker)
                    throw new IOException("安装包两端不匹配；请完整替换同一发布包，不混用旧采集器。");
            }
            var window = new MainWindow(); MainWindow = window; window.Show();
        }
        catch (Exception ex)
        {
            if(Verification!=null){Directory.CreateDirectory(Verification);File.WriteAllText(Path.Combine(Verification,"failure.txt"),ex.ToString());}
            else MessageBox.Show(ex.Message, "ArkDPS 启动失败");
            Shutdown(1);
        }
    }
}

internal static class Theme
{
    public static string Key { get; private set; } = "lone-trail";
    public static void Apply(string key)
    {
        Key = key;
        var colors = key == "original" ? new Dictionary<string, string>
        {
            ["Bg"]="#0B1120", ["Card"]="#111827", ["Inset"]="#1E293B", ["Ink"]="#F8FAFC",
            ["Muted"]="#CBD5E1", ["Line"]="#334155", ["Accent"]="#1D4ED8", ["AccentInk"]="#FFFFFF", ["AccentText"]="#93C5FD", ["Focus"]="#F59E0B",
            ["Console"]="#0F172A", ["ConsoleCard"]="#162033", ["ConsoleInk"]="#F8FAFC",
            ["ConsoleMuted"]="#CBD5E1", ["ConsoleLine"]="#334155", ["Signal"]="#F59E0B"
        } : new Dictionary<string, string>
        {
            ["Bg"]="#F4F1EA", ["Card"]="#FFFFFF", ["Inset"]="#ECE7DC", ["Ink"]="#1B252B",
            ["Muted"]="#5C6A72", ["Line"]="#D9D2C4", ["Accent"]="#C2410C", ["AccentInk"]="#FFFFFF", ["AccentText"]="#B23A0B", ["Focus"]="#C2410C",
            ["Console"]="#101C24", ["ConsoleCard"]="#1B2B35", ["ConsoleInk"]="#F6F1E7",
            ["ConsoleMuted"]="#AFC4CB", ["ConsoleLine"]="#38525E", ["Signal"]="#F2A65A"
        };
        foreach (var pair in colors) System.Windows.Application.Current.Resources[pair.Key] = Brush(pair.Value);
    }
    public static SolidColorBrush Brush(string hex)
    {
        var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex)); brush.Freeze(); return brush;
    }
    public static Brush Resource(string name) => (Brush)System.Windows.Application.Current.Resources[name];
}
