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
    private readonly SemaphoreSlim gate = new(1, 1);
    private readonly Process process;
    private readonly StreamWriter log;
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
        process.ErrorDataReceived += (_, e) => { if(e.Data!=null) lock(log) log.WriteLine(e.Data); };
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
        if(hello.Text("kind")!="hello" || hello.Get("result").Text("transport")!="stdio-jsonl")
            throw new IOException("Python worker 握手失败；请检查 UI 与采集器版本。");
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

    public async Task<JsonElement> RequestAsync(string op, Dictionary<string, object?>? fields = null)
    {
        await gate.WaitAsync();
        try
        {
            var command = fields == null ? new Dictionary<string, object?>() : new(fields);
            int id = ++sequence; command["version"] = Version; command["id"] = id; command["op"] = op;
            string payload=JsonSerializer.Serialize(command);
            if (Encoding.UTF8.GetByteCount(payload)>MaxMessage) throw new IOException("消息过大；导出内容超过 32 MiB");
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(90));
            await process.StandardInput.WriteLineAsync(payload.AsMemory(),timeout.Token);
            await process.StandardInput.FlushAsync(timeout.Token);
            using var document = JsonDocument.Parse(await ReadLineAsync(timeout.Token));
            var reply = document.RootElement;
            if (reply.Number("version") != Version || reply.Number("id") != id)
                throw new IOException("worker 协议版本或消息序号不匹配");
            if (!reply.Bool("ok")) throw new BridgeCommandException(reply.Text("error"));
            return reply.Clone();
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
                var bye=await RequestAsync("quit");
                if(bye.Text("kind")!="bye") error=new IOException("worker 退出确认缺失");
            }
        }
        catch(Exception ex){error=ex;}
        finally
        {
            // EOF is also a shutdown signal on UI crashes. Python joins its
            // collector thread; never kill by name or abandon device cleanup.
            process.StandardInput.Close();
            await process.WaitForExitAsync();process.WaitForExit();
            int exit=process.ExitCode;process.Dispose();log.Dispose();
            if(exit!=0)error=new IOException("采集进程退出码 "+exit+"；清理日志："+LogPath,error);
        }
        if(error!=null)throw error;
    }
}
internal sealed class BridgeCommandException(string message) : Exception(message);
