param(
    [string]$OutputDirectory = "fixtures/pohoda"
)

$ErrorActionPreference = "Stop"
$baseUri = "https://www.stormware.cz/schema/version_2/"
$resolvedOutput = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\$OutputDirectory"))
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null

$queue = [System.Collections.Generic.Queue[string]]::new()
$queue.Enqueue("data.xsd")
$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

while ($queue.Count -gt 0) {
    $name = $queue.Dequeue()
    if (-not $seen.Add($name)) { continue }
    if ($name.Contains("..") -or [System.IO.Path]::GetFileName($name) -ne $name) {
        throw "Unsafe schema name: $name"
    }

    $target = Join-Path $resolvedOutput $name
    if (-not (Test-Path -LiteralPath $target)) {
        Invoke-WebRequest -Uri ($baseUri + $name) -OutFile $target
    }

    $content = Get-Content -LiteralPath $target -Raw
    [regex]::Matches($content, 'schemaLocation\s*=\s*["'']([^"'']+\.xsd)["'']') | ForEach-Object {
        $dependency = $_.Groups[1].Value
        if (-not $seen.Contains($dependency)) {
            $queue.Enqueue($dependency)
        }
    }
}

Write-Output "Downloaded/verified $($seen.Count) official POHODA XSD files in $resolvedOutput"

