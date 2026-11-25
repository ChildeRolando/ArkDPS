using System;
using System.Globalization;
using System.Text.Json;
using System.Windows.Threading;

namespace ArkDPS.Desktop;

public partial class MainWindow
{
    private const string UnknownFps = "FPS —";
    private DispatcherTimer? gameFpsExpiry;
    private (string Source, string Pid, string Created, string Serial, string Window)? gameFpsSample;

    private static string FpsSourceLabel(string source) => source switch
    {
        "dxgi_present" => "PC 游戏 · DXGI 画面提交",
        "surfaceflinger" => "MuMu · SurfaceFlinger 游戏图层",
        _ => "游戏帧率"
    };

    private void UpdateGameFpsStatus(JsonElement state)
    {
        var sample = state.Get("game_fps");
        string status = sample.Text("status");
        string source = sample.Text("source");
        string sourceLabel = FpsSourceLabel(source);
        string logError = sample.Text("log_error");
        string logWarning = logError.Length == 0 ? "" : $"\n帧率日志写入失败 · {logError}";
        double fps = sample.Number("fps", double.NaN);
        if (status != "ok" || !double.IsFinite(fps) || fps < 0)
        {
            StopGameFpsStatus();
            string reason = status switch
            {
                "waiting" => "等待游戏窗口或图层",
                "warming" => "正在测量",
                "lost" => "帧事件采集不完整",
                "error" => "帧率采样失败",
                _ => "暂不可用"
            };
            string error = sample.Text("error_code");
            if (error.Length > 0) reason += $"（{error}）";
            SetGameFpsDisplay(UnknownFps, $"{sourceLabel} · {reason}{logWarning}");
            return;
        }

        string endUtc = sample.Text("window_end_utc");
        string sequence = sample.Text("sample_seq");
        string window = sequence.Length == 0 ? endUtc : sequence;
        var identity = (source, sample.Text("pid"), sample.Text("process_created_filetime"), sample.Text("serial"), window);
        string tooltip = $"{sourceLabel} · 已完成的 1 秒窗口{logWarning}";
        if (gameFpsSample == identity)
        {
            if (gameFpsExpiry?.IsEnabled == true) SetGameFpsTooltip(tooltip);
            return;
        }
        string observedUtc = sample.Text("observed_at_utc");
        string timestamp = observedUtc.Length == 0 ? endUtc : observedUtc;
        // Native ETW delivery can lag the frame window. Android's guest clock
        // has no UTC mapping; both sources can report their host publish time.
        if (window.Length == 0 || (timestamp.Length > 0 &&
            (!DateTimeOffset.TryParse(timestamp, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var observed) ||
             DateTimeOffset.UtcNow - observed > TimeSpan.FromSeconds(5))))
        {
            StopGameFpsStatus();
            SetGameFpsDisplay(UnknownFps, $"{sourceLabel} · 采样时间缺失或已过期{logWarning}");
            return;
        }
        gameFpsSample = identity;
        SetGameFpsDisplay("FPS " + fps.ToString("0.#", CultureInfo.InvariantCulture), tooltip);

        if (gameFpsExpiry is null)
        {
            gameFpsExpiry = new DispatcherTimer { Interval = TimeSpan.FromSeconds(3) };
            gameFpsExpiry.Tick += ExpireGameFpsStatus;
        }
        gameFpsExpiry.Stop();
        gameFpsExpiry.Start();
    }

    private void SetGameFpsDisplay(string text, string tooltip)
    {
        if (MainGameFpsStatus.Text != text) MainGameFpsStatus.Text = SideGameFpsStatus.Text = text;
        SetGameFpsTooltip(tooltip);
    }

    private void SetGameFpsTooltip(string tooltip)
    {
        if (MainGameFpsStatus.ToolTip as string == tooltip) return;
        MainGameFpsStatus.ToolTip = SideGameFpsStatus.ToolTip = tooltip;
    }

    private void ExpireGameFpsStatus(object? sender, EventArgs args)
    {
        gameFpsExpiry?.Stop();
        // An identical worker reply must not reactivate an expired reading.
        SetGameFpsDisplay(UnknownFps, $"{FpsSourceLabel(gameFpsSample?.Source ?? "")} · 采样已过期");
    }

    private void StopGameFpsStatus()
    {
        gameFpsExpiry?.Stop();
        gameFpsSample = null;
    }
}
