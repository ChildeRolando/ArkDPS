using System;
using System.Globalization;
using System.Linq;
using System.Text.Json;

namespace ArkDPS.Desktop;

internal static class MetricCoverage
{
    private static readonly (string Key,string Label)[] Metrics=
    {
        ("observed_damage","已观察伤害"), ("observed_recent_damage","近 5 秒已观察伤害"),
        ("observed_healing","已观察治疗"),
        ("boss_damage","首领伤害"), ("recent_boss_damage","近 5 秒首领伤害"),
        ("effective_damage","有效伤害"), ("recent_effective_damage","近 5 秒有效伤害"),
        ("engagement_effective_damage","接敌有效 DPS"), ("engagement","接敌 DPS"),
        ("engagement_healing","接敌 HPS"), ("shield","有效护盾"),
        ("mitigation","被减伤率"), ("skill_damage","技能期伤害"), ("skill_effective_damage","技能期有效伤害"),
    };

    internal static bool IsPartial(JsonElement coverage,string metric) =>
        coverage.Get(metric).ValueKind==JsonValueKind.Object;

    internal static bool HasPartial(JsonElement coverage) =>
        Metrics.Any(metric=>IsPartial(coverage,metric.Key));

    internal static string Detail(JsonElement coverage,string metric)
    {
        var item=coverage.Get(metric);
        if(item.ValueKind!=JsonValueKind.Object) return "";
        if(IsScopeCoverage(metric,item))
            return item.Text("reason") is { Length: > 0 } reason ? reason : "覆盖不完整";
        string basis=item.Text("basis");
        return string.Join(" · ",new[]{Share(item),basis.Length>0 ? "分母："+basis : "",item.Text("reason")}
            .Where(value=>value.Length>0));
    }

    internal static string Summary(JsonElement coverage,bool shortForm=false)
    {
        var lines=Metrics.Where(metric=>IsPartial(coverage,metric.Key))
            .Select(metric=>metric.Label+" "+(shortForm && !IsScopeCoverage(metric.Key,coverage.Get(metric.Key))
                ? ShortShare(coverage.Get(metric.Key)) : Detail(coverage,metric.Key)));
        string summary=string.Join(shortForm ? " · " : "\n",lines);
        bool observationCoverage=HasHitCoverage(coverage) || HasScopeCoverage(coverage);
        return summary.Length>0 && shortForm ? (observationCoverage ? "部分统计 · " : "统计失败占比 · ")+summary : summary;
    }

    internal static bool HasHitCoverage(JsonElement coverage) =>
        Metrics.Any(metric=>coverage.Get(metric.Key).Text("basis_kind")=="hits");

    internal static bool HasScopeCoverage(JsonElement coverage) =>
        Metrics.Any(metric=>IsScopeCoverage(metric.Key,coverage.Get(metric.Key)));

    private static bool IsScopeCoverage(string metric,JsonElement item) =>
        item.ValueKind==JsonValueKind.Object &&
        (metric is "observed_damage" or "observed_recent_damage" || item.Text("basis_kind")=="scope");

    private static string ShortShare(JsonElement item)=>item.Text("basis_kind")=="hits" ? Share(item) : Percentage(item);

    private static string Share(JsonElement item)
    {
        string percent=Percentage(item);
        if(item.Text("basis_kind")!="hits") return "统计失败占比 "+percent;
        var prefixCount=item.Get("skill_initial_unknown_hits");
        if(prefixCount.ValueKind==JsonValueKind.Number && prefixCount.TryGetInt32(out var initial))
        {
            string prefix=percent=="未知" ? "已记录命中 " : "未核实命中 ";
            string counts=$"{item.Get("unverified_hits").GetRawText()}/{item.Get("observed_hits").GetRawText()}";
            string share=percent=="未知" ? " 未核实，整局占比未知" : $"（{percent}）";
            return prefix+counts+share+(initial>0 ? $"，其中 {initial} 次开局技能状态未知" : "");
        }
        if(percent=="未知") return "未核实命中占比未知";
        var missed=item.Get("unverified_hits");
        var hits=item.Get("observed_hits");
        return missed.ValueKind==JsonValueKind.Number && hits.ValueKind==JsonValueKind.Number
            ? $"未核实命中 {missed.GetRawText()}/{hits.GetRawText()}（{percent}）"
            : "未核实命中 "+percent;
    }

    private static string Percentage(JsonElement item)
    {
        double share=item.Number("failed_share",double.NaN);
        if(share is not (>=0 and <=1)) return "未知";
        if(share is >0 and <.001) return "<0.1%";
        if(share is >.999 and <1) return ">99.9%";
        return (share*100).ToString("0.#",CultureInfo.InvariantCulture)+"%";
    }
}
