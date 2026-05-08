using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;

var options = CliOptions.Parse(args);

if (!options.IsStreamDeckLaunch)
{
    Console.Error.WriteLine("This executable is intended to be launched by Stream Deck.");
    Console.Error.WriteLine("Required args: -port <port> -pluginUUID <uuid> -registerEvent <event> -info <json>");
    return 2;
}

if (SingleInstanceGuard.AnotherInstanceAlreadyRunning())
{
    return 0;
}

FileStream? singleInstanceLock;
try
{
    var lockPath = Path.Combine(Path.GetTempPath(), "PatentCompareXcbkmB64Paster.lock");
    singleInstanceLock = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
}
catch
{
    return 0;
}

using (singleInstanceLock)
{
    using var app = new StreamDeckPlugin(options);
    await app.RunAsync();
}
return 0;

sealed class StreamDeckPlugin : IDisposable
{
    private const string DefaultTitle = "DROP\nFILE";
    private readonly CliOptions _options;
    private readonly ClientWebSocket _socket = new();
    private readonly ConcurrentDictionary<string, ActionState> _states = new();
    private readonly ConcurrentDictionary<string, TypingRun> _typingRuns = new();
    private readonly Channel<object> _outbound = Channel.CreateUnbounded<object>();
    private readonly JsonSerializerOptions _jsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private CancellationTokenSource? _cts;

    public StreamDeckPlugin(CliOptions options) => _options = options;

    public async Task RunAsync()
    {
        FileLog.Write($"Started. port={_options.Port} uuid={_options.PluginUuid}");
        _cts = new CancellationTokenSource();
        var uri = new Uri($"ws://127.0.0.1:{_options.Port}");
        await _socket.ConnectAsync(uri, _cts.Token);
        await SendRawAsync(new Dictionary<string, object?>
        {
            ["event"] = _options.RegisterEvent,
            ["uuid"] = _options.PluginUuid
        }, _cts.Token);

        var writer = Task.Run(() => WriterLoopAsync(_cts.Token));
        await ReaderLoopAsync(_cts.Token);
        _outbound.Writer.TryComplete();
        await writer;
    }

    public void Dispose()
    {
        _cts?.Cancel();
        _cts?.Dispose();
        _socket.Dispose();
    }

