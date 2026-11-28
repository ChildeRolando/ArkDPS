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
    internal static string StateDirectory = "";
    internal static string? Verification;
    internal static ReleaseVersion? InstalledVersion;
    internal static PortableUpdateHost? UpdateHost;
    internal static PortableLaunch? UpdateLaunch;
    internal static UpdateFixture? UpdateVerification;
    internal static bool VerifyInternalCapture;
#if ARKDPS_INTERNAL_EVENT_CANDIDATE
    private static readonly bool InternalEventPackage = true;
#else
    private static readonly bool InternalEventPackage = false;
#endif
#if ARKDPS_COOPERATIVE_EVENT_CANDIDATE
    internal static readonly bool CooperativeEventPackage = true;
#else
    internal static readonly bool CooperativeEventPackage = false;
#endif
#if ARKDPS_NATIVE_HOOK_CANDIDATE
    internal static readonly bool NativeHookCandidate = true;
#else
    internal static readonly bool NativeHookCandidate = false;
#endif
    internal static string[] LaunchArguments = [];
    internal static readonly List<string> BridgeArguments = new();
    internal static void ClearConnectionOverrides()
    {
        for (int index = 0; index < BridgeArguments.Count;)
        {
            string argument = BridgeArguments[index];
            if (argument is "--adb" or "--serial" or "--platform" or "--native-game-root"
                or "--native-pc-event-backend" or "--native-pc-event-feed")
                BridgeArguments.RemoveRange(index, Math.Min(2, BridgeArguments.Count - index));
            else index++;
        }
    }
    internal static void ClearEventQueueOverride()
    {
        for(int i=0;i<BridgeArguments.Count;)
            if(BridgeArguments[i]=="--event-queue-memory-mib") BridgeArguments.RemoveRange(i,Math.Min(2,BridgeArguments.Count-i));
            else i++;
    }
    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        try
        {
            var arguments = e.Args;
            VerifyInternalCapture = arguments.Contains("--verify-internal");
            LaunchArguments = arguments;
            bool internalRequested = arguments.Contains("--internal-capture");
            if (NativeHookCandidate && (arguments.Contains(PortableUpdateHost.ApplyOption)
                || arguments.Contains(PortableUpdateHost.ChildOption)))
                throw new ArgumentException("ArkPie 启动测试候选不支持自动更新、旧版本激活或状态迁移。");
            if ((InternalEventPackage || VerifyInternalCapture) && !internalRequested)
                throw new ArgumentException("内部事件候选需要 --offline --internal-capture 和独立的 --state-dir。");
            var host = InternalEventPackage || internalRequested || CooperativeEventPackage || NativeHookCandidate
                ? null : PortableUpdateHost.Find(AppContext.BaseDirectory);
            if (arguments.Length == 2 && arguments[0] == PortableUpdateHost.ApplyOption)
            {
                if (host == null) throw new IOException("找不到 ArkDPS 便携目录。");
                UpdateHost = host;
                await host.ApplyAsync(arguments[1]);
                Shutdown();
                return;
            }
            if (arguments.Length == 2 && arguments[0] == PortableUpdateHost.ChildOption)
            {
                if (host == null) throw new IOException("找不到 ArkDPS 便携目录。");
                UpdateLaunch = PortableUpdateHost.ReadChild(arguments[1]);
                arguments = UpdateLaunch.Arguments;
            }
            else if (host != null && !arguments.Contains("--repo") && !arguments.Contains("--python") &&
                     host.Redirect(arguments))
            {
                Shutdown();
                return;
            }
            LaunchArguments = arguments;
            string? stateOverride = null;
            string? updateFixture = null;
            for (int i = 0; i < arguments.Length; i++)
            {
                string arg = arguments[i];
                if (NativeHookCandidate && arg is "--repo" or "--python" or "--internal-capture"
                    or "--verify-internal" or "--verify-update" or "--frida-remote" or "--adb" or "--serial" or "--arkframework-root"
                    or "--native-pc-event-feed")
                    throw new ArgumentException("ArkPie 启动测试候选不支持该启动方式：" + arg);
                if (NativeHookCandidate && (arg == "--platform" && arguments.ElementAtOrDefault(i + 1) != "native_pc"
                    || arg == "--native-pc-event-backend" && arguments.ElementAtOrDefault(i + 1) != "native"))
                    throw new ArgumentException("此候选仅支持PC ArkPie 实时采集。");
                if (InternalEventPackage && arg is not ("--offline" or "--internal-capture" or "--state-dir" or "--verify-internal"))
                    throw new ArgumentException("内部事件候选不支持参数：" + arg);
                switch (arg)
                {
                    case "--repo": Root = arguments[++i]; break;
                    case "--python": Python = arguments[++i]; break;
                    case "--verify": Verification = arguments[++i]; break;
                    case "--verify-update": updateFixture = arguments[++i]; break;
                    case "--verify-internal": break;
                    case "--offline": case "--no-events": case "--debug-mode": BridgeArguments.Add(arg); break;
                    case "--state-dir": stateOverride = arguments[++i]; break;
                    case "--internal-capture":
                        BridgeArguments.Add(arg); BridgeArguments.Add(arguments[++i]); break;
                    case "--fixture": case "--adb": case "--serial": case "--frida-remote":
                    case "--platform": case "--native-game-root":
                    case "--native-pc-event-backend": case "--native-pc-event-feed": case "--event-queue-memory-mib":
                        BridgeArguments.Add(arg); BridgeArguments.Add(arguments[++i]); break;
                    case "--arkframework-root":
                        BridgeArguments.Add(arg); BridgeArguments.Add(Path.GetFullPath(arguments[++i])); break;
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
            Root = Path.GetFullPath(Root);
            bool offline = BridgeArguments.Contains("--offline");
            bool internalCapture = BridgeArguments.Contains("--internal-capture");
            if (internalCapture && (!offline || stateOverride == null))
                throw new ArgumentException("内部事件候选需要 --offline 和独立的 --state-dir。");
            if (offline && stateOverride == null)
                throw new ArgumentException("离线验收必须指定独立 --state-dir，不能覆盖产品设置。");
            if (Verification != null && !offline)
                throw new ArgumentException("--verify 必须使用 --offline");
            if (updateFixture != null && (!offline || host == null || Verification != null))
                throw new ArgumentException("--verify-update 必须使用便携包及隔离的离线状态目录。");
            if ((Verification != null || updateFixture != null) && !BridgeArguments.Contains("--debug-mode"))
                BridgeArguments.Add("--debug-mode");
            bool packaged = string.Equals(Root, packagedRoot, StringComparison.OrdinalIgnoreCase);
            string launchRoot = packaged && host != null ? Path.Combine(host.Root, "collector") : Root;
            string configuredState = stateOverride == null
                ? (packaged ? host?.StateDirectory : null) ?? Path.Combine(Root, "runtime")
                : Path.GetFullPath(stateOverride, launchRoot);
            if (internalCapture)
            {
                configuredState = ResolveDirectory(configuredState);
                string statePath = Path.TrimEndingDirectorySeparator(configuredState) + Path.DirectorySeparatorChar;
                string productPath = ResolveDirectory(Path.Combine(Root, "runtime")) + Path.DirectorySeparatorChar;
                if (statePath.StartsWith(productPath, StringComparison.OrdinalIgnoreCase) ||
                    productPath.StartsWith(statePath, StringComparison.OrdinalIgnoreCase))
                    throw new IOException("内部候选的状态目录必须与标准运行目录分开。");
            }
            StateDirectory = configuredState;
            WriteStartupEvent("event=configured");
            if (packaged && Python == "python.exe")
                throw new IOException("便携包缺少自带的 Python 运行时。请重新下载完整发布包。");
            for (int i = 0; i + 1 < BridgeArguments.Count; i++)
            {
                if (BridgeArguments[i] == "--fixture" ||
                    (BridgeArguments[i] == "--internal-capture" && BridgeArguments[i + 1] is not ("normal" or "autochess")) ||
                    (BridgeArguments[i] == "--adb" && Path.GetDirectoryName(BridgeArguments[i + 1]) is { Length: > 0 }))
                    BridgeArguments[i + 1] = Path.GetFullPath(BridgeArguments[i + 1], launchRoot);
            }
            if (stateOverride != null || packaged)
            {
                BridgeArguments.Add("--state-dir");
                BridgeArguments.Add(StateDirectory);
            }
            if (updateFixture != null) UpdateVerification = UpdateFixture.Load(updateFixture, StateDirectory, host!);
            if (Verification is string evidence)
            {
                Directory.CreateDirectory(evidence);
                DispatcherUnhandledException += (sender, failure) =>
                {
                    DesktopLog.Append(evidence, "dispatcher-failure.txt", failure.Exception.ToString(), StateDirectory);
                    failure.Handled = true;
                    if (MainWindow is MainWindow main) _ = main.Quit(1);
                    else Shutdown(1);
                };
                AppDomain.CurrentDomain.UnhandledException += (_, failure) =>
                    DesktopLog.Append(evidence, "runtime-failure.txt", failure.ExceptionObject.ToString() ?? "", StateDirectory);
            }
            string release = Path.Combine(AppContext.BaseDirectory, "metadata", "release.json");
            if (File.Exists(release) || packaged)
            {
                using var manifest=System.Text.Json.JsonDocument.Parse(File.ReadAllText(release));
                string worker=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(Path.Combine(Root,"desktop_bridge.py")))).ToLowerInvariant();
                if (manifest.RootElement.Number("format") != 2 ||
                    manifest.RootElement.Number("protocol") != BridgeClient.Version ||
                    manifest.RootElement.Text("collector_sha256") != worker ||
                    !ReleaseVersion.TryParse(manifest.RootElement.Text("version"), out var version))
                    throw new IOException("安装包两端不匹配；请完整替换同一发布包，不混用旧采集器。");
                if (InternalEventPackage != (manifest.RootElement.Get("internal_event_backend").ValueKind
                                             == System.Text.Json.JsonValueKind.Object))
                    throw new IOException("桌面程序与内部候选清单不匹配。");
                if (CooperativeEventPackage != (manifest.RootElement.Get("native_pc_event_backend").ValueKind
                                                == System.Text.Json.JsonValueKind.Object))
                    throw new IOException("桌面程序与 PC 事件候选清单不匹配。");
                bool nativeHooks = manifest.RootElement.Get("native_pc_native_hooks").ValueKind
                    == System.Text.Json.JsonValueKind.Object;
                if (NativeHookCandidate && !nativeHooks)
                    throw new IOException("桌面程序与 ArkPie 事件组件清单不匹配。");
                if (nativeHooks) VerifyNativeHookPackage(manifest.RootElement);
                InstalledVersion = version;
                if (!NativeHookCandidate && packaged && manifest.RootElement.Number("update_protocol") == 1 &&
                    manifest.RootElement.Text("frida").Length > 0 &&
                    !arguments.Contains("--repo") && !arguments.Contains("--python"))
                    UpdateHost = host;
            }
            else if (NativeHookCandidate)
                throw new IOException("ArkPie 事件组件需要完整便携包。");
            var window = new MainWindow(); MainWindow = window;
            WriteStartupEvent("event=window_created");
            if (UpdateLaunch?.ReadyPipe != null || UpdateVerification != null || VerifyInternalCapture)
            {
                window.IsEnabled = UpdateLaunch?.ReadyPipe == null;
                window.ShowActivated = false;
                if (UpdateLaunch?.Kind == PortableLaunchKind.Probe || UpdateVerification != null || VerifyInternalCapture)
                {
                    window.Opacity = 0;
                    window.ShowInTaskbar = false;
                }
            }
            window.Show();
            if (InstalledVersion is ReleaseVersion installed)
                window.InitializeReleaseUpdates(installed, offline || CooperativeEventPackage || NativeHookCandidate);
        }
        catch (Exception ex)
        {
            WriteStartupEvent($"event=failed error={ex.GetType().Name} hresult=0x{ex.HResult:X8}");
            if (VerifyInternalCapture) WriteInternalCaptureFailure(ex);
            else if (Verification != null) DesktopLog.Append(Verification, "failure.txt", ex.ToString(), StateDirectory);
            else if (UpdateLaunch?.ReadyPipe != null || (e.Args.Length > 0 && e.Args[0] == PortableUpdateHost.ApplyOption))
                UpdateHost?.Log(ex);
            else if (UpdateVerification != null) UpdateVerification.WriteFailure(ex);
            else MessageBox.Show(ex.Message, "ArkDPS 启动失败");
            Shutdown(1);
        }
    }

    internal static bool NativeHookAcceptanceFlagsValid(System.Text.Json.JsonElement profile, bool startupTest)
    {
        var performance = profile.Get("performance_verified").ValueKind;
        var handoff = profile.Get("autochess_handoff_verified").ValueKind;
        if (startupTest)
            return performance == System.Text.Json.JsonValueKind.False && handoff == System.Text.Json.JsonValueKind.False;
        return (performance == System.Text.Json.JsonValueKind.True || performance == System.Text.Json.JsonValueKind.False)
            && (handoff == System.Text.Json.JsonValueKind.True || handoff == System.Text.Json.JsonValueKind.False);
    }

    internal static void VerifyNativeHookPackage(System.Text.Json.JsonElement manifest)
    {
        var profile = manifest.Get("native_pc_native_hooks");
        if (InternalEventPackage || CooperativeEventPackage || profile.Text("provider") != "arkdps-native-hooks"
            || profile.Get("protocol").ValueKind != System.Text.Json.JsonValueKind.Number
            || !profile.Get("protocol").TryGetInt32(out int protocol) || protocol != 1
            || (NativeHookCandidate
                ? profile.Text("scope") != "official-pc-startup-test"
                    || profile.Get("experimental").ValueKind != System.Text.Json.JsonValueKind.True

                : profile.Text("scope") != "official-pc"
                    || profile.Get("experimental").ValueKind != System.Text.Json.JsonValueKind.False
)
            || !NativeHookAcceptanceFlagsValid(profile, NativeHookCandidate)
            || profile.Text("default_platform") != "native_pc" || profile.Text("default_backend") != "native"
            || manifest.Get("files").ValueKind != System.Text.Json.JsonValueKind.Object)
            throw new IOException("ArkPie 事件组件的适用范围或默认来源清单不匹配。");
        string root = Path.GetFullPath(AppContext.BaseDirectory);
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var file in manifest.Get("files").EnumerateObject())
        {
            string name = file.Name;
            string digest = file.Value.ValueKind == System.Text.Json.JsonValueKind.String ? file.Value.GetString()! : "";
            if (!names.Add(name) || name.Length == 0 || name.Any(char.IsControl)
                || name.IndexOfAny(['\\', ':', '*', '?', '"', '<', '>', '|']) >= 0
                || name.StartsWith('/') || name.Split('/').Any(part => part is "" or "." or ".."
                    || part.EndsWith('.') || part.EndsWith(' '))
                || digest.Length != 64 || !digest.All(Uri.IsHexDigit))
                throw new IOException("ArkPie 事件组件的文件清单无效。");
            string path = Path.GetFullPath(Path.Combine(root, name.Replace('/', Path.DirectorySeparatorChar)));
            if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase) || !File.Exists(path))
                throw new IOException("ArkPie 事件组件缺少清单文件：" + name);
            for (FileSystemInfo? entry = new FileInfo(path); entry is not null && entry.FullName != root.TrimEnd(Path.DirectorySeparatorChar);
                 entry = entry is FileInfo info ? info.Directory : ((DirectoryInfo)entry).Parent)
                if ((entry.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("ArkPie 事件组件不接受链接替换的文件：" + name);
            string actual = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
            if (!string.Equals(actual, digest, StringComparison.OrdinalIgnoreCase))
                throw new IOException("ArkPie 事件组件文件摘要不一致：" + name);
        }
        if (!names.Contains("ArkDPS.Desktop.exe") || !names.Contains("collector/desktop_bridge.py")
            || !names.Contains("python/python.exe"))
            throw new IOException("ArkPie 事件组件缺少必要入口文件。");
    }

    private static string ResolveDirectory(string path)
    {
        var directory = new DirectoryInfo(path);
        if (directory.Parent is not { } parent) return directory.FullName;
        DirectoryInfo candidate = new(Path.Combine(ResolveDirectory(parent.FullName), directory.Name));
        return candidate.Exists ? candidate.ResolveLinkTarget(true)?.FullName ?? candidate.FullName : candidate.FullName;
    }

    internal static void WriteInternalCaptureFailure(Exception error)
    {
        Console.Error.WriteLine(error);
        try
        {
            if (StateDirectory.Length > 0)
                DesktopLog.Append(StateDirectory, "wpf-internal-failure.txt", error.ToString());
        }
        catch (Exception writeError) when (writeError is IOException or UnauthorizedAccessException) { }
    }

    private static void WriteStartupEvent(string message)
    {
        try
        {
            string? directory = StateDirectory.Length > 0 ? StateDirectory
                : InternalEventPackage || LaunchArguments.Contains("--internal-capture") ? null
                : PortableUpdateHost.Find(AppContext.BaseDirectory)?.StateDirectory;
            if (directory != null)
                DesktopLog.Append(directory, "wpf-startup.log", $"{DateTimeOffset.UtcNow:O} {message}");
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
    }
}

