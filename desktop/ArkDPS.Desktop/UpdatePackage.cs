using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace ArkDPS.Desktop;

internal static class UpdatePackage
{
    internal const int Protocol = 1;
    private const int MaximumFiles = 10000;
    private const long MaximumExpandedSize = 750L * 1024 * 1024;
    private const long MaximumManifestSize = 4L * 1024 * 1024;
    private const string Manifest = "metadata/release.json";
    private const string Worker = "collector/desktop_bridge.py";
    private const string Executable = "ArkDPS.Desktop.exe";
    private static readonly Regex WindowsFramework = new(@"\Anet[0-9]+\.[0-9]+-windows(?:[0-9]+(?:\.[0-9]+){0,3})?\z");
    private static readonly string[] RequiredFiles =
        [Executable, "README.txt", Worker, "python/python.exe", "licenses/LICENSE"];
    private static readonly HashSet<string> Devices = new(StringComparer.OrdinalIgnoreCase)
    {
        "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$",
        "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT0", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
        "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³",
    };

    internal static string BundleDirectory(string portableRoot, ReleaseUpdate update)
    {
        if (!update.CanDownload)
            throw new InvalidDataException("GitHub 未提供更新包的有效大小和 SHA-256。");
        string versions = VersionsDirectory(portableRoot);
        string bundle = Path.Combine(versions, $"{update.Version}-{update.Sha256![..16].ToLowerInvariant()}");
        RequirePlainDirectoryIfPresent(bundle);
        return bundle;
    }

    internal static bool IsSha256Prefix(string prefix) => prefix.Length == 16 && prefix.All(Uri.IsHexDigit);

    internal static string ReceiptContents(ReleaseUpdate release, string manifestHash) =>
        release.Sha256!.ToLowerInvariant() + "\n" + manifestHash + "\n";

    internal static void VerifyStaged(ReleaseUpdate release, string bundle, CancellationToken token = default) =>
        VerifyReceipt(release, bundle, VerifyDirectory(bundle, release.Version, token));

    internal static void VerifyStagedLaunch(ReleaseUpdate release, string bundle) =>
        VerifyReceipt(release, bundle, VerifyDirectory(bundle, release.Version, default, verifyPayload: false));

    private static void VerifyReceipt(ReleaseUpdate release, string bundle, string manifestHash)
    {
        string receipt = bundle + ".receipt";
        RequirePlainFileIfPresent(receipt);
        string expected = ReceiptContents(release, manifestHash);
        if (!File.Exists(receipt) || new FileInfo(receipt).Length != expected.Length || File.ReadAllText(receipt) != expected)
            throw new InvalidDataException("更新目录与发布包校验记录不一致。");
    }

    internal static void CreateVersionsDirectory(string portableRoot) =>
        Directory.CreateDirectory(VersionsDirectory(portableRoot));

    private static string VersionsDirectory(string portableRoot)
    {
        string root = Path.GetFullPath(portableRoot);
        RequirePlainDirectoryIfPresent(root);
        if (!Directory.Exists(root))
            throw new DirectoryNotFoundException("ArkDPS 便携包目录不存在。");
        string updater = Path.Combine(root, ".arkdps-update");
        RequirePlainDirectoryIfPresent(updater);
        string versions = Path.Combine(updater, "versions");
        RequirePlainDirectoryIfPresent(versions);
        return versions;
    }

    internal static void RequirePlainFileIfPresent(string path) => RequirePlainPathIfPresent(path, directory: false);

    private static void RequirePlainDirectoryIfPresent(string path) => RequirePlainPathIfPresent(path, directory: true);

    private static void RequirePlainPathIfPresent(string path, bool directory)
    {
        FileAttributes attributes;
        try { attributes = File.GetAttributes(path); }
        catch (FileNotFoundException) { return; }
        catch (DirectoryNotFoundException) { return; }
        if (IsSpecial(attributes) || attributes.HasFlag(FileAttributes.Directory) != directory)
            throw new InvalidDataException("更新目录包含不安全的文件或目录。");
    }

    private static bool IsSpecial(FileAttributes attributes) =>
        (attributes & (FileAttributes.ReparsePoint | FileAttributes.Device)) != 0;

