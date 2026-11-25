using System;
using System.Reflection;
using System.Text.Json;
using ArkDPS.Desktop;

internal static class VerificationSkillSplitTests
{
    internal static void Run()
    {
        var verify=typeof(MainWindow).GetMethod("VerifySkillSplitContract",BindingFlags.Static|BindingFlags.NonPublic)
            ?? throw new InvalidOperationException("missing interactive canonical contract");
        int assertions=0;
        void Check(string json,bool accepted)
        {
            using var document=JsonDocument.Parse(json);
            bool actual=true;
            try { verify.Invoke(null,new object[]{document.RootElement,"a"}); }
            catch(TargetInvocationException e) when(e.InnerException is InvalidOperationException) {actual=false;}
            if(actual!=accepted)throw new InvalidOperationException("interactive skill split contract mismatch: "+json);
            assertions++;
        }
        // The same gate used by --verify must accept the wire that Inspector reads,
        // including verified zero, without requiring trimmed source/detail fields.
        Check("""{"details_by_owner":{"a":{"readings":{"skill_split":{"nominal":{"skill":42,"non_skill":58}}}}}}""",true);
        Check("""{"details_by_owner":{"a":{"readings":{"skill_split":{"nominal":{"skill":0,"non_skill":0}}}}}}""",true);
        Check("""{"source":{"a":{"skill_damage":42,"non_skill_damage":58}},"details_by_owner":{"a":{"skill_damage":42,"non_skill_damage":58}}}""",false);
        Check("""{"details_by_owner":{"a":{"readings":{"skill_split":{"nominal":{"skill":null,"non_skill":58}}}}}}""",false);
        Check("""{"details_by_owner":{"a":{"readings":{"skill_split":{"nominal":{"skill":42,"non_skill":null},"effective":{"skill":42,"non_skill":58}}}}}}""",false);
        Check("""{"details_by_owner":{"foreign":{"readings":{"skill_split":{"nominal":{"skill":42,"non_skill":58}}}}}}""",false);
        Console.WriteLine($"PASS_VERIFICATION_SKILL_SPLIT {assertions} assertions (same --verify gate; no windows/input)");
    }
}
