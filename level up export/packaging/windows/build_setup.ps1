param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $ProjectRoot "build\windows-installer"
$VirtualEnv = Join-Path $BuildRoot "venv"
$AppDistDirectory = Join-Path $BuildRoot "app-dist"
$AppSpecDirectory = Join-Path $BuildRoot "app-spec"
$AppWorkDirectory = Join-Path $BuildRoot "app-pyinstaller"
$SetupDistDirectory = Join-Path $BuildRoot "setup-dist"
$SetupSpecDirectory = Join-Path $BuildRoot "setup-spec"
$SetupWorkDirectory = Join-Path $BuildRoot "setup-pyinstaller"
$AppEntryPoint = Join-Path $ProjectRoot "packaging\windows\setup_app.py"
$InstallerEntryPoint = Join-Path $ProjectRoot "packaging\windows\installer.py"
$WebAssets = Join-Path $ProjectRoot "alephgeo\web"
$BrandLogo = Join-Path $ProjectRoot "resources\level-up-studio-aleph-cropped.png"
$AppIcon = Join-Path $ProjectRoot "resources\level-up-studio-aleph.ico"

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ProjectRoot "dist"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $BuildRoot, $AppDistDirectory, $AppSpecDirectory, $AppWorkDirectory, `
    $SetupDistDirectory, $SetupSpecDirectory, $SetupWorkDirectory, $OutputDirectory | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $VirtualEnv "Scripts\python.exe"))) {
    & py -3 -m venv $VirtualEnv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the build environment." }
}

$Python = Join-Path $VirtualEnv "Scripts\python.exe"
& $Python -m pip install --disable-pip-version-check `
    "Pillow>=10" "numpy>=1.25" "truststore>=0.10" "pywebview>=6.2.1,<7" "pyinstaller>=6.21,<7"
if ($LASTEXITCODE -ne 0) { throw "Could not install the app's build requirements." }

$WebDataArgument = "$WebAssets;alephgeo\web"
$BrandLogoArgument = "$BrandLogo;resources"
$AppIconArgument = "$AppIcon;resources"
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name "LevelUpStudioAleph" `
    --icon $AppIcon `
    --paths $ProjectRoot `
    --collect-all truststore `
    --collect-all webview `
    --add-data $WebDataArgument `
    --add-data $BrandLogoArgument `
    --add-data $AppIconArgument `
    --specpath $AppSpecDirectory `
    --distpath $AppDistDirectory `
    --workpath $AppWorkDirectory `
    $AppEntryPoint
if ($LASTEXITCODE -ne 0) { throw "PyInstaller could not create the app." }

$AppBundle = Join-Path $AppDistDirectory "LevelUpStudioAleph"
$SetupDataArgument = "$AppBundle;app"
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "Setup" `
    --icon $AppIcon `
    --add-data $SetupDataArgument `
    --add-data $BrandLogoArgument `
    --add-data $AppIconArgument `
    --specpath $SetupSpecDirectory `
    --distpath $SetupDistDirectory `
    --workpath $SetupWorkDirectory `
    $InstallerEntryPoint
if ($LASTEXITCODE -ne 0) { throw "PyInstaller could not create Setup.exe." }

$BuiltSetup = Join-Path $SetupDistDirectory "Setup.exe"
$FinalSetup = Join-Path $OutputDirectory "Setup.exe"
if (-not (Test-Path -LiteralPath $BuiltSetup)) { throw "The build completed without producing Setup.exe." }
Copy-Item -LiteralPath $BuiltSetup -Destination $FinalSetup -Force
Write-Output "Created $FinalSetup"
