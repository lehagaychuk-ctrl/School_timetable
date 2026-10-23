param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtimeCandidates = @(
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    $env:TIMETABLE_PYTHON,
    'C:\Program Files\LibreOffice\program\python.exe'
)
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') {
    $runtimeCandidates = @($pythonCommand.Source) + $runtimeCandidates
}
$taskPython = $null
foreach ($candidate in $runtimeCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
        & $candidate -c 'import sys; assert sys.version_info >= (3,12)' 2>$null
        if ($LASTEXITCODE -eq 0) { $taskPython = $candidate; break }
    }
}
if (-not $taskPython) { throw 'Python 3.12+ is required. Set TIMETABLE_PYTHON to the full python.exe path.' }
& $taskPython -c "import sys;sys.path.insert(0,'.vendor');import flask,reportlab,openpyxl,docx" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $taskPython -m pip install --target (Join-Path $PSScriptRoot '.vendor') -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
$taskPort = if ($env:PORT) { $env:PORT } else { '8765' }
$taskUrl = "http://127.0.0.1:$taskPort"
try {
    $taskExisting = Invoke-RestMethod -Uri "$taskUrl/api/state" -TimeoutSec 2
} catch { $taskExisting = $null }
if (-not $taskExisting) {
    try { $taskAccountState = Invoke-RestMethod -Uri "$taskUrl/api/account/state" -TimeoutSec 2 } catch { $taskAccountState = $null }
    if ($taskAccountState -and $taskAccountState.version -eq 'school-workspace-2') {
        Write-Host "Application is already running. Sign in at $taskUrl"
        if (-not $NoBrowser) { Start-Process $taskUrl }
        exit 0
    }
}
if ($taskExisting -and $taskExisting.classes -and $taskExisting.settings) {
    Write-Host "Application is already running: $taskUrl"
    if ($taskExisting.PSObject.Properties.Name -notcontains 'teachers' -or $taskExisting.capabilities.teacher_schedule -ne $true -or $taskExisting.capabilities.busy_teacher_tab -ne $true -or $taskExisting.capabilities.class_creation -ne $true -or $taskExisting.capabilities.saved_schedule_picker -ne $true -or $taskExisting.capabilities.reference_print_layout -ne $true -or $taskExisting.capabilities.planning_workspace -ne $true) {
        Write-Host 'An old version is running. Stop the previous server windows with Ctrl+C, then run start.bat again.'
    }
    if (-not $NoBrowser) { Start-Process $taskUrl }
    exit 0
}
Write-Host "School timetable: http://127.0.0.1:$taskPort"
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$taskPort" }
& $taskPython (Join-Path $PSScriptRoot 'app.py')
