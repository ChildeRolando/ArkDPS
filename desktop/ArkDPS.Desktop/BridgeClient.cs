using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;

namespace ArkDPS.Desktop;

internal static class Json
{
    public static JsonElement Get(this JsonElement value, string name) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var result) ? result : default;
    public static string Text(this JsonElement value, string name = "")
    {
        if (name.Length > 0) value = value.Get(name);
        return value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined ? "" : value.ToString();
    }
    public static double Number(this JsonElement value, string name, double fallback = 0) => value.Get(name).TryNumber(fallback);
    public static double TryNumber(this JsonElement value, double fallback = 0) =>
        value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var n) ? n : fallback;
    public static bool Bool(this JsonElement value, string name) => value.Get(name).ValueKind == JsonValueKind.True;
    public static IEnumerable<JsonElement> Items(this JsonElement value) =>
        value.ValueKind == JsonValueKind.Array ? value.EnumerateArray() : Array.Empty<JsonElement>();
    public static string Exact(this JsonElement value, string name) =>
        value.Get(name).ValueKind is JsonValueKind.Null or JsonValueKind.Undefined ? "—" : value.Get(name).ToString();
    // Every user-visible number keeps at most one decimal place. Internal
    // ranking, share and export values remain unrounded.
    public static string Ui(this JsonElement value, string name)
    {
        var item = value.Get(name);
        if (item.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined) return "—";
        if (item.ValueKind == JsonValueKind.Number && item.TryGetDouble(out var number) && double.IsFinite(number))
            return number.ToString("0.#", CultureInfo.InvariantCulture);
        return item.ToString();
    }
    // Detail panels use whole numbers; compact list values keep their own form.
    internal static string NumberUnits { get; set; } = "western";
    internal static string CompactAmount(double? amount)
    {
        if (amount is not double value || !double.IsFinite(value)) return "—";
        bool chinese = NumberUnits == "chinese";
        double whole = Math.Round(Math.Abs(value));
        double major = chinese ? 100_000_000 : 1_000_000;
        double minor = chinese ? 10_000 : 1_000;
        if (whole < minor) return Math.Round(value).ToString("0", CultureInfo.InvariantCulture);
        double scaledMinor = Math.Round(value / minor, 1);
        return whole >= major || Math.Abs(scaledMinor) >= major / minor
            ? Math.Round(value / major, 1).ToString("0.#", CultureInfo.InvariantCulture) + (chinese ? "亿" : "M")
            : scaledMinor.ToString("0.#", CultureInfo.InvariantCulture) + (chinese ? "万" : "K");
    }

    public static string UiInt(this JsonElement value, string name)
    {
        var item = value.Get(name);
        if (item.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined) return "—";
        if (item.ValueKind == JsonValueKind.Number && item.TryGetDouble(out var number) && double.IsFinite(number))
            return Math.Round(number, MidpointRounding.AwayFromZero).ToString("0", CultureInfo.InvariantCulture);
        return item.ToString();
    }
    public static string UiIntText(string text)
    {
        if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var number) && double.IsFinite(number))
            return Math.Round(number, MidpointRounding.AwayFromZero).ToString("0", CultureInfo.InvariantCulture);
        return text;
    }
    public static string UiText(string text)
    {
        if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var number) && double.IsFinite(number))
            return number.ToString("0.#", CultureInfo.InvariantCulture);
        return text;
    }
}

/// <summary>One WPF parent, one Python child. Versioned UTF-8 JSON Lines only.</summary>
internal sealed class BridgeClient : IAsyncDisposable
{
    public const int Version = 1;
    private const int MaxMessage = 32 * 1024 * 1024;
    private static readonly UTF8Encoding WireEncoding = new(false, true);
    private static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(10);
    // Bounded so a worker that stops answering cannot keep the UI alive:
    // quit must be answered in QuitSeconds, and the process must exit in ExitSeconds.
    internal const int QuitSeconds = 5;
    internal const int ExitSeconds = 10;
    private readonly SemaphoreSlim gate = new(1, 1);
    private readonly Channel<string> replies = Channel.CreateBounded<string>(new BoundedChannelOptions(4)
    {
        SingleReader = true,
        SingleWriter = true,
    });
    private readonly Process process;
    private readonly StreamWriter log;
    private readonly Stream input;
    private readonly Stream output;
    private volatile bool closing;
    private volatile BridgeTransportException? firstFault;
    private int sequence;
    private int replayedId;
    private string? replayedReply;
    private long bytesRead;
    public string LogPath { get; }
    public JsonElement Hello { get; private set; }
    public bool Connected => !process.HasExited;
    public bool CanSend => !closing && firstFault == null && Connected;

