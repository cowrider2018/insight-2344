# 每日 06:00 排程包裝：抓資料 -> Claude 分析 -> 寄信。
# 由 Windows 工作排程器呼叫：
#   schtasks /Create /TN "CMoney_2344_Daily" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\johnyou\Desktop\make-money\src\run_daily.ps1" /SC DAILY /ST 06:00 /F

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"   # 強制 Python 以 UTF-8 輸出，避免中文 log 變亂碼
# 排程環境沒有主控台時 [Console]::OutputEncoding 會退回 OEM codepage (cp950)，
# PowerShell 便以 cp950 解碼 Python 的 UTF-8 輸出而寫出亂碼。釘死成 UTF-8。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$proj = Split-Path -Parent $PSScriptRoot   # 專案根目錄
Set-Location $proj

$today = Get-Date -Format "yyyyMMdd"
$logDir = Join-Path $proj "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "run_$today.log"

# Tee-Object 在 PS 5.1 沒有 -Encoding，一律寫 UTF-16LE。此 filter 以 UTF-8 附加並原樣傳遞。
filter TeeUtf8([string]$Path) {
    # 原生指令的 stderr 經 2>&1 會包成 ErrorRecord；取其文字即可，
    # 否則 Out-String 會連 CategoryInfo/FullyQualifiedErrorId 等格式雜訊一起寫進 log。
    $s = if ($_ -is [string]) { $_ }
         elseif ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() }
         else { ($_ | Out-String).TrimEnd("`r", "`n") }
    Add-Content -LiteralPath $Path -Value $s -Encoding UTF8
    $s
}

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    $line | TeeUtf8 $log
}

# 執行原生指令並完整記錄輸出，回傳其 exit code。
# 原生指令的 stderr 經 2>&1 會變成 ErrorRecord，搭配 $ErrorActionPreference="Stop"
# 會立刻拋出 NativeCommandError：只留下 stderr 第一行、吞掉整段 traceback，
# 並讓呼叫端的 $LASTEXITCODE 判斷完全失效。故此處降為 Continue，成敗一律由 exit code 決定。
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

# python / claude 可執行檔。先用 PATH，找不到再退回已知安裝位置。
# claude CLI 在排程環境（VS Code 未開啟）通常不在 PATH，需自行解析。
function Resolve-Exe {
    param([string]$Name, [string[]]$Globs)
    $c = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c -and $c.Source) { return $c.Source }
    foreach ($g in $Globs) {
        $hit = Get-Item $g -ErrorAction SilentlyContinue |
               Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $Name   # 最後退回名稱，交給 PATH 解析
}

# 優先使用專案 .venv（setup.bat 建立）；找不到才退回 PATH / 已知安裝位置。
$venvPy = Join-Path $proj ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $py = $venvPy
} else {
    $py = Resolve-Exe -Name "python" -Globs @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe"
    )
}
$claude = Resolve-Exe -Name "claude" -Globs @(
    "$env:USERPROFILE\.local\bin\claude.exe",
    "$env:APPDATA\npm\claude.cmd",
    "$env:USERPROFILE\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe"
)

try {
    Log "==== 開始每日流程 2344 ===="
    Log "python = $py"
    Log "claude = $claude"

    # 1) 標準化抓取
    Log "Step1 build_dataset"
    $code = Invoke-Step $py @((Join-Path $proj "src\build_dataset.py"))
    if ($code -ne 0) { throw "build_dataset 失敗 (exit $code)" }

    # 2) Claude 分析（無頭模式，寫入 reports/）
    Log "Step2 claude 分析"
    $code = Invoke-Step $claude @("-p", "/cmoney-2344-daily", "--permission-mode", "acceptEdits", "--add-dir", $proj)
    if ($code -ne 0) { Log "warn: claude 回傳非零 exit ($code)，仍嘗試寄出現有報告" }

    # 報告路徑容錯：headless Claude 可能寫頂層 reports\ 或 symbol 子目錄 reports\2344\
    # （後者為 config.report_path 決策卡位置）。頂層優先，退回子目錄，用先找到的。
    $report = Join-Path $proj "reports\2344_$today.md"
    if (-not (Test-Path $report)) {
        $alt = Join-Path $proj "reports\2344\2344_$today.md"
        if (Test-Path $alt) { $report = $alt }
    }
    if (-not (Test-Path $report)) { throw "找不到報告 $report（Claude 分析未產生輸出）" }
    Log "報告位置: $report"

    # 3) 寄信（非致命：未設定 Gmail OAuth 或寄信失敗時只記警告，報告已產生仍算完成）
    Log "Step3 send_email"
    $code = Invoke-Step $py @((Join-Path $proj "src\send_email.py"), $report)
    if ($code -ne 0) { Log "warn: send_email 失敗 (exit $code)，跳過寄信；報告已產生於 $report（完整錯誤見上方 log）" }

    Log "==== 完成 ===="
}
catch {
    $err = $_.Exception.Message
    Log "ERROR: $err"
    try {
        $code = Invoke-Step $py @((Join-Path $proj "src\send_email.py"), "--error", "$err`n詳見 $log")
        if ($code -ne 0) { Log "錯誤通知信也寄送失敗 (exit $code)" }
    }
    catch { Log "錯誤通知信也寄送失敗: $($_.Exception.Message)" }
    exit 1
}
