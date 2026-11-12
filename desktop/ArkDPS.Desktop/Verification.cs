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
    private void ApplyFixture(System.Text.Json.Nodes.JsonNode state)
    {
        using var document = JsonDocument.Parse(state.ToJsonString());
        ApplyState(document.RootElement.Clone());
    }
    private void VerifyEnemyPaging(System.Text.Json.Nodes.JsonNode fixture)
    {
        static Button? FindArrow(DependencyObject parent, string id)
        {
            if (parent is Button button && System.Windows.Automation.AutomationProperties.GetAutomationId(button)==id)
                return button;
            foreach (var child in LogicalTreeHelper.GetChildren(parent).OfType<DependencyObject>())
                if (FindArrow(child,id) is { } match) return match;
            return null;
        }

        var previous=FindArrow(TargetSummary,"PreviousLiveEnemy")
            ?? throw new InvalidOperationException("previous enemy arrow not exposed");
        var next=FindArrow(TargetSummary,"NextLiveEnemy")
            ?? throw new InvalidOperationException("next enemy arrow not exposed");
        Require(System.Windows.Automation.AutomationProperties.GetName(previous)=="上一名敌人" &&
                System.Windows.Automation.AutomationProperties.GetName(next)=="下一名敌人",
                "enemy paging arrows lack accessible names");

        var state=fixture.DeepClone();
        state["battle_id"]="synthetic-enemy-paging";
        var rows=System.Text.Json.Nodes.JsonNode.Parse("""
            [
              {"instance_uid":"101","eid":"enemy_a","name":"首位敌人","lifecycle":"active","alive":true,"hp":900,"max_hp":1000},
              {"instance_uid":"102","eid":"enemy_a","name":"次位敌人","lifecycle":"active","alive":true,"hp":400,"max_hp":5000},
              {"instance_uid":"103","eid":"enemy_b","name":"血量未知敌人","lifecycle":"active","alive":true,"hp":null},
              {"instance_uid":"104","eid":"enemy_c","name":"已离场敌人","lifecycle":"departed","alive":false,"hp":9000},
              {"instance_uid":"105","eid":"enemy_d","name":"推断敌人","lifecycle":"active","alive":true,"hp":10000,"entry_inferred":true}
            ]
            """)!.AsArray();
        state["enemy"]!["rows"]=rows;

        void Publish() => TargetSummary.Update(JsonSerializer.SerializeToElement(state), "—", "—", "—", "");
        static void ClickArrow(Button arrow) =>
            arrow.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent));

        try
        {
            Publish();
            Require(HasText(TargetSummary,"首位敌人") && previous.Visibility==Visibility.Visible &&
                    next.Visibility==Visibility.Visible && !previous.IsEnabled && next.IsEnabled,
                    "first enemy is not the confirmed live instance with highest current HP");
            ClickArrow(next);
            Require(HasText(TargetSummary,"次位敌人") && previous.IsEnabled && next.IsEnabled,
                    "right arrow did not select the next live UID");
            ClickArrow(next);
            Require(HasText(TargetSummary,"血量未知敌人") && previous.IsEnabled && !next.IsEnabled,
                    "unknown HP did not sort last or next arrow remained enabled at the end");
            ClickArrow(previous);
            rows[1]!["hp"]=1100;
            Publish();
            Require(HasText(TargetSummary,"次位敌人") && !previous.IsEnabled && next.IsEnabled,
                    "HP reorder changed the selected UID or left stale arrow bounds");
            rows[1]!["alive"]=false;
            rows[1]!["lifecycle"]="departed";
            Publish();
            Require(HasText(TargetSummary,"首位敌人"), "departed selection did not fall back to a live enemy");
            rows[2]!["alive"]=false;
            rows[2]!["lifecycle"]="departed";
            Publish();
            Require(previous.Visibility==Visibility.Collapsed && next.Visibility==Visibility.Collapsed &&
                    !previous.IsEnabled && !next.IsEnabled, "single live enemy still has paging controls");
            rows[0]!["alive"]=false;
            rows[0]!["lifecycle"]="departed";
            Publish();
            Require(HasText(TargetSummary,"等待敌方数据"), "enemy card retained a departed enemy with no live targets");
        }
        finally
        {
            UpdateEnemySummary();
        }
    }
    private async Task VerifyLoneTrailLayout(string output, List<string> checks)
    {
        await Command("theme", new() { ["theme"] = "lone-trail" });
        await Navigate("live");
        var initial = State.Clone();
        var fixture = System.Text.Json.Nodes.JsonNode.Parse(initial.GetRawText())!;
        var analysis = fixture["live_analysis"] ?? new System.Text.Json.Nodes.JsonObject
        {
            ["segment"] = 0,
            ["team_total_damage"] = 711630,
            ["operators"] = System.Text.Json.Nodes.JsonNode.Parse("""
                [{"owner_id":"char_002_amiya","name":"阿米娅","recent_dps":1090},
                 {"owner_id":"char_017_huang","name":"煌","recent_dps":1010},
                 {"owner_id":"char_003_kalts","name":"凯尔希","recent_dps":950}]
                """),
        };
        if (analysis.Parent == null) fixture["live_analysis"] = analysis;
        const string layoutBattle = "synthetic-lone-trail-layout";
        fixture["battle_id"] = layoutBattle;
        analysis["battle_id"] = layoutBattle;
        analysis["team_average_dps"] = 59302.5;
        analysis["observed_peak_dps"] = 8450;
        var owners = new[]
        {
            ("char_002_amiya", "阿米娅", 39), ("char_017_huang", "煌", 22),
            ("char_003_kalts", "凯尔希", 16), ("char_010_chen", "陈", 10),
            ("char_009_12fce", "12F", 8), ("char_103_angel", "能天使", 5),
        };
        void SetIllustrativeRates(int total)
        {
            analysis["team_recent_dps"] = total;
            analysis["operators"] = JsonSerializer.SerializeToNode(owners.Select(owner => new
            {
                owner_id = owner.Item1, name = owner.Item2, recent_dps = total * (owner.Item3 / 100.0),
            }));
        }
        fixture["status"] = "live";
        fixture["status_text"] = "正在统计 · 离线验收";
        for (int second = 13; second <= 23; second++)
        {
            fixture["game_time"] = second;
            analysis["game_time"] = second;
            SetIllustrativeRates(6800 + (second % 5) * 380);
            if (second == 22) analysis["team_recent_dps"] = null;
            ApplyFixture(fixture);
        }
        Require(trendModel.Points.Count > 1 && trendModel.Points[^1].Team == 7940,
            "advancing worker values did not reach live trend");
        Require(CurrentDps.Text == "7,940" && AverageDps.Text == "59,303", "overview did not round DPS");
        Require(trendModel.Points.Any(point => point.Time == 22 && !point.Team.HasValue),
            "an unavailable sample was converted to a measured zero");
        var gapDrawing = new System.Windows.Media.DrawingGroup();
        using (var drawing = gapDrawing.Open())
            ChartLine.DrawGap(drawing, new System.Windows.Media.Pen(System.Windows.Media.Brushes.White, 1),
                new Point(4, 5), new Point(18, 8), 30);
        Require(gapDrawing.Children.OfType<System.Windows.Media.GeometryDrawing>().Any(item =>
            item.Geometry is System.Windows.Media.LineGeometry line &&
            line.StartPoint == new Point(4,30) && line.EndPoint == new Point(18,30)),
            "a sampling gap did not connect the trace to the baseline");
        VerifyEnemyPaging(fixture);
        fixture["status"] = "paused";
        analysis["game_time"] = 24;
        ApplyFixture(fixture);
        Require(trendModel.Points[^1].Time == 23, "pause advanced the chart");
        fixture["status"] = "disconnected";
        ApplyFixture(fixture);
        fixture["status"] = "live";
        analysis["game_time"] = 25;
        SetIllustrativeRates(0);
        ApplyFixture(fixture);
        Require(trendModel.Points[^1].StartsSegment && trendModel.Points[^1].Team == 0,
            "recovery lost the gap or the reliable zero");
        fixture["game_time"] = 59.5;
        analysis["team_total_damage"] = 1234567.5;
        ApplyFixture(fixture);
        Require(Clock.Text == "01:00" && TotalDamage.Text == "1,234,568",
            "overview time or damage did not round to a whole number");
        analysis["team_total_damage"] = 711630;

        await Navigate("enemies");
        EnemyList.SelectedIndex = 0;
        var selected = (EnemyItem)EnemyList.SelectedItem;
        var firstEnemy = fixture["enemy"]!["rows"]![0]!;
        firstEnemy["hp"] = 2700;
        firstEnemy["max_hp_lower_bound"] = 2700;
        firstEnemy["hp_ratio"] = null;
        fixture["enemy"]!["totals"]!["total_hp_complete"] = false;
        fixture["enemy"]!["series"]![2]!["total_hp_complete"] = false;
        ApplyFixture(fixture);
        Require(ReferenceEquals(selected, EnemyList.SelectedItem) && selected.HpText.Contains("≥") &&
            !selected.HpPercentKnown, "transform replaced selection or represented an unverified denominator as exact");
        Require(EnemyTotalHp.Text.Contains("≥") && !EnemyChart.Points[^1].TotalComplete,
            "enemy total lower bound lost in display or series");
        firstEnemy["max_hp"] = 2800;
        firstEnemy["hp_ratio"] = .964;
        firstEnemy["max_hp_lower_bound"] = null;
        fixture["enemy"]!["totals"]!["total_hp_complete"] = true;
        fixture["enemy"]!["series"]![2]!["total_hp_complete"] = true;
        ApplyFixture(fixture);
        Require(ReferenceEquals(selected, EnemyList.SelectedItem) && selected.HpPercentKnown &&
            !selected.HpText.Contains("≥"), "native MaxHP confirmation did not update the same enemy");
        Require(HasText(EnemyPage, "关卡预估血量"), "stage estimate is not independently labeled");

        await Navigate("live");
        fixture["game_time"] = 26;
        analysis["game_time"] = 26;
        SetIllustrativeRates(7630);
        ApplyFixture(fixture);
        if (Panels.Values.FirstOrDefault() is StatisticPanel ranked)
        {
            ranked.Rows.SelectedIndex = 0;
            ranked.PinRequested?.Invoke(ranked.View.Get("entries").Items().First().Text("owner_id"));
        }
        double savedWidth = Width, savedHeight = Height;
        foreach (var (width, height) in new[] { (1920, 1080), (1672, 941), (1380, 900), (1200, 820), (560, 540) })
        {
            Width = width; Height = height;
            await Settle();
            Require(Math.Abs(ActualWidth - width) < 2 && Math.Abs(ActualHeight - height) < 2,
                "test window did not reach requested DIP size");
            Require(Grid.GetColumnSpan(EnemySummary) == (width >= 1320 ? 1 : 3) &&
                Grid.GetColumn(LiveDetail) == (width < 1100 ? 1 : 2) &&
                (LiveNavLabel.Visibility == Visibility.Collapsed) == (width < 1100),
                "live dashboard breakpoint did not reflow the expected surfaces");
            if (width >= 1320)
            {
                Require(LivePage.ScrollableHeight < 1 &&
                    Math.Abs(LiveBody.TranslatePoint(new Point(0,LiveBody.ActualHeight),LivePage).Y - LivePage.ActualHeight) < 6,
                    "dashboard body did not fill the available window height");
                var panel = Panels.Values.First();
                Require(panel.ReferenceFooter.IsVisible && panel.ReferenceFooterTotal.Text==panel.View.Text("footer_total_display") &&
                    panel.ReferenceFooterRate.Text==panel.View.Text("footer_rate_display") &&
                    Grid.GetColumn(panel.ReferenceFooterTotal)==3 && Grid.GetColumn(panel.ReferenceFooterRate)==4,
                    "team total or DPS did not render in its ranking column");
            }
            if (width >= 1200) VerifyCompactPortraitLayout();
            Screenshot.Window(this, Path.Combine(output, $"lone-trail-live-{width}x{height}-dip.png"));
            if (width == 1380)
            {
                Screenshot.Window(this, Path.Combine(output, "lone-trail-live-render-125-percent.png"), 120);
                Screenshot.Window(this, Path.Combine(output, "lone-trail-live-render-150-percent.png"), 144);
            }
        }
        await Navigate("enemies");
        await Settle();
        Require(EnemyPageScroll.ScrollableHeight > 0 && EnemyChartHost.ActualHeight >= 300,
            "minimum window does not scroll to the full enemy chart");
        Screenshot.Window(this, Path.Combine(output, "lone-trail-enemies-minimum.png"));
        await Navigate("live");
        Width = savedWidth; Height = savedHeight;
        await Settle();
        WindowState = WindowState.Maximized;
        await Settle();
        if (ActualWidth >= 1320 && ActualHeight >= 640)
            Require(LivePage.ScrollableHeight < 1 &&
                Math.Abs(LiveBody.TranslatePoint(new Point(0,LiveBody.ActualHeight),LivePage).Y - LivePage.ActualHeight) < 6,
                "maximized dashboard left unused space below the panels");
        WindowState = WindowState.Normal;
        Width = 560; Height = 540;
        await Navigate("history");
        await Settle();
        Require(HistoryList.ActualHeight >= 58, $"minimum history viewport too short ({HistoryList.ActualHeight:F1} DIPs)");
        Screenshot.Window(this, Path.Combine(output, "lone-trail-history-minimum.png"));
        await Navigate("live");
        Width = savedWidth; Height = savedHeight;
        await Settle();
        ApplyState(initial);
        await Command("theme", new() { ["theme"] = "original" });
        checks.Add("portrait beside three rounded metrics, HP-ordered enemy paging, aligned team footer, pause/recovery/known zero and five Lone Trail DIP layouts");
    }
    private void VerifyCompactPortraitLayout()
    {
        var avatar = FindAutomationElement(LiveInspector,"CompactOperatorPortrait")
            ?? throw new InvalidOperationException("compact detail portrait missing");
        var rows = Enumerable.Range(0,4).Select(index =>
            FindAutomationElement(LiveInspector,"CompactOperatorMetric"+index)
            ?? throw new InvalidOperationException("compact detail metric missing")).ToArray();
        var avatarPosition=avatar.TranslatePoint(new Point(),LiveInspector);
        var positions=rows.Select(row=>row.TranslatePoint(new Point(),LiveInspector)).ToArray();
        Require(avatar.ActualWidth==36 && avatar.ActualHeight==36 && HasText(LiveInspector,"1,167") &&
            positions.Take(3).All(point=>point.X>=avatarPosition.X+avatar.ActualWidth) &&
            avatarPosition.Y>=positions[0].Y-1 &&
            avatarPosition.Y+avatar.ActualHeight<=positions[2].Y+rows[2].ActualHeight+1 &&
            positions[3].X<positions[0].X,
            "portrait did not sit left of the three metrics or the next row failed to regain its width");
    }
    private static FrameworkElement? FindAutomationElement(DependencyObject root, string id)
    {
        if(root is FrameworkElement element && System.Windows.Automation.AutomationProperties.GetAutomationId(element)==id)
            return element;
        for(int i=0;i<System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);i++)
            if(FindAutomationElement(System.Windows.Media.VisualTreeHelper.GetChild(root,i),id) is { } found) return found;
        return null;
    }
    private static async Task Settle()=>await System.Windows.Application.Current.Dispatcher.InvokeAsync(()=>{},DispatcherPriority.ApplicationIdle).Task;
    private static bool HasText(DependencyObject root,string text)
    {
        if(root is TextBlock block && block.Text.Contains(text,StringComparison.Ordinal)) return true;
        for(int i=0;i<System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);i++)
            if(HasText(System.Windows.Media.VisualTreeHelper.GetChild(root,i),text)) return true;
        return false;
    }
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
    private void VerifyShareScope(string id,HudWindow hud)
    {
        const string knownLabel="已知数据内占比";
        const string teamLabel="全队同口径占比";
        const string unknownLabel="占比未知";
        const string reason="部分干员累计快照不完整";
        const string note="已排除非队伍来源：场景装置";
        var original=State.Clone();
        var modified=System.Text.Json.Nodes.JsonNode.Parse(original.GetRawText())!.AsObject();
        var live=modified["panels"]!.AsArray().First(panel=>
            panel!["config"]!["panel_id"]!.GetValue<string>()==id)!.AsObject();
        var history=modified["history_selected"]!["view"]!.AsObject();

        void Apply(string? scope,bool teamKnown,string shareText)
        {
            foreach(var view in new[]{live,history})
            {
                if(scope==null)view.Remove("share_scope");else view["share_scope"]=scope;
                view["share_known"]=teamKnown;
                view["share_reason"]=teamKnown ? "" : reason;
                view["share_note"]=note;
                foreach(var entry in view["entries"]!.AsArray())entry!["share_display"]=shareText;
            }
            ApplyState(JsonSerializer.SerializeToElement(modified));
        }

        static string CoverageExplanation(StatisticPanel panel) =>
            panel.Coverage.ToolTip is string tooltip ? tooltip : panel.Coverage.Text;

        try
        {
            Apply("known",false,"42.5%");
            var livePanel=Panels[id];
            LiveInspector.Pin(livePanel.View.Get("entries").Items().First().Text("owner_id"),livePanel.View);
            HistoryInspector.Pin(HistoryPanel.View.Get("entries").Items().First().Text("owner_id"),HistoryPanel.View);
            foreach(var panel in new[]{livePanel,HistoryPanel})
            {
                var explanation=CoverageExplanation(panel);
                Require(panel.Coverage.Visibility==Visibility.Visible && panel.Coverage.Text.Contains(knownLabel) &&
                        explanation.Contains(reason) && explanation.Contains(note),
                        "ranking lost visible known share scope or coverage details");
            }
            Require(hud.Panel.PanelKind.Visibility==Visibility.Visible && hud.Panel.PanelKind.Text==knownLabel &&
                    Equals(hud.Panel.PanelKind.ToolTip,knownLabel+" · "+reason+" · "+note),
                    "HUD did not identify the partial share denominator");
            var row=(RowItem)livePanel.Rows.Items[0];
            Require(row.Share=="42.5%" && row.Tooltip.Contains(knownLabel) && row.Tooltip.Contains(reason),
                    "row share text or tooltip changed its coverage");
            Require(HasText(LiveInspector,knownLabel) && HasText(HistoryInspector,knownLabel),
                    "operator details lost the share denominator");
            var screenshot=Screenshot.StatisticsDocument(State.Get("history_selected"),Theme.Key);
            Require(HasText(screenshot,knownLabel) && HasText(screenshot,reason) && HasText(screenshot,note),
                    "ranking screenshot lost the partial share explanation");

            Apply(null,true,"50%");
            Require(CoverageExplanation(livePanel).Contains(teamLabel) &&
                    Equals(hud.Panel.Heading.ToolTip,teamLabel+" · "+note) &&
                    HasText(HistoryInspector,teamLabel),"legacy complete share scope or history refresh was lost");
            Require(row.Share=="50%" && row.Tooltip.Contains(teamLabel),"reused row retained an old share tooltip");
            Apply(null,false,"—");
            Require(HistoryPanel.Coverage.Text.Contains(unknownLabel) &&
                    CoverageExplanation(HistoryPanel).Contains(reason) &&
                    Equals(hud.Panel.Heading.ToolTip,unknownLabel+" · "+reason+" · "+note) &&
                    row.Share=="—", "legacy unknown shares acquired a denominator");
        }
        finally { ApplyState(original); }
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
        // Display names come from the Python catalog, never the native path.
        Require(Stage.Text=="OF-8 汐斯塔狂想曲","live stage display was not resolved");
        foreach(var item in State.Get("history").Items())
        {
            // ARK-23 synthetic archives intentionally carry a non-levelId stage.
            if(item.Text("battle_id").StartsWith("ark-23-"))continue;
            string label=item.Get("labels").Items().First().Text();
            Require(!label.Contains('/'),"history stage label leaked the native levelId");
            Require(item.Text("stage_detail").Contains('/'),"history stage detail lost the raw levelId");
        }
        checks.Add("live and history stage labels resolved from native levelId; raw id kept in detail");
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
        string alignedRoot=Path.Combine(output,"aligned-worker");Directory.CreateDirectory(alignedRoot);
        File.WriteAllLines(Path.Combine(alignedRoot,"desktop_bridge.py"),new[]{
            "import json,sys",
            "def reply(c,kind,**body): return dict(version=1,id=c['id'],ok=True,kind=kind,**body)",
            "def encode(value): return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')+b'\\r\\n'",
            "def send(data): sys.stdout.buffer.write(data); sys.stdout.buffer.flush()",
            "label='敌情状态·等待 🐱 '",
            "for line in sys.stdin:",
            "    c=json.loads(line)",
            "    if c['op']=='hello': send(encode(reply(c,'hello',result={'transport':'stdio-jsonl'}))); continue",
            "    if c['op']=='quit': send(encode(reply(c,'bye'))); break",
            "    text={'label':label,'padding':''}",
            "    result=reply(c,'snapshot',state=text)",
            "    remaining=c['size']-len(encode(result))",
            "    copies,tail=divmod(remaining,len(label.encode('utf-8')))",
            "    text['padding']=label*copies+'x'*tail",
            "    data=encode(result)",
            "    assert len(data)==c['size']",
            "    send(data)"});
        await using(var aligned=new BridgeClient(alignedRoot,App.Python,Array.Empty<string>()))
        {
            await aligned.ConnectAsync();
            foreach(int size in new[]{4095,4096,4097,8192,114688})
            {
                var reply=await aligned.RequestAsync("snapshot",new(){["size"]=size},TimeSpan.FromSeconds(3));
                Require(reply.Get("state").Text("label")=="敌情状态·等待 🐱 ","aligned UTF-8 worker response did not decode");
            }
        }
        checks.Add("UTF-8 worker replies complete at the pipe buffer boundary while the worker awaits the next request");
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
        var archive=State.Get("history").Items().FirstOrDefault(x=>x.Text("search").Contains("level_main_01-07"));
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
        var ownerRow=source.Get(owner);
        Require(ownerRow.Get("skill_damage").ValueKind==JsonValueKind.Number &&
                ownerRow.Get("non_skill_damage").ValueKind==JsonValueKind.Number,
                "skill / non-skill split missing from rendered source rows");
        checks.Add("skill / non-skill split reaches the operator detail source");
        activePanel=id;LiveInspector.Pin(owner,Panels[id].View);
        await Navigate("live");await Settle();
        // The right-hand inspector must consume the same effective/overkill
        // switch as the panel, including composition, engagement DPS and the
        // skill split. The synthetic rows carry both datasets.
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",
            ranking="total",rate_basis="recent_5s",effective_only=true}});
        await Settle();
        Require(Panels[id].View.Get("config").Bool("effective_only"),"effective switch did not reach the panel view");
        Require(HasText(LiveInspector,"技能覆盖率"),"inspector did not render skill coverage");
        Require(HasText(LiveInspector,"技能有效伤害"),"inspector did not switch skill split to effective data");
        Screenshot.Window(this,Path.Combine(output,"effective-inspector.png"));
        LastUiError="";
        Panels[id].EffectiveOnly.IsChecked=true;
        Panels[id].EffectiveOnly.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent));
        for(int wait=0;wait<40&&commands>0;wait++) await Task.Delay(50);
        await Settle();
        Require(LastUiError.Length==0,"effective click crashed the UI: "+LastUiError);
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",
            ranking="total",rate_basis="recent_5s",effective_only=false}});
        await Settle();
        Require(HasText(LiveInspector,"技能伤害") &&
                !HasText(LiveInspector,"技能有效伤害"),"inspector did not restore total skill split");
        var priorView = Panels[id].View;
        var unranked = System.Text.Json.Nodes.JsonNode.Parse(priorView.GetRawText())!;
        var rankedRows = unranked["entries"]!.AsArray();
        for (int index = rankedRows.Count - 1; index >= 0; index--)
            if (rankedRows[index]!["owner_id"]!.GetValue<string>() == owner) rankedRows.RemoveAt(index);
        using (var unrankedDoc = JsonDocument.Parse(unranked.ToJsonString()))
            LiveInspector.Update(unrankedDoc.RootElement);
        Require(LiveInspector.Owner == owner && HasText(LiveInspector,"技能覆盖率"),
            "pinned operator lost detail after leaving the ranking");
        LiveInspector.Update(priorView);
        checks.Add("effective switch drives inspector composition, engagement rates and skill split");
        OpenHud(id);var originalHud=Huds[id];originalHud.SetLocked(false);originalHud.SetOpacity(.83);
        await Settle();
        VerifyShareScope(id,originalHud);
        checks.Add("share scope reaches live, history, HUD, details and ranking PNG with legacy fallback");
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
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="full_cycle"}});
        Require(Panels[id].View.Text("rate_label")=="全周期DPS","full-cycle label missing");
        Require(Panels[id].View.Get("entries").Items().Any(e=>e.Text("rate_state")=="value" && e.Text("rate_text").EndsWith("/s")),"full-cycle did not expose a numeric DPS");
        checks.Add("full-cycle DPS uses observed presence time and renders /s");
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="dps",rate_basis="full_cycle"}});
        var dpsEntries=Panels[id].View.Get("entries").Items().ToArray();
        Require(dpsEntries.Length>0 && dpsEntries.All(e=>e.Text("rate_state")=="value"),"DPS ranking does not use the selected algorithm");
        Require(dpsEntries.Zip(dpsEntries.Skip(1)).All(pair=>pair.First.Number("value")>=pair.Second.Number("value")),"DPS ranking is not sorted by rate");
        checks.Add("DPS ranking follows the selected full-cycle algorithm");
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="recent_5s"}});
        string placementBeforeTheme=JsonSerializer.Serialize(originalHud.Placement());
        IntPtr handleBeforeTheme=Native.Handle(originalHud);
        foreach(string theme in new[]{"lone-trail","contingency-contract","babel","near-light","il-siracusano","original"})
        {
            await Command("theme",new(){["theme"]=theme});await Settle();
            await Navigate("live");await Settle();
            Require(ReferenceEquals(originalHud,Huds[id]) && Native.Handle(originalHud)==handleBeforeTheme,"theme recreated HUD");
            Require(JsonSerializer.Serialize(originalHud.Placement())==placementBeforeTheme,"theme changed HUD placement / opacity / lock");
            await Navigate("enemies");await Settle();
            Require(EnemyList.Items.Count==3,"enemy list did not render entered enemies");
            Require(EnemyTotalHp.Text.Contains("26000"),"enemy total HP did not render");
            Require(EnemyOurDamageLabel.Text=="我方对敌扣血" && EnemyOurDamage.Text=="≥ 18700" &&
                !EnemyChart.Points[^1].DamageComplete,"scoped damage lower bound did not render");
            Require(EnemyStatusHint.Text.Contains("离场时尚余 600 HP（1 个实例）"),
                "HP remaining at enemy departure did not render");
            Require(EnemyStatusHint.Text.Contains("已观测敌方回血 400 HP") &&
                EnemyStatusHint.Text.Contains("含回血累计可承受量 ≥ 26400 HP"),
                "enemy recovery and damage capacity did not render");
            if (State.Get("enemy").Get("non_team_native_damage").TryNumber() > 0)
                Require(EnemyStatusHint.Text.Contains("其他来源原生伤害 500"),"non-team damage basis did not render");
            Require(!EnemyEmpty.IsVisible,"enemy empty state shown with entered enemies");
            Screenshot.Window(this,Path.Combine(output,theme+"-enemies.png"));
            await Navigate("live");await Settle();
            Screenshot.Window(this,Path.Combine(output,theme+"-main.png"));Screenshot.Window(originalHud,Path.Combine(output,theme+"-hud.png"));
            var selected=State.Get("history_selected");
            if(selected.ValueKind==JsonValueKind.Object)
            {
                await Navigate("history");
                var selectedView = selected.Get("view");
                HistoryInspector.Pin(selectedView.Get("source").EnumerateObject().First().Name, selectedView);
                await Settle();
                Screenshot.Window(this,Path.Combine(output,theme+"-history.png"));
                File.WriteAllBytes(Path.Combine(output,theme+"-ranking.png"),Screenshot.Statistics(selected,theme));
                string selectedId=selected.Text("battle_id");Search.Text="no-such-record";await Command("history_refresh");
                Require(State.Get("history_selected").Text("battle_id")==selectedId,"filter/refresh replaced selected history");Search.Text="";
                checks.Add(theme+" frozen selected history survives search and refresh");
            }
            await Navigate("settings");await Settle();Screenshot.Window(this,Path.Combine(output,theme+"-settings.png"));await Navigate("live");
            checks.Add(theme+" theme switch keeps HUD HWND, values and source");
        }
        await VerifyLoneTrailLayout(output, checks);
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
