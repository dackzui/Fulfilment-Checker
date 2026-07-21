# Stop all Picking Barcode Scanner desktop processes.
$ErrorActionPreference = "SilentlyContinue"

$targets = Get-CimInstance Win32_Process | Where-Object {
    $cmd = $_.CommandLine
    if (-not $cmd) { return $false }
    $cmd -match 'picker-check[\\/]main\.py' -or
    ($_.Name -eq 'flet.exe' -and $cmd -match 'picker-check')
}

foreach ($p in $targets) {
    Stop-Process -Id $p.ProcessId -Force
}

if ($targets) {
    Write-Host "Stopped $($targets.Count) process(es)."
} else {
    Write-Host "No running app processes found."
}