    public BridgeClient(string root, string python, string[] arguments)
    {
        string directory = Path.Combine(root, "runtime"); Directory.CreateDirectory(directory);
        LogPath = Path.Combine(directory, "wpf-collector-" + DateTime.Now.ToString("yyyyMMdd-HHmmss-fff") + ".log");
        log = new(LogPath, false, new UTF8Encoding(false)) { AutoFlush = true };
        var start = new ProcessStartInfo(python)
        {
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        start.ArgumentList.Add("-X"); start.ArgumentList.Add("utf8");
        start.ArgumentList.Add(Path.Combine(root, "desktop_bridge.py"));
        foreach (string argument in arguments) start.ArgumentList.Add(argument);
        process = new() { StartInfo = start, EnableRaisingEvents = true };
        process.ErrorDataReceived += (_, e) => { if(e.Data!=null){ try{ lock(log) log.WriteLine(e.Data); }catch(ObjectDisposedException){} } };
        try
        {
            if (!process.Start()) throw new IOException("无法启动 Python 采集进程");
            process.BeginErrorReadLine();
            input = process.StandardInput.BaseStream;
            output = process.StandardOutput.BaseStream;
            _ = Task.Factory.StartNew(ReadReplies, CancellationToken.None,
                TaskCreationOptions.LongRunning, TaskScheduler.Default);
        }
        catch { log.Dispose(); throw; }
    }
    public async Task ConnectAsync(TimeSpan? timeoutOverride = null)
    {
        var hello=await RequestAsync("hello",timeoutOverride:timeoutOverride);Hello=hello.Get("result");
    }
    private void ReadReplies()
    {
        // Process redirects are synchronous Windows pipes. The reader owns the
        // stream until shutdown and can drain a late reply while a retry is written.
        var chunk = new byte[4096];
        using var line = new MemoryStream();
        try
        {
            while (true)
            {
                int count = output.Read(chunk);
                if (count == 0) throw new IOException("Python 采集进程已退出；日志：" + LogPath);
                Interlocked.Add(ref bytesRead, count);
                for (int offset = 0; offset < count;)
                {
                    int end = Array.IndexOf(chunk, (byte)'\n', offset, count - offset);
                    int length = (end < 0 ? count : end) - offset;
                    line.Write(chunk, offset, length);
                    int size = (int)line.Length;
                    if (size > 0 && line.GetBuffer()[size - 1] == '\r') size--;
                    if (size > MaxMessage) throw new IOException("worker 响应长度越界");
                    if (end < 0) break;
                    string json = WireEncoding.GetString(line.GetBuffer(), 0, size);
                    if (!replies.Writer.TryWrite(json)) throw new IOException("worker 发送了过多未请求的消息");
                    line.SetLength(0);
                    offset = end + 1;
                }
            }
        }
        catch (Exception ex)
        {
            replies.Writer.TryComplete(ex);
        }
    }

    private async Task<string> ReceiveReply(int id, CancellationToken token)
    {
        while (true)
        {
            string json = await replies.Reader.ReadAsync(token).ConfigureAwait(false);
            if (replayedId == 0 || json != replayedReply) return json;
            // One retransmission can produce one duplicate reply. Match the
            // original bytes before skipping it, so a mismatched ID still fails closed.
            WriteDiagnostic("bridge", $"id={id} ignored_duplicate_id={replayedId}");
            replayedId = 0;
            replayedReply = null;
        }
    }

    public async Task<JsonElement> RequestAsync(string op, Dictionary<string, object?>? fields = null,
                                                TimeSpan? timeoutOverride = null)
    {
        await gate.WaitAsync().ConfigureAwait(false);
        try
        {
            if (firstFault is { } fault)
                throw new BridgeTransportException("worker 协议通道已失效 · " + fault.Message, fault);
            if (!CanSend)
                throw new BridgeTransportException(closing ? "采集进程正在关闭" : "Python 采集进程已退出");
            TimeSpan timeout = timeoutOverride ?? (op is "detect" or "export" ? TimeSpan.FromSeconds(90) : RequestTimeout);
            return await ExchangeAsync(op, fields, timeout).ConfigureAwait(false);
        }
        finally { gate.Release(); }
    }
    private async Task<JsonElement> ExchangeAsync(string op, Dictionary<string, object?>? fields, TimeSpan timeout)
    {
        var command = fields == null ? new Dictionary<string, object?>() : new(fields);
        int id = ++sequence;
        command["version"] = Version;
        command["id"] = id;
        command["op"] = op;
        byte[] request = WireEncoding.GetBytes(JsonSerializer.Serialize(command) + "\n");
        if (request.Length - 1 > MaxMessage) throw new BridgeCommandException("消息过大 · 导出内容超过 32 MiB");
        long started = Stopwatch.GetTimestamp();
        long previousBytes = Interlocked.Read(ref bytesRead);
        bool canReplay = op is not ("hello" or "quit") && Hello.Bool("request_replay");
        bool retried = false;
        string phase = "write";
        try
        {
            TimeSpan firstWait = canReplay ? timeout / 2 : timeout;
            string json;
            while (true)
            {
                TimeSpan remaining = (retried ? timeout : firstWait) - Stopwatch.GetElapsedTime(started);
                if (remaining <= TimeSpan.Zero) throw new TimeoutException();
                using var deadline = new CancellationTokenSource(remaining);
                phase = "write";
                Task write = Task.Run(() =>
                {
                    input.Write(request);
                    input.Flush();
                });
                try
                {
                    await write.WaitAsync(deadline.Token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    // The pipe can still be unwinding during shutdown.
                    _ = write.ContinueWith(t => { _ = t.Exception; }, CancellationToken.None,
                        TaskContinuationOptions.OnlyOnFaulted | TaskContinuationOptions.ExecuteSynchronously,
                        TaskScheduler.Default);
                    throw;
                }
                phase = "read";
                try
                {
                    json = await ReceiveReply(id, deadline.Token).ConfigureAwait(false);
                    break;
                }
                catch (OperationCanceledException) when (canReplay && !retried && deadline.IsCancellationRequested)
                {
                    retried = true;
                    WriteDiagnostic("bridge", $"op={op} id={id} phase=read event=retry " +
                        $"reply_bytes={Interlocked.Read(ref bytesRead) - previousBytes}");
                }
            }
            phase = "validate";
            using var document = JsonDocument.Parse(json);
            var reply = document.RootElement;
            var responseVersion = reply.Get("version");
            var responseId = reply.Get("id");
            if (responseVersion.ValueKind != JsonValueKind.Number || !responseVersion.TryGetInt32(out int version) || version != Version ||
                responseId.ValueKind != JsonValueKind.Number || !responseId.TryGetInt32(out int responseSequence) || responseSequence != id)
                throw new IOException("worker 协议版本或消息序号不匹配");
            replayedId = retried ? id : 0;
            replayedReply = retried ? json : null;
            string kind = reply.Text("kind");
            var ok = reply.Get("ok");
            if (ok.ValueKind == JsonValueKind.False)
            {
                if (kind != "error" || reply.Get("error").ValueKind != JsonValueKind.String)
                    throw new IOException("worker 错误响应格式无效");
                throw new BridgeCommandException(reply.Text("error"));
            }
            if (ok.ValueKind != JsonValueKind.True) throw new IOException("worker 成功响应缺少布尔 ok");
            string expectedKind = op switch { "hello" => "hello", "snapshot" => "snapshot", "quit" => "bye", _ => "state" };
            if (kind != expectedKind) throw new IOException("worker 响应类型不匹配");
            if (op == "hello" && reply.Get("result").Text("transport") != "stdio-jsonl")
                throw new IOException("Python worker 握手失败；请检查 UI 与采集器版本。");
            return reply.Clone();
        }
        catch (BridgeCommandException) { throw; }
        catch (Exception ex)
        {
            double elapsed = Stopwatch.GetElapsedTime(started).TotalMilliseconds;
            Exception cause = ex is OperationCanceledException ? new TimeoutException("等待采集进程超时", ex) :
                ex is ChannelClosedException { InnerException: { } pipeError } ? pipeError : ex;
            string reason = cause is TimeoutException ? "等待采集进程超时" : cause.Message;
            var fault = new BridgeTransportException(reason + $"（{op} #{id}）", cause);
            firstFault = fault;
            WriteDiagnostic("bridge", $"op={op} id={id} phase={phase} " +
                $"elapsed_ms={elapsed:F0} request_bytes={request.Length} " +
                $"reply_bytes={Interlocked.Read(ref bytesRead) - previousBytes} error={cause.GetType().Name}");
            throw fault;
        }
    }
    public async ValueTask DisposeAsync()
    {
        Exception? error=null;
        closing = true;
        try
        {
            if (firstFault == null && Connected && gate.Wait(0))
            {
                try
                {
                    var bye=await ExchangeAsync("quit",null,TimeSpan.FromSeconds(QuitSeconds)).ConfigureAwait(false);
                    if(bye.Text("kind")!="bye") error=new IOException("worker 退出确认缺失");
                }
                finally { gate.Release(); }
            }
        }
        catch(Exception ex){error=ex;}
        finally
        {
            bool exited=false;
            int exit=-1;
            Exception? closeError=null;
            try
            {
                // EOF is also a shutdown signal on UI crashes. Python joins its
                // collector thread; never kill by name or abandon device cleanup.
                var closeInput = Task.Run(input.Dispose);
                // Wait for the process exit itself, not for EOF on the redirected
                // stderr pipe: a grandchild that inherited that handle can keep the
                // pipe open after the worker is already gone, and awaiting the pipe
                // would then block the UI forever. Never use WaitForExitAsync here.
                exited=await WaitForExitBoundedAsync(TimeSpan.FromSeconds(ExitSeconds)).ConfigureAwait(false);
                if(!exited)
                {
                    string diagnostic="worker 未在 "+ExitSeconds+" 秒内退出；仅结束本进程持有的子进程 PID "+process.Id;
                    try{process.Kill();}catch(Exception ex){diagnostic+="；结束子进程失败："+ex.Message;}
                    bool killed=await WaitForExitBoundedAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false);
                    if(!killed) diagnostic+="；结束命令后仍未退出";
                    WriteDiagnostic("exit",diagnostic);
                    error=new IOException(diagnostic+"；诊断日志："+LogPath,error);
                }
                if (closeInput.IsFaulted) closeError=closeInput.Exception?.GetBaseException();
            }
            finally
            {
                if(exited)
                {
                    try{exit=process.ExitCode;}catch(InvalidOperationException){}
                    if(exit!=0)error=new IOException("采集进程退出码 "+exit+"；清理日志："+LogPath,error);
                }
                else error??=new IOException("采集进程退出未确认；诊断日志："+LogPath,closeError);
                output.Dispose();
                try{process.Dispose();}catch(InvalidOperationException){}
                lock(log) log.Dispose();
            }
        }
        if(error!=null)throw error;
    }
    private async Task<bool> WaitForExitBoundedAsync(TimeSpan timeout)
    {
        long deadline=Environment.TickCount64+(long)timeout.TotalMilliseconds;
        while(!process.HasExited)
        {
            long remaining=deadline-Environment.TickCount64;
            if(remaining<=0)return false;
            await Task.Delay((int)Math.Min(100,remaining)).ConfigureAwait(false);
        }
        return true;
    }
    private void WriteDiagnostic(string section,string message)
    {
        try{lock(log) log.WriteLine($"[{section}] "+DateTime.Now.ToString("O")+" "+message);}
        catch (ObjectDisposedException) {}
        catch (IOException) {}
    }
}
internal sealed class BridgeCommandException(string message) : Exception(message);
internal sealed class BridgeTransportException(string message, Exception? cause = null) : IOException(message, cause);
