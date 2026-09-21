$topic = [Environment]::GetEnvironmentVariable('JOB_MONITOR_NTFY_TOPIC', 'User')
if ($topic) {
    $env:JOB_MONITOR_NTFY_TOPIC = $topic
}
$gmailAddress = [Environment]::GetEnvironmentVariable('JOB_MONITOR_GMAIL_ADDRESS', 'User')
if ($gmailAddress) {
    $env:JOB_MONITOR_GMAIL_ADDRESS = $gmailAddress
}
$gmailAppPassword = [Environment]::GetEnvironmentVariable('JOB_MONITOR_GMAIL_APP_PASSWORD', 'User')
if ($gmailAppPassword) {
    $env:JOB_MONITOR_GMAIL_APP_PASSWORD = $gmailAppPassword
}
$recipient = [Environment]::GetEnvironmentVariable('JOB_MONITOR_ALERT_EMAIL', 'User')
if ($recipient) {
    $env:JOB_MONITOR_ALERT_EMAIL = $recipient
}
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$logDirectory = Join-Path $env:LOCALAPPDATA 'WarpJobMonitor'
$logPath = Join-Path $logDirectory 'monitor.log'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Write-MonitorLog([string]$Message) {
    Add-Content -Path $logPath -Value "$(Get-Date -Format o) $Message"
}
if (-not (Test-Path $python)) {
    Write-MonitorLog 'ERROR: Job monitor virtual environment is missing.'
    exit 1
}

Write-MonitorLog 'Monitor run started.'
& $python "$PSScriptRoot\job_monitor.py" *>> $logPath
$exitCode = $LASTEXITCODE
Write-MonitorLog "Monitor run finished with exit code $exitCode."
exit $exitCode
