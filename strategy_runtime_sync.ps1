# 파일명: strategy_runtime_sync.ps1
param(
    [ValidateSet("ApplyPending", "Status", "PlanOnly")]
    [string]$Mode = "ApplyPending"
)

$ErrorActionPreference = "Stop"
$activeInput = $null
try {

# ================= 경로 / 접속 =================
$ROOT = "C:\my\bottest"
$KEY = "C:\my\han.pem"
$SSH = "ec2-user@13.159.128.120"
$REMOTE = "/home/ec2-user/bot"
$LOCK = "/tmp/bbot_source_sync.lock"
$PY = "$ROOT\.venv\Scripts\python.exe"
$REQ_PATH = "$ROOT\strategy_lab\results\v3\runtime_sync_request.json"
$PUSH_HOLD = Join-Path $ROOT "SERVER_PUSH_HOLD.flag"
$SYNC_FROM = Join-Path $ROOT "sync_from_server.ps1"

$FROZEN = [ordered]@{
    "runner.py" = "4926850cabce4d2659d6863032ac9630aaf30c9e074483cc5f38457ab35bc160"
    "order.py" = "513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c"
    "risk_engine.py" = "768aaa01735b82ef2705a186f244fbe3064954888d53f3546b09d05d49280fa6"
    "candidate.py" = "b33cb689602f9dd2c1287ce6ce78c175fb38b4f7c90d9d5d695a34e721d423ac"
}

# ================= 검증 함수 =================
function Assert-FrozenCore {
    foreach ($name in $FROZEN.Keys) {
        $path = Join-Path $ROOT $name
        if (-not (Test-Path $path)) {
            throw "FROZEN_CORE_MISSING:$name"
        }
        $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $FROZEN[$name]) {
            throw "FROZEN_CORE_HASH_MISMATCH:$name"
        }
    }
}

function Get-StrategyInstanceIds {
    param($Request)

    $kind = ([string]$Request.version_kind).ToUpper()
    $ids = @()

    if ($kind -eq "STRATEGY") {
        $id = ([string]$Request.strategy_instance_id).Trim()
        if (-not $id) {
            throw "EXACT_STRATEGY_INSTANCE_ID_REQUIRED"
        }
        $ids += $id
    }
    elseif ($kind -eq "STRATEGY_INSTANCE_POLICY_BUNDLE") {
        $targets = @($Request.strategy_instances)
        if ($targets.Count -eq 0) {
            throw "STRATEGY_INSTANCE_POLICY_TARGETS_REQUIRED"
        }
        foreach ($target in $targets) {
            if ($null -eq $target -or $target.approved_for_server_release -ne $true) {
                throw "STRATEGY_INSTANCE_POLICY_TARGET_NOT_APPROVED"
            }
            $id = ([string]$target.strategy_instance_id).Trim()
            if (-not $id) {
                throw "EXACT_STRATEGY_INSTANCE_ID_REQUIRED"
            }
            $ids += $id
        }
    }
    else {
        throw "VERSION_KIND_INVALID:$kind"
    }

    $unique = @($ids | Sort-Object -Unique)
    if ($unique.Count -ne $ids.Count) {
        throw "STRATEGY_INSTANCE_ID_DUPLICATE"
    }
    return $ids
}

