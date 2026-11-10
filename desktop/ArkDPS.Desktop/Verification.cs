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
    // The collection owner writes validated BattleRecord JSON atomically. The
    // harness writes the same shape so the real reader validates real records.
    private static string ArchiveJson(string stateDirectory,string battleId,string stage)
    {
        string source=Directory.GetFiles(Path.Combine(stateDirectory,"history"),"*.json")
            .OrderBy(x=>x,StringComparer.Ordinal).First();
        var root=System.Text.Json.Nodes.JsonNode.Parse(File.ReadAllText(source))!;
        root["battle_id"]=battleId;root["statistics"]!["session"]=battleId;
        root["statistics"]!["adapter_session"]=battleId;root["last_sample"]!["session"]=battleId;
        root["metadata"]!["stage_id"]=stage;
        string observed=DateTime.UtcNow.AddSeconds(5).ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff")+"+00:00";
        root["observed_at"]=observed;root["recorded_at"]=observed;
        return root.ToJsonString();
    }
    private static string HistoryPath(string stateDirectory,string battleId)=>
        Path.Combine(stateDirectory,"history",Convert.ToHexString(
            System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(battleId)))
            .ToLowerInvariant()+".json");
    private static void WriteArchive(string stateDirectory,string battleId,string stage)=>
        File.WriteAllText(HistoryPath(stateDirectory,battleId),
            ArchiveJson(stateDirectory,battleId,stage),new System.Text.UTF8Encoding(false));
    private static async Task Settle()=>await System.Windows.Application.Current.Dispatcher.InvokeAsync(()=>{},DispatcherPriority.ApplicationIdle).Task;
    private async Task RunRateNoticeChecks(string id)
    {
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="engagement"}});
        var panel=Panels[id];var view=panel.View;
        Require(view.Text("rate_reason").Length>0,"engagement without events has no reason");
        Require(view.Text("rate_action").Length>0,"engagement without events has no recovery action");
        Require(view.Text("rate_action_label").Length>0,"engagement recovery action has no label");
        Require(panel.RateNotice.Visibility==Visibility.Visible,"engagement notice is hidden");
        Require(panel.Rate.IsEnabled,"engagement notice disabled switching back to five seconds");
        foreach(var entry in view.Get("entries").Items())
        {
            if(entry.Text("rate_state")=="value") continue;
            Require(entry.Text("rate").Length==0,"unavailable engagement exposed a numeric rate");
            Require(!entry.Text("rate_text").StartsWith("0"),"unavailable engagement rendered as zero");
        }
    }
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
        string partialRoot=Path.Combine(output,"partial-worker");Directory.CreateDirectory(partialRoot);
        File.WriteAllLines(Path.Combine(partialRoot,"desktop_bridge.py"),new[]{
            "import json,sys",
            "hello=json.loads(sys.stdin.readline())",
            "print(json.dumps(dict(version=1,id=hello['id'],ok=True,kind='hello',result=dict(transport='stdio-jsonl'))),flush=True)",
            "request=json.loads(sys.stdin.readline())",
            "sys.stdout.write('{\\\"version\\\":1,')",
            "sys.stdout.flush()",
            "sys.stdin.readline()"});
        var partial=new BridgeClient(partialRoot,App.Python,Array.Empty<string>());
        bool timedOut=false,followupRejected=false;
        try
        {
            await partial.ConnectAsync();
            try{await partial.RequestAsync("snapshot",timeoutOverride:TimeSpan.FromSeconds(1));}catch(OperationCanceledException){timedOut=true;}
            try{await partial.RequestAsync("snapshot");}
            catch(IOException ex){followupRejected=ex.Message.Contains("通道已失效");}
        }
        finally{await partial.DisposeAsync();}
        Require(timedOut&&followupRejected,"partial response timeout allowed a later command or failed to reject it");
        checks.Add("partial JSONL timeout marks transport unusable, rejects follow-up, and closes via stdin EOF");
        string errorRoot=Path.Combine(output,"error-reply-worker");Directory.CreateDirectory(errorRoot);
        File.WriteAllLines(Path.Combine(errorRoot,"desktop_bridge.py"),new[]{
            "import json,sys",
            "def send(c,ok=True,kind='state',result=None): print(json.dumps(dict(version=1,id=c['id'],ok=ok,kind=kind,result=result,error='expected' if not ok else None)),flush=True)",
            "hello=json.loads(sys.stdin.readline()); send(hello,kind='hello',result=dict(transport='stdio-jsonl'))",
            "first=json.loads(sys.stdin.readline()); send(first,ok=False,kind='error')",
            "second=json.loads(sys.stdin.readline()); send(second,kind='snapshot')",
            "quit=json.loads(sys.stdin.readline()); send(quit,kind='bye')"});
        var errorReply=new BridgeClient(errorRoot,App.Python,Array.Empty<string>());
        bool validErrorPreserved=false,recoveredAfterError=false;
        try
        {
            await errorReply.ConnectAsync();
            try{await errorReply.RequestAsync("snapshot");}catch(BridgeCommandException){validErrorPreserved=true;}
            await errorReply.RequestAsync("snapshot");recoveredAfterError=true;
        }
        finally{await errorReply.DisposeAsync();}
        Require(validErrorPreserved&&recoveredAfterError,"valid worker error reply poisoned the JSONL transport");
        checks.Add("valid worker error response remains non-fatal to the synchronized transport");
        string malformedRoot=Path.Combine(output,"malformed-worker");Directory.CreateDirectory(malformedRoot);
        File.WriteAllLines(Path.Combine(malformedRoot,"desktop_bridge.py"),new[]{
            "import json,sys",
            "hello=json.loads(sys.stdin.readline())",
            "print(json.dumps(dict(version=1,id=hello['id'],ok=True,kind='hello',result=dict(transport='stdio-jsonl'))),flush=True)",
            "request=json.loads(sys.stdin.readline())",
            "print(json.dumps(dict(version=1,id=request['id'],ok='false',kind='error',error='malformed')),flush=True)",
            "line=sys.stdin.readline()",
            "open('after-request.txt','w').write(line if line else 'EOF')"});
        var malformed=new BridgeClient(malformedRoot,App.Python,Array.Empty<string>());
        bool malformedRejected=false,malformedFollowupRejected=false;
        try
        {
            await malformed.ConnectAsync();
            try{await malformed.RequestAsync("snapshot");}catch(IOException){malformedRejected=true;}
            try{await malformed.RequestAsync("snapshot");}
            catch(IOException ex){malformedFollowupRejected=ex.Message.Contains("通道已失效");}
        }
        finally{await malformed.DisposeAsync();}
        Require(malformedRejected&&malformedFollowupRejected&&
                File.ReadAllText(Path.Combine(malformedRoot,"after-request.txt"))=="EOF",
                "malformed ok response was not rejected / stdin EOF was not used");
        checks.Add("malformed ok envelope poisons transport and shutdown closes stdin without quit");
        string badHelloRoot=Path.Combine(output,"bad-transport-worker");Directory.CreateDirectory(badHelloRoot);
        File.WriteAllLines(Path.Combine(badHelloRoot,"desktop_bridge.py"),new[]{
            "import json,sys",
            "hello=json.loads(sys.stdin.readline())",
            "print(json.dumps(dict(version=1,id=hello['id'],ok=True,kind='hello',result=dict(transport='unexpected'))),flush=True)",
            "line=sys.stdin.readline()",
            "open('after-hello.txt','w').write(line if line else 'EOF')",
            "if line: sys.stdout.write(json.dumps(dict(version=1,id=json.loads(line)['id'],ok=True,kind='bye'))+'\\n'); sys.stdout.flush()"});
        var badHello=new BridgeClient(badHelloRoot,App.Python,Array.Empty<string>());
        bool badTransportRejected=false;
        try{await badHello.ConnectAsync();}catch(IOException){badTransportRejected=true;}
        finally{await badHello.DisposeAsync();}
        Require(badTransportRejected&&File.ReadAllText(Path.Combine(badHelloRoot,"after-hello.txt"))=="EOF",
                "invalid hello transport did not fail closed before disposal");
        checks.Add("invalid hello transport poisons channel before disposal");
        foreach(string numericField in new[]{"version","id"})
        {
            string numericRoot=Path.Combine(output,"numeric-"+numericField);Directory.CreateDirectory(numericRoot);
            File.WriteAllLines(Path.Combine(numericRoot,"desktop_bridge.py"),new[]{
                "import json,sys",
                "hello=json.loads(sys.stdin.readline())",
                "reply=dict(version=1,id=hello['id'],ok=True,kind='hello',result=dict(transport='stdio-jsonl'))",
                $"reply['{numericField}']=1.0",
                "print(json.dumps(reply),flush=True)",
                "line=sys.stdin.readline()",
                "open('after-hello.txt','w').write(line if line else 'EOF')",
                "if line: sys.stdout.write(json.dumps(dict(version=1,id=json.loads(line)['id'],ok=True,kind='bye'))+'\\n'); sys.stdout.flush()"});
            var numeric=new BridgeClient(numericRoot,App.Python,Array.Empty<string>());
            bool numericRejected=false;
            try{await numeric.ConnectAsync();}catch(IOException){numericRejected=true;}
            finally{await numeric.DisposeAsync();}
            Require(numericRejected&&File.ReadAllText(Path.Combine(numericRoot,"after-hello.txt"))=="EOF",
                    "floating JSON "+numericField+" was accepted or quit was sent after rejection");
        }
        checks.Add("floating response version and id values are rejected fail-closed");
        // A worker that stops answering quit and never exits must not hold the UI
        // open: disposal has to give up in bounded time, end its own child handle
        // and keep the diagnostic.
        string stubbornRoot=Path.Combine(output,"stubborn-worker");Directory.CreateDirectory(stubbornRoot);
        File.WriteAllLines(Path.Combine(stubbornRoot,"desktop_bridge.py"),new[]{
            "import json,sys,time",
            "hello=json.loads(sys.stdin.readline())",
            "print(json.dumps(dict(version=1,id=hello['id'],ok=True,kind='hello',result=dict(transport='stdio-jsonl'))),flush=True)",
            "time.sleep(120)"});
        var stubborn=new BridgeClient(stubbornRoot,App.Python,Array.Empty<string>());
        bool stubbornReported=false;long stubbornStart=Environment.TickCount64;
        try{await stubborn.ConnectAsync();await stubborn.DisposeAsync();}
        catch(IOException){stubbornReported=true;}
        long stubbornElapsed=Environment.TickCount64-stubbornStart;
        Require(stubbornReported,"a worker that ignored quit was not reported as a cleanup failure");
        Require(stubbornElapsed<=(BridgeClient.QuitSeconds+BridgeClient.ExitSeconds+5)*1000L,
                "worker that ignored quit blocked exit for "+stubbornElapsed+"ms");
        Require(File.ReadAllText(stubborn.LogPath).Contains("[exit]"),"bounded worker exit left no diagnostic");
        checks.Add("worker that ignores quit is ended by handle inside the bounded exit window and logged");
        // The worker can be gone while a grandchild still holds the inherited stderr
        // pipe; waiting for that pipe EOF used to keep the window alive forever.
        string inheritedRoot=Path.Combine(output,"inherited-stderr-worker");Directory.CreateDirectory(inheritedRoot);
        File.WriteAllLines(Path.Combine(inheritedRoot,"desktop_bridge.py"),new[]{
            "import json,subprocess,sys",
            "hello=json.loads(sys.stdin.readline())",
            "print(json.dumps(dict(version=1,id=hello['id'],ok=True,kind='hello',result=dict(transport='stdio-jsonl'))),flush=True)",
            "quit=json.loads(sys.stdin.readline())",
            "print(json.dumps(dict(version=1,id=quit['id'],ok=True,kind='bye')),flush=True)",
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(6)'],close_fds=False)",
            "raise SystemExit(0)"});
        var inherited=new BridgeClient(inheritedRoot,App.Python,Array.Empty<string>());
        long inheritedStart=Environment.TickCount64;
        try{await inherited.ConnectAsync();await inherited.DisposeAsync();}catch(IOException){}// pipe EOF is irrelevant to exit
        long inheritedElapsed=Environment.TickCount64-inheritedStart;
        Require(inheritedElapsed<3000,"inherited stderr pipe delayed exit by "+inheritedElapsed+"ms");
        checks.Add("exit follows the worker process, not EOF on a pipe held by a surviving grandchild");
        commands++;lastResponse=System.Diagnostics.Stopwatch.GetTimestamp()-3*System.Diagnostics.Stopwatch.Frequency;
        await Poll();Require(Status.Text.Contains("worker 响应"),"stalled worker left a live status");commands--;
        checks.Add("UI freshness deadline marks stalled worker responses stale without zeroing data");
        await Command("snapshot");
        string id=Panels.Keys.First();
        // History is a pull cache owned by Python. Entering the page must re-read it,
        // and an archive committed while the page stays open must arrive through the
        // normal snapshot stream. Both use a real BattleRecord on disk, not a stub.
        string stateDirectory=App.BridgeArguments[App.BridgeArguments.IndexOf("--state-dir")+1];
        string fixturePath=App.BridgeArguments[App.BridgeArguments.IndexOf("--fixture")+1];
        string phase=Path.GetFileName(output.TrimEnd(Path.DirectorySeparatorChar));
        string entryBattle="ark-23-entry-"+phase;
        WriteArchive(stateDirectory,entryBattle,"ARK23-ENTRY · 合成验收");
        await Navigate("history");
        Require(State.Get("history").Items().Any(x=>x.Text("battle_id")==entryBattle),
                "entering history did not re-read an archive written while running");
        Require(HistoryList.Items.Cast<HistoryItem>().Any(x=>x.Id==entryBattle),
                "new archive missing from the rendered history list after page entry");
        Require(HistoryPage.Visibility==Visibility.Visible,"history page entry did not show the page");
        checks.Add("history page entry re-reads archives without pressing 刷新");
        string archiveBattle="ark-23-archive-"+phase;
        string recordPath=Path.Combine(output,"archive-record.json");
        File.WriteAllText(recordPath,ArchiveJson(stateDirectory,archiveBattle,"ARK23-ARCHIVE · 合成验收"),new System.Text.UTF8Encoding(false));
        string triggerPath=Path.Combine(output,"archive-trigger");
        if(File.Exists(triggerPath))File.Delete(triggerPath);
        string workerRoot=Path.Combine(output,"archive-worker");Directory.CreateDirectory(workerRoot);
        File.WriteAllLines(Path.Combine(workerRoot,"desktop_bridge.py"),new[]{
            "import json,queue,sys,threading,time",
            "from pathlib import Path",
            "args=sys.argv[1:]",
            "def opt(name):return args[args.index(name)+1]",
            "sys.path.insert(0,opt('--repo'))",
            "from desktop_bridge import DesktopState,serve",
            "from battle_history import BattleHistory,BattleRecord",
            "state=DesktopState(Path(opt('--state-dir')))",
            "state.accept('sample',json.loads(Path(opt('--fixture')).read_text('utf-8')))",
            "class Worker:",
            "    def __init__(self):self.events=queue.Queue()",
            "    def close(self):pass",
            "    def request(self,*a):pass",
            "worker=Worker()",
            "def watch():",
            "    trigger=Path(opt('--trigger'))",
            "    while not trigger.exists():time.sleep(.05)",
            "    history=BattleHistory(Path(opt('--state-dir'))/'history')",
            "    history.save(BattleRecord.from_dict(json.loads(Path(opt('--record')).read_text('utf-8'))))",
            "    worker.events.put(('history',None))",
            "threading.Thread(target=watch,daemon=True).start()",
            "serve(sys.stdin,sys.stdout,state,worker)"},new System.Text.UTF8Encoding(false));
        var archiveWorker=new BridgeClient(workerRoot,App.Python,new[]{"--state-dir",stateDirectory,"--fixture",fixturePath,"--repo",App.Root,"--record",recordPath,"--trigger",triggerPath});
        var liveBridge=bridge;
        try
        {
            await archiveWorker.ConnectAsync();
            bridge=archiveWorker;await Poll();
            Require(State.Get("history").Items().All(x=>x.Text("battle_id")!=archiveBattle),"archive visible before the worker committed it");
            Search.Text="合成验收";await Settle();
            string heldSelection=State.Get("history_selected").Text("battle_id");
            Require(heldSelection.Length>0,"history list had no selected record to preserve");
            string heldSummary=HistorySummary.Text;
            File.WriteAllText(triggerPath,"archive",new System.Text.UTF8Encoding(false));
            long deadline=Environment.TickCount64+8000;
            while(State.Get("history").Items().All(x=>x.Text("battle_id")!=archiveBattle) && Environment.TickCount64<deadline)
            {await Poll();await Task.Delay(100);}
            Require(State.Get("history").Items().Any(x=>x.Text("battle_id")==archiveBattle),
                    "archived battle never reached the open history page");
            Require(HistoryList.Items.Cast<HistoryItem>().Any(x=>x.Id==archiveBattle),"archived battle missing from the rendered list");
            Require(HistoryList.SelectedItem is HistoryItem held && held.Id==heldSelection,"automatic refresh dropped the list selection");
            Require(State.Get("history_selected").Text("battle_id")==heldSelection,"automatic refresh replaced the selected detail");
            Require(HistorySummary.Text==heldSummary,"automatic refresh replaced the detail summary");
            Require(Search.Text=="合成验收","automatic refresh discarded the search filter");
            checks.Add("archived battle reaches an open history page without 刷新, keeping selection and filter");
        }
        finally
        {
            bridge=liveBridge;
            try{await archiveWorker.DisposeAsync();}catch(IOException){}
            await Poll();
            Search.Text="";await Settle();
        }
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
        if(archive.ValueKind==JsonValueKind.Object)
        {
            // 历史记录不得事后补零；必须注明该局事件未覆盖及恢复入口。
            await Command("history_config",new(){["config"]=new{category="damage",ranking="total",rate_basis="engagement"}});
            Require(HistoryPanel.View.Text("rate_reason").Contains("该局"),"history record without events has no reason");
            Require(HistoryPanel.View.Text("rate_action").Length>0,"history record has no recovery entry");
            Require(HistoryPanel.View.Get("entries").Items().All(e=>!e.Text("rate_text").StartsWith("0") || e.Text("rate_state")=="value"),"history backfilled engagement zero");
            checks.Add("history record explains missing event coverage and is not zero-filled");
            await Command("history_config",new(){["config"]=new{category="damage",ranking="total",rate_basis="recent_5s"}});
        }
        var source=Panels[id].View.Get("source");string owner=source.EnumerateObject().First().Name;
        activePanel=id;LiveInspector.Pin(owner,Panels[id].View);
        OpenHud(id);var originalHud=Huds[id];originalHud.SetLocked(false);originalHud.SetOpacity(.83);
        await Settle();
        string ranks=string.Join("|",Panels[id].View.Get("entries").Items().Select(e=>e.Text("owner_id")+e.Text("value")+e.Text("bar_ratio")+e.Text("share")));
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="engagement"}});
        Require(ranks==string.Join("|",Panels[id].View.Get("entries").Items().Select(e=>e.Text("owner_id")+e.Text("value")+e.Text("bar_ratio")+e.Text("share"))),"rate changed ranking / bars / shares");
        checks.Add("rate basis does not change ranks, bars or shares");
        // 事件不可用时，接敌列必须给出原因与恢复入口，不能显示为空白或 0。
        await RunRateNoticeChecks(id);
        checks.Add("engagement without events shows reason + retry/settings entry, no zero substitute");
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="recent_5s"}});
        Require(Panels[id].RateNotice.Visibility==Visibility.Collapsed,"five-second switch did not clear engagement notice");
        checks.Add("switching back to five-second rate clears the unavailable notice");
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
                await Navigate("history");HistoryInspector.Pin(selected.Get("view").Get("source").EnumerateObject().First().Name,selected.Get("view"));await Settle();
                Screenshot.Window(this,Path.Combine(output,theme+"-history.png"));
                File.WriteAllBytes(Path.Combine(output,theme+"-ranking.png"),Screenshot.Statistics(selected,theme));
                string selectedId=selected.Text("battle_id");Search.Text="no-such-record";await Command("history_refresh");
                Require(State.Get("history_selected").Text("battle_id")==selectedId,"filter/refresh replaced selected history");Search.Text="";
                checks.Add(theme+" frozen selected history survives search and refresh");
            }
            await Navigate("settings");await Settle();Screenshot.Window(this,Path.Combine(output,theme+"-settings.png"));await Navigate("live");
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
        await Command("snapshot");activePanel=id;LiveInspector.Pin(owner,Panels[id].View);
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
