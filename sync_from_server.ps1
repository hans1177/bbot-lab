# 파일명: sync_from_server.ps1
param(
    [ValidateSet("Scheduled", "PreRelease", "PostRelease")]
    [string]$Purpose = "Scheduled"
)

$ErrorActionPreference = "Stop"

# ================= 경로 / 접속 =================
$ROOT = "C:\my\bottest"
$KEY  = "C:\my\han.pem"
$SSH  = "ec2-user@13.159.128.120"
$DATA = Join-Path $ROOT "data"
$RUNTIME = Join-Path $DATA "server_runtime"
$STAGE = Join-Path $env:TEMP ("bbot_server_readonly_sync_" + [guid]::NewGuid().ToString("N"))

# SERVER -> LOCAL은 연구 소스/설정을 절대 덮어쓰지 않는다.
$PROTECTED = @(
    "runner.py",
    "order.py",
    "risk_engine.py",
    "candidate.py",
    "gate.py",
    "collector.py",
    "reservation.py",
    "strategy_runtime.py",
    "strategy_config.json",
    "ROADMAP_CONSTITUTION_V3.json",
    "strategy_lab\v3_governance.py",
    "strategy_lab\v3_deployment.py",
    "strategy_lab\v3_shadow_engine.py"
)

# ================= 유틸 =================
function Get-ProtectedHashes {
    $out = @{}
    foreach ($rel in $PROTECTED) {
        $path = Join-Path $ROOT $rel
        if (-not (Test-Path $path)) {
            throw "PROTECTED_LOCAL_FILE_MISSING:$rel"
        }
        $out[$rel] = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    }
    return $out
}

function Copy-ServerFileReadOnly {
    param(
        [Parameter(Mandatory=$true)][string]$RemotePath,
        [Parameter(Mandatory=$true)][string]$LocalPath,
        [switch]$Optional
    )
    & scp -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -q -i $KEY "${SSH}:$RemotePath" $LocalPath 2>$null
    if ($LASTEXITCODE -ne 0) {
        if ($Optional) {
            return $false
        }
        throw "SERVER_FILE_READ_FAIL:$RemotePath"
    }
    return $true
}

# ================= 시작 =================
Write-Host "===== SERVER -> LOCAL R4 READ-ONLY SYNC ====="
Write-Host "SYNC_PURPOSE=$Purpose"
Write-Host "SERVER_ROLE=VALIDATED_RUNTIME_EXECUTOR"
Write-Host "LOCAL_ROLE=RESEARCH_LAB"
Write-Host "SERVER_WRITE=0"
Write-Host "SERVICE_RESTART=0"
Write-Host "POSITION_MUTATION=0"

$before = Get-ProtectedHashes
Remove-Item $STAGE -Recurse -Force -ErrorAction SilentlyContinue
New-Item $STAGE -ItemType Directory -Force | Out-Null
New-Item $DATA -ItemType Directory -Force | Out-Null
New-Item $RUNTIME -ItemType Directory -Force | Out-Null

