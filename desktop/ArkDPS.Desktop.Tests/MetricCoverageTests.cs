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
        Check(!MetricCoverage.HasHitCoverage(coverage) && MetricCoverage.HasScopeCoverage(coverage) &&
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

        using var scopeGap=JsonDocument.Parse("""
            {"name":"水灯心","coverage":{"effective_damage":{
                "basis_kind":"scope","failed_share":null,"basis":null,"reason":"结算事件覆盖不完整"}},
                "detail":{"coverage":{"skill_damage":{
                "basis_kind":"scope","failed_share":null,"basis":null,"reason":"未观察到技能效果的实际结束"}}}}
            """);
        row.Entry=scopeGap.RootElement;
        Check(row.CoverageMarker=="*" && row.Tooltip.Contains("有效伤害 结算事件覆盖不完整") &&
              !row.Tooltip.Contains("未核实命中") && !row.Tooltip.Contains("统计失败占比"),
            "a valid covered subtotal with an unfinished range still has a range marker");
        var detail=scopeGap.RootElement.Get("detail").Get("coverage");
        Check(MetricCoverage.Summary(detail,shortForm:true)=="部分统计 · 技能期伤害 未观察到技能效果的实际结束" &&
              !MetricCoverage.HasHitCoverage(detail), "an unverified skill endpoint has no failed hit percentage");

        using var mixed=JsonDocument.Parse("""
            {"effective_damage":{"basis_kind":"scope","reason":"结算事件覆盖不完整"},
             "skill_damage":{"basis_kind":"hits","failed_share":0.25,"basis":"可对账的伤害命中数",
             "unverified_hits":1,"observed_hits":4,"skill_initial_unknown_hits":1}}
            """);
        Check(MetricCoverage.HasScopeCoverage(mixed.RootElement) && MetricCoverage.HasHitCoverage(mixed.RootElement) &&
              MetricCoverage.Summary(mixed.RootElement).Contains("未核实命中 1/4（25%）"),
            "measured unverified hits retain the real numerator and denominator beside an independent scope gap");

        using var complete=JsonDocument.Parse("""{"name":"协防干员","coverage":{}}""");
        row.Entry=complete.RootElement;
        Check(row.CoverageMarker.Length==0 && !row.Tooltip.Contains("覆盖不完整"),
            "a newer complete row must clear the marker and the prior explanation");
        Console.WriteLine($"PASS_METRIC_COVERAGE {assertions} assertions (headless row presentation)");
    }
}
