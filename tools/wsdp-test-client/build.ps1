param([switch]$Test, [switch]$Live, [switch]$Run, [switch]$Package)
$ErrorActionPreference = 'Stop'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
if (-not (Test-Path -LiteralPath (Join-Path $framework 'csc.exe'))) {
    $framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319'
}
$compiler = Join-Path $framework 'csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Chybi .NET Framework 4.8 s C# kompilatorem.' }
$release = Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -Name Release
if ($release -lt 528040) { throw 'Je vyzadovan .NET Framework 4.8 nebo novejsi.' }
$output = Join-Path $PSScriptRoot 'bin\Release'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$references = @('System.dll', 'System.Core.dll', 'System.Drawing.dll', 'System.Net.Http.dll',
    'System.Windows.Forms.dll', 'System.Xml.dll', 'System.Xml.Linq.dll') | ForEach-Object { '/reference:' + $_ }
$sources = @('WsdpClient.cs', 'MainForm.cs', 'Program.cs') | ForEach-Object { Join-Path $PSScriptRoot $_ }
$executable = Join-Path $output 'WsdpTestClient.exe'
& $compiler /nologo /utf8output /langversion:5 /warn:4 /warnaserror+ /optimize+ /target:winexe "/out:$executable" @references @sources
if ($LASTEXITCODE -ne 0) { throw 'Sestaveni aplikace selhalo.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'App.config') -Destination "$executable.config" -Force
Write-Output "Aplikace: $executable"
if ($Test -or $Live) {
    $testExe = Join-Path $output 'WsdpTests.exe'
    & $compiler /nologo /utf8output /langversion:5 /warn:4 /warnaserror+ /target:exe "/out:$testExe" @references @sources (Join-Path $PSScriptRoot 'Tests.cs') /main:WsdpTestClient.Tests
    if ($LASTEXITCODE -ne 0) { throw 'Sestaveni testu selhalo.' }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'App.config') -Destination "$testExe.config" -Force
    if ($Live) { & $testExe --live } else { & $testExe }
    if ($LASTEXITCODE -ne 0) { throw 'Testy selhaly.' }
}
if ($Package) {
    $archive = Join-Path $output 'WsdpTestClient-win.zip'
    Compress-Archive -LiteralPath @($executable, "$executable.config", (Join-Path $PSScriptRoot 'README.md')) -DestinationPath $archive -Force
    Write-Output "ZIP: $archive"
}
if ($Run) { Start-Process -FilePath $executable }
