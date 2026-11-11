using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using Forms=System.Windows.Forms;

namespace ArkDPS.Desktop;

internal sealed class HistoryItem
{
    public JsonElement Data {get;init;}
    public string Id=>Data.Text("battle_id");
    private string[] Labels=>Data.Get("labels").Items().Select(x=>x.Text()).ToArray();
    public string Stage=>Labels.ElementAtOrDefault(0)??"关卡未知";
    public string StageDetail=>Data.Text("stage_detail");
    public string Summary=>string.Join(" · ",Labels.Skip(1).Take(3));
    public string Date=>Labels.LastOrDefault()??"";
}

public partial class MainWindow : Window
{
    private BridgeClient? bridge;
    private const string EmptyHistoryText="暂无可读战绩；完成对局归档后自动出现。";
    private readonly DispatcherTimer poll=new(){Interval=TimeSpan.FromMilliseconds(200)};
    private readonly DispatcherTimer save=new(){Interval=TimeSpan.FromMilliseconds(600)};
    internal readonly Dictionary<string,StatisticPanel> Panels=new();
    internal readonly Dictionary<string,HudWindow> Huds=new();
    internal JsonElement State;
    private Forms.NotifyIcon? tray;
    private bool initialized,applying,polling,quitting,historyUpdating,historyHidden;
    private object? normalGeometry;
    private bool closingRequested;
    private long lastResponse=System.Diagnostics.Stopwatch.GetTimestamp();
    private bool responseStale;
    private int commands;
    private string activePanel="",lastBattle="",historyBattle="",historySignature="";
    private string route="live";
    public MainWindow()
    {
        InitializeComponent();
        SetNavState("live");
        HistoryPanel.IsHistory=true;HistoryPanel.Command=Command;HistoryPanel.RateActionRequested=HandleRateAction;
        HistoryPanel.PinRequested=id=>HistoryInspector.Pin(id,HistoryPanel.View);
        Outcome.SelectedIndex=0;Practice.SelectedIndex=0;
        poll.Tick+=async(_,_)=>await Poll();
        save.Tick+=async(_,_)=>{save.Stop();await SavePlacement();};
        Loaded+=async(_,_)=>await Start();
        LocationChanged+=(_,_)=>ScheduleSave();
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
            Lost(ex.Message);
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
        if(initialized && !responseStale && System.Diagnostics.Stopwatch.GetElapsedTime(lastResponse)>TimeSpan.FromSeconds(2))
        {
            responseStale=true;Status.Text="数据已过期 · 等待 worker 响应 · 保留最后有效记录";
            foreach(var hud in Huds.Values)if(Panels.TryGetValue(hud.PanelId,out var panel))hud.Update(panel.View,Status.Text);
        }
        if(polling || commands>0 || quitting || bridge==null) return;
        polling=true;
        try {var reply=await bridge.RequestAsync("snapshot");ApplyState(reply.Get("state"));}
        catch(Exception ex){Lost(ex.Message);}
        finally{polling=false;}
    }
    internal async Task Command(string op,Dictionary<string,object?>? fields=null)
    {
        if(bridge==null || quitting) return;
        commands++;
        try
        {
            var reply=await bridge.RequestAsync(op,fields);ApplyState(reply.Get("state"));
            if(reply.Get("result").ValueKind==JsonValueKind.String) Footer.Text=reply.Text("result");
        }
        // Never raise a modal dialog once shutdown has begun: it would block the close.
        catch(BridgeCommandException ex){if(App.Verification!=null)throw;if(!closingRequested)MessageBox.Show(this,ex.Message,"操作未完成");else WriteQuitLog("命令失败："+ex.Message);}
        catch(Exception ex){Lost(ex.Message);if(App.Verification==null&&!closingRequested) MessageBox.Show(this,ex.Message,"采集进程连接中断");}
        finally{commands--;}
    }
    private void Lost(string message)
    {
        poll.Stop();Status.Text="采集进程断开 · 数值为最后有效记录";ErrorText.Text=message;
        Footer.Text="请检查日志并退出后重新启动；未将旧数值冒充实时数据。";
        foreach(var hud in Huds.Values) if(Panels.TryGetValue(hud.PanelId,out var panel)) hud.Update(panel.View,Status.Text);
    }
    internal void ApplyState(JsonElement state)
    {
        if(state.Number("protocol")!=BridgeClient.Version) throw new IOException("不支持的统计消息版本");
        lastResponse=System.Diagnostics.Stopwatch.GetTimestamp();responseStale=false;
        applying=true;State=state;
        var layout=state.Get("layout");string theme=layout.Text("theme");
        bool changed=Theme.Key!=theme;if(changed){Theme.Apply(theme);SetNavState(route);}
        Skin.SelectedIndex=theme=="original" ? 1 : 0;
        Status.Text=state.Text("status_text");EventStatus.Text=state.Text("event_text");
        ErrorText.Text=string.Join(" · ",state.Get("warnings").Items().Select(x=>x.Text()).Append(state.Text("error")).Where(x=>x.Length>0));
        Stage.Text=state.Text("stage").Length>0 ? state.Text("stage") : "关卡未知";
        Stage.ToolTip=state.Text("stage_detail").Length>0 ? state.Text("stage_detail") : null;
        var seconds=state.Get("game_time");double t=seconds.TryNumber();Clock.Text=seconds.ValueKind==JsonValueKind.Number ? $"游戏时间 {(int)t/60:00}:{t%60:00.0}" : "游戏时间 —";
        Footer.Text=state.Text("connection_text");
        if(App.BridgeArguments.Contains("--offline")) ModeLabel.Text="离线验收 · 合成数据 · 不访问游戏";
        var views=state.Get("panels").Items().ToArray();var ids=views.Select(v=>v.Get("config").Text("panel_id")).ToHashSet();
        foreach(string id in Panels.Keys.Where(x=>!ids.Contains(x)).ToArray())
        {
            PanelHost.Children.Remove(Panels[id]);Panels.Remove(id);CloseHud(id);
            if(activePanel==id){activePanel="";LiveInspector.Reset();}
        }
        string battle=state.Text("battle_id");
        if(battle!=lastBattle){lastBattle=battle;LiveInspector.Reset();foreach(var panel in Panels.Values)panel.ClearSelection();}
        foreach(var view in views)
        {
            string id=view.Get("config").Text("panel_id");
            if(!Panels.TryGetValue(id,out var panel))
            {
                panel=new StatisticPanel{Command=Command,HudRequested=id=>OpenHud(id),
                    LockRequested=ToggleLock,AlphaRequested=ChangeAlpha,RateActionRequested=HandleRateAction};
                panel.PinRequested=owner=>{activePanel=id;LiveInspector.Pin(owner,Panels[id].View);};
                Panels.Add(id,panel);PanelHost.Children.Add(panel);
            }
            panel.Update(view,views.Length>1);
            if(Huds.TryGetValue(id,out var hud)) {hud.Update(view,Status.Text+" · "+EventStatus.Text);panel.HudState(true,hud.Locked,hud.Opacity);}
            else panel.HudState(false,false,.9);
            if(activePanel==id) LiveInspector.Update(view);
        }
        if(!initialized)
        {
            Native.Restore(this,layout.Get("gui"));
            foreach(var placement in layout.Get("huds").Items()) OpenHud(placement.Text("panel_id"),placement);
            InitializeSettings();initialized=true;normalGeometry=Native.Geometry(this);
        }
        UpdateHistory();
        var diag=state.Get("diagnostic");
        ConnectionDiagnostics.Text=state.Text("connection_text")+"\n"+state.Text("event_text")+"\n"+
            "版本 "+diag.Text("version_actual")+" / 认证 "+diag.Text("version_expected")+"\n"+diag.Text("message")+"\n"+diag.Text("error");
        applying=false;ArrangePanels();
        if(changed){InvalidateVisualTree(this);foreach(var hud in Huds.Values)InvalidateVisualTree(hud);}
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
    private void OnResize(object sender,SizeChangedEventArgs e){ArrangePanels();ScheduleSave();}
    private void ArrangePanels()
    {
        if(PanelHost==null || PanelScroll==null) return;
        bool narrow=ActualWidth<1000;
        DockPanel.SetDock(InspectorScroll,narrow ? Dock.Bottom : Dock.Right);
        InspectorScroll.Width=narrow ? double.NaN : 300;InspectorScroll.MaxHeight=narrow ? 240 : double.PositiveInfinity;
        double width=PanelScroll.ViewportWidth;
        if(width<100) width=Math.Max(350,ActualWidth-(narrow ? 72 : 390));
        int columns=Math.Max(1,(int)(width/385));
        foreach(var panel in Panels.Values)panel.Width=Math.Max(350,Math.Floor(width/columns));
        if(ActualWidth<850 && !historyHidden){HistoryListColumn.Width=new GridLength(210);}else if(!historyHidden) HistoryListColumn.Width=new GridLength(285);
    }
    private void ScheduleSave(){if(initialized && !applying && !quitting){if(IsVisible && WindowState==WindowState.Normal)normalGeometry=Native.Geometry(this);save.Stop();save.Start();}}
    internal async Task SavePlacement()
    {
        if(!initialized || bridge==null) return;
        if(IsVisible && WindowState==WindowState.Normal)normalGeometry=Native.Geometry(this);
        await Command("placement",new(){["gui"]=normalGeometry??Native.Geometry(this),["huds"]=Huds.Values.Select(h=>h.Placement()).ToArray()});
    }
    internal void OpenHud(string id,JsonElement placement=default)
    {
        if(!Panels.ContainsKey(id)) return;
        if(Huds.TryGetValue(id,out var existing)){if(!existing.Locked)existing.Activate();return;}
        var hud=new HudWindow(id,ShowGui);hud.Panel.RateActionRequested=HandleRateAction;Huds.Add(id,hud);
        hud.PlacementChanged+=()=>{SyncHudControls(id);ScheduleSave();};
        hud.ClosedByUser+=()=>{Huds.Remove(id);SyncHudControls(id);if(Huds.Count==0 && (!IsVisible || WindowState==WindowState.Minimized))ShowGui();ScheduleSave();};
        hud.Show();
        if(placement.ValueKind==JsonValueKind.Object){Native.Restore(hud,placement);hud.SetOpacity(placement.Number("opacity",.9));hud.SetLocked(placement.Bool("locked"));}
        hud.Update(Panels[id].View,Status.Text+" · "+EventStatus.Text);SyncHudControls(id);ScheduleSave();
    }
    private void CloseHud(string id)
    {
        if(!Huds.Remove(id,out var hud))return;hud.SuppressClose=true;hud.Close();
    }
    private void SyncHudControls(string id)
    {
        if(!Panels.TryGetValue(id,out var panel))return;
        bool open=Huds.TryGetValue(id,out var hud);panel.HudState(open,hud?.Locked??false,hud?.Opacity??.9);
    }
    private void ToggleLock(string id)
    {
        if(!Huds.TryGetValue(id,out var hud))return;
        try{hud.SetLocked(!hud.Locked);SyncHudControls(id);ScheduleSave();}
        catch(Exception ex){MessageBox.Show(this,ex.Message,"HUD 锁定失败");}
    }
    private void ChangeAlpha(string id,double alpha){if(Huds.TryGetValue(id,out var hud)){hud.SetOpacity(alpha);ScheduleSave();}}
    private void UnlockAll(object sender,RoutedEventArgs e)
    {
        foreach(string id in Huds.Keys.ToArray())if(Huds[id].Locked)ToggleLock(id);
    }
    private async void Navigate(object sender,RoutedEventArgs e)=>await Navigate((string)((Button)sender).Tag);
    internal async Task Navigate(string page)
    {
        route=page;LivePage.Visibility=page=="live" ? Visibility.Visible : Visibility.Collapsed;
        HistoryPage.Visibility=page=="history" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPage.Visibility=page=="settings" ? Visibility.Visible : Visibility.Collapsed;
        PageTitle.Text=page switch{"history"=>"历史战绩","settings"=>"连接设置",_=>"本局统计"};
        PageSubtitle.Text=page switch{"history"=>"本地归档 · 搜索、导出与分享","settings"=>"连接、事件观测与诊断",_=>"实时贡献、有效伤害与过量拆分"};
        SetNavState(page);
        AddPanelButton.Visibility=page=="live" ? Visibility.Visible : Visibility.Collapsed;ArrangePanels();
        // The list is a pull cache owned by Python: re-read it on every entry so a
        // battle finished a moment ago is visible without pressing 刷新.
        if(page=="history") await Command("history_refresh");
    }
    private void SetNavState(string page)
    {
        foreach(var (button,tag) in new[]{(LiveNav,"live"),(HistoryNav,"history"),(SettingsNav,"settings")})
        {
            bool active=tag==page;
            button.Background=active?Theme.Resource("Accent"):Theme.Resource("ConsoleCard");
            button.Foreground=active?Theme.Resource("AccentInk"):Theme.Resource("ConsoleInk");
            button.BorderBrush=active?Theme.Resource("Accent"):Theme.Resource("ConsoleLine");
        }
    }
    private async void AddPanel(object sender,RoutedEventArgs e)=>await Command("panel_add");
    // 事件不可用时的恢复入口：重试会重新连接采集（不重启应用）；否则打开连接设置。
    private async void HandleRateAction(string action)
    {
        if(action=="retry"){ShowGui();Navigate("live");await Command("rescan");}
        else if(action=="settings"){ShowGui();Navigate("settings");}
    }
    private async void Rescan(object sender,RoutedEventArgs e)=>await Command("rescan");
    private async void SkinChanged(object sender,SelectionChangedEventArgs e)
    {
        if(applying || !initialized || Skin.SelectedItem is not ComboBoxItem item)return;
        await Command("theme",new(){["theme"]=item.Tag});
    }
    private void UpdateHistory()
    {
        var records=State.Get("history");string signature=records.GetRawText();
        if(signature!=historySignature){historySignature=signature;ApplyHistoryFilter();}
        var selected=State.Get("history_selected");
        bool available=selected.ValueKind==JsonValueKind.Object;ExportJson.IsEnabled=ExportPng.IsEnabled=available;
        HistoryNotice.Text=State.Text("history_notice");
        if(!available)
        {
            // No readable record: show the empty state instead of a stale detail.
            if(historyBattle.Length>0)
            {
                historyBattle="";HistoryInspector.Reset();HistoryPanel.ClearSelection();
                HistorySummary.Text=EmptyHistoryText;
            }
            return;
        }
        if(historyBattle!=selected.Text("battle_id")){historyBattle=selected.Text("battle_id");HistoryInspector.Reset();HistoryPanel.ClearSelection();}
        HistorySummary.Text="历史 · "+string.Join(" · ",selected.Get("labels").Items().Select(x=>x.Text()))+"\n"+
            selected.Text("stage_detail")+"\n所选副本独立于实时战斗；实时 HUD 继续跟随新局。";
        HistoryPanel.Update(selected.Get("view"));HistoryInspector.Update(selected.Get("view").Get("source"));
    }
    private void ApplyHistoryFilter()
    {
        if(HistoryList==null || State.ValueKind!=JsonValueKind.Object)return;
        historyUpdating=true;
        string query=Search.Text.Trim();string outcome=(Outcome.SelectedItem as ComboBoxItem)?.Content.ToString()??"全部结果";
        string practice=(Practice.SelectedItem as ComboBoxItem)?.Content.ToString()??"全部模式";
        var values=State.Get("history").Items().Where(x=>
        {
            var labels=x.Get("labels").Items().Select(y=>y.Text()).ToArray();
            return (query.Length==0 || x.Text("search").Contains(query,StringComparison.OrdinalIgnoreCase)) &&
                (outcome=="全部结果" || labels.ElementAtOrDefault(2)==outcome) && (practice=="全部模式" || labels.ElementAtOrDefault(1)==practice);
        }).Select(x=>new HistoryItem{Data=x}).ToArray();
        HistoryList.ItemsSource=values;string selected=State.Get("history_selected").Text("battle_id");
        HistoryList.SelectedItem=values.FirstOrDefault(x=>x.Id==selected);historyUpdating=false;
    }
    private void FilterHistory(object sender,RoutedEventArgs e)=>ApplyHistoryFilter();
    private async void SelectHistory(object sender,SelectionChangedEventArgs e)
    {
        if(!historyUpdating && HistoryList.SelectedItem is HistoryItem item)await Command("history_select",new(){["battle_id"]=item.Id});
    }
    private async void RefreshHistory(object sender,RoutedEventArgs e)=>await Command("history_refresh");
    private void ToggleHistoryList(object sender,RoutedEventArgs e)
    {
        historyHidden=!historyHidden;HistoryListBorder.Visibility=historyHidden ? Visibility.Collapsed : Visibility.Visible;
        HistoryListColumn.Width=new GridLength(historyHidden ? 0 : 285);
    }
    private async void Detect(object sender,RoutedEventArgs e)
    {
        if(bridge==null)return;DetectButton.IsEnabled=false;commands++;
        try
        {
            var reply=await bridge.RequestAsync("detect",new(){["adb"]=AdbPath.Text.Trim()});ApplyState(reply.Get("state"));
            var diag=reply.Get("result");DetectionNote.Text=diag.Text("message")+" "+diag.Text("error");
            Instances.Items.Clear();
            foreach(var instance in diag.Get("instances").Items())
                Instances.Items.Add(new ListBoxItem{Content=instance.Text("model")+" · "+instance.Text("primary_serial")+" · "+instance.Text("problem"),Tag=instance.Text("primary_serial"),IsEnabled=instance.Bool("accessible")&&instance.Bool("certified")});
        }
        catch(Exception ex){DetectionNote.Text=ex.Message;}
        finally{DetectButton.IsEnabled=true;commands--;}
    }
    private void ChooseInstance(object sender,SelectionChangedEventArgs e)
    {
        if(Instances.SelectedItem is ListBoxItem item){Serial.Text=(string)item.Tag;AutoDetect.IsChecked=false;}
    }
    private async void ApplySettings(object sender,RoutedEventArgs e)
    {
        await Command("connection_apply",new(){["config"]=new{adb_path=AdbPath.Text.Trim(),serial=Serial.Text.Trim(),auto_detect=AutoDetect.IsChecked==true},["events"]=EventsEnabled.IsChecked==true});
    }
    private async void SaveJson(object sender,RoutedEventArgs e)=>await Export("json");
    private async void SavePng(object sender,RoutedEventArgs e)=>await Export("png");
    private async Task Export(string format)
    {
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
    internal void ShowGui(){Show();WindowState=WindowState.Normal;Activate();}
    private void OnClosing(object? sender,CancelEventArgs e)
    {
        if(quitting)return;e.Cancel=true;ScheduleSave();
        if(tray?.Visible==true)Hide();else WindowState=WindowState.Minimized;
    }
    private async void QuitClick(object sender,RoutedEventArgs e)=>await Quit();
    internal async Task Quit(int code=0)
    {
        if(quitting || closingRequested)return;closingRequested=true;
        poll.Stop();save.Stop();
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
        if(code==0)
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
