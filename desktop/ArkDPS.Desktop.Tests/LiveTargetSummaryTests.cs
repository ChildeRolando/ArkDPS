using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using ArkDPS.Desktop;

internal static class LiveTargetSummaryTests
{
    private static int passed;

    private static void Check(string name, bool condition)
    {
        if (!condition) throw new InvalidOperationException(name);
        passed++;
    }

    private static IEnumerable<DependencyObject> Children(DependencyObject root)
    {
        yield return root;
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach (var descendant in Children(child)) yield return descendant;
    }

    private static T Find<T>(LiveTargetSummary summary, string id) where T : FrameworkElement =>
        Children(summary).OfType<T>().Single(item => AutomationProperties.GetAutomationId(item) == id);

    private static string Text(DependencyObject root) =>
        string.Join("|", Children(root).OfType<TextBlock>().Select(item => item.Text));

    private static string CardText(LiveTargetSummary summary, int slot) =>
        Text(Find<Border>(summary, "LiveEnemyCard" + slot));

    private static JsonArray Rows(string json) => JsonNode.Parse(json)!.AsArray();

    private static void Publish(LiveTargetSummary summary, JsonArray rows, string battle = "headless-native") =>
        summary.Update(battle, JsonSerializer.SerializeToElement(new
        {
            scope = "native_pc_current_hp", status = "ready", ready = true, rows,
        }), "");

    internal static void Run()
    {
        // Set only the asset lookup root. App is never constructed or started.
        var root = new DirectoryInfo(AppContext.BaseDirectory);
        while (root is not null && !File.Exists(Path.Combine(root.FullName, "assets", "enemy_portraits", "manifest.json")))
            root = root.Parent;
        App.Root = root?.FullName ?? throw new InvalidOperationException("enemy portrait assets not found");

        var summary = new LiveTargetSummary();
        var rows = Rows("""
            [
              {"instance_uid":"701","eid":"enemy_1002_nsabr","name":"原生敌人","lifecycle":"current","hp":731,"max_hp":null,"hp_known":false},
              {"instance_uid":"702","eid":"enemy_1002_nsabr","name":"原生同型","lifecycle":"current","hp":600},
              {"instance_uid":"703","eid":"enemy_second","name":"原生次型","lifecycle":"current","hp":400},
              {"instance_uid":"704","eid":"enemy_pending","name":"尚未入场","lifecycle":"pending","alive":true,"hp":9000},
              {"instance_uid":"705","eid":"enemy_inferred","name":"推断敌人","lifecycle":"current","entry_inferred":true,"hp":10000}
            ]
            """);
        Publish(summary, rows);
        Check("native current without alive appears", CardText(summary, 1).Contains("原生敌人"));
        Check("current HP with unknown max is honest", CardText(summary, 1).Contains("731 / —"));
        Check("native current status is present", CardText(summary, 1).Contains("在场"));
        Check("distinct live types keep priority", CardText(summary, 2).Contains("原生次型") &&
            CardText(summary, 3).Contains("原生同型"));
        Check("pending and inferred rows excluded", !Text(summary).Contains("尚未入场") && !Text(summary).Contains("推断敌人"));
        Check("native portrait uses same enemy identity", Children(Find<Border>(summary, "LiveEnemyCard1"))
            .OfType<Image>().Single().Source is not null);
        var detailed = new EnemyItem(JsonSerializer.SerializeToElement(rows[0]), "701", "");
        Check("compact and detail share name/status/HP", detailed.Name == "原生敌人" && detailed.Status == "在场" &&
            detailed.HpText == "731 / —" && detailed.Portrait is not null && !detailed.HpPercentKnown);

        rows[0]!["hp"] = 530;
        Publish(summary, rows);
        Check("current HP refreshes in selected card", CardText(summary, 1).Contains("530 / —") &&
            !CardText(summary, 1).Contains("731 / —"));
        rows[0]!["hp"] = null;
        Publish(summary, rows);
        Check("unlocked missing current HP remains unknown", CardText(summary, 1).Contains("HP 未知"));
        var zero = new LiveTargetSummary();
        Publish(zero, Rows("""[{"instance_uid":"709","eid":"enemy_zero","name":"零血当前成员","lifecycle":"current","hp":0}]"""));
        Check("numeric zero HP is preserved without death inference", CardText(zero, 1).Contains("0 / —") &&
            CardText(zero, 1).Contains("在场"));

        // The compact list deliberately keeps departed types after live types;
        // this is the existing MuMu history/paging contract, not active membership.
        rows.Add(JsonNode.Parse("""{"instance_uid":"706","eid":"enemy_departed","name":"离场未知原因","lifecycle":"departed","end_reason":"unknown","hp":9000} """));
        Publish(summary, rows);
        Check("departed does not enter live HP priority", CardText(summary, 2).Contains("原生次型") &&
            !CardText(summary, 1).Contains("离场未知原因") && !CardText(summary, 2).Contains("离场未知原因"));
        var departedOnly = new LiveTargetSummary();
        Publish(departedOnly, Rows("""[{"instance_uid":"710","eid":"enemy_departed","name":"离场未知原因","lifecycle":"departed","end_reason":"unknown","hp":null,"last_observed_hp":77}]"""));
        Check("existing departed fallback stays labelled departed", CardText(departedOnly, 1).Contains("已离场") &&
            !CardText(departedOnly, 1).Contains("已击倒"));
        Check("departed last HP does not pretend to be current", CardText(departedOnly, 1).Contains("最后 HP 77") &&
            !CardText(departedOnly, 1).Contains("77 /"));
        using var retainedRow = JsonDocument.Parse("""
            {"instance_uid":"711","eid":"enemy_departed","name":"保留离场实例","lifecycle":"departed","hp":null,"last_observed_hp":65}
            """);
        var retainedDetail = new EnemyItem(retainedRow.RootElement, "711", "");
        Check("retained detailed row shows last observed HP", retainedDetail.HpText == "最后 HP 65");
        Check("absent end reason stays generic departed in detail", retainedDetail.Status == "已离场");

        CheckMuMuPaging();
        Console.WriteLine($"PASS_LIVE_TARGET_SUMMARY {passed} assertions (WPF controls only; no windows/input/target access)");
    }

