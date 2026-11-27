using System;
using System.IO;
using System.Reflection;
using System.Linq;
using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using ArkDPS.Desktop;

internal static class InspectorReadingTests
{
    internal static void Run()
    {
        using var cases=JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory,"Fixtures","inspector-readings.json")));
        int count=0;
        foreach(var item in cases.RootElement.EnumerateArray())
        {
            var detail=JsonNode.Parse(item.GetProperty("detail").GetRawText())!;
            detail["readings"]=JsonNode.Parse(item.GetProperty("readings").GetRawText());
            // Conflicting legacy input must not trigger a C# fallback/recalculation.
            detail["overkill_damage"]=999999;
            detail["skill_damage"]=999999;
            var packed=JsonSerializer.SerializeToElement(detail);
            var row=JsonSerializer.SerializeToElement(new{damage=1,mitigation_rate=99});
            var view=JsonSerializer.SerializeToElement(new{config=new{boss_only=false}});
            foreach(string name in new[]{"OverkillReading","SkillSplitReading","MitigationReading"})
            {
                var method=typeof(Inspector).GetMethod(name,BindingFlags.Static|BindingFlags.NonPublic)!;
                object value; JsonElement expected; string field;
                if(name=="OverkillReading") { value=method.Invoke(null,new object[]{row,packed,view})!;expected=item.GetProperty("readings").GetProperty("overkill");field="Item2"; }
                else if(name=="SkillSplitReading") { value=method.Invoke(null,new object[]{packed,true})!;expected=item.GetProperty("readings").GetProperty("skill_split").GetProperty("effective");field="Item3"; }
                else { value=method.Invoke(null,new object[]{row,packed})!;expected=item.GetProperty("readings").GetProperty("mitigation");field="Item1"; }
                double actual=(double)value.GetType().GetField(field)!.GetValue(value)!;
                var fraction=expected.GetProperty("fraction");
                if(fraction.ValueKind==JsonValueKind.Null ? !double.IsNaN(actual) : actual!=fraction.GetDouble())
                    throw new InvalidOperationException("Inspector must render Python fraction: "+item.GetProperty("name").GetString()+"/"+name);
                count++;
            }
        }
        Console.WriteLine($"PASS_INSPECTOR_CANONICAL_READINGS {count} assertions (Python golden, poisoned legacy fields; no windows/input)");
        Presence();
    }

    private static IEnumerable<DependencyObject> Children(DependencyObject root)
    {
        yield return root;
        foreach(var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>())
            foreach(var descendant in Children(child)) yield return descendant;
    }

    private static void Presence()
    {
        using var data=JsonDocument.Parse("""
            {"config":{"category":"damage","ranking":"total","rate_basis":"full_cycle"},
             "source":{"a":{"name":"测试干员","presence_complete":false,"presence_seconds":null}},
             "entries":[{"owner_id":"a","value":100}],
             "details_by_owner":{"a":{"presence_complete":true,"presence_seconds":10,
              "skill_coverage_complete":true,"skill_coverage":0.5,"skill_active_seconds":5}}}
            """);
        var inspector=new Inspector();
        Theme.Apply("lone-trail");
        foreach(bool compact in new[]{false,true})
        {
            inspector.CompactReference=compact;
            inspector.Pin("a",data.RootElement);
            inspector.Measure(new Size(540,800));
            inspector.Arrange(new Rect(0,0,540,800));
            inspector.UpdateLayout();
            var text=Children(inspector).OfType<TextBlock>().Select(block=>block.Text).ToArray();
            if(!text.Contains("50%") || !text.Contains(compact ? "00:10" : "10"))
                throw new InvalidOperationException("Inspector lost the canonical lifecycle presence or skill coverage: "+string.Join("|",text));
        }
        string? output=Environment.GetEnvironmentVariable("ARKDPS_INSPECTOR_DURATION_RENDER");
        if(!string.IsNullOrWhiteSpace(output))
        {
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(output))!);
            var bitmap=new RenderTargetBitmap(540,800,96,96,PixelFormats.Pbgra32);
            bitmap.Render(inspector);
            var encoder=new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using var file=File.Create(output);
            encoder.Save(file);
        }
        Console.WriteLine("PASS_INSPECTOR_LIFECYCLE_PRESENCE 4 assertions (full/reference controls, raw presence unknown, canonical 10 seconds/50%; no windows/input)");
    }
}
