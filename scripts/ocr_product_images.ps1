param(
    [string[]]$ImagePath,
    [string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string]$Language = 'zh-Hans-CN'
)
# Use Windows PowerShell 5.1: PowerShell 7 does not project these WinRT types.
$ErrorActionPreference = 'Stop'
if ($ManifestPath) {
    $ImagePath = @((Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json) | Where-Object {$_.status -eq 'ok'} | ForEach-Object {$_.local_path})
}
if (-not $ImagePath) { throw 'Provide ImagePath or a downloaded-image ManifestPath' }
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$dtAsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Wait-DtOperation($Operation, [Type]$ResultType) {
    $dtTask = $dtAsTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $dtTask.Wait()
    return $dtTask.Result
}
$dtLanguage = New-Object Windows.Globalization.Language($Language)
$dtOcr = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]::TryCreateFromLanguage($dtLanguage)
if ($null -eq $dtOcr) { throw "OCR language unavailable: $Language" }
$dtResults = @()
foreach ($dtInput in $ImagePath) {
    $dtAbsolute = (Resolve-Path -LiteralPath $dtInput).Path
    $dtFile = Wait-DtOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($dtAbsolute)) ([Windows.Storage.StorageFile])
    $dtStream = Wait-DtOperation ($dtFile.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    try {
        $dtDecoder = Wait-DtOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($dtStream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $dtBitmap = Wait-DtOperation ($dtDecoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $dtRecognized = Wait-DtOperation ($dtOcr.RecognizeAsync($dtBitmap)) ([Windows.Media.Ocr.OcrResult])
            $dtResults += @{image_path=$dtAbsolute;sha256=(Get-FileHash -LiteralPath $dtAbsolute -Algorithm SHA256).Hash.ToLower();language=$Language;engine='Windows.Media.Ocr';text=$dtRecognized.Text;lines=@($dtRecognized.Lines | ForEach-Object {$_.Text});extracted_utc=(Get-Date).ToUniversalTime().ToString('o')}
        } finally { $dtBitmap.Dispose() }
    } finally { $dtStream.Dispose() }
}
$dtJson = ConvertTo-Json -InputObject @($dtResults) -Depth 8
[IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputPath), $dtJson.Replace("`r`n","`n")+"`n", (New-Object Text.UTF8Encoding($false)))
Write-Output "OCR images: $($dtResults.Count); saved $OutputPath"
