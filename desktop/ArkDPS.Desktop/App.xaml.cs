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
    internal static ReleaseVersion? InstalledVersion;
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
            if (Verification is string evidence)
            {
                Directory.CreateDirectory(evidence);
                DispatcherUnhandledException += (sender, failure) =>
                {
                    File.AppendAllText(Path.Combine(evidence, "dispatcher-failure.txt"),
                        failure.Exception + Environment.NewLine);
                    failure.Handled = true;
                    if (MainWindow is MainWindow main) _ = main.Quit(1);
                    else Shutdown(1);
                };
                AppDomain.CurrentDomain.UnhandledException += (_, failure) =>
                    File.WriteAllText(Path.Combine(evidence, "runtime-failure.txt"), failure.ExceptionObject.ToString());
            }
            string release = Path.Combine(AppContext.BaseDirectory, "metadata", "release.json");
            if (File.Exists(release) || string.Equals(Root, packagedRoot, StringComparison.OrdinalIgnoreCase))
            {
                using var manifest=System.Text.Json.JsonDocument.Parse(File.ReadAllText(release));
                string worker=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(Path.Combine(Root,"desktop_bridge.py")))).ToLowerInvariant();
                if (manifest.RootElement.Number("format") != 2 ||
                    manifest.RootElement.Number("protocol") != BridgeClient.Version ||
                    manifest.RootElement.Text("collector_sha256") != worker ||
                    !ReleaseVersion.TryParse(manifest.RootElement.Text("version"), out var version))
                    throw new IOException("安装包两端不匹配；请完整替换同一发布包，不混用旧采集器。");
                InstalledVersion = version;
            }
            var window = new MainWindow(); MainWindow = window; window.Show();
            if (InstalledVersion is ReleaseVersion installed)
                window.InitializeReleaseUpdates(installed, BridgeArguments.Contains("--offline"));
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
            ["Bg"]="#0E141B",
            ["Card"]="#151F29",
            ["Inset"]="#1C2935",
            ["Ink"]="#F1F5F9",
            ["Muted"]="#AFBECF",
            ["Line"]="#516476",
            ["Edge"]="#708497",
            ["Accent"]="#FF982F",
            ["AccentInk"]="#201509",
            ["AccentText"]="#FFAD5A",
            ["Focus"]="#FFBE7D",
            ["FocusOnDark"]="#FFBE7D",
            ["Good"]="#78DCAE",
            ["Warn"]="#FFD275",
            ["Danger"]="#FF9597",
            ["Physical"]="#E5484D",
            ["Arts"]="#3D8BFD",
            ["Elemental"]="#9B5CF6",
            ["True"]="#FFFFFF",
            ["Heal"]="#7CD9A8",
            ["Shield"]="#A1CBEC",
            ["Away"]="#202D38",
            ["Track"]="#293B4A",
            ["Console"]="#0F1B25",
            ["ConsoleCard"]="#182833",
            ["ConsoleInk"]="#F1F5F9",
            ["ConsoleMuted"]="#B8CCDB",
            ["ConsoleLine"]="#566E82",
            ["Signal"]="#FF9B44",
            ["Sidebar"]="#0D1721",
            ["SidebarCard"]="#172636",
            ["SidebarInk"]="#F1F5F9",
            ["SidebarMuted"]="#B9CBDA",
            ["SidebarLine"]="#495F72",
            ["SidebarActive"]="#223549",
            ["Hero"]="#111C27",
            ["HeroInk"]="#F1F5F9",
            ["HeroMuted"]="#B9CBDA",
            ["HeroLine"]="#647B8F",
            ["HeroCard"]="#192B3B",
            ["HeroStatusInk"]="#FFAD5A",
            ["HeroCardInk"]="#F1F5F9",
            ["HeroCardMuted"]="#B9CBDA",
            ["Radius"]="3",
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
        resources["ControlRadius"] = new CornerRadius(IsBabel ? 0 : Math.Max(2, radius - 1));
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