    private async Task ReaderLoopAsync(CancellationToken token)
    {
        var buffer = new byte[64 * 1024];

        while (!token.IsCancellationRequested && _socket.State == WebSocketState.Open)
        {
            using var stream = new MemoryStream();
            WebSocketReceiveResult result;
            do
            {
                result = await _socket.ReceiveAsync(buffer, token);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    return;
                }
                stream.Write(buffer, 0, result.Count);
            }
            while (!result.EndOfMessage);

            var json = Encoding.UTF8.GetString(stream.ToArray());
            await HandleMessageAsync(json, token);
        }
    }

    private async Task HandleMessageAsync(string json, CancellationToken token)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var eventName = ReadString(root, "event");
        var context = ReadString(root, "context");

        switch (eventName)
        {
            case "willAppear":
            case "didReceiveSettings":
                if (!string.IsNullOrEmpty(context))
                {
                    var state = StateFor(context);
                    if (TryGet(root, "payload", out var payload) && TryGet(payload, "settings", out var settings))
                    {
                        ApplySettings(state, settings);
                    }
                    FileLog.Write($"{eventName}: context={context} b64={state.Base64Text.Length} delay={state.DelayMs}");
                    await RefreshButtonAsync(state);
                }
                break;

            case "sendToPlugin":
                if (!string.IsNullOrEmpty(context) && TryGet(root, "payload", out var piPayload))
                {
                    FileLog.Write($"sendToPlugin: context={context} op={ReadString(piPayload, "op")}");
                    await HandlePropertyInspectorAsync(StateFor(context), piPayload, token);
                }
                break;

            case "keyDown":
                if (!string.IsNullOrEmpty(context))
                {
                    FileLog.Write($"keyDown: context={context} foreground=\"{NativeWindow.GetForegroundWindowTitle()}\"");
                    await StartOrCancelTypingAsync(StateFor(context));
                }
                break;

            case "keyUp":
                if (!string.IsNullOrEmpty(context))
                {
                    FileLog.Write($"keyUp: context={context} foreground=\"{NativeWindow.GetForegroundWindowTitle()}\"");
                }
                break;
        }
    }

    private async Task HandlePropertyInspectorAsync(ActionState state, JsonElement payload, CancellationToken token)
    {
        var op = ReadString(payload, "op");
        switch (op)
        {
            case "storeB64":
                state.Base64Text = ReadString(payload, "b64") ?? "";
                state.FileName = ReadString(payload, "fileName") ?? "";
                state.ByteLength = ReadLong(payload, "byteLength");
                state.DelayMs = ClampDelay(ReadInt(payload, "delayMs", state.DelayMs));
                await PersistSettingsAsync(state);
                await RefreshButtonAsync(state);
                await SendToPropertyInspectorAsync(state, new
                {
                    op = "stored",
                    ok = !string.IsNullOrEmpty(state.Base64Text),
                    fileName = state.FileName,
                    byteLength = state.ByteLength,
                    b64Length = state.Base64Text.Length,
                    delayMs = state.DelayMs
                });
                await ShowOkAsync(state.Context);
                FileLog.Write($"stored: context={state.Context} file=\"{state.FileName}\" bytes={state.ByteLength} b64={state.Base64Text.Length} delay={state.DelayMs}");
                break;

            case "setDelay":
                state.DelayMs = ClampDelay(ReadInt(payload, "delayMs", state.DelayMs));
                await PersistSettingsAsync(state);
                await RefreshButtonAsync(state);
                await SendToPropertyInspectorAsync(state, new
                {
                    op = "delayUpdated",
                    delayMs = state.DelayMs
                });
                break;

            case "requestState":
                await SendToPropertyInspectorAsync(state, state.ToPropertyInspectorPayload());
                break;
        }
    }

    private async Task StartOrCancelTypingAsync(ActionState state)
    {
        if (_typingRuns.TryRemove(state.Context, out var running))
        {
            try
            {
                running.Cancel();
                FileLog.Write($"typing cancel requested: context={state.Context}");
                await SetTitleAsync(state.Context, "STOP\nOK");
                await ShowOkAsync(state.Context);
            }
            catch (Exception ex)
            {
                FileLog.Write($"cancel failed: {ex}");
                await ShowAlertAsync(state.Context);
            }
            finally
            {
                running.Dispose();
                await RefreshButtonAsync(state);
            }
            return;
        }

        if (string.IsNullOrWhiteSpace(state.Base64Text))
        {
            FileLog.Write($"paste aborted: empty data context={state.Context}");
            await ShowAlertAsync(state.Context);
            await RefreshButtonAsync(state);
            return;
        }

        var text = state.Base64Text;
        var delayMs = state.DelayMs;
        var run = new TypingRun();
        if (!_typingRuns.TryAdd(state.Context, run))
        {
            run.Dispose();
            return;
        }

        await SetTitleAsync(state.Context, "TYPE\n...");
        await SendToPropertyInspectorAsync(state, new { op = "typing", b64Length = text.Length, delayMs });
        _ = Task.Run(() => RunTypingAsync(state, run, text, delayMs));
    }

    private async Task RunTypingAsync(ActionState state, TypingRun run, string text, int delayMs)
    {
        try
        {
            FileLog.Write($"typing start: context={state.Context} length={text.Length} delay={delayMs} foreground=\"{NativeWindow.GetForegroundWindowTitle()}\"");
            await Task.Delay(500, run.Token);
            PowerShellTyper.TypeText(text, delayMs, run.Token, run.AttachProcess);
            FileLog.Write($"typing done: context={state.Context} length={text.Length}");
            await ShowOkAsync(state.Context);
            await SendToPropertyInspectorAsync(state, new { op = "typed", b64Length = text.Length, delayMs });
        }
        catch (OperationCanceledException)
        {
            FileLog.Write($"typing cancelled: context={state.Context}");
        }
        catch (Exception ex)
        {
            FileLog.Write($"typing failed: {ex}");
            await LogAsync($"Typing failed: {ex}");
            await ShowAlertAsync(state.Context);
        }
        finally
        {
            ((ICollection<KeyValuePair<string, TypingRun>>)_typingRuns).Remove(new KeyValuePair<string, TypingRun>(state.Context, run));
            run.Dispose();
            await RefreshButtonAsync(state);
        }
    }

    private void ApplySettings(ActionState state, JsonElement settings)
    {
        state.Base64Text = ReadString(settings, "b64") ?? state.Base64Text;
        state.FileName = ReadString(settings, "fileName") ?? state.FileName;
        state.ByteLength = ReadLong(settings, "byteLength", state.ByteLength);
        state.DelayMs = ClampDelay(ReadInt(settings, "delayMs", state.DelayMs));
    }

    private ActionState StateFor(string context) => _states.GetOrAdd(context, static ctx => new ActionState(ctx));

    private async Task PersistSettingsAsync(ActionState state)
    {
        await QueueEventAsync("setSettings", state.Context, new
        {
            b64 = state.Base64Text,
            fileName = state.FileName,
            byteLength = state.ByteLength,
            delayMs = state.DelayMs
        });
    }

    private async Task RefreshButtonAsync(ActionState state)
    {
        var hasData = !string.IsNullOrWhiteSpace(state.Base64Text);
        await SetTitleAsync(state.Context, hasData ? $"●\n{state.DelayMs}ms" : DefaultTitle);
        await QueueEventAsync("setImage", state.Context, new
        {
            image = hasData ? "images/actionStored.png" : "images/actionDefault.png"
        });
    }

    private Task SetTitleAsync(string context, string title) => QueueEventAsync("setTitle", context, new { title });
    private Task ShowOkAsync(string context) => QueueRawAsync(new Dictionary<string, object?> { ["event"] = "showOk", ["context"] = context });
    private Task ShowAlertAsync(string context) => QueueRawAsync(new Dictionary<string, object?> { ["event"] = "showAlert", ["context"] = context });
    private Task LogAsync(string message) => QueueRawAsync(new Dictionary<string, object?> { ["event"] = "logMessage", ["payload"] = new { message } });

    private Task SendToPropertyInspectorAsync(ActionState state, object payload) =>
        QueueEventAsync("sendToPropertyInspector", state.Context, payload);

    private Task QueueEventAsync(string eventName, string context, object payload) =>
        QueueRawAsync(new Dictionary<string, object?>
        {
            ["event"] = eventName,
            ["context"] = context,
            ["payload"] = payload
        });

    private Task QueueRawAsync(object value)
    {
        _outbound.Writer.TryWrite(value);
        return Task.CompletedTask;
    }

    private async Task WriterLoopAsync(CancellationToken token)
    {
        await foreach (var item in _outbound.Reader.ReadAllAsync(token))
        {
            await SendRawAsync(item, token);
        }
    }

    private async Task SendRawAsync(object value, CancellationToken token)
    {
        var json = JsonSerializer.Serialize(value, _jsonOptions);
        var bytes = Encoding.UTF8.GetBytes(json);
        await _socket.SendAsync(bytes, WebSocketMessageType.Text, true, token);
    }

    private static bool TryGet(JsonElement element, string name, out JsonElement value)
    {
        value = default;
        return element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out value);
    }

    private static string? ReadString(JsonElement element, string name) =>
        TryGet(element, name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private static int ReadInt(JsonElement element, string name, int fallback = 5)
    {
        if (!TryGet(element, name, out var value)) return fallback;
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number)) return number;
        if (value.ValueKind == JsonValueKind.String && int.TryParse(value.GetString(), out number)) return number;
        return fallback;
    }

    private static long ReadLong(JsonElement element, string name, long fallback = 0)
    {
        if (!TryGet(element, name, out var value)) return fallback;
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var number)) return number;
        if (value.ValueKind == JsonValueKind.String && long.TryParse(value.GetString(), out number)) return number;
        return fallback;
    }

    private static int ClampDelay(int delayMs) => Math.Clamp(delayMs, 1, 10);
}

