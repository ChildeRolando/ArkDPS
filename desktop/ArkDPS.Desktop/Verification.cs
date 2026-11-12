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
        FrameworkElement Find(string id) => FindAutomationElement(TargetSummary, id)
            ?? throw new InvalidOperationException("missing enemy control: " + id);

        var state=fixture.DeepClone();
        var rows=System.Text.Json.Nodes.JsonNode.Parse("""
            [
              {"instance_uid":"101","eid":"enemy_a","name":"首位敌人","lifecycle":"active","alive":true,"hp":900,"max_hp":1000,"atk":173},
              {"instance_uid":"102","eid":"enemy_a","name":"同型敌人","lifecycle":"active","alive":true,"hp":800,"max_hp":1200,"atk":214},
              {"instance_uid":"103","eid":"enemy_b","name":"次型敌人","lifecycle":"active","alive":true,"hp":600},
              {"instance_uid":"104","eid":"enemy_c","name":"第三型敌人","lifecycle":"active","alive":true,"hp":300},
              {"instance_uid":"105","eid":"enemy_d","name":"已离场敌人","lifecycle":"departed","alive":false,"hp":0,"max_hp":9000},
              {"instance_uid":"106","eid":"enemy_e","name":"推断敌人","lifecycle":"active","alive":true,"hp":10000,"entry_inferred":true}
            ]
            """)!.AsArray();
        state["enemy"]!["rows"]=rows;

        void Publish(string battle = "synthetic-enemy-paging") =>
            TargetSummary.Update(battle, JsonSerializer.SerializeToElement(state["enemy"]), "");

        try
        {
            Publish();
            var first = Find("LiveEnemyCard1");
            var second = Find("LiveEnemyCard2");
            var third = Find("LiveEnemyCard3");
            var previous = (Button)Find("PreviousLiveEnemy");
            var next = (Button)Find("NextLiveEnemy");
            var pin = (System.Windows.Controls.Primitives.ToggleButton)Find("LockLiveEnemy");
            Require(System.Windows.Automation.AutomationProperties.GetName(previous)=="上一名敌人" &&
                    System.Windows.Automation.AutomationProperties.GetName(next)=="下一名敌人",
                    "enemy paging arrows lack accessible names");
            Require(HasText(first,"首位敌人") && HasText(first,"173") &&
                    HasText(second,"次型敌人") && HasText(third,"第三型敌人") &&
                    !HasText(TargetSummary,"推断敌人") && !previous.IsEnabled && next.IsEnabled,
                    "cards did not prefer distinct confirmed live types or render attack");
            next.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent));
            Require(HasText(first,"同型敌人") && HasText(first,"214") && HasText(second,"次型敌人"),
                    "right arrow did not skip instances already shown on other cards");
            pin.IsChecked = true;
            rows[1]!["hp"]=230;
            Publish();
            Require(HasText(first,"同型敌人") && HasText(first,"230") && !next.IsEnabled && !previous.IsEnabled,
                    "locked card did not follow the exact instance after HP reorder");
            rows[1]!["alive"]=false;
            rows[1]!["lifecycle"]="departed";
            rows[1]!["hp"]=0;
            rows[1]!["atk"]=null;
            Publish();
            Require(pin.IsChecked == true && HasText(first,"同型敌人") && HasText(first,"214"),
                    "locked card lost the departed instance or last confirmed attributes");
            foreach(int index in new[] { 0, 2, 3 })
            {
                rows[index]!["alive"]=false;
                rows[index]!["lifecycle"]="departed";
            }
            Publish();
            pin.IsChecked = false;
            Require(HasText(TargetSummary,"已离场敌人") || next.IsEnabled || previous.IsEnabled,
                    "departed types disappeared from the cards and queue");
            pin.IsChecked = true;
            Publish("synthetic-enemy-paging-next");
            Require(pin.IsChecked == false, "enemy lock carried over to a new battle");
        }
        finally
        {
            UpdateEnemySummary();
        }
    }
    private async Task VerifyLoneTrailLayout(string output, List<string> checks)
    {
        await Command("theme", new() { ["theme"] = Theme.LoneTrail });
        foreach (var (token,family) in new[] { ("DisplayFont","Barlow Semi Condensed"),("DataFont","IBM Plex Mono") })
        {
            Require(Theme.Typeface(token,FontWeights.SemiBold).TryGetGlyphTypeface(out var glyph) &&
                    glyph.FamilyNames.Values.Contains(family) &&
                    glyph.FontUri.AbsoluteUri.Contains("/Assets/Fonts/",StringComparison.OrdinalIgnoreCase),
                    "Lone Trail did not load the packaged "+family+" font");
        }
        await Navigate("live");
        var initial = State.Clone();
        var fixture = System.Text.Json.Nodes.JsonNode.Parse(initial.GetRawText())!;
        var lobby = fixture.DeepClone();
        lobby["status"] = "waiting";
        lobby["status_text"] = "等待战斗数据";
        lobby["event_status"] = null;
        lobby["observer_status"] = "ready";
        ApplyFixture(lobby);
        Require(Status.Text == "观测中·等待进入战斗" && StatusLight.Fill == StatusGreen,
            "an attached observer did not show a green waiting status before the battle");
        lobby["observer_status"] = "failed";
        ApplyFixture(lobby);
        Require(Status.Text == "观测中断·等待进入战斗" && StatusLight.Fill == StatusRed,
            "a failed lobby observer left the green status on");
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
        var liveView=fixture["panels"]!.AsArray()[0]!;
        string pinnedOwner=liveView["entries"]!.AsArray()[0]!["owner_id"]!.GetValue<string>();
        var pinnedRow=liveView["source"]![pinnedOwner]!;
        Require(HasText(FindOperatorRatio("Mitigation"),"—"),"unavailable mitigation rendered as zero");
        pinnedRow["observed_mitigation_rate"]=58.4695;
        pinnedRow["observed_mitigation_after"]=7660.38;
        pinnedRow["mitigation_matched_hits"]=27;
        pinnedRow["mitigation_unknown_reason"]="部分伤害缺少同次命中的公式前后值";
        ApplyFixture(fixture);
        var observedMitigation=FindOperatorRatio("Mitigation");
        Require(HasText(observedMitigation,"58%") && HasText(observedMitigation,"已观测减伤率") &&
                observedMitigation.ToolTip is ToolTip {Content:TextBlock observedTip} &&
                observedTip.Text.Contains("经护甲/魔抗和物法减伤减少后的伤害 ÷ 原始伤害") &&
                observedTip.Text.Contains("已配对的 27 击"),
                "partial mitigation did not expose its measured value and sample scope");
        pinnedRow["observed_mitigation_rate"]=-11.32;
        ApplyFixture(fixture);
        Require(HasText(observedMitigation,"-11%") && HasText(observedMitigation,"已观测减伤率") &&
                observedMitigation.ToolTip is ToolTip {Content:TextBlock negativeTip} &&
                negativeTip.Text.Contains("无法确定差额来自哪项修正") &&
                negativeTip.Text.Contains("已配对的 27 击"),
                "negative observed mitigation did not retain its sign, uncertainty and sample scope");
        pinnedRow["mitigation_rate"]=0;
        ApplyFixture(fixture);
        Require(HasText(FindOperatorRatio("Mitigation"),"0%") &&
                HasText(FindOperatorRatio("Mitigation"),"被减伤率") &&
                !HasText(FindOperatorRatio("Mitigation"),"已观测减伤率"),
                "complete zero mitigation did not take precedence over the observed subset");
        pinnedRow["mitigation_rate"]=18.34;
        var pinnedDetail=liveView["details_by_owner"]![pinnedOwner]!;
        var savedCoverage=pinnedDetail["skill_coverage"]?.DeepClone();
        pinnedDetail["skill_coverage"]=0.0036;
        ApplyFixture(fixture);
        var coverageField=FindAutomationElement(LiveInspector,"CompactOperatorMetric3")
            ?? throw new InvalidOperationException("missing skill coverage field");
        Require(HasText(coverageField,"<1%"),
            "short confirmed skill duration rendered as zero coverage");
        pinnedDetail["skill_coverage"]=0;
        ApplyFixture(fixture);
        Require(HasText(coverageField,"0%"),"confirmed zero skill coverage was lost");
        pinnedDetail["skill_coverage"]=savedCoverage;
        ApplyFixture(fixture);
        double savedWidth = Width, savedHeight = Height;
        foreach (var (width, height) in new[] { (1920, 1080), (1672, 941), (1380, 900), (1200, 820), (560, 540) })
        {
            Width = width; Height = height;
            await Settle();
            Require(Math.Abs(ActualWidth - width) < 2 && Math.Abs(ActualHeight - height) < 2,
                $"test window did not reach {width}x{height} DIP: {ActualWidth}x{ActualHeight}, {WindowState}, DPI {System.Windows.Media.VisualTreeHelper.GetDpi(this)}");
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
            Require(!HasVisibleScrollbar(this), $"visible scrollbar at {width}x{height} DIPs");
            VerifyLiveRankingToolbar(Panels.Values.First(),width>=1380);
            if (width >= 1200)
            {
                VerifyCompactPortraitLayout();
                VerifyCompactRatioLayout();
            }
            Require(ReferenceEquals(TargetSummary.Background,Theme.Resource("Card")) &&
                    TargetSummary.Child is ScrollViewer { VerticalAlignment: VerticalAlignment.Top } &&
                    !HasText(TargetSummary,"敌情 ›"),
                    "enemy cards are misaligned or show a separate background or duplicate navigation");
            Screenshot.Window(this, Path.Combine(output, $"lone-trail-live-{width}x{height}-dip.png"));
            if (width == 1380)
            {
                Screenshot.Window(this, Path.Combine(output, "lone-trail-live-render-125-percent.png"), 120);
                Screenshot.Window(this, Path.Combine(output, "lone-trail-live-render-150-percent.png"), 144);
            }
        }
        await Navigate("enemies");
        await Settle();
        Require(EnemyPageScroll.ScrollableHeight > 0 && EnemyChartHost.ActualHeight >= 300 &&
                !HasVisibleScrollbar(this),
            "minimum window does not scroll to the full enemy chart");
        Screenshot.Window(this, Path.Combine(output, "lone-trail-enemies-minimum.png"));
        await Navigate("live");
        Width = savedWidth; Height = savedHeight;
        await Settle();
        WindowState = WindowState.Maximized;
        await Settle();
        var workArea = System.Windows.Forms.Screen.FromHandle(Native.Handle(this)).WorkingArea;
        var topLeft = ContentRoot.PointToScreen(new Point());
        var bottomRight = ContentRoot.PointToScreen(new Point(ContentRoot.ActualWidth, ContentRoot.ActualHeight));
        var panelBottom = LiveBody.PointToScreen(new Point(0, LiveBody.ActualHeight));
        Require(topLeft.X >= workArea.Left-1 && topLeft.Y >= workArea.Top-1 &&
                bottomRight.X <= workArea.Right+1 && bottomRight.Y <= workArea.Bottom+1 &&
                panelBottom.Y <= workArea.Bottom+1,
                $"maximized frame runs outside monitor work area: {topLeft} to {bottomRight}, {workArea}");
        Require(SidebarNavigation.Children.IndexOf(EnemyNav) == SidebarNavigation.Children.IndexOf(LiveNav)+1 &&
                DockPanel.GetDock(SidebarFooter) == Dock.Bottom &&
                SkinLabel.IsVisible && SkinLabel.Text == "皮肤" &&
                !HasText(SidebarFooter, "解锁 HUD"), "sidebar navigation or skin control placement is wrong");
        Require(TitleBar.ActualHeight <= 38 &&
                TitleBar.ActualHeight-ChromeControls.ActualHeight is >= 2 and <= 10,
                "window controls do not have a compact title bar");
        Screenshot.Window(this, Path.Combine(output, "lone-trail-live-maximized.png"));
        if (ContentRoot.ActualWidth >= 1320 && ContentRoot.ActualHeight >= 640)
            Require(LivePage.ScrollableHeight < 1 &&
                Math.Abs(LiveBody.TranslatePoint(new Point(0,LiveBody.ActualHeight),LivePage).Y - LivePage.ActualHeight) < 6,
                "maximized dashboard left unused space below the panels");
        WindowState = WindowState.Normal;
        Width = 560; Height = 540;
        await Navigate("history");
        await Settle();
        Require(ContentRoot.Margin == new Thickness(), "restored window kept maximized insets");
        Require(HistoryList.ActualHeight >= 58, $"minimum history viewport too short ({HistoryList.ActualHeight:F1} DIPs)");
        Screenshot.Window(this, Path.Combine(output, "lone-trail-history-minimum.png"));
        await Navigate("live");
        fixture["layout"]!["theme"] = Theme.People;
        for (int second = 17; second <= 26; second++)
        {
            fixture["game_time"] = second;
            analysis["game_time"] = second;
            SetIllustrativeRates(second == 26 ? 7630 : 6400 + second % 6 * 410);
            ApplyFixture(fixture);
        }
        var ranking = Panels.Values.First();
        ranking.Rows.SelectedIndex = 0;
        ranking.PinRequested?.Invoke(ranking.View.Get("entries").Items().First().Text("owner_id"));
        foreach (string theme in new[] { Theme.People, Theme.Babel })
        {
            fixture["layout"]!["theme"] = theme;
            ApplyFixture(fixture);
            Require(trendModel.Points.Count > 1 && trendModel.Points[^1].Team == 7630,
                $"{theme} dashboard did not plot the same live sample series");
            foreach (var (width, height) in new[] { (1380, 900), (560, 540) })
            {
                Width = width; Height = height;
                await Settle();
                Require(Theme.Key == theme && (Skin.SelectedItem as ComboBoxItem)?.Tag as string == theme,
                    $"{theme} and the picker are out of sync");
                if (theme == Theme.People)
                    Require(!SystemTitle.IsVisible &&
                        new[] {LiveNavAsset, EnemyNavAsset, HistoryNavAsset, SettingsNavAsset}
                            .Select(icon => icon.Source).OfType<System.Windows.Media.DrawingImage>().Distinct().Count() == 4,
                        "People retained the title text or did not use distinct navigation symbols");
                else
                {
                    var mark = width >= 1100 ? BabelBrandMark : BabelCompactBrandMark;
                    Require(mark.IsVisible && mark.Source is not null && BabelSidebarMark.Source is not null &&
                        BabelMastheadSurface.IsVisible && BabelMastheadArtwork.Source is not null &&
                        BabelMastheadTitle.Text == "BABEL" && BabelMastheadTitle.IsVisible &&
                        !BabelMastheadArtwork.IsHitTestVisible &&
                        new[] { LiveNavAsset, EnemyNavAsset, HistoryNavAsset, SettingsNavAsset }
                            .Select(icon => icon.Source).OfType<System.Windows.Media.ImageSource>().Distinct().Count() == 4,
                        "Babel shell artwork did not load at the requested width");
                    if (width < 1100)
                        Require(Skin.ActualHeight <= 54, "Babel skin name wrapped in the compact picker");
                }
                Require(Math.Abs(ActualWidth - width) < 2 && Math.Abs(ActualHeight - height) < 2 &&
                    Grid.GetColumnSpan(EnemySummary) == (width >= 1320 ? 1 : 3) &&
                    Grid.GetColumn(LiveDetail) == (width < 1100 ? 1 : 2),
                    $"{theme} dashboard did not reflow at its compact breakpoint");
                Require(CurrentDps.Text == "7,630" && !HasVisibleScrollbar(this),
                    $"{theme} dashboard lost its live rate or showed a scrollbar");
                VerifyLiveRankingToolbar(ranking, width >= 1380);
                if (width >= 1320)
                {
                    Require(ranking.ReferenceFooter.IsVisible, $"{theme} did not use the full ranking layout");
                    VerifyCompactPortraitLayout();
                    VerifyCompactRatioLayout();
                }
                Screenshot.Window(this, Path.Combine(output, $"{theme}-live-{width}x{height}-dip.png"));
                if (theme == Theme.Babel && width < 1100)
                {
                    analysis["team_recent_dps"] = 123456789;
                    ApplyFixture(fixture);
                    await Settle();
                    var text = new System.Windows.Media.FormattedText(CurrentDps.Text,
                        System.Globalization.CultureInfo.CurrentUICulture, CurrentDps.FlowDirection,
                        new System.Windows.Media.Typeface(CurrentDps.FontFamily, CurrentDps.FontStyle,
                            CurrentDps.FontWeight, CurrentDps.FontStretch),
                        CurrentDps.FontSize, System.Windows.Media.Brushes.Black,
                        System.Windows.Media.VisualTreeHelper.GetDpi(CurrentDps).PixelsPerDip);
                    Require(CurrentDps.Text == "123,456,789" && text.Width <= CurrentDps.ActualWidth + 1,
                        "Babel's compact masthead clipped a nine-digit live DPS readout");
                    analysis["team_recent_dps"] = 7630;
                    ApplyFixture(fixture);
                }
            }
            checks.Add($"{theme} uses the live ranking layout and reflows at minimum width");
        }
        Width = savedWidth; Height = savedHeight;
        await Settle();
        ApplyState(initial);
        await Command("theme", new() { ["theme"] = Theme.People });
        checks.Add("compact portrait, three parallel ratio rings and field formula tooltips, themed enemy cards, five Lone Trail layouts and physical maximized bounds");
    }
    private static void VerifyLiveRankingToolbar(StatisticPanel panel,bool singleRow)
    {
        var toolbar=panel.Selectors;
        var tableHeader=panel.ReferenceTableHeader.IsVisible ? panel.ReferenceTableHeader : panel.TableHeader;
        var toolbarBottom=toolbar.TranslatePoint(new Point(0,toolbar.ActualHeight),panel).Y;
        Require(toolbarBottom<=tableHeader.TranslatePoint(new Point(),panel).Y &&
                new[] {panel.Category, panel.Ranking, panel.Rate}.All(selector=>
                    selector.IsVisible && selector.ActualWidth<=80 && selector.ActualHeight==24 &&
                    selector.ToolTip is string {Length:>0}) &&
                panel.CategoryLabel.IsVisible && panel.RankingLabel.IsVisible && panel.RateLabel.IsVisible,
                "live ranking filters do not fit above the table with their guidance");
        if(singleRow)
        {
            double baseline=panel.Category.TranslatePoint(new Point(0,panel.Category.ActualHeight),toolbar).Y;
            Require(new FrameworkElement[] {panel.Ranking,panel.Rate,panel.OpenHudButton,panel.DuplicateButton,panel.DeleteButton}
                    .All(control=>Math.Abs(control.TranslatePoint(new Point(0,control.ActualHeight),toolbar).Y-baseline)<2),
                    "live ranking controls do not share a baseline at desktop width");
        }
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
        Require(avatar.ActualWidth==46 && avatar.ActualHeight==46 && HasText(LiveInspector,"1,167") &&
            positions.Take(3).All(point=>point.X>=avatarPosition.X+avatar.ActualWidth) &&
            avatarPosition.Y>=positions[0].Y-1 &&
            avatarPosition.Y+avatar.ActualHeight<=positions[2].Y+rows[2].ActualHeight+1 &&
            positions[3].X<positions[0].X,
            $"portrait or metrics misaligned at {ActualWidth} DIPs: avatar {avatarPosition} {avatar.ActualWidth}x{avatar.ActualHeight}, " +
            $"rows {string.Join(" / ",positions)}, DPS available {HasText(LiveInspector,"1,167")}");
    }
    private FrameworkElement FindOperatorRatio(string suffix) =>
        FindAutomationElement(LiveInspector,"CompactOperatorRatio"+suffix)
        ?? throw new InvalidOperationException("missing operator ratio "+suffix);

    private void VerifyCompactRatioLayout()
    {
        var ratios=new[] { FindOperatorRatio("Overkill"), FindOperatorRatio("Mitigation"), FindOperatorRatio("SkillShare") };
        var values=new[] { "8%", "18%", "42%" };
        var formulas=new[] { "过量伤害 ÷ 总伤害", "1 − 经护甲/魔抗和物法减伤减少后的伤害 ÷ 原始伤害",
            "技能期伤害 ÷ 已拆分的总伤害" };
        var positions=ratios.Select(field=>field.TranslatePoint(new Point(),LiveInspector)).ToArray();
        var stats=FindAutomationElement(LiveInspector,"CompactOperatorStats")
            ?? throw new InvalidOperationException("main data card missing");
        var presence=FindAutomationElement(stats,"CompactOperatorMetric4")
            ?? throw new InvalidOperationException("presence row missing");
        double presenceBottom=presence.TranslatePoint(new Point(0,presence.ActualHeight),LiveInspector).Y;
        Require(FindAutomationElement(stats,"CompactOperatorRatios") is not null && positions[0].Y>=presenceBottom,
                "ratio rings are not inside the main data card below on-field time");
        Require(positions.All(position=>Math.Abs(position.Y-positions[0].Y)<1) &&
                positions[0].X<positions[1].X && positions[1].X<positions[2].X &&
                ratios.All(field=>field.ActualWidth>=62 && field.ActualHeight>=58),
                "the three ratio rings did not fit side by side");
        for(int index=0;index<ratios.Length;index++)
        {
            Require(HasText(ratios[index],values[index]) &&
                    ratios[index].ToolTip is ToolTip {Content:TextBlock description} &&
                    description.Text.Contains(formulas[index],StringComparison.Ordinal) &&
                    ToolTipService.GetShowsToolTipOnKeyboardFocus(ratios[index])==true,
                    "ratio did not display its value or expose its formula");
        }
        for(int index=0;index<5;index++)
        {
            var field=FindAutomationElement(LiveInspector,"CompactOperatorMetric"+index);
            Require(field?.ToolTip is ToolTip {Content:TextBlock description} &&
                    description.Text.Contains('\n'),"operator field does not explain its calculation");
        }
        Require(!HasText(LiveInspector,"阿米娅") && !HasText(LiveInspector,"非技能伤害占比") &&
                FindAutomationElement(LiveInspector,"CompactOperatorDetails")==null,
                "compact inspector still displays the removed title, row or details button");
    }

    private static FrameworkElement? FindAutomationElement(DependencyObject root,string id) =>
        FindVisual<FrameworkElement>(root,element=>System.Windows.Automation.AutomationProperties.GetAutomationId(element)==id);

    private static T? FindVisual<T>(DependencyObject root,Func<T,bool> match) where T : DependencyObject
    {
        if(root is T visual && match(visual)) return visual;
        for(int index=0;index<System.Windows.Media.VisualTreeHelper.GetChildrenCount(root);index++)
            if(FindVisual(System.Windows.Media.VisualTreeHelper.GetChild(root,index),match) is { } found) return found;
        return null;
    }

    private static async Task Settle()=>await System.Windows.Application.Current.Dispatcher.InvokeAsync(()=>{},DispatcherPriority.ApplicationIdle).Task;
    private static bool HasVisibleScrollbar(DependencyObject root)
    {
        if (root is System.Windows.Controls.Primitives.ScrollBar { IsVisible: true } bar &&
            bar.ActualWidth > 0 && bar.ActualHeight > 0) return true;
        for (int index = 0; index < System.Windows.Media.VisualTreeHelper.GetChildrenCount(root); index++)
            if (HasVisibleScrollbar(System.Windows.Media.VisualTreeHelper.GetChild(root, index))) return true;
        return false;
    }
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
    private async Task VerifyHudLayoutEditor(string sourceId,string output)
    {
        var existing=Panels.Keys.ToHashSet();
        await Command("panel_duplicate",new(){["panel_id"]=sourceId});
        string id=Panels.Keys.Single(key=>!existing.Contains(key));
        OpenHud(id);
        HudWindow hud=Huds[id];
        try { await VerifyHudLayoutEditorPanel(id,hud,output); }
        finally
        {
            CloseHud(id);
            await Command("panel_delete",new(){["panel_id"]=id});
            await Navigate("live");
        }
    }

    private async Task VerifyHudLayoutEditorPanel(string id,HudWindow hud,string output)
    {
        await Navigate("settings");
        Require(EditHudLayoutButton.IsEnabled && HudLayoutTarget.Items.Count==Panels.Count,
                "settings cannot select a source HUD panel");
        var empty=JsonSerializer.SerializeToElement(new { config=Panels[id].View.Get("config"), entries=Array.Empty<object>() });
        var sampleEditor=new HudLayoutEditor("示例验收",empty,HudLayout.Default,_=>Task.FromResult(false)){Owner=this};
        sampleEditor.Show();
        try
        {
            await Settle();
            Require(sampleEditor.PreviewLabel.Text=="示例预览" &&
                    FindVisual<StatisticPanel>(sampleEditor,_=>true)?.Rows.Items.Count==5,
                    "HUD editor did not supply a labeled preview outside battle");
        }
        finally { sampleEditor.Close(); }
        var completion=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var editor=new HudLayoutEditor("HUD 布局验收",Panels[id].View,HudLayout.Default,async style=>
        {
            bool saved=await SaveHudLayout(id,style);
            completion.TrySetResult(saved);
            return saved;
        }){Owner=this};
        editor.Show();
        try
        {
            await Settle();
            TextBox Field(string key) => (TextBox)(FindAutomationElement(editor,"HudLayout"+key)
                ?? throw new InvalidOperationException("HUD layout field missing: "+key));
            var width=Field("Width");
            width.Text="2200";
            Require(!editor.SaveButton.IsEnabled && editor.StatusText.Text.Contains("HUD 宽度"),
                    "HUD layout editor accepted a width outside its range");
            width.Text="640";
            Field("NameFont").Text="23";
            Field("NameWidth").Text="160";
            Field("RowGap").Text="5";
            Field("ColumnGap").Text="7";
            Field("BarWidth").Text="0";
            await Settle();
            var preview=FindVisual<StatisticPanel>(editor,_=>true)
                ?? throw new InvalidOperationException("HUD layout preview missing");
            var previewName=FindVisual<TextBlock>(preview,element=>element.Name=="HudOperatorName");
            var previewRow=FindVisual<Grid>(preview,element=>element.Name=="HudOperatorRow");
            Require(editor.SaveButton.IsEnabled && editor.PreviewSurface.Width==640 && previewName?.FontSize==23 &&
                    previewRow?.ColumnDefinitions[3].ActualWidth==0 && hud.LayoutProfile==HudLayout.Default,
                    "HUD draft did not update only the preview or hide a zero-width column");
            Require(!HasVisibleScrollbar(editor),"HUD layout editor displayed a scrollbar");
            Screenshot.Window(editor,Path.Combine(output,"hud-layout-editor.png"));
            editor.SaveButton.RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent));
            Require(await completion.Task.WaitAsync(TimeSpan.FromSeconds(10)),"HUD layout save did not succeed");
            await Settle();
            var saved=ReadHudLayout(id);
            Require(saved is { Width:640, NameFont:23, NameWidth:160, RowGap:5, ColumnGap:7, BarWidth:0 } &&
                    hud.LayoutProfile==saved && Math.Abs(hud.ActualWidth-640)<2,
                    "HUD layout did not persist or apply to the open HUD");
            CloseHud(id);
            OpenHud(id);
            hud=Huds[id];
            await Settle();
            Require(hud.LayoutProfile==saved && Math.Abs(hud.ActualWidth-640)<2,
                    "reopened HUD lost its saved style");
            Require(await SaveHudLayout(id,null),"HUD layout reset did not succeed");
            await Settle();
            Require(ReadHudLayout(id)==null && hud.LayoutProfile==HudLayout.Default &&
                    Math.Abs(hud.ActualWidth-HudLayout.Default.Width)<2,
                    "HUD layout reset did not restore default rendering");
        }
        finally
        {
            if(editor.IsVisible) editor.Close();
        }
    }

    private async Task VerifyHudRows(HudWindow hud,string output)
    {
        var style=hud.LayoutProfile;
        Require(style is {
            TitleFont:20, NameFont:15, NumberFont:15, Width:484,
            RowHeight:46, TitleHeight:44, PortraitSize:38,
            RankWidth:28, PortraitWidth:52, NameWidth:92, BarWidth:158,
            TotalWidth:78, RateWidth:76, ShareWidth:68, RowGap:1, ColumnGap:2, Padding:4
        }, "fresh HUD did not use the shipped layout profile");
        var original=hud.Panel.View.Clone();
        var fixture=System.Text.Json.Nodes.JsonNode.Parse(original.GetRawText())!;
        var entries=fixture["entries"]!.AsArray().ToArray();
        var heights=new Dictionary<int,double>();
        bool pollingEnabled=poll.IsEnabled;
        poll.Stop();
        try
        {
            foreach(int count in new[] { 1,4,5,6 })
            {
                fixture["entries"]=new System.Text.Json.Nodes.JsonArray(entries.Take(count)
                    .Select(entry=>entry!.DeepClone()).ToArray());
                hud.Update(JsonSerializer.SerializeToElement(fixture),"");
                await Settle();
                heights[count]=hud.ActualHeight;
                var bounds=Native.Bounds(hud);
                var work=System.Windows.Forms.Screen.FromHandle(Native.Handle(hud)).WorkingArea;
                Require(bounds[0]>=work.Left && bounds[1]>=work.Top &&
                        bounds[0]+bounds[2]<=work.Right+1 && bounds[1]+bounds[3]<=work.Bottom+1,
                        "auto-sized HUD went outside its monitor work area");
                if(count is 1 or 5) Screenshot.Window(hud,Path.Combine(output,$"{Theme.Key}-hud-{count}-rows.png"));
            }
            Require(Math.Abs(heights[4]-heights[1]-3*style.RowPitch)<2 &&
                    Math.Abs(heights[5]-heights[4]-style.RowPitch)<2 &&
                    Math.Abs(heights[6]-heights[5])<2,
                    $"HUD height did not fit one through five rows: {string.Join(", ",heights.Values)}");
            var rows=hud.Panel.Rows;
            var row=FindVisual<Grid>(rows,element=>element.Name=="HudOperatorRow");
            var name=FindVisual<TextBlock>(rows,element=>element.Name=="HudOperatorName");
            var amount=FindVisual<TextBlock>(rows,element=>element.Name=="HudOperatorTotal");
            Require(row is not null && row.ColumnDefinitions[2].Width.Value==style.NameWidth &&
                    row.ColumnDefinitions[3].Width.Value==style.BarWidth &&
                    name?.FontSize==style.NameFont && amount?.FontSize==style.NumberFont &&
                    hud.Panel.Heading.FontSize==style.TitleFont &&
                    Math.Abs(hud.Panel.TitleRegion.ActualHeight-style.TitleHeight)<2 &&
                    Math.Abs(hud.ActualWidth-style.Width)<2,
                    "HUD compact dimensions or readable type did not fit");
            var scroll=FindVisual<ScrollViewer>(rows,_=>true);
            Require(scroll is { ScrollableHeight: > 0 } && !HasVisibleScrollbar(hud),
                    "HUD cannot show extra rows without a scrollbar");
            rows.ScrollIntoView(rows.Items[^1]);
            await Settle();
            Require(scroll!.VerticalOffset>0,"sixth HUD row cannot be brought into view");
            scroll.ScrollToTop();
        }
        finally
        {
            hud.Update(original,"");
            await Settle();
            if(pollingEnabled) poll.Start();
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
    private static async Task WaitForClick(Func<bool> delivered)
    {
        long deadline=Environment.TickCount64+1000;
        while(!delivered() && Environment.TickCount64<deadline) await Task.Delay(25);
    }

    internal async Task Verify(string output)
    {
        Directory.CreateDirectory(output);poll.Stop();save.Stop();
        var checks=new List<string>();
        // Check the initial worker reply before any later fixture or layout changes.
        var restoredLayout=State.Get("layout").Clone();
        await Settle();
        foreach(var placement in restoredLayout.Get("huds").Items())
        {
            Require(Huds.TryGetValue(placement.Text("panel_id"),out var hud) && hud is not null,
                    "saved HUD was not opened");
            Require(hud!.Locked==placement.Bool("locked"),"HUD lock restore");
            Require(Math.Abs(hud.Opacity-placement.Number("opacity"))<.001,"HUD alpha restore");
            var actual=Native.Bounds(hud);
            var dpi=System.Windows.Media.VisualTreeHelper.GetDpi(hud);
            var work=System.Windows.Forms.Screen.FromHandle(Native.Handle(hud)).WorkingArea;
            double width=Math.Clamp(placement.Number("width"),hud.MinWidth*dpi.DpiScaleX,work.Width);
            double height=Math.Min(work.Height,(hud.Panel.TitleRegion.Height+hud.Panel.Rows.Height)*dpi.DpiScaleY);
            double x=Math.Clamp(placement.Number("x"),work.Left,Math.Max(work.Left,work.Right-width));
            double y=Math.Clamp(placement.Number("y"),work.Top,Math.Max(work.Top,work.Bottom-height));
            Require(Math.Abs(actual[0]-x)<=2 && Math.Abs(actual[1]-y)<=2,
                    $"HUD restored outside expected position: ({actual[0]}, {actual[1]}) expected ({x}, {y})");
            Require(Math.Abs(actual[2]-width)<=2 && Math.Abs(actual[3]-height)<=2,
                    $"HUD did not restore width and fit its current rows: {actual[2]}x{actual[3]} expected {width}x{height}");
            checks.Add("saved HUD position, width, lock and opacity restored with current-row height");
        }
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
            var display=new HistoryItem{Data=item};
            Require(!display.StageDescription.Contains("原始关卡 ID"),"history tooltip leaked the native levelId");
            Require(display.Summary==$"{item.Text("outcome")} · {item.Text("duration")}" &&
                    !display.Summary.Contains("最后可靠游戏时刻"),"history row leaked practice or timing metadata");
            Require(DateTime.TryParseExact(display.Date,"yyyy-MM-dd HH:mm",System.Globalization.CultureInfo.InvariantCulture,
                    System.Globalization.DateTimeStyles.None,out _),"history timestamp was not displayed to the minute");
        }
        Require(!HistorySummary.Text.Contains("原始关卡 ID") && !HistorySummary.Text.Contains("最后观察") &&
                !HistorySummary.Text.Contains("最后可靠游戏时刻"),"history detail leaked raw stage or observation labels");
        checks.Add("history rows and details use readable stage names, minute timestamps and a compact summary");
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
        await Poll();Require(Status.Text.Contains("worker 响应") && StatusLight.Fill == StatusRed,
            "stalled worker left a live status");commands--;
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
        if (LiveInspector.CompactReference)
        {
            var skillRing=FindOperatorRatio("SkillShare");
            Require(HasText(skillRing,"技能期有效伤害占比") && HasText(skillRing,"37%") &&
                    skillRing.ToolTip is ToolTip {Content:TextBlock skillTip} &&
                    skillTip.Text.Contains("技能期有效伤害 ÷",StringComparison.Ordinal),
                    "skill-period ring did not switch to effective data");
        }
        else Require(HasText(LiveInspector,"技能有效伤害"),"inspector did not switch skill split to effective data");
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
        if (LiveInspector.CompactReference)
        {
            var skillRing=FindOperatorRatio("SkillShare");
            Require(HasText(skillRing,"技能期伤害占比") && HasText(skillRing,"42%") &&
                    !HasText(skillRing,"技能期有效伤害占比"),
                    "skill-period ring did not restore total damage");
        }
        else Require(HasText(LiveInspector,"技能伤害") && !HasText(LiveInspector,"技能有效伤害"),
                "inspector did not restore total skill split");
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
        OpenHud(id);
        var originalHud=Huds[id];
        await VerifyHudLayoutEditor(id,output);
        originalHud.SetLocked(false);originalHud.SetOpacity(.83);
        checks.Add("HUD layout editor previews fonts and columns, validates inputs, saves and restores profiles");
        await Settle();
        VerifyShareScope(id,originalHud);
        await VerifyHudRows(originalHud,output);
        checks.Add("HUD typography, one/four/five rows auto-fit, sixth row scroll and monitor work area");
        checks.Add("share scope reaches live, history, HUD, details and ranking PNG with legacy fallback");
        Require(originalHud.Panel.Heading.Text=="溢出总伤·近5秒DPS","HUD title lost the initial rank and rate basis");
        string ranks=string.Join("|",Panels[id].View.Get("entries").Items().Select(e=>e.Text("owner_id")+e.Text("value")+e.Text("bar_ratio")+e.Text("share")));
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="engagement"}});
        Require(ranks==string.Join("|",Panels[id].View.Get("entries").Items().Select(e=>e.Text("owner_id")+e.Text("value")+e.Text("bar_ratio")+e.Text("share"))),"rate changed ranking / bars / shares");
        Require(originalHud.Panel.Heading.Text=="溢出总伤·接敌DPS","open HUD missed the new rate basis");
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
        Require(originalHud.Panel.Heading.Text=="溢出总伤·按全周期DPS","open HUD missed the DPS sorting basis");
        checks.Add("DPS ranking follows the selected full-cycle algorithm");
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="recent_5s",effective_only=true}});
        Require(originalHud.Panel.Heading.Text=="有效伤害·近5秒DPS","open HUD missed the effective damage selection");
        checks.Add("open HUD title follows the damage selection, ranking basis and DPS window");
        await Command("panel_update",new(){["config"]=new{panel_id=id,category="damage",ranking="total",rate_basis="recent_5s"}});
        string placementBeforeTheme=JsonSerializer.Serialize(originalHud.Placement());
        IntPtr handleBeforeTheme=Native.Handle(originalHud);
        Require(Skin.Items.OfType<ComboBoxItem>().Select(item => item.Tag).SequenceEqual(Theme.Keys),
            "the theme picker did not match the supported themes");
        foreach(string theme in Theme.Keys)
        {
            await Command("theme",new(){["theme"]=theme});await Settle();
            await Navigate("live");await Settle();
            Require(ReferenceEquals(originalHud,Huds[id]) && Native.Handle(originalHud)==handleBeforeTheme,"theme recreated HUD");
            Require(!HasVisibleScrollbar(originalHud), "HUD displayed a scrollbar");
            var hudPanel = originalHud.Panel;
            Require(Theme.Key==theme && (Skin.SelectedItem as ComboBoxItem)?.Tag as string==theme &&
                hudPanel.View.Get("entries").GetRawText()==Panels[id].View.Get("entries").GetRawText(),
                "theme switch did not reach the picker and linked HUD");
            var titleRegion = hudPanel.TitleRegion;
            Require(hudPanel.PanelFrame.Margin == new Thickness() &&
                    hudPanel.PanelFrame.BorderThickness == new Thickness() &&
                    titleRegion.ActualWidth >= originalHud.ActualWidth - 2,
                    "HUD does not fill its window or retains the extra frame");
            bool loneTrail=theme==Theme.LoneTrail;
            Require(originalHud.IsTitleDragSource(titleRegion.InputHitTest(new Point(2,2)) as DependencyObject) &&
                    originalHud.IsTitleDragSource(titleRegion.InputHitTest(new Point(titleRegion.ActualWidth*.68,
                        titleRegion.ActualHeight/2)) as DependencyObject) &&
                    !originalHud.IsTitleDragSource(hudPanel.HudClose) &&
                    hudPanel.LoneTrailHudMark.IsVisible==loneTrail && hudPanel.LoneTrailHudBackdrop.IsVisible==loneTrail &&
                    hudPanel.LoneTrailHudChevrons.IsVisible==loneTrail &&
                    !hudPanel.LoneTrailHudBackdrop.IsHitTestVisible && !hudPanel.LoneTrailHudChevrons.IsHitTestVisible &&
                    hudPanel.PeopleHudArtwork.IsVisible==Theme.IsPeople &&
                    (!Theme.IsPeople || hudPanel.PeopleHudArtwork.Source!=null) && !hudPanel.PeopleHudArtwork.IsHitTestVisible &&
                    hudPanel.BabelHudMark.IsVisible==Theme.IsBabel && hudPanel.BabelHudRule.IsVisible==Theme.IsBabel &&
                    (!Theme.IsBabel || hudPanel.BabelHudMark.Data!=null &&
                        Equals(titleRegion.Background,Theme.Resource("Console")) && hudPanel.PanelFrame.CornerRadius==new CornerRadius(0)) &&
                    !hudPanel.BabelHudMark.IsHitTestVisible && !hudPanel.BabelHudRule.IsHitTestVisible,
                    "HUD drag surface, close hit target, or theme artwork did not render");
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
            await Navigate("settings");await Settle();
            Require(!HasVisibleScrollbar(this), "settings displayed a scrollbar");
            Screenshot.Window(this,Path.Combine(output,theme+"-settings.png"));await Navigate("live");
            checks.Add(theme+" theme switch keeps HUD HWND, values and source");
        }
        await Navigate("settings");
        await Command("number_units", new() { ["number_units"] = "chinese" });
        Require(NumberUnits.SelectedIndex == 1 && Json.CompactAmount(1_000_000) == "100万" &&
                Json.CompactAmount(120_000_000) == "1.2亿" &&
                Panels[id].View.Get("entries").Items().Any(row => row.Text("display_value").Contains("万")) &&
                originalHud.Panel.View.Get("entries").GetRawText() == Panels[id].View.Get("entries").GetRawText(),
                "Chinese display units did not reach the settings, chart formatter, live panel and HUD");
        Screenshot.Window(this, Path.Combine(output, "number-units-settings.png"));
        await Command("number_units", new() { ["number_units"] = "western" });
        Require(NumberUnits.SelectedIndex == 0 && Json.CompactAmount(1150) == "1.2K" &&
                Json.CompactAmount(1_000_000) == "1M" &&
                Panels[id].View.Get("entries").Items().Any(row => row.Text("display_value").Contains("K")),
                "western display units did not restore without restarting");
        checks.Add("global number units switch updates live ranking, chart labels and existing HUD");
        await VerifyLoneTrailLayout(output,checks);
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
        var under=new Window{Title="ArkDPS private click underlay — NOT MuMu",Width=originalHud.ActualWidth+30,Height=originalHud.ActualHeight+30,Left=originalHud.Left,Top=originalHud.Top,Topmost=true,Background=System.Windows.Media.Brushes.DarkSlateGray};
        under.PreviewMouseDown+=(_,_)=>underClicks++;
        originalHud.PreviewMouseDown+=(_,_)=>hudClicks++;
        GetCursorPos(out var pointer);
        try
        {
            originalHud.Topmost=false;under.Show();under.Activate();originalHud.Topmost=true;originalHud.Activate();await Settle();
            bounds=Native.Bounds(originalHud);int x=bounds[0]+90,y=bounds[1]+Math.Min(130,bounds[3]-14);
            Require(GetAncestor(WindowFromPoint(new PointNative{X=x,Y=y}),2)==Native.Handle(originalHud),"unlocked HUD does not own test point");
            Click(x,y);await WaitForClick(()=>hudClicks>0 || underClicks>0);
            Require(hudClicks==1 && underClicks==0,"unlocked HUD input route");
            originalHud.SetLocked(true);await Settle();
            Require(GetAncestor(WindowFromPoint(new PointNative{X=x,Y=y}),2)==Native.Handle(under),"locked HUD point is not private underlay; no click sent");
            Click(x,y);await WaitForClick(()=>underClicks>0 || hudClicks>1);
            Require(underClicks==1 && hudClicks==1,"locked HUD did not pass input to private underlay");
            originalHud.SetLocked(false);await Settle();
            Require(GetAncestor(WindowFromPoint(new PointNative{X=x,Y=y}),2)==Native.Handle(originalHud),
                "unlocked HUD did not reclaim the private test point");
            Click(x,y);await WaitForClick(()=>hudClicks>1 || underClicks>1);
            Require(hudClicks==2 && underClicks==1,"unlocked input not restored");
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
        await Command("theme",new(){["theme"]=Theme.Babel});originalHud.SetOpacity(.83);originalHud.SetLocked(true);await SavePlacement();
        var disk=JsonDocument.Parse(File.ReadAllText(Path.Combine(App.BridgeArguments[App.BridgeArguments.IndexOf("--state-dir")+1],"layout.json")));
        Require(disk.RootElement.Text("theme")==Theme.Babel,"theme persistence");checks.Add("shared v3 layout saved: Babel theme / locked HUD / 83% opacity / physical geometry");
        File.WriteAllText(Path.Combine(output,"receipt.json"),JsonSerializer.Serialize(new{stack="C# / .NET 10 / WPF",transport="parent-child stdio JSON Lines",dotnet=Environment.Version.ToString(),protocol=BridgeClient.Version,offline=true,worker_runtime=bridge?.Hello,checks,restored_layout=restoredLayout,layout=State.Get("layout"),limitations=new[]{"Synthetic data only; no MuMu / game input acceptance",trayEvidence}},new JsonSerializerOptions{WriteIndented=true}));
        await Quit();
    }
}
