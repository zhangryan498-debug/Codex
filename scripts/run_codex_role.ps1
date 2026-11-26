param(
    [Parameter(Mandatory=$true)][ValidatePattern('^(douyin|tiktok)-[a-z-]+$')][string]$Role,
    [string]$Task,
    [string]$TaskFile,
    [string]$Model,
    [ValidateSet("low","medium","high","xhigh","max","ultra")][string]$ReasoningEffort,
    [ValidateSet('read-only','workspace-write')][string]$Sandbox = 'read-only',
    [string[]]$Images = @(),
    [string]$ImageManifest,
    [string]$RecheckFrom,
    [ValidateSet("1.1","1.2")][string]$RecheckVersion = "1.1",
    [ValidateSet("legacy","1.3","1.4")][string]$ReviewVersion = "legacy",
    [string]$ReviewGoals,
    [string]$PriceHandoffFile,
    [string]$PriceEvaluationTime,
    [string[]]$DisableMcpServers = @(),
    [switch]$RestrictedTools,
    [switch]$OfflineText,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $OutputEncoding
[Console]::OutputEncoding = $OutputEncoding
$dtRoot = Split-Path $PSScriptRoot -Parent
$dtPython = Join-Path $dtRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $dtPython -PathType Leaf)) {
    Write-Error 'Project .venv\Scripts\python.exe is missing; no WindowsApps fallback is used.'
    exit 2
}
$dtHasTask = $PSBoundParameters.ContainsKey('Task')
$dtHasTaskFile = $PSBoundParameters.ContainsKey('TaskFile')
if ($dtHasTask -eq $dtHasTaskFile) {
    Write-Error 'Supply exactly one of -Task or -TaskFile.'
    exit 2
}
if ($PSBoundParameters.ContainsKey('Images') -and $Images.Count -eq 0) {
    Write-Error 'Images cannot be empty'
    exit 2
}
$dtTempTask = $null
$dtExit = 1
try {
    if ($dtHasTask) {
        if ([string]::IsNullOrWhiteSpace($Task)) { throw 'Task cannot be empty' }
        # PS5 native argv strips embedded quotation marks. Never pass task content through argv.
        $dtTempTask = [IO.Path]::GetTempFileName()
        [IO.File]::WriteAllText($dtTempTask, $Task, $OutputEncoding)
        $dtTaskPath = $dtTempTask
    } else {
        if ([string]::IsNullOrWhiteSpace($TaskFile)) { throw 'TaskFile cannot be empty' }
        $dtTaskPath = (Resolve-Path -LiteralPath $TaskFile -ErrorAction Stop).Path
        if (-not (Test-Path -LiteralPath $dtTaskPath -PathType Leaf)) { throw 'TaskFile must be a regular UTF-8 file' }
    }
    $dtArgs = @((Join-Path $PSScriptRoot 'run_codex_role.py'), "--role=$Role", "--task-file=$dtTaskPath", "--sandbox=$Sandbox")
    if ($PSBoundParameters.ContainsKey("Model")) { $dtArgs += "--model=$Model" }
    if ($PSBoundParameters.ContainsKey("ReasoningEffort")) { $dtArgs += "--reasoning-effort=$ReasoningEffort" }
    foreach ($image in $Images) { $dtArgs += "--image=$image" }
    if ($PSBoundParameters.ContainsKey('ImageManifest')) {
        if ([string]::IsNullOrWhiteSpace($ImageManifest)) { throw 'ImageManifest cannot be empty' }
        $dtArgs += "--image-manifest=$ImageManifest"
    }
    if ($PSBoundParameters.ContainsKey("RecheckFrom")) {
        if ([string]::IsNullOrWhiteSpace($RecheckFrom)) { throw "RecheckFrom cannot be empty" }
        $dtArgs += "--recheck-from=$RecheckFrom"
    }
    $dtArgs += "--recheck-version=$RecheckVersion"
    $dtArgs += "--review-version=$ReviewVersion"
    if ($PSBoundParameters.ContainsKey("ReviewGoals")) {
        if ([string]::IsNullOrWhiteSpace($ReviewGoals)) { throw "ReviewGoals cannot be empty" }
        $dtArgs += "--review-goals=$ReviewGoals"
    }
    if ($PSBoundParameters.ContainsKey("PriceHandoffFile")) {
        if ([string]::IsNullOrWhiteSpace($PriceHandoffFile)) { throw "PriceHandoffFile cannot be empty" }
        $dtPricePath = (Resolve-Path -LiteralPath $PriceHandoffFile -ErrorAction Stop).Path
        $dtArgs += "--price-handoff-file=$dtPricePath"
    }
    if ($PSBoundParameters.ContainsKey("PriceEvaluationTime")) {
        if ([string]::IsNullOrWhiteSpace($PriceEvaluationTime)) { throw "PriceEvaluationTime cannot be empty" }
        $dtArgs += "--price-evaluation-time=$PriceEvaluationTime"
    }
    foreach ($server in $DisableMcpServers) {
        if ($server -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid MCP server identifier' }
        $dtArgs += "--disable-mcp-server=$server"
    }
    if ($RestrictedTools) { $dtArgs += '--restricted-tools' }
    if ($OfflineText) { $dtArgs += '--offline-text' }
    if ($ValidateOnly) { $dtArgs += '--validate-only' }
    $ErrorActionPreference = 'Continue' # Windows PowerShell 5 treats native stderr as errors.
    & $dtPython @dtArgs
    if ($null -ne $LASTEXITCODE) { $dtExit = $LASTEXITCODE }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $dtExit = 2
} finally {
    $ErrorActionPreference = 'Stop'
    # Only the single temporary file created by this invocation is removed.
    if ($null -ne $dtTempTask -and (Test-Path -LiteralPath $dtTempTask -PathType Leaf)) {
        Remove-Item -LiteralPath $dtTempTask -Force
    }
}
exit $dtExit

