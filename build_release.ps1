$ErrorActionPreference = "Stop"
$appName = "NussmannMediaForge"

function Assert-Success($StepName) {
  if ($LASTEXITCODE -ne 0) {
    throw "$StepName failed with exit code $LASTEXITCODE"
  }
}

$releaseDist = "release-dist"
$releaseBuild = "release-build"

$runningApp = Get-Process $appName -ErrorAction SilentlyContinue | Where-Object {
  $_.Path -like (Join-Path $PWD "*\$appName.exe")
}
if ($runningApp) {
  throw "$appName.exe is currently running. Close the app before building a release."
}

Remove-Item -Path $releaseDist, $releaseBuild -Recurse -Force -ErrorAction SilentlyContinue

python -m pip install --upgrade pip
Assert-Success "pip upgrade"

python -m pip install -r requirements.txt
Assert-Success "dependency install"

pyinstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name $appName `
  --distpath $releaseDist `
  --workpath $releaseBuild `
  --icon "assets\app.ico" `
  --add-data "assets;assets" `
  --add-data "README.md;." `
  nussmann_mediaforge.py
Assert-Success "PyInstaller build"

Copy-Item -Path ".\README.md" -Destination ".\$releaseDist\$appName\README.md" -Force

Write-Host ""
Write-Host "Build complete: $releaseDist\$appName\$appName.exe"