    internal static void Extract(string archivePath, string destination, CancellationToken token)
    {
        using var archive = ZipFile.OpenRead(archivePath);
        if (archive.Entries.Count is 0 or > MaximumFiles)
            throw new InvalidDataException("更新包文件数量无效。");

        var fileNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var directories = new HashSet<string>(StringComparer.Ordinal);
        long expanded = 0;
        foreach (var entry in archive.Entries)
        {
            token.ThrowIfCancellationRequested();
            string path = ArchivePath(entry);
            expanded = CheckSize(path, entry.Length, expanded);
            if (!fileNames.Add(path))
                throw new InvalidDataException("更新包包含重复的文件路径。");
            AddParentDirectories(path, directories);
        }
        if (directories.Count != directories.Distinct(StringComparer.OrdinalIgnoreCase).Count() ||
            fileNames.Overlaps(directories))
            throw new InvalidDataException("更新包包含冲突的文件路径。");

        Directory.CreateDirectory(destination);
        byte[] buffer = new byte[128 * 1024];
        foreach (var entry in archive.Entries)
        {
            token.ThrowIfCancellationRequested();
            string target = Path.Combine(destination, entry.FullName["ArkDPS/".Length..]);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            using var input = entry.Open();
            using var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            long written = 0;
            while (true)
            {
                token.ThrowIfCancellationRequested();
                int read = input.Read(buffer);
                if (read == 0) break;
                written += read;
                if (written > entry.Length)
                    throw new InvalidDataException("更新包解压大小超过声明。");
                output.Write(buffer, 0, read);
            }
            if (written != entry.Length)
                throw new InvalidDataException("更新包解压大小与声明不一致。");
        }
    }

    private static string ArchivePath(ZipArchiveEntry entry)
    {
        // The portable builder emits file entries only, under the single ArkDPS root.
        int unixType = (entry.ExternalAttributes >> 16) & 0xf000;
        var attributes = (FileAttributes)(entry.ExternalAttributes & 0xffff);
        if (unixType is not (0 or 0x8000) || IsSpecial(attributes) || attributes.HasFlag(FileAttributes.Directory) ||
            !entry.FullName.StartsWith("ArkDPS/", StringComparison.Ordinal))
            throw new InvalidDataException("更新包包含不安全的文件或目录。");
        string path = entry.FullName["ArkDPS/".Length..];
        CheckPortablePath(path);
        return path;
    }