internal static class Theme
{
    public const string LoneTrail = "lone-trail";
    public const string People = "people";
    public const string Babel = "babel";

    private static readonly string[] ThemeKeys = { LoneTrail, People, Babel };
    public static IReadOnlyList<string> Keys { get; } = Array.AsReadOnly(ThemeKeys);
    public static string Key { get; private set; } = LoneTrail;
    public static bool IsPeople => Key == People;
    public static bool IsBabel => Key == Babel;

    public static string Normalize(string key) => Skins.ContainsKey(key) ? key : LoneTrail;
    public static int IndexOf(string key) => Array.IndexOf(ThemeKeys, Normalize(key));

    private static readonly Dictionary<string,Dictionary<string,string>> Skins=new()
    {
        [LoneTrail]=new()
        {
            ["Bg"]="#0B0F14",
            ["Card"]="#111720",
            ["Inset"]="#171F2B",
            ["Ink"]="#F1F5F9",
            ["Muted"]="#8B9BB0",
            ["Line"]="#17FFFFFF",
            ["Edge"]="#33FFFFFF",
            ["Accent"]="#F97316",
            ["AccentInk"]="#1A0D04",
            ["AccentText"]="#FB923C",
            ["Focus"]="#F97316",
            ["FocusOnDark"]="#FB923C",
            ["Good"]="#78DCAE",
            ["Warn"]="#FFD275",
            ["Danger"]="#FF9597",
            ["Physical"]="#E5484D",
            ["Arts"]="#3D8BFD",
            ["Elemental"]="#9B5CF6",
            ["True"]="#FFFFFF",
            ["Heal"]="#7CD9A8",
            ["Shield"]="#A1CBEC",
            ["Away"]="#141B26",
            ["Track"]="#1E2836",
            ["Console"]="#111720",
            ["ConsoleCard"]="#171F2B",
            ["ConsoleInk"]="#F1F5F9",
            ["ConsoleMuted"]="#B8CCDB",
            ["ConsoleLine"]="#1FFFFFFF",
            ["Signal"]="#2DD4BF",
            ["Sidebar"]="#0E131B",
            ["SidebarCard"]="#171F2B",
            ["SidebarInk"]="#F1F5F9",
            ["SidebarMuted"]="#B9CBDA",
            ["SidebarLine"]="#1FFFFFFF",
            ["SidebarActive"]="#1E2836",
            ["Hero"]="#111C27",
            ["HeroInk"]="#F1F5F9",
            ["HeroMuted"]="#B9CBDA",
            ["HeroLine"]="#647B8F",
            ["HeroCard"]="#192B3B",
            ["HeroStatusInk"]="#FFAD5A",
            ["HeroCardInk"]="#F1F5F9",
            ["HeroCardMuted"]="#B9CBDA",
            ["Radius"]="8",
        },
        [People]=new()
        {
            ["Bg"]="#E2D9CC",
            ["Card"]="#F3EADF",
            ["Inset"]="#EAE0D4",
            ["Ink"]="#262322",
            ["Muted"]="#58504A",
            ["Line"]="#B4A395",
            ["Edge"]="#7D6C60",
            ["Accent"]="#8D312A",
            ["AccentInk"]="#FFF6EB",
            ["AccentText"]="#8D312A",
            ["Focus"]="#8D312A",
            ["FocusOnDark"]="#FFA08B",
            ["Good"]="#435B35",
            ["Warn"]="#785015",
            ["Danger"]="#8E2E29",
            ["Physical"]="#C35A4F",
            ["Arts"]="#D78958",
            ["Elemental"]="#D1A54B",
            ["True"]="#A786B2",
            ["Heal"]="#526D34",
            ["Shield"]="#396785",
            ["Away"]="#DBCEC1",
            ["Track"]="#CEC0B2",
            ["Console"]="#292727",
            ["ConsoleCard"]="#373231",
            ["ConsoleInk"]="#F9F0E4",
            ["ConsoleMuted"]="#D4C7BA",
            ["ConsoleLine"]="#8E8077",
            ["Signal"]="#FFA08B",
            ["Sidebar"]="#292727",
            ["SidebarCard"]="#373231",
            ["SidebarInk"]="#F9F0E4",
            ["SidebarMuted"]="#D4C7BA",
            ["SidebarLine"]="#8E8077",
            ["SidebarActive"]="#79332C",
            ["Hero"]="#EAE0D4",
            ["HeroInk"]="#262322",
            ["HeroMuted"]="#58504A",
            ["HeroLine"]="#B4A395",
            ["HeroCard"]="#F3EADF",
            ["HeroStatusInk"]="#8D312A",
            ["HeroCardInk"]="#262322",
            ["HeroCardMuted"]="#58504A",
            ["Radius"]="2",
        },
        [Babel]=new()
        {
            ["Bg"]="#131214",
            ["Card"]="#1C1A1D",
            ["Inset"]="#252226",
            ["Ink"]="#F4EFEA",
            ["Muted"]="#C3B8B5",
            ["Line"]="#564C54",
            ["Edge"]="#8D7E87",
            ["Accent"]="#D7525E",
            ["AccentInk"]="#170E11",
            ["AccentText"]="#FFADB1",
            ["Focus"]="#FFB3B7",
            ["FocusOnDark"]="#FFB3B7",
            ["Good"]="#B0D2B9",
            ["Warn"]="#E7C189",
            ["Danger"]="#FF9C9F",
            ["Physical"]="#F07A82",
            ["Arts"]="#98ABC8",
            ["Elemental"]="#B9A5CB",
            ["True"]="#E5DEDA",
            ["Heal"]="#9CB7A7",
            ["Shield"]="#8CB4C0",
            ["Away"]="#2A252B",
            ["Track"]="#39323A",
            ["Console"]="#171518",
            ["ConsoleCard"]="#242024",
            ["ConsoleInk"]="#F4EFEA",
            ["ConsoleMuted"]="#C3B8B5",
            ["ConsoleLine"]="#6D626A",
            ["Signal"]="#FF9C9F",
            ["Sidebar"]="#151416",
            ["SidebarCard"]="#211D22",
            ["SidebarInk"]="#F4EFEA",
            ["SidebarMuted"]="#C3B8B5",
            ["SidebarLine"]="#6D626A",
            ["SidebarActive"]="#38262D",
            ["Hero"]="#131214",
            ["HeroInk"]="#F4EFEA",
            ["HeroMuted"]="#C3B8B5",
            ["HeroLine"]="#564C54",
            ["HeroCard"]="#1C1A1D",
            ["HeroStatusInk"]="#FFADB1",
            ["HeroCardInk"]="#F4EFEA",
            ["HeroCardMuted"]="#C3B8B5",
            ["Radius"]="0",
        },
    };
    private static readonly Uri FontBase=new("pack://application:,,,/");
    private static readonly FontFamily LoneTrailInterface=new(FontBase,
        "./Assets/Fonts/#Barlow Semi Condensed, Microsoft YaHei UI, Segoe UI");
    private static readonly FontFamily DataFamily=new(FontBase,
        "./Assets/Fonts/#IBM Plex Mono, Microsoft YaHei UI, Consolas");
    private static readonly FontFamily StandardInterface=new("Segoe UI Variable Text, Microsoft YaHei UI");
    private static readonly FontFamily PeopleDisplay=new("Georgia, Microsoft YaHei UI");
    private static readonly Brush LoneTrailRankSelection=RankGradient("#885020", "#473119", "#29251E");
    private static readonly Brush PeopleRankSelection=RankGradient("#78312B", "#592D29", "#3C2927");
    private static readonly Brush BabelRankSelection=Brush("#39272D");
    private static readonly Brush PeopleRankLeader=Brush("#E8C486");

