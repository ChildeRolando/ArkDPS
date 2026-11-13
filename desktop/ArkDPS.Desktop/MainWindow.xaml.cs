using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Forms=System.Windows.Forms;

namespace ArkDPS.Desktop;

internal sealed class HistoryItem
{
    public JsonElement Data {get;init;}
    public string Id=>Data.Text("battle_id");
    public string Stage=>Data.Text("stage");
    public string StageDescription=>Data.Text("stage_description");
    public string Outcome=>Data.Text("outcome");
    public string Practice=>Data.Get("labels").Items().ElementAtOrDefault(1).Text();
    public string Summary=>string.Join(" · ",Outcome,Data.Text("duration"));
    public string Date=>Data.Text("date");
}

public partial class MainWindow : Window
{
    private BridgeClient? bridge;
    private Task? recoveryTask;
    private readonly CancellationTokenSource recoveryCancellation=new();
    private const int MaxAutomaticRecoveryAttempts=3;
    private static readonly TimeSpan HealthyRecoveryPeriod=TimeSpan.FromMinutes(1);
    private int automaticRecoveryAttempts;
    private long healthyResponsesSince;
    private bool workerUnavailable;
    private string? lastPollError;
    private const string EmptyHistoryText="暂无可读战绩；完成对局归档后自动出现。";
    private static readonly Brush StatusGreen=Theme.Brush("#348447");
    private static readonly Brush StatusRed=Theme.Brush("#C84F49");
    private static readonly Brush StatusAmber=Theme.Brush("#A46A16");
    private static readonly Brush StatusGray=Theme.Brush("#777B80");
    private readonly DispatcherTimer poll=new(){Interval=TimeSpan.FromMilliseconds(200)};
    private readonly DispatcherTimer save=new(){Interval=TimeSpan.FromMilliseconds(600)};
    private readonly LiveTrendModel trendModel = new();
    internal readonly Dictionary<string,StatisticPanel> Panels=new();
    internal readonly Dictionary<string,HudWindow> Huds=new();
    internal JsonElement State;
    private Forms.NotifyIcon? tray;
    private bool initialized,applying,polling,quitting,historyUpdating,historyHidden;
    private bool syncingHudFooter;
    private object? normalGeometry;
    private bool closingRequested;
    private DispatcherOperation? workAreaLayout;
    private long lastResponse=Stopwatch.GetTimestamp();
    private bool responseStale;
    private bool retainBattleSnapshot;
    private bool ShowingPreviousBattle=>retainBattleSnapshot && State.Text("battle_id").Length==0;
    private int commands;
    private string activePanel="",lastBattle="",historyBattle="",historySignature="";
    private const int HistoryCacheLimit=8;
    private readonly Dictionary<string,JsonElement> historyViews=new();
    private readonly LinkedList<string> historyViewOrder=new();
    private string historyCacheUnits="";
    private HistoryItem? cachedHistorySelection;
    private string? historySelectionInFlight;
    private bool HistoryCacheOnly=>workerUnavailable || bridge is not {CanSend:true};
    private string HistorySelectionId=>cachedHistorySelection?.Id ?? State.Get("history_selected").Text("battle_id");
    private JsonElement HistoryRecoveryConfig=>State.Get("history_selected").Get("view").Get("config");
    private string route="live";
    private sealed record HudLayoutTargetItem(string Id,string Label);
    internal string LastUiError="";
    public MainWindow()
    {
        Theme.Apply(Theme.Key);
        InitializeComponent();
        CompactTrend.Bind(trendModel);
        OperatorTrend.Bind(trendModel);
        RankingScroll.SizeChanged += (_, _) => FitLivePanels();
        PanelHost.SizeChanged += (_, _) => FitLivePanels();
        LiveDetailScroll.SizeChanged += (_, _) => FitLivePanels();
        LiveInspector.SizeChanged += (_, _) => FitLivePanels();
        ApplyThemeArtwork();
        SetNavState("live");
        HistoryPanel.IsHistory=true;HistoryPanel.Command=Command;HistoryPanel.RateActionRequested=HandleRateAction;
        HistoryPanel.PinRequested=id=>HistoryInspector.Pin(id,HistoryPanel.View);
        Outcome.SelectedIndex=0;Practice.SelectedIndex=0;
        poll.Tick+=async(_,_)=>await Poll();
        save.Tick+=async(_,_)=>{save.Stop();await SavePlacement();};
        Loaded+=async(_,_)=>await Start();
        ContentRoot.SizeChanged += (_, _) => ArrangePanels();
        StateChanged += (_, _) => QueueWorkAreaLayout();
        DpiChanged += (_, _) => QueueWorkAreaLayout();
        LocationChanged += (_, _) => { if(!sideMode) { QueueWorkAreaLayout(); ScheduleSave(); } };
        SideAttachmentTimer.Tick += (_, _) => UpdateSidePlacement();
    }
    private async Task Start()
    {
        try
        {
            bridge=new BridgeClient(App.Root,App.Python,App.BridgeArguments.ToArray());
            await bridge.ConnectAsync();
            var reply=await bridge.RequestAsync("snapshot");ApplyState(reply.Get("state"));
            CreateTray();poll.Start();
            if(App.Verification!=null) await Verify(App.Verification);
        }
        catch(Exception ex)
        {
            if(App.Verification!=null)
            {
                Directory.CreateDirectory(App.Verification);
                File.WriteAllText(Path.Combine(App.Verification,"failure.txt"),ex.ToString());
                await Quit(1);
            }
            else {MessageBox.Show(this,ex.Message,"ArkDPS 启动失败");await Quit(1);}
        }
    }
    private async Task Poll()
    {
        if(closingRequested || quitting || workerUnavailable) return;
        if(initialized && !responseStale && Stopwatch.GetElapsedTime(lastResponse)>TimeSpan.FromSeconds(2))
        {
            MarkDataStale("stale","数据已过期 · 等待 worker 响应 · 保留最后有效记录");
        }
        if(polling || commands>0 || bridge is not { } source) return;
        polling=true;
        try
        {
            var reply=await source.RequestAsync("snapshot");
            if(closingRequested || workerUnavailable || bridge!=source) return;
            ApplyState(reply.Get("state"));
            lastPollError=null;
        }
        catch(BridgeTransportException ex){ReportTransportFailure(source,ex);}
        catch(Exception ex)
        {
            if(closingRequested || workerUnavailable || bridge!=source) return;
            ReportSnapshotFailure(ex);
        }
        finally{polling=false;}
    }
    internal async Task Command(string op,Dictionary<string,object?>? fields=null)
    {
        if(bridge is not { CanSend:true } source || workerUnavailable || quitting ||
           (closingRequested && op!="placement") ||
           recoveryTask is { IsCompleted:false }) return;
        commands++;
        try
        {
            var reply=await source.RequestAsync(op,fields);
            if(bridge!=source || workerUnavailable || closingRequested || quitting) return;
            ApplyState(reply.Get("state"));
            if(reply.Get("result").ValueKind==JsonValueKind.String) Footer.Text=reply.Text("result");
        }
        catch(BridgeTransportException ex){ReportTransportFailure(source,ex);}
        // Never raise a modal dialog once shutdown has begun: it would block the close.
        catch(BridgeCommandException ex){if(App.Verification!=null)throw;if(!closingRequested)MessageBox.Show(this,ex.Message,"操作未完成");else WriteQuitLog("命令失败："+ex.Message);}
        catch(Exception ex)
        {
            if(App.Verification!=null) throw;
            if(bridge!=source || workerUnavailable || closingRequested) return;
            RecordUiError(ex);
            ErrorText.Text=ex.Message;
            MarkDataStale("stale","统计更新失败 · 保留最后有效记录");
        }
        finally{commands--;}
    }
    internal void RecordUiError(Exception ex)
    {
        LastUiError=ex.ToString();
        try
        {
            Directory.CreateDirectory(Path.Combine(App.Root,"runtime"));
            File.AppendAllText(Path.Combine(App.Root,"runtime","ui-errors.log"),
                DateTime.Now.ToString("O")+Environment.NewLine+LastUiError+Environment.NewLine);
        }
        catch(IOException){}
        catch(UnauthorizedAccessException){}
    }
    private void MarkDataStale(string status,string label)
    {
        responseStale=true;
        UpdateLiveStatus(status,label);
        trendModel.Interrupt();
        UpdateEnemies(State.Get("enemy"),DisplayedBattleId,
            ShowingPreviousBattle ? "previous" : status=="reconnecting" ? "disconnected" : status);
        UpdateEnemySummary();
        RefreshLiveAvailability();
        RefreshHistoryAvailability();
        foreach(var hud in Huds.Values) if(Panels.TryGetValue(hud.PanelId,out var panel)) hud.Update(panel.View,Status.Text);
    }
    private void ReportSnapshotFailure(Exception error)
    {
        if(lastPollError!=error.Message) RecordUiError(error);
        lastPollError=error.Message;
        ErrorText.Text=error.Message;
        MarkDataStale("stale","统计刷新失败 · 保留最后有效记录");
    }
    private void ReportTransportFailure(BridgeClient source,BridgeTransportException error)
    {
        if(bridge!=source || workerUnavailable || closingRequested) return;
        RecordUiError(error);
        healthyResponsesSince=0;
        retainBattleSnapshot=lastBattle.Length>0;
        if(automaticRecoveryAttempts<MaxAutomaticRecoveryAttempts)
        {
            ErrorText.Text=error.Message;
            recoveryTask=RecoverWorker(automatic:true);
        }
        else ShowWorkerUnavailable(error.Message,"自动重连次数已用完");
    }
    private void ShowWorkerUnavailable(string message,string label)
    {
        poll.Stop();
        workerUnavailable=true;
        ErrorText.Text=message;
        MarkDataStale("disconnected",label+" · 点击重连观察者");
        HistoryNotice.Text="显示已载入的战绩 · 点击重连观察者恢复更新";
        ConnectionDiagnostics.Text=Status.Text+"\n"+message;
    }
    internal void ApplyState(JsonElement state)
    {
        if(state.Number("protocol")!=BridgeClient.Version) throw new IOException("不支持的统计消息版本");
        var lastState=State;
        try {ApplySnapshot(state);}
        catch {State=lastState;throw;}
        finally{applying=false;}
        long now=Stopwatch.GetTimestamp();
        if(healthyResponsesSince==0 || responseStale || Stopwatch.GetElapsedTime(lastResponse,now)>TimeSpan.FromSeconds(2))
            healthyResponsesSince=now;
        if(automaticRecoveryAttempts>0 && Stopwatch.GetElapsedTime(healthyResponsesSince,now)>=HealthyRecoveryPeriod)
            automaticRecoveryAttempts=0;
        lastResponse=now;
        responseStale=false;
    }
    private void ApplySnapshot(JsonElement state)
    {
        applying=true;State=state;
        var layout=state.Get("layout");string theme=Theme.Normalize(layout.Text("theme"));
        string units = layout.Text("number_units") == "chinese" ? "chinese" : "western";
        bool unitsChanged = Json.NumberUnits != units;
        Json.NumberUnits = units;
        NumberUnits.SelectedIndex = units == "chinese" ? 1 : 0;
        if (unitsChanged) EnemyChart.RefreshNumberUnits();
        bool changed=Theme.Key!=theme;
        if(changed)
        {
            Theme.Apply(theme);
            ApplyThemeArtwork();
            SetNavState(route);
        }
        if (changed || unitsChanged)
        {
            CompactTrend.RefreshTheme();
            OperatorTrend.RefreshTheme();
        }
        Skin.SelectedIndex=Theme.IndexOf(theme);
        EventStatus.Text=state.Text("event_text");
        ErrorText.Text=string.Join(" · ",state.Get("warnings").Items().Select(x=>x.Text()).Append(state.Text("error")).Where(x=>x.Length>0));
        bool previousBattle=ShowingPreviousBattle;
        bool panelLayoutChanged=ApplyLiveSnapshot(state,previousBattle,changed || unitsChanged);
        Footer.Text=state.Text("connection_text");
        if(!initialized)
        {
            Native.Restore(this,layout.Get("gui"));
            foreach(var placement in layout.Get("huds").Items()) OpenHud(placement.Text("panel_id"),placement);
            InitializeSettings();initialized=true;normalGeometry=Native.Geometry(this);
            LivePage.ScrollToTop();
            if (layout.Bool("side_mode")) SetSideMode(true);
            panelLayoutChanged = true;
        }
        UpdateHistory();
        var diag=state.Get("diagnostic");
        ConnectionDiagnostics.Text=state.Text("connection_text")+"\n"+state.Text("event_text")+"\n"+
            "版本 "+diag.Text("version_actual")+" / 认证 "+diag.Text("version_expected")+"\n"+diag.Text("message")+"\n"+diag.Text("error");
        applying=false;
        if (panelLayoutChanged || changed) ArrangePanels();
        UpdateEnemies(state.Get("enemy"),DisplayedBattleId,previousBattle ? "previous" : state.Text("status"));
        UpdateEnemySummary();
        if(changed){InvalidateVisualTree(this);foreach(var hud in Huds.Values)InvalidateVisualTree(hud);}
    }
    private bool ApplyLiveSnapshot(JsonElement state,bool previousBattle,bool appearanceChanged)
    {
        if(previousBattle)
        {
            // The replacement can be healthy in the lobby before it has any battle
            // samples. Keep the existing rows, pins, and chart points intact.
            UpdateLiveStatus(state.Text("status"),state.Text("status_text"));
            if(appearanceChanged)
            {
                foreach(var (id,panel) in Panels)
                    UpdateLivePanel(id,panel,panel.View,Panels.Count>1);
                SideFriendly.RefreshAppearance();
            }
            RefreshLiveAvailability();
            return false;
        }
        retainBattleSnapshot=false;
        Stage.Text=state.Text("stage").Length>0 ? state.Text("stage") : "关卡未知";
        Stage.ToolTip=state.Text("stage_detail").Length>0 ? state.Text("stage_detail") : null;
        var analysis = state.Get("live_analysis");
        var totals=state.Get("totals");
        CurrentDps.Text = OverviewNumber(analysis.Get("team_recent_dps"));
        AverageDps.Text = OverviewNumber(analysis.Get("team_average_dps"));
        PeakDps.Text = OverviewNumber(analysis.Get("observed_peak_dps"));
        TotalDamage.Text = OverviewNumber(analysis.ValueKind == JsonValueKind.Object
            ? analysis.Get("team_total_damage") : totals.Get("damage"));
        UpdateLiveStatus(state.Text("status"),state.Text("status_text"));
        trendModel.Apply(state);
        double time = state.Get("game_time").TryNumber(double.NaN);
        int elapsed = double.IsFinite(time) ? (int)Math.Clamp(Math.Round(time, MidpointRounding.AwayFromZero), 0, int.MaxValue) : 0;
        Clock.Text = double.IsFinite(time) && time >= 0 ? $"{elapsed / 60:00}:{elapsed % 60:00}" : "—";
        var views=state.Get("panels").Items().ToArray();var ids=views.Select(v=>v.Get("config").Text("panel_id")).ToHashSet();
        bool panelLayoutChanged = !ids.SetEquals(Panels.Keys);
        foreach(string id in Panels.Keys.Where(x=>!ids.Contains(x)).ToArray())
        {
            PanelHost.Children.Remove(Panels[id]);Panels.Remove(id);CloseHud(id);
            if(activePanel==id){activePanel="";ResetLiveInspector();}
        }
        string battle=state.Text("battle_id");
        bool battleChanged = battle != lastBattle;
        if(battleChanged){lastBattle=battle;ResetLiveInspector();foreach(var panel in Panels.Values)panel.ClearSelection();}
        var sideRanking = state.Get("side_ranking");
        if (sideMode || battleChanged || sideRanking.ValueKind == JsonValueKind.Object)
            SideFriendly.Update(battle, sideRanking);
        foreach(var view in views)
        {
            string id=view.Get("config").Text("panel_id");
            if(!Panels.TryGetValue(id,out var panel))
            {
                panel=new StatisticPanel{Command=Command,HudRequested=id=>OpenHud(id),RateActionRequested=HandleRateAction};
                panel.PinRequested = owner =>
                {
                    activePanel = id;
                    LiveInspector.Pin(owner, Panels[id].View);
                    OperatorTrend.SetOwner(owner);
                    OperatorTrend.Visibility = Visibility.Visible;
                };
                Panels.Add(id,panel);PanelHost.Children.Add(panel);
            }
            UpdateLivePanel(id,panel,view,views.Length>1);
        }
        RefreshHudLayoutTargets(views);
        RefreshHudFooter();
        RefreshLiveAvailability();
        return panelLayoutChanged;
    }
    private void UpdateBabelMasthead(string status,bool observing)
    {
        bool waiting=status=="waiting" || State.Bool("battle_waiting") || State.Text("battle_id").Length==0;
        string title, subtitle;
        if(waiting)
        {
            title=observing ? "观察中" : "观察停止";
            subtitle="等待进入战斗";
        }
        else
        {
            string code=State.Text("stage_code"), name=State.Text("stage_name");
            if(code.Length==0 && name.Length==0 && State.Text("stage_raw").Length>0)
                code=State.Text("stage");
            title=name.Length>0 ? name : code.Length>0 ? code : "关卡未知";
            subtitle=code==title ? "" : code;
        }
        BabelMastheadTitle.Text=title;
        BabelMastheadStageCode.Text=subtitle;
        BabelMastheadSubtitle.Visibility=subtitle.Length>0 ? Visibility.Visible : Visibility.Collapsed;
        BabelMastheadHeading.ToolTip=subtitle.Length>0 ? $"{title} · {subtitle}" : title;
    }
    private void UpdateLivePanel(string id,StatisticPanel panel,JsonElement view,bool canDelete)
    {
        panel.Update(view,canDelete);
        if(Huds.TryGetValue(id,out var hud))
            hud.Update(view,Status.Text+" · "+EventStatus.Text);
        if(activePanel==id) LiveInspector.Update(view);
    }
    private void RefreshLiveAvailability()
    {
        bool available=!workerUnavailable && !ShowingPreviousBattle;
        AddPanelButton.IsEnabled=available;
        foreach(var panel in Panels.Values) panel.SetConfigurationAvailable(available);
    }
    private void UpdateLiveStatus(string status,string workerLabel)
    {
        string observer=State.Text("observer_status");
        string coverage=State.Text("event_status");
        bool disabled=observer=="disabled" || (observer.Length==0 &&
            State.Get("layout").Get("event_observation").ValueKind==JsonValueKind.False);
        bool observing=!disabled && status is not ("disconnected" or "reconnecting" or "locating")
            && (observer=="ready" || (observer.Length==0 && coverage=="recording"));
        UpdateBabelMasthead(status,observing);
        string phase=status switch
        {
            "waiting" => "等待进入战斗",
            "degraded" => "快照统计",
            _ => workerLabel,
        };
        (string label,Brush light) = status switch
        {
            "stale" or "disconnected" => (workerLabel,StatusRed),
            "reconnecting" => (workerLabel,StatusAmber),
            "locating" => (disabled ? workerLabel : "正在连接观察者",StatusAmber),
            _ when disabled => ($"观察已关闭·{phase}",StatusGray),
            _ when (observer is "failed" or "incompatible" or "stopped") ||
                   ((status is "waiting" or "live" or "degraded") && (coverage is "failed" or "gap"))
                => ($"观察中断·{phase}",StatusRed),
            _ when status!="degraded" && (observer=="ready" || coverage=="recording")
                => ($"观察中·{phase}",StatusGreen),
            "waiting" => ("等待连接观察者",StatusAmber),
            _ => ($"等待观察·{phase}",StatusAmber),
        };
        if(ShowingPreviousBattle) label="上次快照 · "+label;
        Status.Text=label;
        StatusLight.Fill=light;
        StatusBadge.ToolTip=string.Join(" · ",new[] { label,Stage.Text,EventStatus.Text,ErrorText.Text }
            .Where(text=>text.Length>0));
    }
    private static void InvalidateVisualTree(DependencyObject obj)
    {
        if(obj is UIElement ui) ui.InvalidateVisual();
        for(int i=0;i<VisualTreeHelper.GetChildrenCount(obj);i++) InvalidateVisualTree(VisualTreeHelper.GetChild(obj,i));
    }
    private void InitializeSettings()
    {
        var config=State.Get("connection_config");AutoDetect.IsChecked=config.Bool("auto_detect");
        AdbPath.Text=config.Text("adb_path");Serial.Text=config.Text("serial");EventsEnabled.IsChecked=State.Get("layout").Bool("event_observation");
        DetectionNote.Text=App.BridgeArguments.Any(x=>x is "--adb" or "--serial" or "--no-events" or "--frida-remote") ? "本次启动含命令行覆盖；表单编辑长期设置，应用后以此处为准。" : "多实例不会自动选择任意目标；请选择检测结果。";
    }
    private void OnResize(object sender, SizeChangedEventArgs e)
    {
        if(sideMode) return;
        QueueWorkAreaLayout();
        ScheduleSave();
    }
    private void QueueWorkAreaLayout()
    {
        if (WindowSurface == null || workAreaLayout?.Status == DispatcherOperationStatus.Pending) return;
        workAreaLayout = Dispatcher.InvokeAsync(() =>
        {
            var insets = new Thickness();
            if (WindowState == WindowState.Maximized && PresentationSource.FromVisual(WindowSurface) != null)
            {
                // WindowChrome can extend the client past the taskbar. Measure the overlap in the
                // same coordinate system as the content, including the current monitor's DPI.
                var work = Forms.Screen.FromHandle(Native.Handle(this)).WorkingArea;
                var topLeft = WindowSurface.PointFromScreen(new Point(work.Left, work.Top));
                var bottomRight = WindowSurface.PointFromScreen(new Point(work.Right, work.Bottom));
                insets = new Thickness(
                    Math.Max(0, topLeft.X), Math.Max(0, topLeft.Y),
                    Math.Max(0, WindowSurface.ActualWidth - bottomRight.X),
                    Math.Max(0, WindowSurface.ActualHeight - bottomRight.Y));
            }
            ContentRoot.Margin = insets;
        }, DispatcherPriority.Loaded);
    }
    private void ArrangePanels()
    {
        if (sideMode || PanelHost == null || HistoryDetailHost == null || ContentRoot.ActualWidth <= 0) return;
        double width = ContentRoot.ActualWidth;
        bool narrow = width < 1100;
        bool wide = width >= 1320;
        bool scrollLive = narrow || ContentRoot.ActualHeight < (wide ? 640 : 960);
        bool narrowOverview = width < 920;
        NavigationColumn.Width = new GridLength(Theme.IsBabel ? (narrow ? 86 : 172) : (narrow ? 92 : 208));
        BrandBadge.Width = NavigationColumn.Width.Value - (narrow ? 5 : 6);
        if (narrow) Skin.Padding = new Thickness(8, 24, Theme.IsBabel ? 20 : 27, 4);
        else Skin.ClearValue(Control.PaddingProperty);
        ArrangeThemeArtwork(narrow);
        foreach (var (button, label) in new[] { (LiveNav, LiveNavLabel), (HistoryNav, HistoryNavLabel), (EnemyNav, EnemyNavLabel), (SettingsNav, SettingsNavLabel) })
        {
            button.HorizontalContentAlignment = narrow ? HorizontalAlignment.Center : HorizontalAlignment.Left;
            label.Visibility = narrow ? Visibility.Collapsed : Visibility.Visible;
        }
        ArrangeOverview(narrowOverview, width < 710);

        EnemySummaryColumn.Width = wide ? new GridLength(22, GridUnitType.Star) : new GridLength(0);
        RankingColumn.Width = new GridLength(41, GridUnitType.Star);
        DetailColumn.Width = wide ? new GridLength(37, GridUnitType.Star) : new GridLength(narrow ? 0 : 340);
        LivePage.VerticalScrollBarVisibility = scrollLive ? ScrollBarVisibility.Hidden : ScrollBarVisibility.Disabled;
        LiveBodyFirstRow.Height = wide && !scrollLive ? new GridLength(1, GridUnitType.Star) : GridLength.Auto;
        LiveBodySecondRow.Height = wide ? new GridLength(0) : scrollLive
            ? GridLength.Auto : new GridLength(1, GridUnitType.Star);
        LiveBodyThirdRow.Height = narrow ? GridLength.Auto : new GridLength(0);
        Grid.SetColumnSpan(EnemySummary, wide ? 1 : 3);
        EnemySummary.Margin = wide ? new Thickness(0, 0, 5, 0) : new Thickness(0, 0, 0, 5);
        Grid.SetRow(RankingFrame, wide ? 0 : 1);
        Grid.SetColumnSpan(RankingFrame, narrow ? 2 : 1);
        RankingFrame.Margin = narrow ? new Thickness(0) : new Thickness(0, 0, 5, 0);
        Grid.SetColumn(LiveDetail, narrow ? 1 : 2);
        Grid.SetColumnSpan(LiveDetail, narrow ? 2 : 1);
        Grid.SetRow(LiveDetail, wide ? 0 : narrow ? 2 : 1);
        LiveDetail.Margin = narrow ? new Thickness(0, 5, 0, 0) : new Thickness(0);
        RankingScroll.MaxHeight = scrollLive ? 390 : double.PositiveInfinity;
        LiveDetailScroll.MaxHeight = scrollLive ? 440 : double.PositiveInfinity;
        foreach (var panel in Panels.Values)
        {
            panel.Width = double.NaN;
            panel.SetReferenceStyle(wide);
        }
        FitLivePanels();
        ArrangeHistory();
        ArrangeEnemyPage();
        bool stackedSettings = width < 1000;
        SettingsRightColumn.Width = stackedSettings ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        Grid.SetColumn(ConnectionEventForm, stackedSettings ? 0 : 1);
        Grid.SetRow(ConnectionEventForm, stackedSettings ? 2 : 1);
        ConnectionForm.Margin = new Thickness(0, 0, stackedSettings ? 0 : 12, 12);
    }
    private void ArrangeOverview(bool narrowOverview, bool twoMetricColumns)
    {
        MetricCards.Columns = twoMetricColumns ? 2 : 4;
        if (Theme.IsBabel)
        {
            OverviewTrendColumn.Width = new GridLength(narrowOverview ? 151 : 236);
            OverviewStatsColumn.Width = new GridLength(1, GridUnitType.Star);
            Grid.SetColumn(CurrentMetric, 1);
            Grid.SetColumn(MetricCards, 0);
            Grid.SetColumnSpan(MetricCards, 2);
            Grid.SetRow(MetricCards, 1);
            BabelMastheadSurface.Height = narrowOverview ? 112 : 145;
            BabelMastheadVeilLayer.SetResourceReference(Border.BackgroundProperty,
                narrowOverview ? "BabelMastheadVeilCompact" : "BabelMastheadVeil");
            BabelMastheadHeading.Margin = new Thickness(narrowOverview ? 14 : 22, 0, 8, 0);
            BabelMastheadTitle.FontSize = narrowOverview ? 48 : 72;
            BabelMastheadTitle.LineHeight = BabelMastheadTitle.FontSize;
            CurrentMetric.Margin = new Thickness(8, 0, 12, 0);
            CurrentDps.FontSize = narrowOverview ? 35 : 55;
            return;
        }
        OverviewTrendColumn.Width = new GridLength(1.69, GridUnitType.Star);
        OverviewStatsColumn.Width = narrowOverview ? new GridLength(0) : new GridLength(4.15, GridUnitType.Star);
        Grid.SetColumn(CurrentMetric, 0);
        Grid.SetColumn(MetricCards, narrowOverview ? 0 : 1);
        Grid.SetColumnSpan(MetricCards, 1);
        Grid.SetRow(MetricCards, narrowOverview ? 1 : 0);
        CurrentMetric.Margin = new Thickness(4, 0, 4, narrowOverview ? 4 : 0);
        CurrentDps.FontSize = 39;
    }
    private void FitLivePanels()
    {
        bool fill = LivePage.VerticalScrollBarVisibility == ScrollBarVisibility.Disabled;
        double panelHeight = fill ? RankingScroll.ActualHeight / Math.Max(1,Panels.Count) : 0;
        foreach (var panel in Panels.Values) panel.FitRankingRows(panelHeight);
        double trendHeight = fill ? LiveDetailScroll.ActualHeight - LiveInspector.ActualHeight - OperatorTrend.Margin.Top : 0;
        OperatorTrend.FitOperatorChart(trendHeight);
    }
    private void ArrangeHistory()
    {
        bool stacked = ContentRoot.ActualWidth < 850;
        HistoryListColumn.Width = new GridLength(historyHidden || stacked ? 0 : 285);
        HistoryListRow.Height = new GridLength(!historyHidden && stacked ? 198 : 0);
        Grid.SetRow(HistoryListBorder, stacked ? 0 : 1);
        Grid.SetColumnSpan(HistoryListBorder, stacked ? 2 : 1);
        HistoryListBorder.Margin = stacked ? new Thickness(20, 0, 20, 8) : new Thickness(20, 0, 10, 12);
        HistoryDetailHost.Margin = new Thickness(historyHidden || stacked ? 20 : 0, 0, 20, 12);
    }
    private void ArrangeEnemyPage()
    {
        bool stacked = ContentRoot.ActualWidth < 920;
        EnemyMetrics.Columns = ContentRoot.ActualWidth < 730 ? 2 : 4;
        EnemyPageScroll.VerticalScrollBarVisibility = stacked ? ScrollBarVisibility.Hidden : ScrollBarVisibility.Disabled;
        EnemyChartColumn.Width = stacked ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        EnemyRosterRow.Height = stacked ? new GridLength(300) : new GridLength(1, GridUnitType.Star);
        EnemyChartRow.Height = new GridLength(stacked ? 312 : 0);
        Grid.SetColumn(EnemyChartHost, stacked ? 0 : 1);
        Grid.SetRow(EnemyChartHost, stacked ? 1 : 0);
        EnemyRosterHost.Margin = stacked ? new Thickness(0, 0, 0, 8) : new Thickness(0, 0, 12, 0);
    }
    private void ApplyThemeArtwork()
    {
        bool loneTrail = Theme.Key == Theme.LoneTrail;
        string palette = Theme.Key switch
        {
            Theme.People => "People",
            Theme.Babel => "Babel",
            _ => "LoneTrail"
        };
        var shell = new ResourceDictionary
        {
            Source = new Uri($"pack://application:,,,/Themes/MainWindow.{palette}.xaml")
        };
        Resources.MergedDictionaries.Clear();
        Resources.MergedDictionaries.Add(shell);
        ApplyShellLayout();
        PageTitle.SetResourceReference(TextBlock.ForegroundProperty, Theme.IsPeople ? "SubpageHeadingInk" : "HeroInk");
        PageSubtitle.SetResourceReference(TextBlock.ForegroundProperty, Theme.IsPeople ? "SubpageHeadingInk" : "HeroMuted");
        Skin.Resources["Focus"] = Theme.Resource("FocusOnDark");
        if (Theme.IsBabel) ChromeControls.Resources["Focus"] = Theme.Resource("FocusOnDark");
        else ChromeControls.Resources.Remove("Focus");
        HeaderArtwork.Visibility = loneTrail ? Visibility.Visible : Visibility.Collapsed;
        PeoplePennant.Visibility = Theme.IsPeople ? Visibility.Visible : Visibility.Collapsed;
        BabelTitlebarSurface.Visibility = BabelSidebarMark.Visibility =
            Theme.IsBabel ? Visibility.Visible : Visibility.Collapsed;
        bool offline = App.BridgeArguments.Contains("--offline");
        SystemTitle.Visibility = loneTrail ? Visibility.Visible : Visibility.Collapsed;
        ModeLabel.Visibility = offline || loneTrail ? Visibility.Visible : Visibility.Collapsed;
        ModeLabel.Text = offline ? "离线验收 · 合成数据 · 不访问游戏" : "ARKDPS / ORBITAL CONSOLE";
        ArrangeThemeArtwork(ContentRoot.ActualWidth is > 0 and < 1100);
    }

