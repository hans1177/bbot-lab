# 파일명: ENABLE_SERVER_SYNC_AFTER_LOCAL_PASS.ps1
param(
    [Parameter(Mandatory=$true)][double]$KrwPerUsdt,
    [switch]$SyncNow
)

$ErrorActionPreference = "Stop"

# ================= 경로 =================
$ROOT = "C:\my\bottest"
$PY = "$ROOT\.venv\Scripts\python.exe"
$REPORT = "$ROOT\LOCAL_V3_TEST_REPORT.json"
$REQ = "$ROOT\strategy_lab\results\v3\runtime_sync_request.json"
$HOLD = "$ROOT\SERVER_PUSH_HOLD.flag"

if ($KrwPerUsdt -le 0) { throw "KrwPerUsdt must be > 0" }
if (-not (Test-Path $REPORT)) { throw "LOCAL_V3_TEST_REPORT.json MISSING" }
if (-not (Test-Path $HOLD)) { throw "SERVER_PUSH_HOLD_ALREADY_OFF" }

# ================= LOCAL 검증 결과 =================
$reportObj = Get-Content $REPORT -Raw -Encoding UTF8 | ConvertFrom-Json
if ($reportObj.ok -ne $true -or $reportObj.real_order_off -ne $true) {
    throw "LOCAL_TEST_PASS_CONTRACT_NOT_SATISFIED"
}

# ================= FX local config 반영 =================
$targets = @("$ROOT\strategy_config.json") + @(
    Get-ChildItem "$ROOT\strategy_lab\results\v3" -Filter "strategy_config_pending_*.json" -File -ErrorAction SilentlyContinue |
    ForEach-Object { $_.FullName }
)
foreach ($path in $targets) {
    $obj = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $obj.account_mode) {
        $obj | Add-Member -NotePropertyName account_mode -NotePropertyValue ([pscustomobject]@{})
    }
    $obj.account_mode | Add-Member -NotePropertyName krw_per_usdt -NotePropertyValue $KrwPerUsdt -Force
    $obj.account_mode | Add-Member -NotePropertyName fx_source -NotePropertyValue "LOCAL_CONFIG_OR_ENV" -Force
    $obj.account_mode | Add-Member -NotePropertyName external_crypto_exchange_fx -NotePropertyValue $false -Force
    $tmp = "$path.tmp.r4fx"
    $obj | ConvertTo-Json -Depth 40 | Set-Content $tmp -Encoding UTF8
    Move-Item $tmp $path -Force
}

# ================= 최종 로컬 검증 =================
$env:BBOT_LOCAL_ROOT = $ROOT
$env:BOT_BASE_DIR = $ROOT
$env:RUNNER_DRY_RUN = "1"
$env:B_BOT_V3_VERIFY_MODE = "POST_MIGRATION_RUNTIME"
$env:B_BOT_V3_LOGIC_VERIFY_MODE = "RELEASE_CANDIDATE"

# 최신 헌법 구조 검증 + R4 동기화 구현 검증
$verifiers = @(
    @{ Path = "$ROOT\verify_v3.py"; Fail = "VERIFY_V3_PASS_REQUIRED" },
    @{ Path = "$ROOT\verify_overfit_validation.py"; Fail = "OVERFIT_PASS_REQUIRED" },
    @{ Path = "$ROOT\verify_logic_validation.py"; Fail = "LOGIC_PASS_REQUIRED" },
    @{ Path = "$ROOT\verify_r4_sync.py"; Fail = "R4_SYNC_PASS_REQUIRED" },
    @{ Path = "$ROOT\verify_autonomous_runtime_release.py"; Fail = "R4_RELEASE_POLICY_PASS_REQUIRED" }
)
foreach ($row in $verifiers) {
    $verifier = [string]$row.Path
    if (-not (Test-Path $verifier)) { throw "LOCAL_VERIFIER_MISSING:$verifier" }
    & $PY $verifier | Out-Null
    if ($LASTEXITCODE -ne 0) { throw ([string]$row.Fail) }
}

# ================= exact R4 release request =================
if (-not (Test-Path $REQ)) {
    throw "R4_RUNTIME_SYNC_REQUEST_MISSING"
}
$reqObj = Get-Content $REQ -Raw -Encoding UTF8 | ConvertFrom-Json
if ($reqObj.schema -ne "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1") { throw "R4_REQUEST_SCHEMA_INVALID" }
if ($reqObj.autonomous_rule_passed -ne $true) { throw "R4_AUTONOMOUS_RULE_REQUIRED" }
if ($reqObj.approved_for_server_release -ne $true) { throw "R4_APPROVED_RELEASE_REQUIRED" }
if ($reqObj.core_mutation -ne $false) { throw "R4_CORE_MUTATION_FORBIDDEN" }
if (([string]$reqObj.tier).ToUpper() -ne "CANARY") { throw "R4_INITIAL_RELEASE_MUST_BE_CANARY" }
if (([string]$reqObj.local_qualification_tier).ToUpper() -ne "CANARY") { throw "R4_LOCAL_QUALIFICATION_CANARY_REQUIRED" }
if (([string]$reqObj.shadow_gate).ToUpper() -ne "PASS") { throw "R4_SHADOW_PASS_REQUIRED" }
$kind = ([string]$reqObj.version_kind).ToUpper()
if ($kind -notin @("STRATEGY", "STRATEGY_INSTANCE_POLICY_BUNDLE")) { throw "R4_VERSION_KIND_INVALID" }

if ($kind -eq "STRATEGY") {
    if (-not ([string]$reqObj.strategy_instance_id).Trim()) { throw "R4_STRATEGY_INSTANCE_ID_REQUIRED" }
}
else {
    $rows = @($reqObj.strategy_instances)
    if ($rows.Count -eq 0) { throw "R4_POLICY_INSTANCE_TARGETS_REQUIRED" }
    foreach ($row in $rows) {
        if (-not ([string]$row.strategy_instance_id).Trim() -or $row.approved_for_server_release -ne $true) {
            throw "R4_POLICY_INSTANCE_TARGET_INVALID"
        }
    }
}

# ================= 서버 읽기 전용 사전 snapshot =================
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ROOT\sync_from_server.ps1" -Purpose PreRelease
if ($LASTEXITCODE -ne 0) { throw "R4_PRE_RELEASE_PULL_FAIL" }

# HOLD 해제는 validated release writer만 허용한다. 실서비스 전환은 여기서 하지 않는다.
Remove-Item $HOLD -Force
Remove-Item "$ROOT\LOCAL_TEST_MODE.flag" -Force -ErrorAction SilentlyContinue
Write-Host "SERVER_PUSH_HOLD=OFF"
Write-Host "R4_VALIDATED_RELEASE_WRITER=ENABLED"
Write-Host "LIVE_CUTOVER=NOT_PERFORMED"
Write-Host "SERVICE_RESTART=0"

if ($SyncNow) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ROOT\strategy_runtime_sync.ps1" -Mode ApplyPending
    if ($LASTEXITCODE -ne 0) { throw "R4_RUNTIME_SYNC_NOW_FAIL" }
}
else {
    Write-Host "NEXT=R4_RUNTIME_SYNC"
}
