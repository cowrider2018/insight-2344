# 每日 05:30 盤前排程：刷新日 K + 補齊 1 分 K，供 06:00 的分析使用。
# 由 Windows 工作排程器呼叫（schedule_create.bat 會一併建立）：
#   schtasks /Create /TN "CMoney_2344_Intraday" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File ...\src\run_intraday.ps1" /SC DAILY /ST 05:30 /F

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"   # 強制 Python 以 UTF-8 輸出，避免中文 log 變亂碼
# 排程環境沒有主控台時 [Console]::OutputEncoding 會退回 OEM codepage (cp950)，
# PowerShell 便以 cp950 解碼 Python 的 UTF-8 輸出而寫出亂碼。釘死成 UTF-8。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$proj = Split-Path -Parent $PSScriptRoot
Set-Location $proj

$today = Get-Date -Format "yyyyMMdd"
$logDir = Join-Path $proj "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
# 與 06:00 的流程共用當日 log，時序一目了然。
$log = Join-Path $logDir "run_$today.log"

# Tee-Object 在 PS 5.1 沒有 -Encoding，一律寫 UTF-16LE。此 filter 以 UTF-8 附加並原樣傳遞。
filter TeeUtf8([string]$Path) {
    $s = if ($_ -is [string]) { $_ }
         elseif ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() }
         else { ($_ | Out-String).TrimEnd("`r", "`n") }
    Add-Content -LiteralPath $Path -Value $s -Encoding UTF8
    $s
}

function Log($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | TeeUtf8 $log
}

# 原生指令的 stderr 經 2>&1 會變成 ErrorRecord，搭配 $ErrorActionPreference="Stop"
# 會立刻拋出 NativeCommandError 並吞掉 traceback。故降為 Continue，成敗由 exit code 決定。
function Invoke-Step {
    param([string]$Exe, [string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments 2>&1 | TeeUtf8 $log | Out-Host
        return $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $prev }
}

$venvPy = Join-Path $proj ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $py = $venvPy
} else {
    $c = Get-Command "python" -ErrorAction SilentlyContinue | Select-Object -First 1
    $py = if ($c -and $c.Source) { $c.Source } else { "python" }
}

Log "==== 盤前 1 分 K 補齊 2344 ===="
Log "python = $py"

# 非致命：這是 06:00 分析的「先行 + 保險」，build_dataset 本身仍會抓當日 1 分 K。
# 失敗只記錄，不寄錯誤信、不擋 06:00 的流程。
$code = Invoke-Step $py @((Join-Path $proj "src\backfill.py"), "--intraday", "--days", "30")
if ($code -ne 0) {
    Log "warn: 盤前 1 分 K 補齊失敗 (exit $code)；06:00 的 build_dataset 仍會自行抓取"
}

Log "==== 盤前完成 ===="
