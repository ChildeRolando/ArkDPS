param(
    [Parameter(Mandatory=$true)][string]$Head,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$FridaRemote,
    [Parameter(Mandatory=$true)][ValidateSet('OF-8','OF-2')][string]$Stage,
    [Parameter(Mandatory=$true)][string]$MaaJob,
    [string]$MaaLog,
    [string]$StopFile,
    [double]$Seconds = 180,
    [string]$Python = 'E:/AgentWorkspaces/ArkDPS/probe-output/ark5-frida-17.26.1/Scripts/python.exe'
)
$ErrorActionPreference = 'Stop'
$captureRepo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $captureRepo
try {
    $captureArgs = @('-X', 'utf8', 'app.py', '--capture-seconds', "$Seconds",
        '--adb', 'C:/Program Files/Netease/MuMu Player 12/shell/adb.exe',
        '--serial', '127.0.0.1:16384',
        '--wait-battle-seconds', '240', '--baseline-timeout', '90',
        '--expect-head', $Head, '--output-dir', $OutputDirectory,
        '--stage', $Stage, '--maa-job', $MaaJob, '--require-progress', '--require-events')
    if ($FridaRemote) { $captureArgs += @('--frida-remote', $FridaRemote) }
    if ($MaaLog) { $captureArgs += @('--maa-log', $MaaLog) }
    if ($StopFile) { $captureArgs += @('--stop-file', $StopFile) }
    & $Python @captureArgs
    $captureExit = $LASTEXITCODE
    if (Test-Path -LiteralPath $OutputDirectory) {
        $manifest = @(Get-ChildItem -LiteralPath $OutputDirectory -File |
            Where-Object { $_.Name -ne 'sha256.json' } | ForEach-Object {
                [pscustomobject]@{file=$_.Name; bytes=$_.Length;
                    sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
            })
        $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutputDirectory 'sha256.json') -Encoding UTF8
    }
    if ($captureExit -ne 0) { throw "Capture failed with exit code $captureExit; inspect summary.json and capture.jsonl" }
} finally {
    Pop-Location
}