    private static void CheckMuMuPaging()
    {
        var summary = new LiveTargetSummary();
        var rows = Rows("""
            [
              {"instance_uid":"101","eid":"enemy_a","name":"首位敌人","lifecycle":"active","alive":true,"hp":900,"atk":173},
              {"instance_uid":"102","eid":"enemy_a","name":"同型敌人","lifecycle":"active","alive":true,"hp":800,"atk":214},
              {"instance_uid":"103","eid":"enemy_b","name":"次型敌人","lifecycle":"rebirthing","alive":true,"hp":600},
              {"instance_uid":"104","eid":"enemy_c","name":"第三型敌人","lifecycle":"active","alive":true,"hp":300},
              {"instance_uid":"105","eid":"enemy_d","name":"已离场敌人","lifecycle":"departed","alive":false,"hp":9000},
              {"instance_uid":"106","eid":"enemy_e","name":"缺少存活证明","lifecycle":"active","hp":10000}
            ]
            """);
        void PublishLegacy(string battle = "headless-mumu") => summary.Update(battle,
            JsonSerializer.SerializeToElement(new { status = "ready", ready = true, rows }), "");
        PublishLegacy();
        Check("MuMu distinct types and filtering unchanged", CardText(summary, 1).Contains("首位敌人") &&
            CardText(summary, 2).Contains("次型敌人") && CardText(summary, 3).Contains("第三型敌人") &&
            !Text(summary).Contains("缺少存活证明"));
        var next = Find<Button>(summary, "NextLiveEnemy");
        next.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent));
        Check("MuMu paging skips other selected instances", CardText(summary, 1).Contains("同型敌人"));
        var pin = Find<ToggleButton>(summary, "LockLiveEnemy");
        pin.IsChecked = true;
        rows[1]!["hp"] = 230;
        PublishLegacy();
        Check("MuMu lock follows exact UID after reorder", CardText(summary, 1).Contains("同型敌人") &&
            CardText(summary, 1).Contains("230 / —") && !next.IsEnabled);
        rows[1]!["lifecycle"] = "departed";
        rows[1]!["alive"] = false;
        rows[1]!["hp"] = 0;
        rows[1]!["atk"] = null;
        PublishLegacy();
        Check("MuMu departed pin retains confirmed readings", pin.IsChecked == true &&
            CardText(summary, 1).Contains("同型敌人") && CardText(summary, 1).Contains("214") &&
            CardText(summary, 1).Contains("已离场"));
        PublishLegacy("headless-next-battle");
        Check("new battle clears enemy pin", pin.IsChecked == false);
    }
}
