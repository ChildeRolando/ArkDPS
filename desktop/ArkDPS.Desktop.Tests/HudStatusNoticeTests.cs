using System;
using System.Windows;
using System.Windows.Automation;
using ArkDPS.Desktop;

internal static class HudStatusNoticeTests
{
    private static int passed;

    private static void Check(string name, bool condition)
    {
        if (!condition) throw new InvalidOperationException(name);
        passed++;
    }

    internal static void Run()
    {
        var notice = new HudStatusNotice();
        Check("status notice never takes pointer or keyboard input", !notice.IsHitTestVisible && !notice.Focusable);
        notice.Update(new HudDataStatus("live", "PC 版 · 累计统计", false, false));
        notice.Measure(new Size(300, double.PositiveInfinity));
        Check("normal native live has no added layout", notice.Visibility == Visibility.Collapsed &&
            notice.DesiredSize.Height == 0 && notice.Message.Length == 0);
        notice.Update(new HudDataStatus("live", "观察中 · 战斗中", false, false));
        Check("normal MuMu live stays unchanged", notice.Visibility == Visibility.Collapsed);
        notice.Update(new HudDataStatus("live", "重连字样不是状态判断依据", false, false));
        Check("classification uses machine state rather than label words", notice.Visibility == Visibility.Collapsed);
        foreach (string status in new[] { "paused", "ended", "waiting", "degraded" })
        {
            notice.Update(new HudDataStatus(status, "正常状态", false, false));
            Check(status + " keeps the normal HUD layout", notice.Visibility == Visibility.Collapsed);
        }

        foreach (string status in new[] { "stale", "disconnected", "reconnecting", "locating", "frozen", "preparing" })
        {
            notice.Update(new HudDataStatus(status, "受控状态 · 保留最后有效记录", false, false));
            Check(status + " displays shared status", notice.Visibility == Visibility.Visible &&
                notice.Message == "受控状态 · 保留最后有效记录");
        }
        notice.Measure(new Size(300, double.PositiveInfinity));
        Check("warning reserves visible readable space", notice.DesiredSize.Height > 0);
        Check("warning is accessible", AutomationProperties.GetName(notice) == notice.Message);
        Check("unchanged warning does not request repeated window layout", !notice.Update(
            new HudDataStatus("locating", "受控状态 · 保留最后有效记录", false, false)));

        notice.Update(new HudDataStatus("live", "响应超时 · 保留最后有效记录", false, true));
        Check("transport stale overrides the last live worker code", notice.Visibility == Visibility.Visible &&
            notice.Message.StartsWith("响应超时"));
        notice.Update(new HudDataStatus("waiting", "上次快照 · 等待进入战斗", true, false));
        Check("healthy lobby still identifies retained rows", notice.Visibility == Visibility.Visible &&
            notice.Message == "上次快照 · 等待进入战斗");
        notice.Update(new HudDataStatus("waiting", "", true, false));
        Check("retained rows have a fallback warning", notice.Message == "上次快照 · 等待当前战局");
        notice.Update(new HudDataStatus("disconnected", "", false, false));
        Check("missing worker label does not hide disconnected state", notice.Message.Contains("保留最后有效记录"));
        notice.Update(new HudDataStatus("live", "PC 版 · 累计统计", false, false));
        notice.Measure(new Size(300, double.PositiveInfinity));
        Check("fresh battle removes warning and its entire layout space", notice.Visibility == Visibility.Collapsed &&
            notice.DesiredSize.Height == 0 && notice.Message.Length == 0);
        Console.WriteLine($"PASS_HUD_STATUS_NOTICE {passed} assertions (WPF control only; no windows/input/target access)");
    }
}
