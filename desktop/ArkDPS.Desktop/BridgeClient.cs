using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
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
}

/// <summary>One WPF parent, one Python child. Versioned UTF-8 JSON Lines only.</summary>
internal sealed class BridgeClient : IAsyncDisposable
{
    public const int Version = 1;
    private const int MaxMessage = 32 * 1024 * 1024;
    // Bounded so a worker that stops answering cannot keep the UI alive:
    // quit must be answered in QuitSeconds, and the process must exit in ExitSeconds.
    internal const int QuitSeconds = 5;
    internal const int ExitSeconds = 10;
    private readonly SemaphoreSlim gate = new(1, 1);
    private readonly Process process;
    private readonly StreamWriter log;
    private bool transportUnusable;
    private int sequence;
    public string LogPath { get; }
    public JsonElement Hello { get; private set; }
    public bool Connected => !process.HasExited;
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
        }
        catch { log.Dispose(); throw; }
    }
    public async Task ConnectAsync()
    {
        var hello=await RequestAsync("hello");Hello=hello.Get("result");
    }
    private async Task<string> ReadLineAsync(CancellationToken token)
    {
        // Bound input while reading, rather than allocating an unbounded line.
        var result=new StringBuilder();char[] chunk=new char[4096];
        while(true)
        {
            int count=await process.StandardOutput.ReadAsync(chunk.AsMemory(),token);
            if(count==0)throw new IOException("Python 采集进程已退出；日志："+LogPath);
            int end=Array.IndexOf(chunk,(char)10,0,count);
            if(result.Length+(end>=0 ? end : count)>MaxMessage)throw new IOException("worker 响应长度越界");
            result.Append(chunk,0,end>=0 ? end : count);
            if(end>=0)
            {
                if(end!=count-1)throw new IOException("worker 发送了未请求的消息");
                return result.ToString();
            }
        }
    }

    public async Task<JsonElement> RequestAsync(string op, Dictionary<string, object?>? fields = null,
                                                TimeSpan? timeoutOverride = null)
    {
        await gate.WaitAsync();
        try
        {
            if(transportUnusable)throw new IOException("worker 协议通道已失效；已拒绝后续命令");
            var command = fields == null ? new Dictionary<string, object?>() : new(fields);
            int id = ++sequence; command["version"] = Version; command["id"] = id; command["op"] = op;
            string payload=JsonSerializer.Serialize(command);
            if (Encoding.UTF8.GetByteCount(payload)>MaxMessage) throw new IOException("消息过大；导出内容超过 32 MiB");
            using var timeout = new CancellationTokenSource(timeoutOverride ?? TimeSpan.FromSeconds(90));
            bool requestMayHaveBeenSent = false;
            try
            {
                requestMayHaveBeenSent = true;
                await process.StandardInput.WriteLineAsync(payload.AsMemory(),timeout.Token);
                await process.StandardInput.FlushAsync(timeout.Token);
                using var document = JsonDocument.Parse(await ReadLineAsync(timeout.Token));
                var reply = document.RootElement;
                var responseVersion=reply.Get("version");var responseId=reply.Get("id");
                if(responseVersion.ValueKind!=JsonValueKind.Number || !responseVersion.TryGetInt32(out int version) || version!=Version ||
                   responseId.ValueKind!=JsonValueKind.Number || !responseId.TryGetInt32(out int responseSequence) || responseSequence!=id)
                    throw new IOException("worker 协议版本或消息序号不匹配");
                string kind=reply.Text("kind");var ok=reply.Get("ok");
                if(ok.ValueKind==JsonValueKind.False)
                {
                    if(kind!="error" || reply.Get("error").ValueKind!=JsonValueKind.String)
                        throw new IOException("worker 错误响应格式无效");
                    throw new BridgeCommandException(reply.Text("error"));
                }
                if(ok.ValueKind!=JsonValueKind.True)throw new IOException("worker 成功响应缺少布尔 ok");
                string expectedKind=op switch{"hello"=>"hello","snapshot"=>"snapshot","quit"=>"bye",_=>"state"};
                if(kind!=expectedKind)throw new IOException("worker 响应类型不匹配");
                if(op=="hello" && reply.Get("result").Text("transport")!="stdio-jsonl")
                    throw new IOException("Python worker 握手失败；请检查 UI 与采集器版本。");
                return reply.Clone();
            }
            catch(BridgeCommandException){throw;}
            catch when(requestMayHaveBeenSent)
            {
                // A partial write, timeout, malformed reply or sequence mismatch
                // makes it impossible to know which response belongs to which request.
                transportUnusable = true;
                throw;
            }
        }
        finally { gate.Release(); }
    }
    public async ValueTask DisposeAsync()
    {
        Exception? error=null;
        try
        {
            if(!process.HasExited)
            {
                if(!transportUnusable)
                {
                    var bye=await RequestAsync("quit",timeoutOverride:TimeSpan.FromSeconds(QuitSeconds));
                    if(bye.Text("kind")!="bye") error=new IOException("worker 退出确认缺失");
                }
                // On an uncertain stream, quit could consume stale/partial output.
                // Closing stdin is the worker's documented EOF cleanup signal.
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
                try{process.StandardInput.Close();}catch(IOException ex){closeError=ex;}
                // Wait for the process exit itself, not for EOF on the redirected
                // stderr pipe: a grandchild that inherited that handle can keep the
                // pipe open after the worker is already gone, and awaiting the pipe
                // would then block the UI forever. Never use WaitForExitAsync here.
                exited=await WaitForExitBoundedAsync(TimeSpan.FromSeconds(ExitSeconds));
                if(!exited)
                {
                    string diagnostic="worker 未在 "+ExitSeconds+" 秒内退出；仅结束本进程持有的子进程 PID "+process.Id;
                    try{process.Kill();}catch(Exception ex){diagnostic+="；结束子进程失败："+ex.Message;}
                    bool killed=await WaitForExitBoundedAsync(TimeSpan.FromSeconds(2));
                    if(!killed) diagnostic+="；结束命令后仍未退出";
                    WriteDiagnostic(diagnostic);
                    error=new IOException(diagnostic+"；诊断日志："+LogPath,error);
                }
            }
            finally
            {
                if(exited)
                {
                    try{exit=process.ExitCode;}catch(InvalidOperationException){}
                    if(exit!=0)error=new IOException("采集进程退出码 "+exit+"；清理日志："+LogPath,error);
                }
                else error??=new IOException("采集进程退出未确认；诊断日志："+LogPath,closeError);
                try{process.Dispose();}catch(InvalidOperationException){}
                log.Dispose();
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
            await Task.Delay((int)Math.Min(100,remaining));
        }
        return true;
    }
    private void WriteDiagnostic(string message)
    {
        try{lock(log) log.WriteLine("[exit] "+DateTime.Now.ToString("O")+" "+message);}catch(ObjectDisposedException){}
    }
}
internal sealed class BridgeCommandException(string message) : Exception(message);
