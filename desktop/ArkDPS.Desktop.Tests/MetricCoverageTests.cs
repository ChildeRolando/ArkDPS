using System;
using System.Text.Json;
using ArkDPS.Desktop;

internal static class MetricCoverageTests
{
    internal static void Run()
    {
        int assertions=0;
        void Check(bool condition,string message)
        {
            if(!condition) throw new InvalidOperationException(message);
            assertions++;
        }

        var row=new RowItem();
        using var cumulative=JsonDocument.Parse("""
            {"name":"协防干员","coverage":{
                "observed_damage":{"failed_share":null,"basis":null,"reason":"结算事件覆盖不完整"}}}
            """);
        row.Entry=cumulative.RootElement;
        Check(row.CoverageMarker=="*" && row.Tooltip.Contains("已观察伤害 结算事件覆盖不完整"),
            "event-only nominal damage lost its partial-coverage marker");
        var coverage=cumulative.RootElement.Get("coverage");
        Check(!MetricCoverage.HasHitCoverage(coverage) && MetricCoverage.HasObservedDamageCoverage(coverage) &&
              MetricCoverage.Summary(coverage,shortForm:true)=="部分统计 · 已观察伤害 结算事件覆盖不完整" &&
              !row.Tooltip.Contains("统计失败占比"),
            "a scope-only explanation must not advertise a hit share or a failed-damage percentage");

        using var recent=JsonDocument.Parse("""
            {"name":"协防干员","coverage":{"observed_recent_damage":{"failed_share":null}}}
            """);
        row.Entry=recent.RootElement;
        Check(row.CoverageMarker=="*" && row.Tooltip.Contains("近 5 秒已观察伤害 覆盖不完整") &&
              !row.Tooltip.Contains("结算事件覆盖不完整"),
            "switching the metric must replace the previous cumulative explanation");

        using var complete=JsonDocument.Parse("""{"name":"协防干员","coverage":{}}""");
        row.Entry=complete.RootElement;
        Check(row.CoverageMarker.Length==0 && !row.Tooltip.Contains("覆盖不完整"),
            "a newer complete row must clear the marker and the prior explanation");
        Console.WriteLine($"PASS_METRIC_COVERAGE {assertions} assertions (headless row presentation)");
    }
}
