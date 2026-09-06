$topic = [Environment]::GetEnvironmentVariable('JOB_MONITOR_NTFY_TOPIC', 'User')
if ($topic) {
    $env:JOB_MONITOR_NTFY_TOPIC = $topic
}
& py -3 "$PSScriptRoot\job_monitor.py"
exit $LASTEXITCODE
