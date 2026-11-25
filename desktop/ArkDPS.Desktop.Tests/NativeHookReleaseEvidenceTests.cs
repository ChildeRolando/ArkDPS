using System;
using System.Collections.Generic;
using System.Text.Json;
using ArkDPS.Desktop;

internal static class NativeHookReleaseEvidenceTests
{
    internal static void Run()
    {
        int passed = 0;
        foreach (object? performance in new object?[] { false, true, 0, 1, null, "false" })
        foreach (object? handoff in new object?[] { false, true, 0, 1, null, "true" })
        {
            var profile = JsonSerializer.SerializeToElement(new Dictionary<string, object?> {
                ["performance_verified"] = performance, ["autochess_handoff_verified"] = handoff });
            bool standard = performance is bool && handoff is bool;
            bool startup = performance is false && handoff is false;
            if (App.NativeHookAcceptanceFlagsValid(profile, false) != standard
                || App.NativeHookAcceptanceFlagsValid(profile, true) != startup)
                throw new InvalidOperationException("Native hook release acceptance facts were used as support capabilities");
            passed++;
        }
        var missing = JsonSerializer.SerializeToElement(new { });
        if (App.NativeHookAcceptanceFlagsValid(missing, false) || App.NativeHookAcceptanceFlagsValid(missing, true))
            throw new InvalidOperationException("Missing native hook acceptance facts must not become booleans");
        Console.WriteLine($"Native hook release evidence: {passed + 1} passed");
    }
}
