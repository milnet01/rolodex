# Run by one scheduled task in the signed-in session (ROLO-0042 section 4.7), so GUI work
# happens on the real desktop rather than ssh's session 0. Each step, the Linux side copies a
# job.ps1 (and its job.json) beside this file and runs the task; this runs the job and writes
# job.log, ending with DONE, which the Linux side polls for.
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $base 'job.log'
try {
    & (Join-Path $base 'job.ps1') *>&1 | Out-File -FilePath $log -Encoding utf8
} catch {
    "runner: job threw: $_" | Out-File -FilePath $log -Append -Encoding utf8
}
'DONE' | Out-File -FilePath $log -Append -Encoding utf8
