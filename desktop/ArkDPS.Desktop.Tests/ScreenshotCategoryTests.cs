using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using ArkDPS.Desktop;

internal static class ScreenshotCategoryTests
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

    private static string Text(DependencyObject root) =>
        string.Join("|", Children(root).OfType<TextBlock>().Select(item => item.Text));

    internal static void Run()
    {
        // Frozen real CE-5 healing projection; provenance is inside the fixture.
        using var fixture = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory,
            "Fixtures", "native-ce5-healing.selected.json")));
        var healing = fixture.RootElement;
        var healer = healing.GetProperty("view").GetProperty("entries")[0];
        Check("real CE-5 healer and cumulative value retained", healer.GetProperty("owner_id").GetString() ==
            "char_147_shining" && healer.GetProperty("exact_value").GetString() == "10919.251953125");
        Check("unavailable recent HPS is not zero", healer.GetProperty("rate").ValueKind == JsonValueKind.Null &&
            healer.GetProperty("rate_text").GetString() == "—");

        if (Application.Current is null)
        {
            var application = new App();
            application.InitializeComponent(); // Resources only; never Run/Show or invoke OnStartup.
        }
        foreach (string theme in new[] { "lone-trail", "people", "babel" })
        {
            Theme.Apply(theme);
            var document = Screenshot.StatisticsDocument(healing, theme);
            string text = Text(document);
            Check(theme + " healing exports actual healer data", text.Contains("闪灵") && text.Contains("10919.3") &&
                text.Contains("100.0%   —"));
            Check(theme + " healing describes HPS", text.Contains("治疗") && text.Contains("HPS"));
            Check(theme + " healing excludes damage-only notes", !text.Contains("有效伤害") &&
                !text.Contains("过量") && !text.Contains("分型："));
            using var png = new MemoryStream(Screenshot.Statistics(healing, theme));
            var decoder = new PngBitmapDecoder(png, BitmapCreateOptions.None, BitmapCacheOption.OnLoad);
            Check(theme + " real healing renders a complete PNG", decoder.Frames[0].PixelWidth == 900 &&
                decoder.Frames[0].PixelHeight > 100);

            // Synthetic category boundary controls retain the same rendering path.
            var damage = JsonNode.Parse(healing.GetRawText())!;
            damage["view"]!["config"]!["category"] = "damage";
            damage["view"]!["entries"]![0]!["detail"]!["overkill_damage"] = 120;
            text = Text(Screenshot.StatisticsDocument(JsonSerializer.SerializeToElement(damage), theme));
            Check(theme + " damage footnote and overkill preserved", text.Contains("有效伤害 = 总伤害 − 过量伤害") &&
                text.Contains("过量 120"));
            Check(theme + " damage attribute legend preserved", text.Contains("分型：物理红 / 法术") && text.Contains("真实"));
            damage["view"]!["entries"]![0]!["detail"]!["settled_damage"] = new JsonObject();
            text = Text(Screenshot.StatisticsDocument(JsonSerializer.SerializeToElement(damage), theme));
            Check(theme + " settled damage footnote preserved", text.Contains("有效伤害按实际扣血与扣盾统计"));

            var shield = JsonNode.Parse(healing.GetRawText())!;
            shield["view"]!["config"]!["category"] = "shield";
            shield["view"]!["rate_label"] = "";
            shield["view"]!["entries"] = new JsonArray();
            text = Text(Screenshot.StatisticsDocument(JsonSerializer.SerializeToElement(shield), theme));
            Check(theme + " shield uses absorption note without damage legend", text.Contains("护盾") &&
                !text.Contains("有效伤害") && !text.Contains("过量") && !text.Contains("分型：") && !text.Contains(" / 速率"));
        }
        Check("offscreen rendering never creates windows", Application.Current!.Windows.Count == 0);
        Console.WriteLine($"PASS_SCREENSHOT_CATEGORIES {passed} assertions (real CE-5 fixture; offscreen, no windows/input)");
    }
}
