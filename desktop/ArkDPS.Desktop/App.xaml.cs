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
    public static readonly string[] Keys={"lone-trail","contingency-contract","babel","near-light","il-siracusano","original"};
    public static string Key { get; private set; } = "lone-trail";
    public static int IndexOf(string key)
    {
        int index=Array.IndexOf(Keys,key);
        return index<0 ? 0 : index;
    }
    private static readonly Dictionary<string,Dictionary<string,string>> Skins=new()
    {
        ["lone-trail"]=new()
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
        ["contingency-contract"]=new()
        {
            ["Bg"]="#111819",
            ["Card"]="#1C2628",
            ["Inset"]="#232F30",
            ["Ink"]="#F0F2E8",
            ["Muted"]="#BDC8C1",
            ["Line"]="#3B4948",
            ["Edge"]="#7C8D88",
            ["Accent"]="#E9B84B",
            ["AccentInk"]="#1B1C17",
            ["AccentText"]="#FFE08A",
            ["Focus"]="#FFE08A",
            ["Good"]="#8BD6B4",
            ["Warn"]="#F1C568",
            ["Danger"]="#FF9A93",
            ["Physical"]="#D64F59",
            ["Arts"]="#0086BF",
            ["Elemental"]="#D34493",
            ["True"]="#F3F0E6",
            ["Heal"]="#76CFA6",
            ["Shield"]="#80C9D4",
            ["Away"]="#303A3A",
            ["Track"]="#354243",
            ["Console"]="#111819",
            ["ConsoleCard"]="#202B2B",
            ["ConsoleInk"]="#F0F2E8",
            ["ConsoleMuted"]="#BDC8C1",
            ["ConsoleLine"]="#7C8D88",
            ["Signal"]="#E9B84B",
            ["Sidebar"]="#111819",
            ["SidebarCard"]="#202B2B",
            ["SidebarInk"]="#F0F2E8",
            ["SidebarMuted"]="#BDC8C1",
            ["SidebarLine"]="#7C8D88",
            ["SidebarActive"]="#2E3A3B",
            ["Hero"]="#111819",
            ["HeroInk"]="#F0F2E8",
            ["HeroMuted"]="#BDC8C1",
            ["HeroLine"]="#7C8D88",
            ["HeroCard"]="#202B2B",
            ["HeroStatusInk"]="#E9B84B",
            ["HeroCardInk"]="#F0F2E8",
            ["HeroCardMuted"]="#BDC8C1",
            ["Radius"]="4",
        },
        ["babel"]=new()
        {
            ["Bg"]="#E7DED4",
            ["Card"]="#F6EEE5",
            ["Inset"]="#EEE3D8",
            ["Ink"]="#342C2A",
            ["Muted"]="#665955",
            ["Line"]="#D5C6BB",
            ["Edge"]="#90766D",
            ["Accent"]="#A43E42",
            ["AccentInk"]="#FFF8EF",
            ["AccentText"]="#8C2E36",
            ["Focus"]="#70252D",
            ["Good"]="#216B48",
            ["Warn"]="#875B17",
            ["Danger"]="#A13234",
            ["Physical"]="#C43B4D",
            ["Arts"]="#136F9E",
            ["Elemental"]="#B13A76",
            ["True"]="#493C38",
            ["Heal"]="#2C7C56",
            ["Shield"]="#286E82",
            ["Away"]="#DDD2C8",
            ["Track"]="#DDD2C9",
            ["Console"]="#342C2A",
            ["ConsoleCard"]="#4A3B37",
            ["ConsoleInk"]="#F6EEE5",
            ["ConsoleMuted"]="#CBBAB0",
            ["ConsoleLine"]="#7A625A",
            ["Signal"]="#A43E42",
            ["Sidebar"]="#342C2A",
            ["SidebarCard"]="#4A3B37",
            ["SidebarInk"]="#F6EEE5",
            ["SidebarMuted"]="#CBBAB0",
            ["SidebarLine"]="#7A625A",
            ["SidebarActive"]="#513B37",
            ["Hero"]="#EBE2D9",
            ["HeroInk"]="#342C2A",
            ["HeroMuted"]="#665955",
            ["HeroLine"]="#D4C6BC",
            ["HeroCard"]="#F6EEE5",
            ["HeroStatusInk"]="#8C2E36",
            ["HeroCardInk"]="#342C2A",
            ["HeroCardMuted"]="#665955",
            ["Radius"]="3",
        },
        ["near-light"]=new()
        {
            ["Bg"]="#0E1928",
            ["Card"]="#13263A",
            ["Inset"]="#1D3449",
            ["Ink"]="#EFF7F6",
            ["Muted"]="#BED0D6",
            ["Line"]="#31495D",
            ["Edge"]="#7D9CAC",
            ["Accent"]="#F6CF68",
            ["AccentInk"]="#182333",
            ["AccentText"]="#F6CF68",
            ["Focus"]="#FFF0B0",
            ["Good"]="#82D8B0",
            ["Warn"]="#F4C46C",
            ["Danger"]="#FF9996",
            ["Physical"]="#D64F59",
            ["Arts"]="#0086BF",
            ["Elemental"]="#D34493",
            ["True"]="#F3F0E6",
            ["Heal"]="#76CFA6",
            ["Shield"]="#80C9D4",
            ["Away"]="#25333F",
            ["Track"]="#2B4357",
            ["Console"]="#0A1724",
            ["ConsoleCard"]="#13263A",
            ["ConsoleInk"]="#EFF7F6",
            ["ConsoleMuted"]="#BED0D6",
            ["ConsoleLine"]="#7D9CAC",
            ["Signal"]="#F6CF68",
            ["Sidebar"]="#0A1724",
            ["SidebarCard"]="#13263A",
            ["SidebarInk"]="#EFF7F6",
            ["SidebarMuted"]="#BED0D6",
            ["SidebarLine"]="#7D9CAC",
            ["SidebarActive"]="#223C52",
            ["Hero"]="#0A1724",
            ["HeroInk"]="#EFF7F6",
            ["HeroMuted"]="#BED0D6",
            ["HeroLine"]="#7D9CAC",
            ["HeroCard"]="#13263A",
            ["HeroStatusInk"]="#F6CF68",
            ["HeroCardInk"]="#EFF7F6",
            ["HeroCardMuted"]="#BED0D6",
            ["Radius"]="5",
        },
        ["il-siracusano"]=new()
        {
            ["Bg"]="#1A1114",
            ["Card"]="#28191C",
            ["Inset"]="#352326",
            ["Ink"]="#F2E9DF",
            ["Muted"]="#CBB7AC",
            ["Line"]="#51373D",
            ["Edge"]="#94746F",
            ["Accent"]="#D9B77C",
            ["AccentInk"]="#211714",
            ["AccentText"]="#ECD09B",
            ["Focus"]="#F5D895",
            ["Good"]="#91BD93",
            ["Warn"]="#E8BF76",
            ["Danger"]="#ED9992",
            ["Physical"]="#D64F59",
            ["Arts"]="#0086BF",
            ["Elemental"]="#D34493",
            ["True"]="#F3F0E6",
            ["Heal"]="#76CFA6",
            ["Shield"]="#80C9D4",
            ["Away"]="#302427",
            ["Track"]="#403037",
            ["Console"]="#171014",
            ["ConsoleCard"]="#28191C",
            ["ConsoleInk"]="#F2E9DF",
            ["ConsoleMuted"]="#CBB7AC",
            ["ConsoleLine"]="#94746F",
            ["Signal"]="#D9B77C",
            ["Sidebar"]="#171014",
            ["SidebarCard"]="#28191C",
            ["SidebarInk"]="#F2E9DF",
            ["SidebarMuted"]="#CBB7AC",
            ["SidebarLine"]="#94746F",
            ["SidebarActive"]="#38262B",
            ["Hero"]="#171014",
            ["HeroInk"]="#F2E9DF",
            ["HeroMuted"]="#CBB7AC",
            ["HeroLine"]="#94746F",
            ["HeroCard"]="#28191C",
            ["HeroStatusInk"]="#ECD09B",
            ["HeroCardInk"]="#F2E9DF",
            ["HeroCardMuted"]="#CBB7AC",
            ["Radius"]="3",
        },
        ["original"]=new()
        {
            ["Bg"]="#F3F5F8",
            ["Card"]="#FFFFFF",
            ["Inset"]="#F6F8FB",
            ["Ink"]="#182238",
            ["Muted"]="#53617A",
            ["Line"]="#DCE2EC",
            ["Edge"]="#818FA7",
            ["Accent"]="#244B9E",
            ["AccentInk"]="#FFFFFF",
            ["AccentText"]="#244B9E",
            ["Focus"]="#244B9E",
            ["Good"]="#196447",
            ["Warn"]="#795117",
            ["Danger"]="#9D3339",
            ["Physical"]="#BC5660",
            ["Arts"]="#537CCE",
            ["Elemental"]="#8261B8",
            ["True"]="#FFFFFF",
            ["Heal"]="#3C8D72",
            ["Shield"]="#658498",
            ["Away"]="#E9EBEF",
            ["Track"]="#E8ECF3",
            ["Console"]="#1C2A44",
            ["ConsoleCard"]="#24365A",
            ["ConsoleInk"]="#FFFFFF",
            ["ConsoleMuted"]="#C2CEE3",
            ["ConsoleLine"]="#66789A",
            ["Signal"]="#244B9E",
            ["Sidebar"]="#1C2A44",
            ["SidebarCard"]="#24365A",
            ["SidebarInk"]="#FFFFFF",
            ["SidebarMuted"]="#C2CEE3",
            ["SidebarLine"]="#66789A",
            ["SidebarActive"]="#2B4066",
            ["Hero"]="#EAF0F7",
            ["HeroInk"]="#182238",
            ["HeroMuted"]="#53617A",
            ["HeroLine"]="#CDD8E8",
            ["HeroCard"]="#FFFFFF",
            ["HeroStatusInk"]="#244B9E",
            ["HeroCardInk"]="#182238",
            ["HeroCardMuted"]="#53617A",
            ["Radius"]="12",
        },
    };
    private static readonly Uri FontBase=new("pack://application:,,,/");
    private static readonly FontFamily LoneTrailInterface=new(FontBase,
        "./Assets/Fonts/#Barlow Semi Condensed, Microsoft YaHei UI, Segoe UI");
    private static readonly FontFamily LoneTrailData=new(FontBase,
        "./Assets/Fonts/#IBM Plex Mono, Microsoft YaHei UI, Consolas");
    private static readonly FontFamily StandardInterface=new("Segoe UI Variable Text, Microsoft YaHei UI");
    private static readonly FontFamily StandardHeading=new("Segoe UI Variable Display, Microsoft YaHei UI");
    private static readonly FontFamily StandardDisplay=new("Bahnschrift, Segoe UI");
    private static readonly FontFamily StandardData=new("Cascadia Mono, Consolas");

    public static void Apply(string key)
    {
        if(!Skins.TryGetValue(key,out var colors)){key="lone-trail";colors=Skins[key];}
        Key=key;
        foreach(var pair in colors)
            if(pair.Key!="Radius") System.Windows.Application.Current.Resources[pair.Key]=Brush(pair.Value);
        var resources=System.Windows.Application.Current.Resources;
        bool loneTrail=key=="lone-trail";
        resources["InterfaceFont"]=loneTrail ? LoneTrailInterface : StandardInterface;
        resources["HeadingFont"]=loneTrail ? LoneTrailInterface : StandardHeading;
        resources["DisplayFont"]=loneTrail ? LoneTrailInterface : StandardDisplay;
        resources["DataFont"]=loneTrail ? LoneTrailData : StandardData;
        System.Windows.Application.Current.Resources["DataHighlight"]=Brush(key=="lone-trail" ? "#61DFFF" : colors["Ink"]);
        int radius=int.Parse(colors["Radius"]);
        System.Windows.Application.Current.Resources["CardRadius"]=new CornerRadius(radius);
        System.Windows.Application.Current.Resources["ControlRadius"]=new CornerRadius(Math.Max(2,radius-1));
        System.Windows.Application.Current.Resources["BgGrid"]=Grid(colors["Bg"],colors["Line"]);
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
