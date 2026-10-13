$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Build = Join-Path $Root ".build"
$Upstream = Join-Path $Build "upstream"
if (Test-Path $Build) { Remove-Item $Build -Recurse -Force }
New-Item -ItemType Directory -Path $Build | Out-Null
git clone --depth 1 --branch 2.2.6 --recurse-submodules https://github.com/2dust/v2rayNG.git $Upstream
python (Join-Path $Root "tools\patch_v2rayng_v1.0.py") --source (Join-Path $Upstream "V2rayNG") --kit-root $Root
Push-Location (Join-Path $Upstream "V2rayNG")
& .\gradlew.bat :app:assemblePlaystoreDebug --no-daemon
Pop-Location
$Apk = Get-ChildItem (Join-Path $Upstream "V2rayNG\app\build\outputs\apk\playstore\debug") -Filter "*.apk" | Select-Object -First 1
if (-not $Apk) { throw "Universal APK not found" }
Copy-Item $Apk.FullName (Join-Path $Root "PAUK-v1.1.apk")
Write-Host "APK created: $Root\PAUK-v1.1.apk"
