param(
    [string]$Version = "2025-10-16",
    [string]$ExpectedSha256 = "AB6A9F3C406A9E2257F544203D21DF3723E8E10026E73A0898AA6249446BFD9B"
)

$ErrorActionPreference = "Stop"
$source = "https://www.stormware.cz/xml/schema/all_schema_ver2.zip"
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\schemas\pohoda"))
$target = Join-Path $root $Version
$archive = Join-Path $root "all_schema_ver2.zip"

if (Test-Path -LiteralPath $target) {
    throw "Target already exists: $target. Create a new dated version instead of overwriting it."
}
New-Item -ItemType Directory -Force -Path $root | Out-Null
Invoke-WebRequest -Uri $source -OutFile $archive
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
if ($actual -ne $ExpectedSha256) {
    throw "Unexpected bundle SHA-256: $actual"
}
Expand-Archive -LiteralPath $archive -DestinationPath $target
$schemas = @(Get-ChildItem -LiteralPath $target -Filter *.xsd -File)
if ($schemas.Count -lt 4) {
    throw "Incomplete bundle: only $($schemas.Count) XSD files"
}
Write-Output "Downloaded $($schemas.Count) official schemas to $target; SHA-256 $actual"