try {
    # ================= 1. Runtime state snapshot =================
    # live SQLite 파일은 전송하지 않는다. JSON/상태 파일만 읽기 전용으로 가져온다.
    New-Item "$STAGE\state" -ItemType Directory -Force | Out-Null
    $stateFiles = @(
        & ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i $KEY $SSH "cd /home/ec2-user/bot && find state -type f ! -name '*.db' ! -name '*.db-*' ! -name '*.db.*' ! -name 'r4_cutover.lock' ! -name 'r4_runtime.env' -printf '%P\n'" 2>$null
    )
    if ($LASTEXITCODE -ne 0) {
        throw "SERVER_STATE_LIST_READ_FAIL"
    }

    foreach ($rawRel in $stateFiles) {
        $rel = ([string]$rawRel).Trim()
        if (-not $rel) { continue }
        if ($rel.Contains("..") -or $rel -notmatch '^[A-Za-z0-9_./-]+$') {
            throw "SERVER_STATE_PATH_INVALID:$rel"
        }
        $localRel = $rel.Replace("/", "\")
        $localPath = Join-Path "$STAGE\state" $localRel
        New-Item (Split-Path $localPath) -ItemType Directory -Force | Out-Null
        [void](Copy-ServerFileReadOnly -RemotePath ("/home/ec2-user/bot/state/" + $rel) -LocalPath $localPath)
    }

    # ================= 2. history.db 일관 읽기 =================
    $dumpHelper = Join-Path $ROOT "tools\server_history_dump_ro.py"
    if (-not (Test-Path $dumpHelper)) {
        throw "SERVER_HISTORY_DUMP_HELPER_MISSING"
    }
    $sshErr = Join-Path $STAGE "history_ssh_error.txt"
    $rawDb = Get-Content $dumpHelper -Raw | & ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -i $KEY $SSH "/home/ec2-user/bot/venv/bin/python -" 2>$sshErr
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path $sshErr) { Get-Content $sshErr }
        throw "REMOTE_HISTORY_READ_FAIL"
    }
    $db64 = ($rawDb -join "").Trim()
    if (-not $db64) {
        throw "REMOTE_HISTORY_EMPTY"
    }
    $dumpFile = Join-Path $STAGE "history_dump.b64"
    Set-Content $dumpFile $db64 -Encoding ASCII -NoNewline
    $dbPath = Join-Path $STAGE "history_snapshot.db"
    & "$ROOT\.venv\Scripts\python.exe" "$ROOT\tools\rebuild_history_snapshot.py" $dumpFile $dbPath
    if ($LASTEXITCODE -ne 0) {
        throw "LOCAL_HISTORY_REBUILD_FAIL"
    }

    # ================= 3. Runtime config / 로그 읽기 =================
    [void](Copy-ServerFileReadOnly -RemotePath "/home/ec2-user/bot/config.json" -LocalPath "$STAGE\config.json")
    [void](Copy-ServerFileReadOnly -RemotePath "/home/ec2-user/bot/strategy_config.json" -LocalPath "$STAGE\strategy_config.json")
    $logOk = Copy-ServerFileReadOnly -RemotePath "/home/ec2-user/bot/logs/runner.log" -LocalPath "$STAGE\runner.log" -Optional

    Get-Content "$STAGE\config.json" -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null
    Get-Content "$STAGE\strategy_config.json" -Raw -Encoding UTF8 | ConvertFrom-Json | Out-Null

    # ================= 4. LOCAL server_runtime snapshot 원자 교체 =================
    $runtimeState = Join-Path $RUNTIME "state"
    $nextState = Join-Path $RUNTIME "state.next"
    Remove-Item $nextState -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item "$STAGE\state" $nextState
    Copy-Item $dbPath "$nextState\history.db" -Force

    Remove-Item $runtimeState -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item $nextState $runtimeState

    Copy-Item $dbPath "$RUNTIME\history.db" -Force
    Copy-Item $dbPath "$DATA\history_archive.db" -Force
    Copy-Item "$STAGE\config.json" "$RUNTIME\config.json" -Force
    Copy-Item "$STAGE\strategy_config.json" "$RUNTIME\strategy_config.json" -Force
    if ($logOk -and (Test-Path "$STAGE\runner.log")) {
        Copy-Item "$STAGE\runner.log" "$RUNTIME\runner.log" -Force
    }

    # 사전/사후 release 검증에서 사용할 읽기 전용 receipt.
    $serverStrategyHash = (Get-FileHash "$RUNTIME\strategy_config.json" -Algorithm SHA256).Hash.ToLower()
    $releaseManifestPath = Join-Path $runtimeState "r4_release_last_applied.json"
    $releaseManifest = $null
    if (Test-Path $releaseManifestPath) {
        try {
            $releaseManifest = Get-Content $releaseManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        catch {
            throw "SERVER_R4_RELEASE_MANIFEST_INVALID"
        }
    }

    $receipt = [ordered]@{
        schema = "B_BOT_R4_SERVER_PULL_RECEIPT_V1"
        purpose = $Purpose
        pulled_at = (Get-Date).ToUniversalTime().ToString("o")
        read_only = $true
        server_write = $false
        service_restart = $false
        position_mutation = $false
        server_strategy_config_sha256 = $serverStrategyHash
        last_applied_request_id = if ($null -ne $releaseManifest) { [string]$releaseManifest.request_id } else { "" }
        last_applied_strategy_config_sha256 = if ($null -ne $releaseManifest) { [string]$releaseManifest.strategy_config_sha256 } else { "" }
    }
    $receiptTmp = "$RUNTIME\sync_receipt.json.tmp"
    $receipt | ConvertTo-Json -Depth 10 | Set-Content $receiptTmp -Encoding UTF8
    Move-Item $receiptTmp "$RUNTIME\sync_receipt.json" -Force

    # ================= 5. LOCAL 보호 파일 불변 확인 =================
    $after = Get-ProtectedHashes
    foreach ($rel in $PROTECTED) {
        if ($before[$rel] -ne $after[$rel]) {
            throw "LOCAL_FILE_CHANGED_DURING_PULL:$rel"
        }
    }

    # ================= 6. 포지션 snapshot 상태 =================
    $positionCache = Join-Path $runtimeState "position_cache.json"
    $count = "UNKNOWN"
    if (Test-Path $positionCache) {
        $obj = Get-Content $positionCache -Raw -Encoding UTF8 | ConvertFrom-Json
        $positions = if ($obj -and $obj.PSObject.Properties.Name -contains "positions") { $obj.positions } else { $obj }
        if ($null -eq $positions) {
            $count = 0
        }
        elseif ($positions -is [System.Array]) {
            $count = $positions.Count
        }
        else {
            $count = @($positions.PSObject.Properties | Where-Object { $_.MemberType -eq "NoteProperty" }).Count
        }
    }

    # ================= 7. R4 Runtime 성과 -> LOCAL 연구 환류 =================
    # 서버에는 쓰지 않고 방금 받은 read-only snapshot만 exact Strategy Instance 기준으로 연구 evidence화한다.
    & "$ROOT\.venv\Scripts\python.exe" "$ROOT\strategy_lab\v3_evidence_research.py" --runtime-feedback
    if ($LASTEXITCODE -ne 0) {
        throw "R4_RUNTIME_RESEARCH_FEEDBACK_FAIL"
    }

    # ================= 8. LOCAL 연구 무결성 CHECK =================
    & "$ROOT\.venv\Scripts\python.exe" "$ROOT\strategy_lab\auto_research_local.py" --mode check
    if ($LASTEXITCODE -ne 0) {
        throw "LOCAL_RESEARCH_CHECK_FAIL"
    }

    Write-Host "SOURCE_CHANGED=0"
    Write-Host "CURRENT_SERVER_POSITION_SNAPSHOT=$count"
    Write-Host "SERVER_STRATEGY_CONFIG_SHA256=$serverStrategyHash"
    Write-Host "SERVER_TO_LOCAL_READ_ONLY_SYNC=PASS"
    Write-Host "LOCAL_RUNTIME_UPDATED=YES"
    Write-Host "LOCAL_RESEARCH_CONFIG_UNCHANGED=PASS"
    Write-Host "R4_RUNTIME_RESEARCH_FEEDBACK=PASS"
    Write-Host "SERVER_WRITE=0"
    Write-Host "SERVICE_RESTART=0"
    Write-Host "POSITION_MUTATION=0"
}
finally {
    Remove-Item $STAGE -Recurse -Force -ErrorAction SilentlyContinue
}
