# 每日 06:00 排程包裝：抓資料 -> Claude 分析 -> 寄信。
# 由 Windows 工作排程器呼叫：
#   schtasks /Create /TN "CMoney_2344_Daily" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\johnyou\Desktop\make-money\src\run_daily.ps1" /SC DAILY /ST 06:00 /F

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"   # 強制 Python 以 UTF-8 輸出，避免中文 log 變亂碼
$proj = Split-Path -Parent $PSScriptRoot   # 專案根目錄
Set-Location $proj

$today = Get-Date -Format "yyyyMMdd"
$logDir = Join-Path $proj "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "run_$today.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    $line | Tee-Object -FilePath $log -Append
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

$py = Resolve-Exe -Name "python" -Globs @(
    "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe"
)
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
    & $py (Join-Path $proj "src\build_dataset.py") 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "build_dataset 失敗 (exit $LASTEXITCODE)" }

    # 2) Claude 分析（無頭模式，寫入 reports/）
    Log "Step2 claude 分析"
    & $claude -p "/cmoney-2344-daily" --permission-mode acceptEdits --add-dir $proj 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { Log "warn: claude 回傳非零 exit ($LASTEXITCODE)，仍嘗試寄出現有報告" }

    $report = Join-Path $proj "reports\2344_$today.md"
    if (-not (Test-Path $report)) { throw "找不到報告 $report（Claude 分析未產生輸出）" }

    # 3) 寄信
    Log "Step3 send_email"
    & $py (Join-Path $proj "src\send_email.py") $report 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "send_email 失敗 (exit $LASTEXITCODE)" }

    Log "==== 完成 ===="
}
catch {
    $err = $_.Exception.Message
    Log "ERROR: $err"
    try { & $py (Join-Path $proj "src\send_email.py") "--error" "$err`n詳見 $log" 2>&1 | Tee-Object -FilePath $log -Append }
    catch { Log "錯誤通知信也寄送失敗: $($_.Exception.Message)" }
    exit 1
}
