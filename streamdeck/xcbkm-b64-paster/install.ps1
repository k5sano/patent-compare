$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root "src"
$plugin = Join-Path $root "com.patentcompare.xcbkm-b64-paster.sdPlugin"
$publish = Join-Path $root "publish"
$bin = Join-Path $plugin "bin"
$destRoot = Join-Path $env:APPDATA "Elgato\StreamDeck\Plugins"
$dest = Join-Path $destRoot "com.patentcompare.xcbkm-b64-paster.sdPlugin"

dotnet publish $src -c Release -r win-x64 --self-contained true /p:PublishSingleFile=true /p:PublishTrimmed=false -o $publish

New-Item -ItemType Directory -Force -Path $bin | Out-Null
Copy-Item -Force (Join-Path $publish "XcbkmB64Paster.exe") (Join-Path $bin "XcbkmB64Paster.exe")

Get-Process XcbkmB64Paster -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process StreamDeck -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

New-Item -ItemType Directory -Force -Path $destRoot | Out-Null
if (Test-Path $dest) {
    Remove-Item -LiteralPath $dest -Recurse -Force
}
Copy-Item -LiteralPath $plugin -Destination $destRoot -Recurse -Force

Write-Host "Installed: $dest"
Write-Host "Restart the Stream Deck app if it is already running."
