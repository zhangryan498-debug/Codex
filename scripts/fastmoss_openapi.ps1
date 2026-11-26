[CmdletBinding(DefaultParameterSetName = 'Call')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Configure')]
    [switch]$Configure,

    [Parameter(Mandatory, ParameterSetName = 'Call')]
    [ValidatePattern('^/(agency|creator|live|product|shop|video)/v[0-9]+(/[A-Za-z0-9]+)+$')]
    [string]$Endpoint,

    [Parameter(ParameterSetName = 'Call')]
    [string]$BodyPath
)

$ErrorActionPreference = 'Stop'
$credentialFile = Join-Path $env:LOCALAPPDATA 'DandT\fastmoss-openapi.dpapi'

if ($Configure) {
    $credential = Read-Host 'FastMoss OpenAPI client_secret' -AsSecureString
    if (-not $credential.Length) { throw 'FastMoss OpenAPI client_secret is empty.' }
    New-Item -ItemType Directory -Path (Split-Path -Parent $credentialFile) -Force | Out-Null
    ConvertFrom-SecureString -SecureString $credential |
        Set-Content -LiteralPath $credentialFile -Encoding Ascii -NoNewline
    Write-Output 'FastMoss OpenAPI credential saved for the current Windows account.'
    return
}

$body = if ($BodyPath) { Get-Content -LiteralPath $BodyPath -Encoding UTF8 -Raw } else { '{}' }
if (-not $body.TrimStart().StartsWith('{')) { throw 'Request body must be a JSON object.' }
$null = ConvertFrom-Json -InputObject $body
if (-not (Test-Path -LiteralPath $credentialFile -PathType Leaf)) {
    throw 'Run fastmoss_openapi.ps1 -Configure to save the FastMoss OpenAPI client_secret.'
}
$credential = Get-Content -LiteralPath $credentialFile -Raw | ConvertTo-SecureString
$key = [System.Net.NetworkCredential]::new('', $credential).Password

Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(25)
$client.DefaultRequestHeaders.Authorization =
    [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $key)
$content = [System.Net.Http.StringContent]::new($body, [Text.Encoding]::UTF8, 'application/json')
try {
    $response = $client.PostAsync("https://openapi.fastmoss.com$Endpoint", $content).GetAwaiter().GetResult()
    $status = [int]$response.StatusCode
    $raw = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $result = $null
    try { $result = ConvertFrom-Json -InputObject ($raw.Replace($key, '[redacted]')) }
    catch { [Console]::Error.WriteLine("FastMoss returned a non-JSON response (HTTP $status)."); exit 1 }
    $ok = $status -eq 200 -and $null -ne $result.code -and $result.code -eq 0
    [ordered]@{ http_status = $status; endpoint = $Endpoint; response = $result } |
        ConvertTo-Json -Depth 50
    if (-not $ok) { exit 1 }
} finally {
    if ($response) { $response.Dispose() }
    $content.Dispose()
    $client.Dispose()
}