sealed class ActionState
{
    public ActionState(string context) => Context = context;
    public string Context { get; }
    public string Base64Text { get; set; } = "";
    public string FileName { get; set; } = "";
    public long ByteLength { get; set; }
    public int DelayMs { get; set; } = 5;

    public object ToPropertyInspectorPayload() => new
    {
        op = "state",
        ok = !string.IsNullOrEmpty(Base64Text),
        fileName = FileName,
        byteLength = ByteLength,
        b64Length = Base64Text.Length,
        delayMs = DelayMs
    };
}

static class PowerShellTyper
{
    public static void TypeText(string text, int delayMs, CancellationToken token, Action<Process> onStarted)
    {
        token.ThrowIfCancellationRequested();
        var scriptPath = Path.Combine(AppContext.BaseDirectory, "typer.ps1");
        if (!File.Exists(scriptPath))
        {
            throw new FileNotFoundException("typer.ps1 was not found.", scriptPath);
        }

        var payload = Convert.ToBase64String(Encoding.Unicode.GetBytes(text));
        var startInfo = new ProcessStartInfo("powershell.exe")
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardError = true
        };

        startInfo.ArgumentList.Add("-NoProfile");
        startInfo.ArgumentList.Add("-NonInteractive");
        startInfo.ArgumentList.Add("-ExecutionPolicy");
        startInfo.ArgumentList.Add("Bypass");
        startInfo.ArgumentList.Add("-WindowStyle");
        startInfo.ArgumentList.Add("Hidden");
        startInfo.ArgumentList.Add("-File");
        startInfo.ArgumentList.Add(scriptPath);
        startInfo.ArgumentList.Add("-Base64");
        startInfo.ArgumentList.Add(payload);
        startInfo.ArgumentList.Add("-DelayMs");
        startInfo.ArgumentList.Add(delayMs.ToString());