function Assert-R4Request {
    param($Request)

    if ($Request.schema -ne "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1") {
        throw "STRATEGY_SYNC_REQUEST_SCHEMA_INVALID"
    }
    if ($Request.autonomous_rule_passed -ne $true) {
        throw "AUTONOMOUS_RULE_PASS_REQUIRED"
    }
    if ($Request.approved_for_server_release -ne $true) {
        throw "APPROVED_FOR_SERVER_RELEASE_REQUIRED"
    }
    if ($Request.core_mutation -ne $false) {
        throw "CORE_MUTATION_REQUEST_FORBIDDEN"
    }
    if (([string]$Request.tier).ToUpper() -ne "CANARY") {
        throw "LOCAL_RELEASE_TIER_MUST_BE_CANARY"
    }
    if (([string]$Request.local_qualification_tier).ToUpper() -ne "CANARY") {
        throw "LOCAL_QUALIFICATION_CANARY_REQUIRED"
    }
    if (([string]$Request.shadow_gate).ToUpper() -ne "PASS") {
        throw "LOCAL_SHADOW_PASS_REQUIRED"
    }
    if (-not ([string]$Request.request_id).Trim()) {
        throw "REQUEST_ID_MISSING"
    }

    $kind = ([string]$Request.version_kind).ToUpper()
    if ($kind -notin @("STRATEGY", "STRATEGY_INSTANCE_POLICY_BUNDLE")) {
        throw "VERSION_KIND_INVALID:$kind"
    }
    if ($kind -eq "STRATEGY" -and -not ([string]$Request.strategy_version).Trim()) {
        throw "STRATEGY_VERSION_MISSING"
    }
    if ($kind -eq "STRATEGY_INSTANCE_POLICY_BUNDLE") {
        if (-not ([string]$Request.policy_track).Trim()) {
            throw "POLICY_TRACK_MISSING"
        }
        if (-not ([string]$Request.policy_version).Trim()) {
            throw "POLICY_VERSION_MISSING"
        }
    }

    $roadmap = Get-Content "$ROOT\ROADMAP_CONSTITUTION_V3.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    if (([string]$Request.constitution_version).Trim() -ne ([string]$roadmap.constitution_version).Trim()) {
        throw "CONSTITUTION_VERSION_MISMATCH"
    }
    if (-not ([string]$Request.constitution_hash).Trim()) {
        throw "CONSTITUTION_HASH_MISSING"
    }

    $cvRaw = & $PY -c "import json,sys;sys.path.insert(0,r'$ROOT\strategy_lab');import v3_governance as g;print(json.dumps(g.validate_constitution(),ensure_ascii=False))"
    if ($LASTEXITCODE -ne 0 -or -not $cvRaw) {
        throw "CONSTITUTION_VALIDATION_EXEC_FAIL"
    }
    $cv = ($cvRaw -join "`n") | ConvertFrom-Json
    if ($cv.ok -ne $true) {
        throw "CONSTITUTION_VALIDATION_FAIL"
    }
    if (([string]$Request.constitution_hash).Trim().ToLower() -ne ([string]$cv.constitution_hash).Trim().ToLower()) {
        throw "CONSTITUTION_HASH_MISMATCH"
    }

    [void](Get-StrategyInstanceIds -Request $Request)
}

# ================= 상태 =================
if ($Mode -eq "Status") {
    if (Test-Path $REQ_PATH) {
        Get-Content $REQ_PATH -Raw -Encoding UTF8
    }
    else {
        Write-Host "STRATEGY_SYNC_PENDING=NO"
    }
    exit 0
}

if (-not (Test-Path $REQ_PATH)) {
    Write-Host "STRATEGY_SYNC_PENDING=NO"
    exit 0
}

Assert-FrozenCore
$reqObj = Get-Content $REQ_PATH -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-R4Request -Request $reqObj
$instanceIds = @(Get-StrategyInstanceIds -Request $reqObj)
$kind = ([string]$reqObj.version_kind).ToUpper()
$rid = ([string]$reqObj.request_id).Trim()
$requestShaBefore = (Get-FileHash $REQ_PATH -Algorithm SHA256).Hash.ToLower()

