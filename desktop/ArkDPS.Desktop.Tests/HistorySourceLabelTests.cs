using System;
using System.Text.Json;
using ArkDPS.Desktop;

internal static class HistorySourceLabelTests
{
    private static int passed;
    private static void Check(string name, bool condition)
    {
        if (!condition) throw new InvalidOperationException(name);
        passed++;
    }

    private static HistoryItem Item(string? platform = null, string? sourceLabel = null) => new()
    {
        Data = JsonSerializer.SerializeToElement(new
        {
            battle_id = "headless-source", stage = "0-1", outcome = "胜利", duration = "71.3 秒",
            platform, source_label = sourceLabel,
            labels = new[] { "0-1", "演习", "胜利", "71.3 秒", "最后观察", sourceLabel ?? "来源未记录" },
        }),
    };

    internal static void Run()
    {
        foreach (var (platform, label) in new[] { ("native_pc", "官服 PC"), ("android", "Android（ADB）") })
        {
            var item = Item(platform, label);
            Check(platform + " shared source label reaches summary", item.SourceLabel == label &&
                item.Summary == label + " · 胜利 · 71.3 秒");
            Check(platform + " preserves practice label position", item.Practice == "演习");
            Check(platform + " older worker uses explicit route fallback", Item(platform).SourceLabel == label);
        }
        var legacy = Item();
        Check("legacy record never defaults to current source", legacy.SourceLabel == "来源未记录" &&
            legacy.Summary.StartsWith("来源未记录 · "));
        Check("legacy practice filter position unchanged", legacy.Practice == "演习");
        Check("unrecognised platform stays unknown", Item("unknown").SourceLabel == "来源未记录");
        Check("shared projection is authoritative", Item("native_pc", "共享来源提示").SourceLabel == "共享来源提示");
        Console.WriteLine($"PASS_HISTORY_SOURCE_LABEL {passed} assertions (loaded projection only; no windows/input)");
    }
}