        var process = Process.Start(startInfo) ?? throw new InvalidOperationException("Failed to start powershell.exe.");
        onStarted(process);
        if (token.IsCancellationRequested)
        {
            KillProcess(process);
            token.ThrowIfCancellationRequested();
        }
        var stderr = process.StandardError.ReadToEnd();
        while (!process.WaitForExit(100))
        {
            if (!token.IsCancellationRequested)
            {
                continue;
            }
            KillProcess(process);
            token.ThrowIfCancellationRequested();
        }
        if (process.ExitCode != 0)
        {
            throw new InvalidOperationException($"powershell.exe exited {process.ExitCode}: {stderr.Trim()}");
        }
    }

    private static void KillProcess(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            // The process may have exited between HasExited and Kill.
        }
    }
}

static class SingleInstanceGuard
{
    public static bool AnotherInstanceAlreadyRunning()
    {
        try
        {
            Thread.Sleep(500);
            using var current = Process.GetCurrentProcess();
            var currentStart = current.StartTime;
            foreach (var process in Process.GetProcessesByName(current.ProcessName))
            {
                using (process)
                {
                    if (process.Id == current.Id)
                    {
                        continue;
                    }

                    try
                    {
                        if (process.StartTime < currentStart || (process.StartTime == currentStart && process.Id < current.Id))
                        {
                            FileLog.Write($"exiting duplicate process: current={current.Id} existing={process.Id}");
                            return true;
                        }
                    }
                    catch
                    {
                        if (process.Id < current.Id)
                        {
                            return true;
                        }
                    }
                }
            }
        }
        catch
        {
            // If process inspection fails, keep running and let Stream Deck manage the process.
        }

        return false;
    }
}

sealed class TypingRun : IDisposable
{
    private readonly object _sync = new();
    private Process? _process;
    private bool _disposed;
    private bool _cancelRequested;
    private readonly CancellationTokenSource _cts = new();

    public CancellationToken Token => _cts.Token;

    public void AttachProcess(Process process)
    {
        lock (_sync)
        {
            if (_disposed)
            {
                DisposeProcess(process);
                return;
            }

            _process = process;
            if (_cancelRequested)
            {
                KillProcess(process);
            }
        }
    }

    public void Cancel()
    {
        lock (_sync)
        {
            _cancelRequested = true;
            _cts.Cancel();
            if (_process is not null)
            {
                KillProcess(_process);
            }
        }
    }

    public void Dispose()
    {
        lock (_sync)
        {
            if (_disposed)
            {
                return;
            }
            _disposed = true;
            _cts.Dispose();
            if (_process is not null)
            {
                DisposeProcess(_process);
                _process = null;
            }
        }
    }

    private static void KillProcess(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            // Ignore races with natural process exit.
        }
    }

    private static void DisposeProcess(Process process)
    {
        try
        {
            process.Dispose();
        }
        catch
        {
            // Best-effort cleanup.
        }
    }
}

static class NativeWindow
{
    public static string GetForegroundWindowTitle()
    {
        var handle = GetForegroundWindow();
        if (handle == IntPtr.Zero)
        {
            return "";
        }

        var builder = new StringBuilder(512);
        _ = GetWindowText(handle, builder, builder.Capacity);
        return builder.ToString();
    }

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
}

static class FileLog
{
    private static readonly object Lock = new();
    private static readonly string Path = System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "Elgato",
        "StreamDeck",
        "logs",
        "com.patentcompare.xcbkm-b64-paster.log");

    public static void Write(string message)
    {
        try
        {
            lock (Lock)
            {
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
                File.AppendAllText(Path, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} {message}{Environment.NewLine}", Encoding.UTF8);
            }
        }
        catch
        {
            // Logging must never break the Stream Deck action.
        }
    }
}

sealed record CliOptions(int Port, string PluginUuid, string RegisterEvent, string InfoJson)
{
    public bool IsStreamDeckLaunch => Port > 0 && !string.IsNullOrWhiteSpace(PluginUuid) && !string.IsNullOrWhiteSpace(RegisterEvent);

    public static CliOptions Parse(string[] args)
    {
        var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (var i = 0; i < args.Length; i++)
        {
            if (!args[i].StartsWith("-", StringComparison.Ordinal)) continue;
            var key = args[i].TrimStart('-');
            if (i + 1 < args.Length && !args[i + 1].StartsWith("-", StringComparison.Ordinal))
            {
                map[key] = args[++i];
            }
            else
            {
                map[key] = "";
            }
        }

        _ = int.TryParse(map.GetValueOrDefault("port"), out var port);
        return new CliOptions(
            port,
            map.GetValueOrDefault("pluginUUID") ?? "",
            map.GetValueOrDefault("registerEvent") ?? "",
            map.GetValueOrDefault("info") ?? "");
    }
}
