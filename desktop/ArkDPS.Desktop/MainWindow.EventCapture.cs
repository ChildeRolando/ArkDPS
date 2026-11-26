using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

namespace ArkDPS.Desktop;

public partial class MainWindow
{
    private bool eventCaptureCommandPending;
    private bool eventCaptureRequestedPause;
    private string eventCaptureRequestError = "";
    private double eventCaptureErrorRevision;
    private BridgeClient? eventCaptureErrorSource;
    private const string EventCaptureScope = "累计快照与FPS继续采集；暂停期间的逐次事件无法补采。";

    private void SetEventCaptureRequestError(string error)
    {
        eventCaptureRequestError = error;
        eventCaptureErrorRevision = State.Get("event_capture_control").Number("revision");
        eventCaptureErrorSource = bridge;
        UpdateEventCaptureControl();
    }

    private void UpdateEventCaptureControl()
    {
        var control = State.Get("event_capture_control");
        if (eventCaptureRequestError.Length > 0 && (eventCaptureErrorSource != bridge ||
            control.Number("revision") > eventCaptureErrorRevision))
            eventCaptureRequestError = "";

        bool pausedKnown = control.Get("paused").ValueKind is JsonValueKind.True or JsonValueKind.False;
        bool requestedKnown = control.Get("requested_paused").ValueKind is JsonValueKind.True or JsonValueKind.False;
        bool paused = control.Bool("paused");
        bool supported = control.Bool("supported");
        string phase = control.Text("state");
        string reason = control.Text("error");
        if (reason.Length == 0) reason = control.Text("reason");
        bool pending = phase is "pausing" or "resuming" || eventCaptureCommandPending ||
            supported && pausedKnown && requestedKnown && (phase is "running" or "paused") &&
            control.Bool("requested_paused") != paused;
        bool requestingPause = eventCaptureCommandPending ? eventCaptureRequestedPause
            : phase == "pausing" || phase != "resuming" && control.Bool("requested_paused");
        bool confirmed = pausedKnown && (phase == "running" && !paused || phase == "paused" && paused || phase == "error");
        bool connected = !workerUnavailable && !(responseStale && !applying);
        bool enabled = supported && confirmed && !pending && connected &&
            !closingRequested && !quitting && !updateShutdownPreparing && recoveryTask is not { IsCompleted:false };
        string action = paused ? "继续事件" : "暂停事件";
        string status;
        string detail;
        bool failed = false;

        if (!connected)
        {
            status = "事件状态未确认 · 请重新连接";
            detail = "采集连接不可用，恢复连接后再操作。";
        }
        else if (!supported || phase == "unavailable")
        {
            enabled = false;
            detail = reason.Length > 0 ? reason : "当前连接未提供事件暂停能力。";
            status = "事件暂停不可用 · " + detail;
        }
        else if (pending)
        {
            action = requestingPause ? "正在暂停…" : "正在继续…";
            status = "等待采集端确认";
            detail = (requestingPause ? "正在请求暂停逐次事件采集。" : "正在请求继续逐次事件采集。") + EventCaptureScope;
        }
        else if (!confirmed)
        {
            enabled = false;
            status = "等待事件采集状态";
            detail = reason.Length > 0 ? reason : "尚未确认采集端实际状态，请等待下一次更新。";
        }
        else if (phase == "error" || eventCaptureRequestError.Length > 0)
        {
            failed = true;
            string error = eventCaptureRequestError.Length > 0 ? eventCaptureRequestError : reason;
            detail = error.Length > 0 ? error : "采集端未能完成操作，请重试。";
            status = "事件操作失败 · " + detail;
        }
        else
        {
            status = paused ? "事件已暂停 · 快照与FPS继续" : "逐次事件采集中";
            detail = (paused ? "继续本局的逐次事件采集。" : "暂停本局的逐次事件采集。") + EventCaptureScope;
        }

        if (failed) action = paused ? "重试继续" : "重试暂停";
        SideEventCaptureButton.Content = action;
        SideEventCaptureButton.IsEnabled = enabled;
        SideEventCaptureButton.ToolTip = status + "\n" + detail;
        AutomationProperties.SetName(SideEventCaptureButton, pending
            ? requestingPause ? "正在暂停逐次事件采集" : "正在继续逐次事件采集"
            : (failed ? "重试" : "") + (paused ? "继续逐次事件采集" : "暂停逐次事件采集"));
        AutomationProperties.SetItemStatus(SideEventCaptureButton, status);
        AutomationProperties.SetHelpText(SideEventCaptureButton, status + "\n" + detail);
        SideEventCaptureButton.SetResourceReference(Control.ForegroundProperty, failed ? "Danger" : "Ink");
    }

    private async void ToggleEventCapture(object sender, RoutedEventArgs e) => await RequestEventCaptureToggle();

    private async Task RequestEventCaptureToggle()
    {
        if (!SideEventCaptureButton.IsEnabled || eventCaptureCommandPending) return;
        if (bridge is not { CanSend:true })
        {
            SetEventCaptureRequestError("采集连接不可用，恢复连接后再试。");
            return;
        }
        eventCaptureRequestedPause = !State.Get("event_capture_control").Bool("paused");
        eventCaptureRequestError = "";
        eventCaptureCommandPending = true;
        UpdateEventCaptureControl();
        try { await Command("event_pause", new Dictionary<string, object?> { ["paused"] = eventCaptureRequestedPause }); }
        finally
        {
            eventCaptureCommandPending = false;
            UpdateEventCaptureControl();
        }
    }
}