# ================= R4 정확 병합 =================
$cfgRel = ([string]$reqObj.strategy_config_file).Replace("/", "\")
if (-not $cfgRel -or $cfgRel.Contains("..") -or -not $cfgRel.StartsWith("strategy_lab\results\v3\strategy_config_pending_")) {
    throw "PENDING_STRATEGY_CONFIG_PATH_INVALID"
}
$pendingCfg = Join-Path $ROOT $cfgRel
if (-not (Test-Path $pendingCfg)) {
    throw "PENDING_STRATEGY_CONFIG_MISSING"
}

$merger = "$ROOT\strategy_lab\v3_runtime_config_merge.py"
$mergedCfg = Join-Path $env:TEMP ("r4_runtime_" + [guid]::NewGuid().ToString("N") + ".json")
$activeInput = Join-Path $env:TEMP ("r4_server_active_" + [guid]::NewGuid().ToString("N") + ".json")
& scp -q -o BatchMode=yes -o ConnectTimeout=15 -i $KEY "${SSH}:$REMOTE/strategy_config.json" $activeInput
if ($LASTEXITCODE -ne 0) { throw "LIVE_ACTIVE_CONFIG_READ_FAILED" }
$activeConfigHash = (Get-FileHash $activeInput -Algorithm SHA256).Hash.ToLower()

$mergeArgs = @(
    $merger,
    "--active", $activeInput,
    "--pending", $pendingCfg,
    "--output", $mergedCfg,
    "--version-kind", $kind,
    "--tier", "CANARY"
)

if ($kind -eq "STRATEGY") {
    $mergeArgs += @(
        "--timeframe", ([string]$reqObj.timeframe),
        "--strategy", ([string]$reqObj.strategy),
        "--strategy-version", ([string]$reqObj.strategy_version)
    )
}
else {
    $mergeArgs += @(
        "--policy-track", ([string]$reqObj.policy_track),
        "--policy-version", ([string]$reqObj.policy_version)
    )
}
foreach ($instanceId in $instanceIds) {
    $mergeArgs += @("--strategy-instance-id", $instanceId)
}

& $PY @mergeArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $mergedCfg)) {
    throw "R4_RUNTIME_CONFIG_MERGE_FAIL"
}

# 실제 서버 validator로 병합 설정을 검증한다. 서비스/주문 함수는 호출하지 않는다.
$cfgB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($mergedCfg))
$validateSource = @"
import base64,json,os,sys,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='r4-config-validation-') as folder:
    path=Path(folder)/'strategy_config.json'
    path.write_bytes(base64.b64decode('$cfgB64'))
    os.environ['BOT_BASE_DIR']='/home/ec2-user/bot'
    os.environ['STRATEGY_CONFIG_FILE']=str(path)
    sys.path.insert(0,'/home/ec2-user/bot')
    import strategy_runtime
    result=strategy_runtime.validate()
    print(json.dumps(result,ensure_ascii=True))
    if not result.get('ok'):raise SystemExit(1)
"@
$validationB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($validateSource))
$validationInput = "import base64;exec(base64.b64decode('$validationB64'))"
$validationInput | & ssh -o BatchMode=yes -o ConnectTimeout=15 -i $KEY $SSH "/home/ec2-user/bot/venv/bin/python -B -"
if ($LASTEXITCODE -ne 0) { throw "PENDING_CONFIG_LIVE_VALIDATION_FAILED" }

$mergedObj = Get-Content $mergedCfg -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($instanceId in $instanceIds) {
    $row = $mergedObj.r4_runtime.strategy_instances.$instanceId
    if ($null -eq $row) {
        throw "R4_MERGED_INSTANCE_MISSING:$instanceId"
    }
    if ($row.server_activation_authorized -ne $true -or $row.approved_for_server_release -ne $true) {
        throw "R4_MERGED_INSTANCE_NOT_AUTHORIZED:$instanceId"
    }
    if (([string]$row.tier).ToUpper() -ne "CANARY") {
        throw "R4_MERGED_INSTANCE_TIER_INVALID:$instanceId"
    }
}

