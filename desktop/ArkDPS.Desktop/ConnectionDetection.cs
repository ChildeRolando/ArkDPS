using System;

namespace ArkDPS.Desktop;

/// <summary>Binds an asynchronous instance scan to the current settings draft.</summary>
internal sealed class ConnectionDetection
{
    internal readonly record struct Request(long Revision, string Platform, string AdbPath);
    private long revision;

    internal Request Begin(string platform, string adbPath) =>
        new(++revision, platform, adbPath.Trim());

    internal void Invalidate() => revision++;

    internal bool IsCurrent(Request request, string platform, string adbPath) =>
        request.Revision == revision && request.Platform == platform &&
        string.Equals(request.AdbPath, adbPath.Trim(), StringComparison.Ordinal);
}
