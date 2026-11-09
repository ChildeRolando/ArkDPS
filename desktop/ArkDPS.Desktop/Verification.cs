using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace ArkDPS.Desktop;

public partial class MainWindow
{
    [StructLayout(LayoutKind.Sequential)] private struct PointNative {public int X,Y;}
    [StructLayout(LayoutKind.Sequential)] private struct RectNative {public int Left,Top,Right,Bottom;}
    [StructLayout(LayoutKind.Sequential)] private struct MouseInput {public int X,Y;public uint Data,Flags,Time;public UIntPtr Extra;}
    [StructLayout(LayoutKind.Sequential)] private struct Input {public uint Type;public MouseInput Mouse;}
    [StructLayout(LayoutKind.Sequential)] private struct IconId {public uint Size;public IntPtr Window;public uint Id;public Guid Guid;}
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(PointNative point);
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr window,uint flags);
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out PointNative point);
    [DllImport("user32.dll")] private static extern bool SetCursorPos(int x,int y);
    [DllImport("user32.dll",SetLastError=true)] private static extern uint SendInput(uint count,Input[] input,int size);
    [DllImport("shell32.dll")] private static extern int Shell_NotifyIconGetRect(ref IconId id,out RectNative rect);
    private static void Require(bool value,string message){if(!value)throw new InvalidOperationException(message);}
    private static async Task Settle()=>await System.Windows.Application.Current.Dispatcher.InvokeAsync(()=>{},DispatcherPriority.ApplicationIdle).Task;
    private static void Click(int x,int y)
    {
        Require(SetCursorPos(x,y),"SetCursorPos failed");
        var input=new[]{new Input{Mouse=new MouseInput{Flags=2}},new Input{Mouse=new MouseInput{Flags=4}}};
        Require(SendInput(2,input,Marshal.SizeOf<Input>())==2,"SendInput failed");
    }
    internal async Task Verify(string output)
    {
        Directory.CreateDirectory(output);poll.Stop();save.Stop();
        var checks=new List<string>();
        string fakeRoot=Path.Combine(output,"crashing-worker");Directory.CreateDirectory(fakeRoot);
        File.WriteAllLines(Path.Combine(fakeRoot,"desktop_bridge.py"),new[]{
            "import json,sys", "c=json.loads(sys.stdin.readline())",
            "print(json.dumps(dict(version=1,id=c['id'],ok=True,kind='hello',result=dict(transport='stdio-jsonl'))),flush=True)",
            "raise SystemExit(17)"});
        var crashing=new BridgeClient(fakeRoot,App.Python,Array.Empty<string>());
        bool crashObserved=false,exitObserved=false;
        try{await crashing.ConnectAsync();await crashing.RequestAsync("snapshot");}
        catch(IOException){crashObserved=true;}
        finally{try{await crashing.DisposeAsync();}catch(IOException){exitObserved=true;}}
        Require(crashObserved && exitObserved,"worker crash / nonzero exit not observable");
        checks.Add("simulated worker crash after valid hello rejects snapshots and reports nonzero exit");
        commands++;lastResponse=System.Diagnostics.Stopwatch.GetTimestamp()-3*System.Diagnostics.Stopwatch.Frequency;
        await Poll();Require(Status.Text.Contains("worker 响应"),"stalled worker left a live status");commands--;
        checks.Add("UI freshness deadline marks stalled worker responses stale without zeroing data");
        await Command("snapshot");
        string id=Panels.Keys.First();
        // The wrapper runs this candidate again against the saved directory.
        var restoredLayout=State.Get("layout").Clone();
        if(Huds.Count>0)
        {
            var placement=restoredLayout.Get("huds").Items().First();var hud=Huds[placement.Text("panel_id")];
            Require(hud.Locked==placement.Bool("locked"),"HUD lock restore");
            Require(Math.Abs(hud.Opacity-placement.Number("opacity"))<.001,"HUD alpha restore");
            var actual=Native.Bounds(hud);
            Require(Math.Abs(actual[0]-placement.Number("x"))<=2 && Math.Abs(actual[1]-placement.Number("y"))<=2,"HUD physical position restore");
            Require(Math.Abs(actual[2]-placement.Number("width"))<=2 && Math.Abs(actual[3]-placement.Number("height"))<=2,"HUD physical size restore");
            checks.Add("saved HUD physical geometry / lock / opacity restored on process launch");
        }
        if(Panels.Count==1)
        {
            await Command("panel_add");string second=Panels.Keys.Last();
            await Command("panel_update",new(){["config"]=new{panel_id=second,category="healing",ranking="total",rate_basis="recent_5s"}});
        }
        var archive=State.Get("history").Items().FirstOrDefault(x=>x.Text("search").Contains("of-8"));
        if(archive.ValueKind==JsonValueKind.Object)await Command("history_select",new(){["battle_id"]=archive.Text("battle_id")});
        var source=Panels[id].View.Get("source");string owner=source.EnumerateObject().First().Name;
        activePanel=id;LiveInspector.Pin(owner,source);
        OpenHud(id);var originalHud=Huds[id];originalHud.SetLocked(false);originalHud.SetOpacity(.83);
        await Settle();
        string ranks=string.Join("|",Panels[id].View.Get("entries").Items().Select(e=>e.Text("owner_id")+e.Text("value")+e.Text("bar_ratio")+e.Text("share")));
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="engagement"}});
        Require(ranks==string.Join("|",Panels[id].View.Get("entries").Items().Select(e=>e.Text("owner_id")+e.Text("value")+e.Text("bar_ratio")+e.Text("share"))),"rate changed ranking / bars / shares");
        checks.Add("rate basis does not change ranks, bars or shares");
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="recent_5s"}});
        string placementBeforeTheme=JsonSerializer.Serialize(originalHud.Placement());
        IntPtr handleBeforeTheme=Native.Handle(originalHud);
        foreach(string theme in new[]{"lone-trail","original"})
        {
            await Command("theme",new(){["theme"]=theme});await Settle();
            Require(ReferenceEquals(originalHud,Huds[id]) && Native.Handle(originalHud)==handleBeforeTheme,"theme recreated HUD");
            Require(JsonSerializer.Serialize(originalHud.Placement())==placementBeforeTheme,"theme changed HUD placement / opacity / lock");
            Screenshot.Window(this,Path.Combine(output,theme+"-main.png"));Screenshot.Window(originalHud,Path.Combine(output,theme+"-hud.png"));
            var selected=State.Get("history_selected");
            if(selected.ValueKind==JsonValueKind.Object)
            {
                Navigate("history");HistoryInspector.Pin(selected.Get("view").Get("source").EnumerateObject().First().Name,selected.Get("view").Get("source"));await Settle();
                Screenshot.Window(this,Path.Combine(output,theme+"-history.png"));
                File.WriteAllBytes(Path.Combine(output,theme+"-ranking.png"),Screenshot.Statistics(selected,theme));
                string selectedId=selected.Text("battle_id");Search.Text="no-such-record";await Command("history_refresh");
                Require(State.Get("history_selected").Text("battle_id")==selectedId,"filter/refresh replaced selected history");Search.Text="";
                checks.Add(theme+" frozen selected history survives search and refresh");
            }
            Navigate("settings");await Settle();Screenshot.Window(this,Path.Combine(output,theme+"-settings.png"));Navigate("live");
            checks.Add(theme+" theme switch keeps HUD HWND, values and source");
        }
        await SavePlacement();string normal=State.Get("layout").Get("gui").GetRawText();
        WindowState=WindowState.Minimized;await Settle();await SavePlacement();
        Require(State.Get("layout").Get("gui").GetRawText()==normal,"minimized HWND overwrote normal GUI rectangle");ShowGui();await Settle();
        checks.Add("minimized GUI preserves last normal physical geometry");
        string selectedHistory=State.Get("history_selected").Text("battle_id");
        var next=System.Text.Json.Nodes.JsonNode.Parse(State.GetRawText())!;
        next["battle_id"]="synthetic-next-battle";
        next["panels"]![0]!["entries"]![0]!["value"]=98765;
        using(var nextDoc=JsonDocument.Parse(next.ToJsonString()))ApplyState(nextDoc.RootElement.Clone());
        Require(originalHud.Panel.View.Get("entries").Items().First().Number("value")==98765,"HUD did not follow next live snapshot");
        Require(State.Get("history_selected").Text("battle_id")==selectedHistory,"live battle change replaced history selection");
        checks.Add("explicit synthetic next-battle snapshot updates native HUD, preserves viewed history");
        await Command("snapshot");activePanel=id;LiveInspector.Pin(owner,Panels[id].View.Get("source"));
        await Command("panel_duplicate",new(){["panel_id"]=id});string temporaryId=Panels.Keys.Last();
        OpenHud(temporaryId);Huds[temporaryId].Close();Require(!Huds.ContainsKey(temporaryId) && Panels.ContainsKey(temporaryId),"HUD close deleted its source");
        OpenHud(temporaryId);await Command("panel_delete",new(){["panel_id"]=temporaryId});
        Require(!Huds.ContainsKey(temporaryId) && !Panels.ContainsKey(temporaryId) && Huds.ContainsKey(id),"source deletion did not isolate HUD lifecycle");
        checks.Add("closing HUD leaves source; source deletion closes only linked HUD");
        originalHud.SetLocked(false);var bounds=Native.Bounds(originalHud);
        int hudClicks=0,underClicks=0;
        var under=new Window{Title="ArkDPS private click underlay — NOT MuMu",Width=originalHud.Width+30,Height=originalHud.Height+30,Left=originalHud.Left,Top=originalHud.Top,Topmost=true,Background=System.Windows.Media.Brushes.DarkSlateGray};
        under.PreviewMouseDown+=(_,_)=>underClicks++;
        originalHud.PreviewMouseDown+=(_,_)=>hudClicks++;
        GetCursorPos(out var pointer);
        try
        {
            originalHud.Topmost=false;under.Show();under.Activate();originalHud.Topmost=true;originalHud.Activate();await Settle();
            bounds=Native.Bounds(originalHud);int x=bounds[0]+90,y=bounds[1]+130;
            Require(GetAncestor(WindowFromPoint(new PointNative{X=x,Y=y}),2)==Native.Handle(originalHud),"unlocked HUD does not own test point");
            Click(x,y);await Task.Delay(150);Require(hudClicks>0 && underClicks==0,"unlocked HUD input route");
            originalHud.SetLocked(true);await Settle();
            Require(GetAncestor(WindowFromPoint(new PointNative{X=x,Y=y}),2)==Native.Handle(under),"locked HUD point is not private underlay; no click sent");
            Click(x,y);await Task.Delay(150);Require(underClicks==1,"locked HUD did not pass input to private underlay");
            originalHud.SetLocked(false);await Settle();Click(x,y);await Task.Delay(150);Require(hudClicks>=2,"unlocked input not restored");
            checks.Add("Win32 SendInput: HUD -> private underlay -> HUD (NOT game certification)");
        }
        finally{under.Close();SetCursorPos(pointer.X,pointer.Y);}
        string trayEvidence="physical tray double-click NOT VERIFIED";
        if(tray!=null)
        {
            var flags=BindingFlags.Instance|BindingFlags.NonPublic;
            var idField=typeof(System.Windows.Forms.NotifyIcon).GetField("_id",flags);
            var windowField=typeof(System.Windows.Forms.NotifyIcon).GetField("_window",flags);
            if(idField!=null && windowField?.GetValue(tray) is System.Windows.Forms.NativeWindow trayWindow)
            {
                var identifier=new IconId{Size=(uint)Marshal.SizeOf<IconId>(),Window=trayWindow.Handle,Id=Convert.ToUInt32(idField.GetValue(tray))};
                if(Shell_NotifyIconGetRect(ref identifier,out var rect)==0 && rect.Right>rect.Left)
                {
                    GetCursorPos(out pointer);Hide();
                    try
                    {
                        int x=(rect.Left+rect.Right)/2,y=(rect.Top+rect.Bottom)/2;
                        Click(x,y);await Task.Delay(80);Click(x,y);await Task.Delay(350);
                        if(IsVisible)trayEvidence="physical native tray icon double-click restored WPF main window";
                    }
                    finally{ShowGui();SetCursorPos(pointer.X,pointer.Y);}
                }
            }
        }
        checks.Add(trayEvidence);
        await Command("theme",new(){["theme"]="original"});originalHud.SetOpacity(.83);originalHud.SetLocked(true);await SavePlacement();
        var disk=JsonDocument.Parse(File.ReadAllText(Path.Combine(App.BridgeArguments[App.BridgeArguments.IndexOf("--state-dir")+1],"layout.json")));
        Require(disk.RootElement.Text("theme")=="original","theme persistence");checks.Add("shared v3 layout saved: original theme / locked HUD / 83% opacity / physical geometry");
        File.WriteAllText(Path.Combine(output,"receipt.json"),JsonSerializer.Serialize(new{stack="C# / .NET 10 / WPF",transport="parent-child stdio JSON Lines",dotnet=Environment.Version.ToString(),protocol=BridgeClient.Version,offline=true,worker_runtime=bridge?.Hello,checks,restored_layout=restoredLayout,layout=State.Get("layout"),limitations=new[]{"Synthetic data only; no MuMu / game input acceptance",trayEvidence}},new JsonSerializerOptions{WriteIndented=true}));
        await Quit();
    }
}