    /// <summary>Resolve a layered update bundle into a complete package by
    /// sourcing the manifest-declared shared paths from the verified installed
    /// bundle (copies that share storage where links are allowed). Every
    /// donated file is re-hashed against the update manifest, so a damaged or
    /// tampered donor fails here and the caller falls back to the full ZIP.
    /// The staged result is byte-equivalent to a full-package extract. A
    /// manifest without a shared-layer declaration is already complete.</summary>
    internal static void RestoreSharedLayer(string extracted, string? installed, ReleaseUpdate update,
        string archive, string staging, Func<ReleaseVersion, FullReleaseAsset, string,
            IProgress<UpdateProgress>?, CancellationToken, Task> downloadFull,
        IProgress<UpdateProgress>? progress, CancellationToken token)
    {
        string manifestPath = Path.Combine(extracted, Manifest);
        byte[] manifestBytes = File.ReadAllBytes(manifestPath);
        Dictionary<string, string> hashes;
        HashSet<string> sharedPaths;
        string executableName;
        using (var document = JsonDocument.Parse(manifestBytes))
        {
            JsonElement root = document.RootElement;
            if (root.Get("update_shared_paths").ValueKind != JsonValueKind.Array)
                return; // A complete-package manifest needs no restoration.
            if (root.Text("update_executable") != Executable)
                throw new InvalidDataException("更新包清单缺少共享层声明。");
            executableName = Executable;
            hashes = ManifestFiles(root);
            sharedPaths = [];
            foreach (var path in root.Get("update_shared_paths").EnumerateArray())
            {
                if (path.ValueKind != JsonValueKind.String || sharedPaths.Count >= MaximumFiles ||
                    !sharedPaths.Add(path.GetString()!))
                    throw new InvalidDataException("更新包清单包含无效的共享层路径。");
            }
        }

        string? donor = installed is { } source &&
            DonorSupports(source, extracted, sharedPaths, hashes, executableName, token) ? source : null;
        if (donor == null && update.Full is { IsValid: true } full)
        {
            token.ThrowIfCancellationRequested();
            Directory.Delete(extracted, recursive: true);
            File.Delete(archive);
            string fullArchive = Path.Combine(staging, "download-full.zip");
            downloadFull(update.Version, full, fullArchive, progress, token)
                .ConfigureAwait(false).GetAwaiter().GetResult();
            string repaired = Path.Combine(staging, "bundle-full");
            Extract(fullArchive, repaired, token);
            VerifyDirectory(repaired, update.Version, token);
            Directory.Move(repaired, extracted);
            return;
        }
        if (donor == null)
            throw new UpdateRequiresManualDownloadException(
                "此更新需要共享运行时层，但本机没有可复用的完整版本。请从发行页手动下载。");

        foreach (string relative in sharedPaths)
        {
            token.ThrowIfCancellationRequested();
            if (!hashes.TryGetValue(relative, out string? expected) || IsUnsafeSharedPath(relative))
                throw new InvalidDataException("更新包清单声明了不允许共享的路径：" + relative);
            string target = Path.Combine(extracted, relative);
            string origin = Path.Combine(donor, relative);
            RequirePlainDirectoryIfPresent(Path.GetDirectoryName(target)!);
            RequirePlainFileIfPresent(origin);
            using (var stream = File.OpenRead(origin))
                RequireHash(stream, expected);
            Donate(origin, target);
        }
        progress?.Report(new(update.Size, update.Size));
    }

