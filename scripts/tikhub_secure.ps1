[CmdletBinding()]
param([Parameter(Mandatory)][ValidatePattern('^/api/v1/[A-Za-z0-9_/]+$')][string]$Endpoint,[string]$QueryJson='{}')
$ErrorActionPreference='Stop'
$secure=Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'DandT\tikhub-api.dpapi') -Raw | ConvertTo-SecureString
$key=[System.Net.NetworkCredential]::new('', $secure).Password
$q=ConvertFrom-Json -InputObject $QueryJson -AsHashtable
$parts=@($q.GetEnumerator() | ForEach-Object { [Uri]::EscapeDataString([string]$_.Key)+'='+[Uri]::EscapeDataString([string]$_.Value) })
$uri='https://api.tikhub.io'+$Endpoint
if($parts.Count){$uri+='?'+($parts -join '&')}
try {
 $response=Invoke-WebRequest -Uri $uri -Headers @{Authorization=('Bearer '+$key)} -TimeoutSec 45 -MaximumRedirection 0
 [ordered]@{http_status=[int]$response.StatusCode;endpoint=$Endpoint;response=(ConvertFrom-Json ($response.Content.Replace($key,'[redacted]')))} | ConvertTo-Json -Depth 60
} catch {
 [ordered]@{endpoint=$Endpoint;error_type=$_.Exception.GetType().Name;message=$_.Exception.Message.Replace($key,'[redacted]')} | ConvertTo-Json
 exit 1
} finally {$key=$null}
