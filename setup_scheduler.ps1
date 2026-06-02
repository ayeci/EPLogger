# ============================================================
# EPLogger 自動実行タスク登録
# - scraper.py: 30分おき
# - report_summary.py: 朝7時、正午、夕方17時の3回
# - スリープ中はスキップ
# ============================================================

$ProjectDir = "D:\Users\ayebee\source\repos\EPLogger"
$PyExe = "py"
$PyArg = "-3.12"

# ログ保存先（自動作成）
$LogDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# 共通設定（スリープ中スキップ、失敗時リトライ）
$commonSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

# WakeToRun を明示的に false に
$commonSettings.WakeToRun = $false


# ============================================================
# タスク1: scraper.py を30分おきに実行
# ============================================================
$scraperAction = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c $PyExe $PyArg scraper.py >> logs\scraper.log 2>&1" `
    -WorkingDirectory $ProjectDir

$scraperTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName "EPLogger-Scraper" `
    -Description "EPLogger スクレイパー（30分おき）" `
    -Action $scraperAction `
    -Trigger $scraperTrigger `
    -Settings $commonSettings `
    -Force


# ============================================================
# タスク2: report_summary.py を1日3回実行
# ============================================================
$reportAction = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c $PyExe $PyArg report_summary.py >> logs\report.log 2>&1" `
    -WorkingDirectory $ProjectDir

$reportTriggers = @(
    New-ScheduledTaskTrigger -Daily -At 7:00am
    New-ScheduledTaskTrigger -Daily -At 12:00pm
    New-ScheduledTaskTrigger -Daily -At 17:00pm
)

Register-ScheduledTask `
    -TaskName "EPLogger-Report" `
    -Description "発電状況をずんだもんが Google Home で読み上げ（朝・昼・夕）" `
    -Action $reportAction `
    -Trigger $reportTriggers `
    -Settings $commonSettings `
    -Force


Write-Host "`n登録完了！"
Write-Host "  - EPLogger-Scraper: 30分おき"
Write-Host "  - EPLogger-Report:  7:00, 12:00, 17:00"
Write-Host "`n確認コマンド:"
Write-Host "  Get-ScheduledTask -TaskName 'EPLogger-*' | Format-Table TaskName, State, NextRunTime"