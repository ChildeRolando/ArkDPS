using System;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
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
    }
}
