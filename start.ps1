param([string]$PythonPath = "", [int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (-not $PythonPath) {
    $studioPython = Get-Command python -ErrorAction SilentlyContinue
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if ($studioPython -and $studioPython.Source -notlike '*WindowsApps*') {
        $PythonPath = $studioPython.Source
    } elseif (Test-Path -LiteralPath $bundledPython) {
        $PythonPath = $bundledPython
    } else {
        throw "Python 3.10 이상을 설치하거나 -PythonPath로 실행 파일 경로를 지정하세요."
    }
}
$studioArgs = @("-m", "harness.app", "--port", "$Port")
if (-not $NoBrowser) { $studioArgs += "--open" }
& $PythonPath @studioArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "Python 3.10 이상과 Git 설치 여부를 확인하세요. -PythonPath로 Python 경로를 지정할 수 있습니다."
    exit $LASTEXITCODE
}