# ================= Runtime 전략 산출물 =================
$strategyRel = ([string]$reqObj.strategy_file).Replace("/", "\")
$strategyPath = $null
$destName = ""
if ($strategyRel) {
    if ($kind -ne "STRATEGY") {
        throw "POLICY_BUNDLE_MUST_NOT_PUBLISH_STRATEGY_SOURCE"
    }
    if ($strategyRel.Contains("..")) {
        throw "STRATEGY_SOURCE_PATH_INVALID"
    }
    $strategyPath = Join-Path $ROOT $strategyRel
    if (-not (Test-Path $strategyPath)) {
        throw "PENDING_STRATEGY_FILE_MISSING"
    }
    & $PY -m py_compile $strategyPath
    if ($LASTEXITCODE -ne 0) {
        throw "LOCAL_STRATEGY_COMPILE_FAIL"
    }
}

# ================= PlanOnly =================
if ($Mode -eq "PlanOnly") {
    Write-Host "R4_RUNTIME_SYNC_PLAN_ONLY=YES"
    Write-Host "REQUEST_ID=$rid"
    Write-Host "VERSION_KIND=$kind"
    Write-Host "TIER=CANARY"
    $instanceIds | ForEach-Object { Write-Host "STRATEGY_INSTANCE_ID=$_" }
    Write-Host "SERVER_WRITE=0"
    Write-Host "SERVICE_RESTART=0"
    Remove-Item $mergedCfg -Force -ErrorAction SilentlyContinue
    exit 0
}

if (Test-Path $PUSH_HOLD) {
    Write-Host "SERVER_PUSH_HOLD=ON"
    Write-Host "STRATEGY_RUNTIME_SYNC=HOLD"
    Remove-Item $mergedCfg -Force -ErrorAction SilentlyContinue
    exit 0
}

# ================= 강제 사전 Pull =================
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SYNC_FROM -Purpose PreRelease
if ($LASTEXITCODE -ne 0) {
    throw "PRE_RELEASE_SERVER_TO_LOCAL_PULL_FAIL"
}

# Pull 사이 request가 바뀌면 적용하지 않는다.
$requestShaAfterPull = (Get-FileHash $REQ_PATH -Algorithm SHA256).Hash.ToLower()
if ($requestShaAfterPull -ne $requestShaBefore) {
    throw "REQUEST_BYTES_CHANGED_AFTER_PRE_RELEASE_PULL"
}
$reqAfterPull = Get-Content $REQ_PATH -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-R4Request -Request $reqAfterPull
if (([string]$reqAfterPull.request_id).Trim() -ne $rid) {
    throw "REQUEST_CHANGED_AFTER_PRE_RELEASE_PULL"
}

# ================= 서버 Stage =================
$stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
$stage = Join-Path $env:TEMP "bbot_r4_runtime_sync_$stamp"
$archive = Join-Path $env:TEMP "bbot_r4_runtime_sync_$stamp.tar.gz"
$remoteStage = "/tmp/bbot_r4_runtime_sync_$stamp"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $archive -Force -ErrorAction SilentlyContinue
New-Item "$stage\assist\strategies" -ItemType Directory -Force | Out-Null
New-Item "$stage\state" -ItemType Directory -Force | Out-Null

try {
    Copy-Item $mergedCfg "$stage\strategy_config.json" -Force

    if ($strategyPath) {
        $name = ([string]$reqObj.strategy).ToUpper()
        $registry = $mergedObj.strategy_registry
        $row = $registry.$name
        if ($null -eq $row -or -not ([string]$row.file).Trim()) {
            throw "STRATEGY_REGISTRY_DEST_MISSING"
        }
        $destName = ([string]$row.file).Trim()
        if ($destName.Contains("/") -or $destName.Contains("\") -or $destName.Contains("..")) {
            throw "STRATEGY_REGISTRY_DEST_INVALID"
        }

        $runtimeArtifact = Join-Path "$stage\assist\strategies" $destName
        $packager = "$ROOT\strategy_lab\v3_portable_strategy_packager.py"
        if (-not (Test-Path $packager)) {
            throw "PORTABLE_STRATEGY_PACKAGER_MISSING"
        }
        & $PY $packager --source $strategyPath --output $runtimeArtifact
        if ($LASTEXITCODE -ne 0) {
            throw "PORTABLE_STRATEGY_PACKAGE_FAIL"
        }
        & $PY -m py_compile $runtimeArtifact
        if ($LASTEXITCODE -ne 0) {
            throw "PORTABLE_RUNTIME_STRATEGY_COMPILE_FAIL"
        }
    }

    $configHash = (Get-FileHash "$stage\strategy_config.json" -Algorithm SHA256).Hash.ToLower()
    $manifest = [ordered]@{
        schema = "B_BOT_R4_VALIDATED_RELEASE_MANIFEST_V1"
        request_id = $rid
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        version_kind = $kind
        tier = "CANARY"
        strategy_instance_ids = @($instanceIds)
        strategy_version = ([string]$reqObj.strategy_version)
        policy_track = ([string]$reqObj.policy_track)
        policy_version = ([string]$reqObj.policy_version)
        strategy_policy_bundle_version = ([string]$reqObj.strategy_policy_bundle_version)
        constitution_version = ([string]$reqObj.constitution_version)
        constitution_hash = ([string]$reqObj.constitution_hash)
        strategy_config_sha256 = $configHash
        runtime_strategy_file = $destName
        approved_for_server_release = $true
        local_shadow_pass = $true
        core_mutation = $false
    }
    $manifest | ConvertTo-Json -Depth 20 | Set-Content "$stage\state\r4_release_manifest.json" -Encoding UTF8

    & tar.exe -czf $archive -C $stage .
    if ($LASTEXITCODE -ne 0) {
        throw "R4_RUNTIME_TAR_FAIL"
    }

    $releaseExitCode = $LASTEXITCODE
    & ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -i $KEY $SSH "rm -rf '$remoteStage' && mkdir -p '$remoteStage'"
    if ($LASTEXITCODE -ne 0) {
        throw "REMOTE_R4_STAGE_FAIL"
    }
    & scp -q -o BatchMode=yes -o ConnectTimeout=15 -i $KEY $archive "${SSH}:$remoteStage/payload.tar.gz"
    if ($LASTEXITCODE -ne 0) {
        throw "R4_RUNTIME_UPLOAD_FAIL"
    }

    $destB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($destName))
    $remoteScript = @'
set -euo pipefail
STAGE="__STAGE__"
ROOT="/home/ec2-user/bot"
LOCK="/tmp/bbot_source_sync.lock"
cd "$STAGE"
tar -xzf payload.tar.gz

# R4 release payload에는 연구/검증 트리나 raw 결과가 들어갈 수 없다.
if tar -tzf payload.tar.gz | grep -Eq '(^|/)(strategy_lab|backtest_data|verify_[^/]*|results)(/|$)'; then
  echo R4_FORBIDDEN_RELEASE_PAYLOAD >&2
  exit 31
fi

# 기존 Frozen legacy writer가 살아있는 상태에서는 config를 바꾸지 않는다.
if systemctl is-active --quiet runner.service; then
  echo LEGACY_RUNNER_STILL_ACTIVE >&2
  exit 32
fi

/home/ec2-user/bot/venv/bin/python - <<'PY'
import hashlib, json
from pathlib import Path
cfg = json.load(open('strategy_config.json', encoding='utf-8-sig'))
man = json.load(open('state/r4_release_manifest.json', encoding='utf-8-sig'))
assert man.get('schema') == 'B_BOT_R4_VALIDATED_RELEASE_MANIFEST_V1'
assert man.get('approved_for_server_release') is True
assert man.get('local_shadow_pass') is True
assert man.get('core_mutation') is False
assert str(man.get('tier') or '').upper() == 'CANARY'
assert man.get('strategy_instance_ids')
raw = Path('strategy_config.json').read_bytes()
assert hashlib.sha256(raw).hexdigest() == man.get('strategy_config_sha256')
rows = ((cfg.get('r4_runtime') or {}).get('strategy_instances') or {})
for iid in man['strategy_instance_ids']:
    row = rows.get(iid)
    assert isinstance(row, dict)
    assert row.get('approved_for_server_release') is True
    assert row.get('server_activation_authorized') is True
    assert str(row.get('tier') or '').upper() == 'CANARY'
    assert row.get('exact_strategy_version')
    assert row.get('strategy_policy_bundle_version')
    assert isinstance(row.get('policy_snapshot'), dict) and row.get('policy_snapshot')
print('R4_STAGED_RELEASE_CONTRACT=PASS')
PY

DEST="$(printf '%s' '__DESTB64__' | base64 -d)"
if [ -n "$DEST" ]; then
  /home/ec2-user/bot/venv/bin/python -m py_compile "$STAGE/assist/strategies/$DEST"
fi

(
  flock -w 120 -x 9
  mkdir -p "$ROOT/state" "$ROOT/assist/strategies"

  # 동일 request/config 재시도는 idempotent하게 성공 처리한다.
  if [ -f "$ROOT/state/r4_release_last_applied.json" ]; then
    DUP="$(STAGE_MAN="$STAGE/state/r4_release_manifest.json" ROOT_MAN="$ROOT/state/r4_release_last_applied.json" /home/ec2-user/bot/venv/bin/python - <<'PY'
import json, os
try:
    a=json.load(open(os.environ['STAGE_MAN'],encoding='utf-8-sig'))
    b=json.load(open(os.environ['ROOT_MAN'],encoding='utf-8-sig'))
    print('1' if a.get('request_id')==b.get('request_id') and a.get('strategy_config_sha256')==b.get('strategy_config_sha256') else '0')
except Exception:
    print('0')
PY
)"
    if [ "$DUP" = "1" ]; then
      echo R4_RUNTIME_SYNC_DUPLICATE=YES
      exit 0
    fi
  fi

  CURRENT_CONFIG_SHA="$(sha256sum "$ROOT/strategy_config.json" | cut -d ' ' -f1)"
  if [ "$CURRENT_CONFIG_SHA" != "__ACTIVE_SHA__" ]; then
    echo LIVE_CONFIG_CHANGED_RETRY_REQUIRED >&2
    exit 34
  fi

  BACK="$STAGE/backup"
  mkdir -p "$BACK/assist/strategies" "$BACK/state"
  cp -a "$ROOT/strategy_config.json" "$BACK/strategy_config.json"
  DEST_EXISTED=0
  if [ -n "$DEST" ] && [ -f "$ROOT/assist/strategies/$DEST" ]; then
    DEST_EXISTED=1
    cp -a "$ROOT/assist/strategies/$DEST" "$BACK/assist/strategies/$DEST"
  fi
  [ -f "$ROOT/state/r4_release_last_applied.json" ] && cp -a "$ROOT/state/r4_release_last_applied.json" "$BACK/state/r4_release_last_applied.json" || true

  R4_WAS_ACTIVE=0
  systemctl is-active --quiet r4-runtime.service && R4_WAS_ACTIVE=1 || true

  cp "$STAGE/strategy_config.json" "$ROOT/strategy_config.json.tmp.r4"
  mv -f "$ROOT/strategy_config.json.tmp.r4" "$ROOT/strategy_config.json"
  if [ -n "$DEST" ]; then
    cp "$STAGE/assist/strategies/$DEST" "$ROOT/assist/strategies/$DEST.tmp.r4"
    mv -f "$ROOT/assist/strategies/$DEST.tmp.r4" "$ROOT/assist/strategies/$DEST"
  fi
  cp "$STAGE/state/r4_release_manifest.json" "$ROOT/state/r4_release_last_applied.json.tmp.r4"
  mv -f "$ROOT/state/r4_release_last_applied.json.tmp.r4" "$ROOT/state/r4_release_last_applied.json"

  # 서비스 재시작은 하지 않는다. 이미 R4가 실행 중이면 hot-reload 후 생존만 확인한다.
  if [ "$R4_WAS_ACTIVE" = "1" ]; then
    sleep 3
    if ! systemctl is-active --quiet r4-runtime.service; then
      cp -a "$BACK/strategy_config.json" "$ROOT/strategy_config.json"
      if [ -n "$DEST" ]; then
        if [ "$DEST_EXISTED" = "1" ]; then
          cp -a "$BACK/assist/strategies/$DEST" "$ROOT/assist/strategies/$DEST"
        else
          rm -f "$ROOT/assist/strategies/$DEST"
        fi
      fi
      if [ -f "$BACK/state/r4_release_last_applied.json" ]; then
        cp -a "$BACK/state/r4_release_last_applied.json" "$ROOT/state/r4_release_last_applied.json"
      else
        rm -f "$ROOT/state/r4_release_last_applied.json"
      fi
      echo R4_RUNTIME_DIED_AFTER_HOT_RELOAD >&2
      exit 42
    fi
  fi
) 9>"$LOCK"

echo R4_RUNTIME_SYNC=PASS
'@

    $remoteScript = $remoteScript.Replace("__STAGE__", $remoteStage).Replace("__DESTB64__", $destB64).Replace("__ACTIVE_SHA__", $activeConfigHash)
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($remoteScript -replace "`r", "")))
    & ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -i $KEY $SSH "echo '$b64' | base64 -d | bash"
    if ($LASTEXITCODE -ne 0) {
        throw "R4_RUNTIME_SYNC_REMOTE_FAIL"
    }

    # 서버 적용 성공 후 동일 config를 로컬 active config에도 원자적으로 채택한다.
    $tmp = "$ROOT\strategy_config.json.tmp.r4sync"
    Copy-Item -LiteralPath $mergedCfg -Destination $tmp -Force
    Move-Item -LiteralPath $tmp -Destination "$ROOT\strategy_config.json" -Force

    # ================= 강제 사후 Pull =================
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SYNC_FROM -Purpose PostRelease
    if ($LASTEXITCODE -ne 0) {
        throw "POST_RELEASE_SERVER_TO_LOCAL_PULL_FAIL"
    }

    # 사후 Pull까지 성공해야 local deployment를 APPLIED로 소비한다.
    $env:BBOT_LOCAL_ROOT = $ROOT
    & $PY -c "import json,sys; sys.path.insert(0,sys.argv[1]); import v3_deployment as d; d.mark_applied(json.load(open(sys.argv[2],encoding='utf-8-sig')))" "$ROOT\strategy_lab" $REQ_PATH
    if ($LASTEXITCODE -ne 0) {
        throw "LOCAL_DEPLOYMENT_MARK_FAIL"
    }

    $applied = "$ROOT\strategy_lab\results\v3\runtime_sync_last_applied.json"
    Copy-Item $REQ_PATH $applied -Force
    Remove-Item $REQ_PATH -Force

    Write-Host "R4_RUNTIME_SYNC=PASS request=$rid"
    Write-Host "SERVICE_RESTART=0"
    Write-Host "RESEARCH_PAYLOAD=0"
}
finally {
    Remove-Item $mergedCfg -Force -ErrorAction SilentlyContinue
    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $archive -Force -ErrorAction SilentlyContinue
    $releaseExitCode = $LASTEXITCODE
    & ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -i $KEY $SSH "rm -rf '$remoteStage'" 2>$null | Out-Null
    $global:LASTEXITCODE = $releaseExitCode
}

}
finally {
    if ($activeInput) { Remove-Item -LiteralPath $activeInput -Force -ErrorAction SilentlyContinue }
}