    public static void Apply(string key)
    {
        Key = Normalize(key);
        var colors = Skins[Key];
        var resources = System.Windows.Application.Current.Resources;
        foreach (var pair in colors)
            if (pair.Key != "Radius") resources[pair.Key] = Brush(pair.Value);

        resources["Brush.Surface.Popup"] = IsPeople ? resources["Card"] : resources["Inset"];
        resources["Brush.Surface.Hover"] = Key == LoneTrail ? Brush("#1E2836") : resources["Card"];
        resources["Brush.Border.Active"] = resources["Accent"];
        resources["ControlSelection"] = resources[IsPeople ? "Inset" : "Track"];
        var danger = (SolidColorBrush)resources["Danger"];
        resources["Brush.Status.DangerSoft"] = Brush(Color.FromArgb(0x24, danger.Color.R, danger.Color.G, danger.Color.B).ToString());
        resources["Brush.Status.DangerBorder"] = Brush(Color.FromArgb(0x66, danger.Color.R, danger.Color.G, danger.Color.B).ToString());

        resources["InterfaceFont"] = Key == LoneTrail ? LoneTrailInterface : StandardInterface;
        resources["HeadingFont"] = IsPeople ? PeopleDisplay : LoneTrailInterface;
        resources["DisplayFont"] = Key switch
        {
            People => PeopleDisplay,
            Babel => DataFamily,
            _ => LoneTrailInterface,
        };
        resources["DataFont"] = DataFamily;
        resources["DataHighlight"] = Brush(Key == LoneTrail ? "#61DFFF" : colors["Signal"]);
        resources["ContributionTrack"] = resources["Track"];
        resources["ReferenceRankSelection"] = Key switch
        {
            People => PeopleRankSelection,
            Babel => BabelRankSelection,
            _ => LoneTrailRankSelection,
        };
        resources["ReferenceRankSelectionEdge"] = Brush(Key switch
        {
            People => "#ED8875",
            Babel => colors["Signal"],
            _ => colors["Accent"],
        });
        resources["ReferenceRankLeader"] = IsPeople ? PeopleRankLeader : Brushes.Transparent;
        int radius = int.Parse(colors["Radius"]);
        resources["CardRadius"] = new CornerRadius(radius);
        resources["ControlRadius"] = new CornerRadius(Key == LoneTrail ? 6 : IsBabel ? 0 : 2);
        resources["BgGrid"] = Grid(colors["Bg"], colors["Line"]);
    }

