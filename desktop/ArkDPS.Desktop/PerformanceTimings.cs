using System;
using System.Diagnostics;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

namespace ArkDPS.Desktop;

// Only publication/layout boundaries call this class, never individual bars.
// One bounded background write may be in flight; a slow disk retains aggregates
// rather than creating an unbounded Task.Run queue on the UI thread.
internal sealed class PerformanceTimings(Action<string> sink, Func<long>? clock = null,
    Action<Action>? schedule = null)
{
    internal enum Phase { BridgeWrite, BridgeRead, BridgeValidate, ApplyState, Measure, Arrange }
    private readonly object gate = new();
    private readonly long[] calls = new long[6], ticks = new long[6], maxima = new long[6];
    private readonly Func<long> now = clock ?? Stopwatch.GetTimestamp;
    private readonly Action<Action> enqueue = schedule ?? (action => { _ = Task.Run(action); });
    private volatile bool enabled;
    private bool writing;
    private long window, failures;
    internal bool Enabled { get => enabled; set { lock (gate) {
        if (enabled == value) return;
        enabled = value; window = 0;
        Array.Clear(calls); Array.Clear(ticks); Array.Clear(maxima);
    } } }
    internal long Begin() => enabled ? now() : 0;
    internal void End(Phase phase, long started)
    {
        if (started == 0 || !enabled) return;
        long finished = now(), elapsed = Math.Max(0, finished-started);
        lock (gate)
        {
            if (!enabled) return;
            int index = (int)phase;
            calls[index]++; ticks[index] += elapsed; maxima[index] = Math.Max(maxima[index], elapsed);
            if (window == 0) window = started;
            if (writing || finished-window < Stopwatch.Frequency) return;
            var rows = Enum.GetValues<Phase>().Select(item => new {
                phase = item.ToString(), calls = calls[(int)item], ticks = ticks[(int)item], max_ticks = maxima[(int)item]
            }).Where(row => row.calls > 0).ToArray();
            long span = finished-window, dropped = failures;
            Array.Clear(calls); Array.Clear(ticks); Array.Clear(maxima);
            window = finished; writing = true;
            enqueue(() => {
                try { sink(JsonSerializer.Serialize(new { schema = 1, source = "wpf", pid = Environment.ProcessId,
                    desktop_run_id = DesktopLog.RunId, utc = DateTimeOffset.UtcNow, qpc = finished,
                    qpc_frequency = Stopwatch.Frequency, window_ticks = span, emission_failures = dropped, phases = rows })); }
                catch { lock (gate) failures++; }
                finally { lock (gate) writing = false; }
            });
        }
    }
    internal static readonly PerformanceTimings Shared = new(message => {
        if (App.StateDirectory.Length > 0)
            DesktopLog.Append(App.StateDirectory, "wpf-performance.jsonl", message);
    });
}