    private void ApplyShellLayout()
    {
        bool babel = Theme.IsBabel;
        foreach (var frame in new[] { LiveOverviewFrame, EnemySummary, RankingFrame, LiveDetail })
            frame.SetResourceReference(StyleProperty, babel ? "BabelResearchFrame" : "ResearchFrame");
        foreach (var button in new[] { LiveNav, EnemyNav, HistoryNav, SettingsNav })
            button.SetResourceReference(StyleProperty, babel ? "BabelResearchNav" : "ResearchNav");
        foreach (Border metric in MetricCards.Children)
            metric.SetResourceReference(StyleProperty, babel ? "BabelSecondaryMetric" : "ResearchMetric");
        CurrentMetric.SetResourceReference(StyleProperty, babel ? "BabelCurrentMetric" : "ResearchCurrentMetric");

        BabelMastheadSurface.Visibility = BabelMastheadHeading.Visibility = BabelMetricStrip.Visibility =
            babel ? Visibility.Visible : Visibility.Collapsed;
        CurrentMetricPulse.Visibility = CurrentMetricUnit.Visibility = babel ? Visibility.Collapsed : Visibility.Visible;
        LiveOverviewFrame.Padding = babel ? new Thickness(8, 2, 8, 5) : new Thickness(22, 7, 14, 5);
        LiveOverviewFrame.MinHeight = babel ? 0 : 304;
        LiveFrameTitle.Text = babel ? "实时战况" : "实时DPS / LIVE DPS";
        EnemySummary.Header = sideMode || babel ? "敌方单位" : "敌方单位 / ENEMY UNIT";
        RankingFrame.Header = babel ? "干员 DPS 排行" : "干员DPS排行 / OPERATOR DPS RANKING";
        LiveDetail.Header = babel ? "干员详情" : "干员详情 / OPERATOR DETAIL";
        if (babel)
        {
            CurrentMetricLabel.SetResourceReference(TextBlock.ForegroundProperty, "BabelPaperMuted");
            CurrentDps.SetResourceReference(TextBlock.ForegroundProperty, "BabelPaperInk");
        }
        else
        {
            CurrentMetricLabel.ClearValue(TextBlock.ForegroundProperty);
            CurrentDps.ClearValue(TextBlock.ForegroundProperty);
        }
    }
    private void ArrangeThemeArtwork(bool narrow)
    {
        bool loneTrail = Theme.Key == Theme.LoneTrail;
        ChromeDescription.Visibility = ContentRoot.ActualWidth >= 740 && ModeLabel.Visibility == Visibility.Visible
            ? Visibility.Visible : Visibility.Collapsed;
        BrandMark.Visibility = loneTrail && !narrow ? Visibility.Visible : Visibility.Collapsed;
        CompactBrandMark.Visibility = loneTrail && narrow ? Visibility.Visible : Visibility.Collapsed;
        PeopleBrandMark.Visibility = Theme.IsPeople && !narrow ? Visibility.Visible : Visibility.Collapsed;
        PeopleCompactBrandMark.Visibility = Theme.IsPeople && narrow ? Visibility.Visible : Visibility.Collapsed;
        BabelBrandMark.Visibility = Theme.IsBabel && !narrow ? Visibility.Visible : Visibility.Collapsed;
        BabelCompactBrandMark.Visibility = Theme.IsBabel && narrow ? Visibility.Visible : Visibility.Collapsed;
        ChromeLab.Visibility = loneTrail && ContentRoot.ActualWidth >= 940 ? Visibility.Visible : Visibility.Collapsed;
        SideLab.Visibility = SideLabName.Visibility = loneTrail && !narrow ? Visibility.Visible : Visibility.Collapsed;
    }
    private void ResetLiveInspector()
    {
        LiveInspector.Reset();
        OperatorTrend.SetOwner(null);
        OperatorTrend.Visibility = Visibility.Collapsed;
    }
    private string DisplayedBattleId=>ShowingPreviousBattle ? lastBattle : State.Text("battle_id");
    private void UpdateEnemySummary()
    {
        TargetSummary.Update(DisplayedBattleId,shownEnemy,EnemyStatusText.Text);
    }
    private void ScheduleSave()
    {
        if (!initialized || applying || quitting || closingRequested) return;
        RememberNormalGeometry();
        save.Stop();
        save.Start();
    }
    private void RememberNormalGeometry()
    {
        if (!sideMode && IsVisible && WindowState == WindowState.Normal)
            normalGeometry = Native.Geometry(this);
    }
    internal async Task SavePlacement()
    {
        if(!initialized || bridge==null) return;
        RememberNormalGeometry();
        await Command("placement",new(){["gui"]=normalGeometry??Native.Geometry(this),
            ["huds"]=Huds.Values.Select(h=>h.Placement()).ToArray(),["side_mode"]=sideMode});
    }
    internal void OpenHud(string id,JsonElement placement=default)
    {
        if(!Panels.ContainsKey(id)) return;
        if(Huds.TryGetValue(id,out var existing))
        {
            RefreshHudFooter(id);
            if(!existing.Locked) existing.Activate();
            return;
        }
        var hud=new HudWindow(id){SaveScale=style=>SaveHudLayout(id,style)};
        hud.Panel.RateActionRequested=HandleRateAction;Huds.Add(id,hud);
        hud.PlacementChanged+=()=>
        {
            if(HudFooterTarget.SelectedValue is string selected && selected==id) SyncHudFooter();
            ScheduleSave();
        };
        hud.ClosedByUser+=()=>
        {
            Huds.Remove(id);
            RefreshHudFooter();
            if(!closingRequested && Huds.Count==0 && (!IsVisible || WindowState==WindowState.Minimized)) ShowGui();
            ScheduleSave();
        };
        var profile=ReadHudLayout(id);
        if(profile!=null) hud.ApplyHudLayout(profile);
        hud.Update(Panels[id].View,Status.Text+" · "+EventStatus.Text);
        hud.Show();
        if(placement.ValueKind==JsonValueKind.Object) hud.RestorePlacement(placement,profile!=null);
        RefreshHudFooter(id);
        ScheduleSave();
    }
    private HudLayout? ReadHudLayout(string panelId)
    {
        var profile=State.Get("layout").Get("hud_styles").Get(panelId);
        return profile.ValueKind==JsonValueKind.Object ? HudLayout.FromJson(profile) : null;
    }

