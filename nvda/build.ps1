param(
	[string] $Version,
	[switch] $UseMockEngine
)

$ErrorActionPreference = "Stop"
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
	$PSNativeCommandUseErrorActionPreference = $true
}

$addonSource = $PSScriptRoot
$repository = Split-Path -Parent $addonSource
$manifest = Get-Content -LiteralPath (Join-Path $addonSource "manifest.ini") -Raw
$match = [regex]::Match($manifest, '(?m)^version\s*=\s*"?([^"\r\n]+)')
if (-not $match.Success) {
	throw "manifest.ini has no version field"
}
$manifestVersion = $match.Groups[1].Value.Trim()
if ($Version -and $Version -ne $manifestVersion) {
	throw "Requested version $Version does not match manifest version $manifestVersion"
}

function Invoke-NativeChecked {
	param([string] $FilePath, [string[]] $Arguments)
	& $FilePath @Arguments
	if ($LASTEXITCODE -ne 0) {
		throw "$FilePath failed with exit code $LASTEXITCODE"
	}
}

Push-Location $repository
try {
	Invoke-NativeChecked python @("-m", "SCons", "-s")
	Invoke-NativeChecked python @("tests/test_driver.py")
	$engine = if ($UseMockEngine) { "bin/mock_b32_tts.dll" } else { "bin/b32_tts.dll" }
	Invoke-NativeChecked python @("tests/test_helper.py", $engine)

	Copy-Item -LiteralPath @("bin/b32_tts.dll", "bin/b32_wrapper.dll", "bin/b32_helper.exe") -Destination "nvda/synthDrivers" -Force
	Copy-Item -LiteralPath @("LICENSE", "deps.txt") -Destination "nvda" -Force

	$outputDirectory = Join-Path $addonSource "outputs"
	New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
	$package = Join-Path $outputDirectory "BestSpeech-$manifestVersion.nvda-addon"
	Invoke-NativeChecked python @("tests/build_package.py", $addonSource, $package)
	Invoke-NativeChecked python @("tests/validate_package.py", $package)
	Write-Output $package
}
finally {
	Pop-Location
}
