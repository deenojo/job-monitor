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
if (-not (Test-Path $python)) {
    Write-Error 'Job monitor virtual environment is missing. Recreate it before running scheduled alerts.'
    exit 1
}
& $python "$PSScriptRoot\job_monitor.py"
exit $LASTEXITCODE