    private static LinearGradientBrush RankGradient(string start, string middle, string end)
    {
        var brush = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(1, 0) };
        brush.GradientStops.Add(new GradientStop(Brush(start).Color, 0));
        brush.GradientStops.Add(new GradientStop(Brush(middle).Color, .56));
        brush.GradientStops.Add(new GradientStop(Brush(end).Color, 1));
        brush.Freeze();
        return brush;
    }

    private static DrawingBrush Grid(string background,string line)
    {
        var group=new DrawingGroup();
        group.Children.Add(new GeometryDrawing(Brush(background),null,new RectangleGeometry(new Rect(0,0,48,48))));
        var pen=new Pen(Brush(WithAlpha(line,0.10)),1);pen.Freeze();
        group.Children.Add(new GeometryDrawing(null,pen,new LineGeometry(new Point(47.5,0),new Point(47.5,48))));
        var brush=new DrawingBrush(group){TileMode=TileMode.Tile,Viewport=new Rect(0,0,48,48),ViewportUnits=BrushMappingMode.Absolute,Stretch=Stretch.None};
        brush.Freeze();return brush;
    }
    private static string WithAlpha(string hex,double alpha)
    {
        var color=(Color)ColorConverter.ConvertFromString(hex);
        return $"#{((byte)Math.Round(alpha*255)):X2}{color.R:X2}{color.G:X2}{color.B:X2}";
    }
    public static SolidColorBrush Brush(string hex)
    {
        var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex)); brush.Freeze(); return brush;
    }
    internal static Typeface Typeface(string key,FontWeight weight) =>
        new((FontFamily)System.Windows.Application.Current.Resources[key],FontStyles.Normal,weight,FontStretches.Normal);

    public static Brush Resource(string name) => (Brush)System.Windows.Application.Current.Resources[name];
}