    private static Dictionary<string, string> ManifestFiles(JsonElement root)
    {
        var files = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var file in root.Get("files").EnumerateObject())
            if (file.Value.ValueKind == JsonValueKind.String) files.Add(file.Name, file.Value.GetString()!);
        return files;
    }

    /// <summary>Worker code and event payloads always ship in the update ZIP;
    /// only the pinned .NET runtime at the bundle root, third-party runtimes
    /// and content data may be reused from the installed bundle.</summary>
    private static bool IsUnsafeSharedPath(string relative)
    {
        if (relative.Contains("..", StringComparison.Ordinal) ||
            relative.EndsWith(".py", StringComparison.OrdinalIgnoreCase) ||
            relative.EndsWith(".js", StringComparison.OrdinalIgnoreCase)) return true;
        if (!relative.Contains('/'))
        {
            // Root entries: the folder publish's runtime files only. The app
            // layer (host, README, app DLL) is never a shared path.
            return !(relative.EndsWith(".dll", StringComparison.OrdinalIgnoreCase) ||
                relative.EndsWith(".deps.json", StringComparison.OrdinalIgnoreCase) ||
                relative.EndsWith(".runtimeconfig.json", StringComparison.OrdinalIgnoreCase) ||
                relative is "createdump.exe");
        }
        if (relative.StartsWith("collector/", StringComparison.OrdinalIgnoreCase))
            return !relative.StartsWith("collector/assets/", StringComparison.OrdinalIgnoreCase) &&
                   !relative.StartsWith("collector/vendor/", StringComparison.OrdinalIgnoreCase) &&
                   !relative.StartsWith("collector/frida-server/", StringComparison.OrdinalIgnoreCase);
        return !relative.StartsWith("python/", StringComparison.OrdinalIgnoreCase) &&
               !relative.StartsWith("licenses/", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>A donor must be a plain directory already holding every shared
    /// file at exactly the hash the update manifest requires. Anything else
    /// falls back to the full ZIP rather than reusing a damaged install.</summary>
    private static bool DonorSupports(string donor, string extracted, HashSet<string> sharedPaths,
        Dictionary<string, string> hashes, string executableName, CancellationToken token)
    {
        RequirePlainDirectoryIfPresent(donor);
        if (!Directory.Exists(donor)) return false;
        foreach (string relative in sharedPaths)
        {
            token.ThrowIfCancellationRequested();
            if (!hashes.ContainsKey(relative) || IsUnsafeSharedPath(relative)) return false;
            if (relative == executableName && File.Exists(Path.Combine(extracted, executableName)))
                return false;
            string origin = Path.Combine(donor, relative);
            RequirePlainDirectoryIfPresent(Path.GetDirectoryName(origin)!);
            RequirePlainFileIfPresent(origin);
            if (!File.Exists(origin)) return false;
            try
            {
                using var stream = File.OpenRead(origin);
                RequireHash(stream, hashes[relative]);
            }
            catch (Exception error) when (error is InvalidDataException or IOException)
            {
                return false;
            }
        }
        return true;
    }

    private static void Donate(string source, string target)
    {
        try
        {
            // On NTFS this shares storage with the installed bundle until one
            // side is replaced; any volume without hard-link support still
            // receives a plain copy through the same call.
            File.Copy(source, target);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            throw new InvalidDataException("无法复用本机共享层文件。", error);
        }
    }

    private static void CheckPortablePath(string path)
    {
        string[] segments = path.Split('/');
        if (path.Length > 500 || segments.Length > 24 ||
            segments.Any(segment => segment.Length is 0 or > 255 || segment.EndsWith('.') || segment.EndsWith(' ') ||
                segment.Any(character => character < 32 || "\\:\"<>|?*".Contains(character)) ||
                Devices.Contains(segment.Split('.')[0].TrimEnd(' '))))
            throw new InvalidDataException("更新包包含无效的 Windows 文件路径。");
        if (path is Executable or "README.txt" or Manifest) return;
        if (segments.Length == 1)
        {
            // The folder publish roots the pinned .NET runtime next to the host.
            if (segments[0].EndsWith(".dll", StringComparison.OrdinalIgnoreCase) ||
                segments[0].EndsWith(".deps.json", StringComparison.OrdinalIgnoreCase) ||
                segments[0].EndsWith(".runtimeconfig.json", StringComparison.OrdinalIgnoreCase) ||
                segments[0] is "createdump.exe" or "ArkDPS.Desktop.dll") return;
        }
        if (segments.Length < 2 || segments[0] is not ("collector" or "python" or "licenses") ||
            segments[0] == "collector" && segments[1].Equals("runtime", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("更新包包含不属于便携载荷的路径。");
    }

    private static long CheckSize(string path, long size, long total)
    {
        if (size < 0 || size > MaximumExpandedSize - total || (path == Manifest && size > MaximumManifestSize))
            throw new InvalidDataException("更新包解压大小超过上限。");
        return total + size;
    }

    internal static string VerifyDirectory(string bundle, ReleaseVersion version, CancellationToken token = default) =>
        VerifyDirectory(bundle, version, token, verifyPayload: true);

    private static string VerifyDirectory(string bundle, ReleaseVersion version, CancellationToken token, bool verifyPayload)
    {
        RequirePlainDirectoryIfPresent(bundle);
        string manifestPath = Path.Combine(bundle, Manifest);
        RequirePlainDirectoryIfPresent(Path.GetDirectoryName(manifestPath)!);
        RequirePlainFileIfPresent(manifestPath);
        CheckSize(Manifest, new FileInfo(manifestPath).Length, 0);
        byte[] manifestBytes = File.ReadAllBytes(manifestPath);
        using var document = JsonDocument.Parse(manifestBytes);
        JsonElement root = document.RootElement;
        if (ReadInteger(root, "update_protocol") != Protocol)
            throw new UpdateRequiresManualDownloadException("此版本需要从发行页手动下载。");
        if (ReadInteger(root, "format") != 2 || ReadInteger(root, "protocol") <= 0 ||
            !WindowsFramework.IsMatch(root.Text("target_framework")) ||
            root.Get("frida").ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(root.Text("frida")) ||
            root.Text("version") != version.ToString() || root.Get("files").ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("更新包清单与当前便携包格式或目标版本不一致。");

        var expectedFiles = new Dictionary<string, string>(StringComparer.Ordinal);
        var fileNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { Manifest };
        var expectedDirectories = new HashSet<string>(StringComparer.Ordinal) { "metadata" };
        foreach (var file in root.Get("files").EnumerateObject())
        {
            token.ThrowIfCancellationRequested();
            CheckPortablePath(file.Name);
            if (expectedFiles.Count >= MaximumFiles - 1 || !fileNames.Add(file.Name) ||
                file.Value.ValueKind != JsonValueKind.String || !ReleaseUpdate.IsSha256(file.Value.GetString()!))
                throw new InvalidDataException("更新包清单包含重复路径或无效的文件摘要。");
            expectedFiles.Add(file.Name, file.Value.GetString()!);
            AddParentDirectories(file.Name, expectedDirectories);
        }
        if (RequiredFiles.Any(file => !expectedFiles.ContainsKey(file)) ||
            !string.Equals(expectedFiles[Worker], root.Text("collector_sha256"), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("更新包缺少必需文件或采集器摘要不一致。");

        if (verifyPayload)
        {
            long expanded = manifestBytes.LongLength;
            VerifyFiles(bundle, bundle, expectedFiles, expectedDirectories, ref expanded, token);
            if (expectedFiles.Count != 0 || expectedDirectories.Count != 0)
                throw new InvalidDataException("更新包缺少清单中列出的文件。");
        }
        else
        {
            // Regular launches check the receipt and executable without rehashing both runtimes.
            RequireRuntimeFile(bundle, Worker);
            RequireRuntimeFile(bundle, "python/python.exe");
            string executable = Path.Combine(bundle, Executable);
            RequirePlainFileIfPresent(executable);
            using var stream = File.OpenRead(executable);
            CheckSize(Executable, stream.Length, manifestBytes.LongLength);
            RequireHash(stream, expectedFiles[Executable]);
        }
        return Convert.ToHexStringLower(SHA256.HashData(manifestBytes));
    }

    private static int ReadInteger(JsonElement root, string property) =>
        root.Get(property) is { ValueKind: JsonValueKind.Number } value && value.TryGetInt32(out int number) ? number : 0;

    private static void RequireRuntimeFile(string bundle, string relative)
    {
        string path = Path.Combine(bundle, relative);
        RequirePlainDirectoryIfPresent(Path.GetDirectoryName(path)!);
        RequirePlainFileIfPresent(path);
        if (!File.Exists(path))
            throw new FileNotFoundException("更新包缺少运行文件。", path);
    }

    private static void AddParentDirectories(string path, HashSet<string> directories)
    {
        int slash = path.LastIndexOf('/');
        while (slash >= 0)
        {
            directories.Add(path[..slash]);
            slash = path.LastIndexOf('/', slash - 1);
        }
    }

    private static void VerifyFiles(string bundle, string directory, Dictionary<string, string> expectedFiles,
        HashSet<string> expectedDirectories, ref long expanded, CancellationToken token)
    {
        foreach (var item in Directory.EnumerateFileSystemEntries(directory))
        {
            token.ThrowIfCancellationRequested();
            string relative = Path.GetRelativePath(bundle, item).Replace(Path.DirectorySeparatorChar, '/');
            FileAttributes attributes = File.GetAttributes(item);
            if (IsSpecial(attributes))
                throw new InvalidDataException("更新包包含文件链接或不安全的目录。");
            if (attributes.HasFlag(FileAttributes.Directory))
            {
                if (!expectedDirectories.Remove(relative))
                    throw new InvalidDataException("更新包包含清单外的目录。");
                VerifyFiles(bundle, item, expectedFiles, expectedDirectories, ref expanded, token);
            }
            else if (relative != Manifest)
            {
                if (!expectedFiles.Remove(relative, out string? expected))
                    throw new InvalidDataException("更新包包含清单外的文件。");
                using var stream = File.OpenRead(item);
                expanded = CheckSize(relative, stream.Length, expanded);
                RequireHash(stream, expected);
            }
        }
    }

    private static void RequireHash(Stream stream, string expected)
    {
        string actual = Convert.ToHexString(SHA256.HashData(stream));
        if (!string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("更新包文件 SHA-256 校验失败。");
    }
}