    private void RefreshHudLayoutTargets(JsonElement[] views)
    {
        var choices=views.Select((view,index)=>new HudLayoutTargetItem(
            view.Get("config").Text("panel_id"),$"{index+1}. {view.Text("title")}")).ToArray();
        if(HudLayoutTarget.ItemsSource is HudLayoutTargetItem[] old && old.SequenceEqual(choices)) return;
        string? selected=(HudLayoutTarget.SelectedItem as HudLayoutTargetItem)?.Id;
        HudLayoutTarget.ItemsSource=choices;
        HudLayoutTarget.SelectedItem=choices.FirstOrDefault(item=>item.Id==selected) ?? choices.FirstOrDefault();
        EditHudLayoutButton.IsEnabled=choices.Length>0;
    }

    private void EditHudLayoutClick(object sender,RoutedEventArgs e)
    {
        if(HudLayoutTarget.SelectedItem is not HudLayoutTargetItem item ||
           !Panels.TryGetValue(item.Id,out var panel)) return;
        var initial=ReadHudLayout(item.Id) ?? HudLayout.Default;
        if(Huds.TryGetValue(item.Id,out var hud))
            initial=hud.LayoutProfile with {Width=hud.BaseWidth};
        var editor=new HudLayoutEditor(item.Label,panel.View,initial,style=>SaveHudLayout(item.Id,style)){Owner=this};
        editor.ShowDialog();
    }

    internal async Task<bool> SaveHudLayout(string panelId,HudLayout? layout)
    {
        if(bridge==null || closingRequested || !Panels.ContainsKey(panelId)) return false;
        await Command("hud_style",new(){["panel_id"]=panelId,["style"]=layout});
        if(ReadHudLayout(panelId)!=layout || !Panels.ContainsKey(panelId)) return false;
        if(Huds.TryGetValue(panelId,out var hud))
        {
            hud.ApplyHudLayout(layout ?? HudLayout.Default);
            ScheduleSave();
        }
        return true;
    }

    private void CloseHud(string id)
    {
        if(!Huds.Remove(id,out var hud)) return;
        hud.SuppressClose=true;
        hud.Close();
        RefreshHudFooter();
    }
    private void RefreshHudFooter(string? preferredId=null)
    {
        if(Huds.Count==0)
        {
            HudFooterTarget.ItemsSource=null;
            HudFooterControls.Visibility=Visibility.Collapsed;
            HudFooterTarget.Visibility=Visibility.Collapsed;
            return;
        }
        var choices=PanelHost.Children.OfType<StatisticPanel>()
            .Select((panel,index)=>new HudLayoutTargetItem(panel.PanelId,$"HUD {index+1}"))
            .Where(item=>Huds.ContainsKey(item.Id)).ToArray();
        var previous=HudFooterTarget.SelectedValue as string;
        if(HudFooterTarget.ItemsSource is not HudLayoutTargetItem[] old || !old.SequenceEqual(choices))
            HudFooterTarget.ItemsSource=choices;
        HudFooterTarget.SelectedValue=choices.FirstOrDefault(item=>item.Id==(preferredId??previous))?.Id
            ?? choices.FirstOrDefault()?.Id;
        HudFooterControls.Visibility=Visibility.Visible;
        HudFooterTarget.Visibility=choices.Length>1 ? Visibility.Visible : Visibility.Collapsed;
        SyncHudFooter();
    }
    private void HudFooterSelectionChanged(object sender,SelectionChangedEventArgs e)=>SyncHudFooter();
    private void SyncHudFooter()
    {
        if(HudFooterTarget.SelectedValue is not string id || !Huds.TryGetValue(id,out var hud)) return;
        syncingHudFooter=true;
        try
        {
            HudFooterLock.Content=hud.Locked ? "解锁 HUD" : "锁定 HUD";
            HudFooterOpacity.Value=Math.Round((1-hud.Opacity)*100);
        }
        finally{syncingHudFooter=false;}
    }
    private void ToggleFooterHudLock(object sender,RoutedEventArgs e)
    {
        if(HudFooterTarget.SelectedValue is string id) ToggleLock(id);
    }
    private void FooterHudOpacityChanged(object sender,RoutedPropertyChangedEventArgs<double> e)
    {
        if(!syncingHudFooter && HudFooterTarget?.SelectedValue is string id && Huds.TryGetValue(id,out var hud))
            hud.SetOpacity(1-e.NewValue/100);
    }
    private void ToggleLock(string id)
    {
        if(!Huds.TryGetValue(id,out var hud))return;
        try{hud.SetLocked(!hud.Locked);}
        catch(Exception ex){MessageBox.Show(this,ex.Message,"HUD 锁定失败");}
    }
    private void UnlockAll(object sender,RoutedEventArgs e)
    {
        foreach(string id in Huds.Keys.ToArray())if(Huds[id].Locked)ToggleLock(id);
    }
    private async void Navigate(object sender,RoutedEventArgs e)=>await Navigate((string)((Button)sender).Tag);
    internal async Task Navigate(string page)
    {
        route=page;LivePage.Visibility=page=="live" ? Visibility.Visible : Visibility.Collapsed;
        HistoryPage.Visibility=page=="history" ? Visibility.Visible : Visibility.Collapsed;
        EnemyPageScroll.Visibility=page=="enemies" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPage.Visibility=page=="settings" ? Visibility.Visible : Visibility.Collapsed;
        bool live=page=="live";
        SubpageHeader.Visibility = live ? Visibility.Collapsed : Visibility.Visible;
        PageTitle.Text=page switch{"history"=>"历史战绩","enemies"=>"敌情","settings"=>"设置",_=>"实时战况"};
        PageSubtitle.Text=page switch{"history"=>"本地战绩 · 搜索与导出","enemies"=>"已入场敌人 · 属性与血量走势","settings"=>"显示与 MuMu 连接",_=>"团队输出与干员详情"};
        SetNavState(page);
        AddPanelButton.Visibility=live ? Visibility.Visible : Visibility.Collapsed;
        ArrangePanels();
        // The list is a pull cache owned by Python: re-read it on every entry so a
        // battle finished a moment ago is visible without pressing 刷新.
        if(page=="history") await Command("history_refresh");
    }
    private void SetNavState(string page)
    {
        foreach(var (button,tag) in new[]{(LiveNav,"live"),(HistoryNav,"history"),(EnemyNav,"enemies"),(SettingsNav,"settings")})
        {
            bool active=tag==page;
            button.SetResourceReference(Control.BackgroundProperty, active ? "ShellNavActive" : "Sidebar");
            button.Foreground=active?Theme.Resource("SidebarInk"):Theme.Resource("SidebarMuted");
            if (Theme.IsBabel)
            {
                if (active) button.SetResourceReference(Control.BorderBrushProperty, "ShellNavSelectedEdge");
                else button.BorderBrush = Brushes.Transparent;
                button.BorderThickness = new Thickness(2, 0, 0, 0);
            }
            else
            {
                button.SetResourceReference(Control.BorderBrushProperty, active ? "ShellNavSelectedEdge" : "SidebarLine");
                button.BorderThickness = new Thickness(active ? 2 : 1);
            }
        }
    }
    private static string OverviewNumber(JsonElement value) =>
        value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out double number) && double.IsFinite(number)
            ? Math.Round(number, MidpointRounding.AwayFromZero).ToString("N0", System.Globalization.CultureInfo.InvariantCulture) : "—";
    private async void AddPanel(object sender,RoutedEventArgs e)=>await Command("panel_add");
    private void MinimizeWindow(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;
    private void ToggleMaximizeWindow(object sender, RoutedEventArgs e) =>
        WindowState = WindowState == WindowState.Maximized ? WindowState.Normal : WindowState.Maximized;
    private void DragTitleBar(object sender, MouseButtonEventArgs e)
    {
        if (e.ClickCount == 2) ToggleMaximizeWindow(sender, e);
        else if (WindowState == WindowState.Normal)
        {
            try { DragMove(); }
            catch (InvalidOperationException) { }
        }
    }
    // 事件不可用时的恢复入口：重试会重新连接采集（不重启应用）；否则打开连接设置。
    private async void HandleRateAction(string action)
    {
        if(action=="retry"){ShowGui();await Navigate("live");await ReconnectObserver();}
        else if(action=="settings")
        {
            ShowGui();
            bool restoreFullPanel = sideMode;
            SetSideMode(false);
            await Navigate("settings");
            if (restoreFullPanel) await SavePlacement();
        }
    }
    private async void Rescan(object sender,RoutedEventArgs e)=>await ReconnectObserver();
    private Task ReconnectObserver()
    {
        if(closingRequested || quitting) return Task.CompletedTask;
        if(recoveryTask is {IsCompleted:false}) return recoveryTask;
        if(bridge is {CanSend:true} && !workerUnavailable) return Command("rescan");
        return recoveryTask=RecoverWorker(automatic:false);
    }
    private async Task RecoverWorker(bool automatic)
    {
        poll.Stop();
        save.Stop();
        workerUnavailable=true;
        retainBattleSnapshot=lastBattle.Length>0;
        LiveReconnectButton.IsEnabled=SubpageReconnectButton.IsEnabled=false;
        var stop=recoveryCancellation.Token;
        try
        {
            do
            {
                int attempt=automatic ? ++automaticRecoveryAttempts : 1;
                string progress=automatic ? $"正在自动重连观察者 · {attempt}/{MaxAutomaticRecoveryAttempts}" : "正在重连观察者";
                MarkDataStale("reconnecting",progress+" · 保留最后有效记录");
                HistoryNotice.Text=Status.Text;
                ConnectionDiagnostics.Text=Status.Text+"\n"+ErrorText.Text;
                if(attempt>1) await Task.Delay(TimeSpan.FromSeconds(attempt-1),stop);
                try
                {
                    var state=await RestoreWorkerSnapshot(stop);
                    workerUnavailable=false;
                    lastPollError=null;
                    try {ApplyState(state);}
                    catch(Exception ex){ReportSnapshotFailure(ex);}
                    ScheduleSave();
                    poll.Start();
                    return;
                }
                catch(OperationCanceledException) when(stop.IsCancellationRequested){return;}
                catch(Exception ex)
                {
                    workerUnavailable=true;
                    RecordUiError(ex);
                    ErrorText.Text=ex.Message;
                }
            }
            while(automatic && automaticRecoveryAttempts<MaxAutomaticRecoveryAttempts && !stop.IsCancellationRequested);
            if(!stop.IsCancellationRequested)
                ShowWorkerUnavailable(ErrorText.Text,automatic ? "自动重连失败" : "重连失败");
        }
        catch(OperationCanceledException) when(stop.IsCancellationRequested){}
        finally{LiveReconnectButton.IsEnabled=SubpageReconnectButton.IsEnabled=true;}
    }
    private async Task<JsonElement> RestoreWorkerSnapshot(CancellationToken stop)
    {
        stop.ThrowIfCancellationRequested();
        var retired=bridge;
        bridge=null;
        if(retired!=null)
        {
            try {await retired.DisposeAsync();}
            catch(Exception ex){RecordUiError(ex);}
        }
        stop.ThrowIfCancellationRequested();
        var replacement=new BridgeClient(App.Root,App.Python,App.BridgeArguments.ToArray());
        bridge=replacement;
        var timeout=TimeSpan.FromSeconds(5);
        await replacement.ConnectAsync(timeout);
        stop.ThrowIfCancellationRequested();
        var reply=await replacement.RequestAsync("snapshot",timeoutOverride:timeout);
        stop.ThrowIfCancellationRequested();
        var state=reply.Get("state");
        string selected=HistorySelectionId;
        if(selected.Length>0 && selected!=state.Get("history_selected").Text("battle_id") &&
           state.Get("history").Items().Any(item=>item.Text("battle_id")==selected))
        {
            reply=await replacement.RequestAsync("history_select",new(){["battle_id"]=selected},timeout);
            stop.ThrowIfCancellationRequested();
            state=reply.Get("state");
        }
        JsonElement historyConfig=HistoryRecoveryConfig;
        if(historyConfig.ValueKind==JsonValueKind.Object &&
           state.Get("history_selected").Get("view").Get("config") is {ValueKind:JsonValueKind.Object} restored &&
           !JsonElement.DeepEquals(historyConfig,restored))
        {
            reply=await replacement.RequestAsync("history_config",new(){["config"]=historyConfig},timeout);
            stop.ThrowIfCancellationRequested();
            state=reply.Get("state");
        }
        return state;
    }
    private async void SkinChanged(object sender,SelectionChangedEventArgs e)
    {
        if(applying || !initialized || Skin.SelectedItem is not ComboBoxItem item)return;
        await Command("theme",new(){["theme"]=item.Tag});
    }
    private async void NumberUnitsChanged(object sender, SelectionChangedEventArgs e)
    {
        if (applying || !initialized || NumberUnits.SelectedItem is not ComboBoxItem item) return;
        await Command("number_units", new() { ["number_units"] = item.Tag });
    }
    private void UpdateHistory()
    {
        var selected=State.Get("history_selected");
        var view=selected.Get("view");
        string id=selected.Text("battle_id");
        string units=State.Get("layout").Text("number_units");
        if(historyCacheUnits!=units)
        {
            historyCacheUnits=units;
            historyViews.Clear();
            historyViewOrder.Clear();
        }
        if(id.Length>0 && view.ValueKind==JsonValueKind.Object &&
           historyViewOrder.Last?.Value!=id)
        {
            historyViewOrder.Remove(id);
            if(historyViewOrder.Count>=HistoryCacheLimit)
            {
                historyViews.Remove(historyViewOrder.First!.Value);
                historyViewOrder.RemoveFirst();
            }
            historyViewOrder.AddLast(id);
        }
        if(id.Length>0 && view.ValueKind==JsonValueKind.Object) historyViews[id]=view;
        var records=State.Get("history");
        if(cachedHistorySelection is { } pending &&
           (pending.Id==id && (historySelectionInFlight is null || historySelectionInFlight==id) ||
            (!HistoryCacheOnly && !records.Items().Any(item=>item.Text("battle_id")==pending.Id))))
            cachedHistorySelection=null;
        string signature=records.GetRawText();
        if(signature!=historySignature){historySignature=signature;ApplyHistoryFilter();}
        else if((HistoryList.SelectedItem as HistoryItem)?.Id!=HistorySelectionId &&
                (HistoryList.SelectedItem!=null || HistoryList.Items.Cast<HistoryItem>().Any(item=>item.Id==HistorySelectionId)))
            SelectHistoryListItem();
        if(!HistoryCacheOnly) HistoryNotice.Text=State.Text("history_notice");
        if(cachedHistorySelection is { } cached)
            RenderHistory(cached,historyViews.GetValueOrDefault(cached.Id));
        else RenderHistory(id.Length>0 ? new HistoryItem{Data=selected} : null,view);
        if(cachedHistorySelection is not null && !HistoryCacheOnly && historySelectionInFlight is null)
            _=Dispatcher.InvokeAsync(SyncCachedHistorySelection);
    }
    private void RenderHistory(HistoryItem? item,JsonElement view)
    {
        string id=item?.Id ?? "";
        if(historyBattle!=id)
        {
            historyBattle=id;HistoryInspector.Reset();HistoryPanel.ClearSelection();
        }
        bool available=view.ValueKind==JsonValueKind.Object;
        HistoryPanel.Visibility=HistoryInspector.Visibility=available ? Visibility.Visible : Visibility.Collapsed;
        RefreshHistoryAvailability();
        if(item is null)
        {
            HistorySummary.Text=EmptyHistoryText;
            return;
        }
        HistorySummary.Text=string.Join(" · ","历史",item.Stage,item.Summary,item.Date)+"\n"+
            item.StageDescription+"\n"+(available ? "查看战绩时，实时 HUD 仍跟随当前战斗。"
                : HistoryCacheOnly ? "这场战绩的详情尚未载入，连接恢复后可查看。" : "正在载入这场战绩的详情。");
        if(!available) return;
        HistoryPanel.Update(view);
        HistoryInspector.Update(view);
    }
    private void RefreshHistoryAvailability()
    {
        bool connected=!HistoryCacheOnly;
        HistoryPanel.SetConfigurationAvailable(connected && cachedHistorySelection is null);
        HistoryRefreshButton.IsEnabled=connected;
        ExportJson.IsEnabled=ExportPng.IsEnabled=connected && cachedHistorySelection is null &&
            State.Get("history_selected").Get("record").ValueKind==JsonValueKind.Object;
    }
    private async Task SyncCachedHistorySelection()
    {
        if(historySelectionInFlight is not null) return;
        while(!HistoryCacheOnly && !closingRequested && recoveryTask is not {IsCompleted:false} &&
              cachedHistorySelection is { } desired)
        {
            historySelectionInFlight=desired.Id;
            try{await Command("history_select",new(){["battle_id"]=desired.Id});}
            finally{historySelectionInFlight=null;}
            if(!HistoryCacheOnly && !closingRequested && cachedHistorySelection?.Id==desired.Id)
            {
                cachedHistorySelection=null;
                UpdateHistory();
            }
        }
    }
    private void ApplyHistoryFilter()
    {
        if(HistoryList==null || State.ValueKind!=JsonValueKind.Object)return;
        string query=Search.Text.Trim();string outcome=(Outcome.SelectedItem as ComboBoxItem)?.Content.ToString()??"全部结果";
        string practice=(Practice.SelectedItem as ComboBoxItem)?.Content.ToString()??"全部模式";
        var values=State.Get("history").Items().Select(data=>new HistoryItem{Data=data}).Where(item=>
            (query.Length==0 || item.Data.Text("search").Contains(query,StringComparison.OrdinalIgnoreCase)) &&
            (outcome=="全部结果" || item.Outcome==outcome) && (practice=="全部模式" || item.Practice==practice)).ToArray();
        historyUpdating=true;
        try
        {
            HistoryList.ItemsSource=values;
            HistoryList.SelectedItem=values.FirstOrDefault(item=>item.Id==HistorySelectionId);
        }
        finally{historyUpdating=false;}
    }
    private void SelectHistoryListItem()
    {
        historyUpdating=true;
        try{HistoryList.SelectedItem=HistoryList.Items.Cast<HistoryItem>().FirstOrDefault(item=>item.Id==HistorySelectionId);}
        finally{historyUpdating=false;}
    }
    private void FilterHistory(object sender,RoutedEventArgs e)=>ApplyHistoryFilter();
    private async void SelectHistory(object sender,SelectionChangedEventArgs e)
    {
        if(historyUpdating || HistoryList.SelectedItem is not HistoryItem item) return;
        if(item.Id==State.Get("history_selected").Text("battle_id") && historySelectionInFlight is null)
        {
            cachedHistorySelection=null;
            UpdateHistory();
            return;
        }
        cachedHistorySelection=item;
        RenderHistory(item,historyViews.GetValueOrDefault(item.Id));
        await SyncCachedHistorySelection();
    }
    private async void RefreshHistory(object sender,RoutedEventArgs e)=>await Command("history_refresh");
    private void ToggleHistoryList(object sender,RoutedEventArgs e)
    {
        historyHidden=!historyHidden;HistoryListBorder.Visibility=historyHidden ? Visibility.Collapsed : Visibility.Visible;
        ArrangeHistory();
    }
    private async void Detect(object sender,RoutedEventArgs e)
    {
        if(bridge is not {CanSend:true} source || workerUnavailable ||
           recoveryTask is {IsCompleted:false} || closingRequested) return;
        DetectButton.IsEnabled=false;commands++;
        try
        {
            var reply=await source.RequestAsync("detect",new(){["adb"]=AdbPath.Text.Trim()});
            if(bridge!=source || workerUnavailable || closingRequested) return;
            ApplyState(reply.Get("state"));
            var diag=reply.Get("result");DetectionNote.Text=diag.Text("message")+" "+diag.Text("error");
            Instances.Items.Clear();
            foreach(var instance in diag.Get("instances").Items())
                Instances.Items.Add(new ListBoxItem{Content=instance.Text("model")+" · "+instance.Text("primary_serial")+" · "+instance.Text("problem"),Tag=instance.Text("primary_serial"),IsEnabled=instance.Bool("accessible")&&instance.Bool("certified")});
        }
        catch(BridgeTransportException ex){ReportTransportFailure(source,ex);DetectionNote.Text=Status.Text;}
        catch(Exception ex){DetectionNote.Text=ex.Message;}
        finally{DetectButton.IsEnabled=true;commands--;}
    }
    private void ChooseInstance(object sender,SelectionChangedEventArgs e)
    {
        if(Instances.SelectedItem is ListBoxItem item){Serial.Text=(string)item.Tag;AutoDetect.IsChecked=false;}
    }
    private async void ApplySettings(object sender,RoutedEventArgs e)
    {
        if(closingRequested || quitting) return;
        var config=new{adb_path=AdbPath.Text.Trim(),serial=Serial.Text.Trim(),auto_detect=AutoDetect.IsChecked==true};
        bool events=EventsEnabled.IsChecked==true;
        if(bridge is not {CanSend:true} || workerUnavailable)
        {
            await ReconnectObserver();
            if(workerUnavailable) return;
        }
        await Command("connection_apply",new(){["config"]=config,["events"]=events});
    }
    private async void SaveJson(object sender,RoutedEventArgs e)=>await Export("json");
    private async void SavePng(object sender,RoutedEventArgs e)=>await Export("png");
    private async Task Export(string format)
    {
        if(HistoryCacheOnly || cachedHistorySelection is not null) return;
        var selected=State.Get("history_selected");if(selected.ValueKind!=JsonValueKind.Object)return;
        var frozen=selected.Clone();string theme=Theme.Key;
        byte[]? png=format=="png" ? Screenshot.Statistics(frozen,theme) : null;
        var dialog=new Microsoft.Win32.SaveFileDialog{Title="保存所选战绩",FileName="ArkDPS-历史."+format,Filter=format=="png" ? "PNG 统计截图|*.png" : "JSON 战绩|*.json",AddExtension=true,DefaultExt="."+format,OverwritePrompt=true};
        if(dialog.ShowDialog(this)!=true)return;
        await Command("export",new(){["record"]=frozen.Get("record"),["format"]=format,["path"]=dialog.FileName,["overwrite"]=File.Exists(dialog.FileName),["payload"]=png==null ? null : Convert.ToBase64String(png)});
    }
    private void CreateTray()
    {
        try
        {
            tray=new Forms.NotifyIcon{Text="ArkDPS · 实时统计",Icon=new System.Drawing.Icon(Path.Combine(App.Root,"assets","arkdps.ico")),Visible=true};
            var menu=new Forms.ContextMenuStrip();menu.Items.Add("恢复主界面",null,(_,_)=>Dispatcher.Invoke(ShowGui));
            menu.Items.Add("恢复并解锁全部 HUD",null,(_,_)=>Dispatcher.Invoke(()=>{ShowGui();UnlockAll(this,new RoutedEventArgs());}));
            menu.Items.Add("退出 ArkDPS",null,async(_,_)=>await Dispatcher.InvokeAsync(()=>Quit()).Task.Unwrap());
            tray.ContextMenuStrip=menu;tray.DoubleClick+=(_,_)=>Dispatcher.Invoke(ShowGui);
        }
        catch(Exception ex){tray?.Dispose();tray=null;Footer.Text="托盘不可用；关闭时保留任务栏恢复入口。"+ex.Message;}
    }
    internal void ShowGui(){if(closingRequested || quitting)return;Show();WindowState=WindowState.Normal;UpdateSidePlacement();Activate();}
    private void OnClosing(object? sender,CancelEventArgs e)
    {
        if(quitting)return;e.Cancel=true;ScheduleSave();
        if(tray?.Visible==true)Hide();else WindowState=WindowState.Minimized;
    }
    private async void QuitClick(object sender,RoutedEventArgs e)=>await Quit();
    internal async Task Quit(int code=0)
    {
        if(quitting || closingRequested)return;closingRequested=true;
        recoveryCancellation.Cancel();
        poll.Stop();save.Stop();SideAttachmentTimer.Stop();
        // Exit must never depend on a healthy worker or on a dialog. Every step is
        // bounded and failures are logged; the window closes even if cleanup stalls.
        try
        {
            var cleanup=Release(code);
            if(await Task.WhenAny(cleanup,Task.Delay(TimeSpan.FromSeconds(20)))!=cleanup)
            {
                code=1;WriteQuitLog("退出清理超过 20 秒，强制结束界面");
            }
            else code=await cleanup;
        }
        catch(Exception ex){code=1;WriteQuitLog("退出流程异常："+ex);}
        System.Windows.Application.Current.Shutdown(code);
    }
    private async Task<int> Release(int code)
    {
        if(recoveryTask is {IsCompleted:false} recovery) await recovery;
        if(code==0 && commands==0 && !polling && !workerUnavailable && bridge is {CanSend:true})
        {
            try{await SavePlacement();}catch(Exception ex){code=1;WriteQuitLog("保存布局失败："+ex.Message);}
        }
        quitting=true;
        foreach(var hud in Huds.Values.ToArray()){hud.SuppressClose=true;hud.Close();}Huds.Clear();
        tray?.Dispose();tray=null;Footer.Text="正在释放 Python 采集会话…";
        try{if(bridge!=null)await bridge.DisposeAsync();}
        catch(Exception ex)
        {
            code=1;WriteQuitLog("退出清理未确认："+ex.Message);
            if(App.Verification!=null)
                try{Directory.CreateDirectory(App.Verification);File.WriteAllText(Path.Combine(App.Verification,"cleanup-error.txt"),ex.ToString());}catch(IOException){}
        }
        return code;
    }
    private static void WriteQuitLog(string message)
    {
        try
        {
            string directory=Path.Combine(App.Root,"runtime");Directory.CreateDirectory(directory);
            File.AppendAllText(Path.Combine(directory,"wpf-quit.log"),DateTime.Now.ToString("O")+" "+message+Environment.NewLine);
        }
        catch(IOException){}
        catch(UnauthorizedAccessException){}
    }
}
