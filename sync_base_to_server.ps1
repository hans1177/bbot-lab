# 파일명: sync_base_to_server.ps1
param(
    [switch]$DeferStrategyServices,
    [switch]$MaintenanceUnderHold,
    [string[]]$MaintenanceFiles = @(),
    [switch]$PlanOnly,
    [switch]$PerformanceTierRelease,
    [switch]$Leverage30Release,
    [string]$SourceRoot = ""
)

$ErrorActionPreference = "Stop"

# ================= 경로 / 접속 =================
$ROOT = "C:\my\bottest"
$KEY  = "C:\my\han.pem"
$SSH  = "ec2-user@13.159.128.120"
$REMOTE_ROOT = "/home/ec2-user/bot"
$LOCK = "/tmp/bbot_source_sync.lock"
$PUSH_HOLD = Join-Path $ROOT "SERVER_PUSH_HOLD.flag"
$TYPED_RELEASE = $PerformanceTierRelease -or $Leverage30Release
if ($PerformanceTierRelease -and $Leverage30Release) { throw "ONE_RELEASE_KIND_REQUIRED" }

# ================= Frozen Core =================
$FROZEN = [ordered]@{
    "runner.py" = "4926850cabce4d2659d6863032ac9630aaf30c9e074483cc5f38457ab35bc160"
    "order.py" = "513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c"
    "risk_engine.py" = "768aaa01735b82ef2705a186f244fbe3064954888d53f3546b09d05d49280fa6"
    "candidate.py" = "b33cb689602f9dd2c1287ce6ce78c175fb38b4f7c90d9d5d695a34e721d423ac"
}

foreach ($name in $FROZEN.Keys) {
    $path = Join-Path $ROOT $name
    if (-not (Test-Path $path)) { throw "FROZEN_CORE_MISSING:$name" }
    $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $FROZEN[$name]) { throw "FROZEN_CORE_HASH_MISMATCH:$name" }
}

# ================= Base 후보 =================
# R4 헌법: 일반 base sync는 자동 배포하지 않는다.
# 이 목록은 drift 확인용이며 Strategy payload/config/research/verify/Frozen Core는 제외한다.
$NORMAL_BASE_FILES = @(
    "gate.py",
    "collector.py",
    "reservation.py",
    "leverage.py",
    "symbol.py",
    "major.py",
    "symbol_cleaner.py",
    "strategy_runtime.py",
    "late_entry.py",
    "capital_allocator.py",
    "account_mode.py",
    "config.py",
    "server_status_telegram.py",
    "sync_snapshot.py",
    "strategy_leg_ledger.py",
    "multi_strategy_runtime.py",
    "r4_exchange_adapter.py",
    "r4_user_stream.py",
    "r4_runtime_service.py",
    "r4_cutover.py",
    "r4-runtime.service",
    "assist\__init__.py",
    "assist\daemon.py",
    "assist\strategy_registry.py",
    "assist\utils.py",
    "assist\cycle.py",
    "assist\logic.py",
    "assist\state.py"
)

# HOLD 상태의 수동 maintenance만 명시 파일 적용 가능.
# Frozen Core와 전략/연구 산출물은 어떤 경우에도 허용하지 않는다.
$MAINTENANCE_ALLOWLIST = @($NORMAL_BASE_FILES) + @("r4_funding_feed.py", "r4_performance_tiering.py", "tier_statistics.py")

function RelUnix([string]$Path) { return ($Path -replace '\\','/') }
function Sha([string]$Path) {
    if (-not (Test-Path $Path)) { return "" }
    return (Get-FileHash $Path -Algorithm SHA256).Hash.ToLower()
}

# ================= 성과 등급 수동 배포 =================
$GRADE_FILES = @('r4_performance_tiering.py', 'r4_runtime_service.py', 'strategy_leg_ledger.py', 'multi_strategy_runtime.py', 'r4_exchange_adapter.py')
$GRADE_NEW = @{
    'r4_performance_tiering.py' = 'e89a4e03297074f497b929058034bab7e12739f8ea60e854c4a0d941a66b841a'
    'r4_runtime_service.py' = '9a4a810add27b7939a246e00b298c95cd3d8772163a0fef240930fc75afd8ee5'
    'strategy_leg_ledger.py' = 'f8a2b3f315599293a853de8990a62d98ce242f42a8ad83368f1da56fbd6e04d2'
    'multi_strategy_runtime.py' = '8e0704c8be02527acd4f9671427fafd290b756a8270ce551e24dd735886654fe'
    'r4_exchange_adapter.py' = '77441158f20a5f34055bbc90733178112387f9a13c8bf4d3f7ed3f3bab6e279a'
}
$GRADE_OLD = @{
    'r4_performance_tiering.py' = '82625d2cdaf5566ceccc09e16d143ff962cfafdcdbdea184369aa74d1a8953ac'
    'r4_runtime_service.py' = '048b6861f70a8df679b8b04c4d5926ced2aa9703232e92510361bfd222f0e6e1'
    'strategy_leg_ledger.py' = 'e73cfa1e002dfbe0b25e0960be6d0d2439629b2a6a3c2c4716ec53164bdaa544'
    'multi_strategy_runtime.py' = 'a163d2c1db78eaeb6ec089d5f27686b169842f8c1d985fb075a19cf8c521ad47'
    'r4_exchange_adapter.py' = '938accb17e4cd4681a34405979317daacad346dd52454669a95fa1414fcf5957'
}
if ($Leverage30Release) {
    $GRADE_FILES = @('r4_runtime_service.py', 'strategy_runtime.py')
    $GRADE_NEW = @{
        'r4_runtime_service.py' = 'e588591e5d2632b99e7b328a045d51ba13d34c53665ba25ee121673af05e3395'
        'strategy_runtime.py' = '1cb77962db51e26fccc83cdcbb88b5f801fb0c2dd0d5b29aef3a7637bf486cfb'
    }
    $GRADE_OLD = @{
        'r4_runtime_service.py' = 'ddc27d06723bc7ed4086b6affc97311465fb926a0394e91115b798b5f7dcca98'
        'strategy_runtime.py' = '635d14a29e31dfaccf1bfe4fdc3b9f68bbcbaf326dc4e6d3b0dafd109b872d60'
    }
}
$SOURCE_ROOT = $ROOT
if ($TYPED_RELEASE) {
    if (-not $MaintenanceUnderHold) { throw 'GRADE_RELEASE_REQUIRES_MANUAL_MAINTENANCE' }
    if (-not $SourceRoot -or -not (Test-Path -LiteralPath $SourceRoot -PathType Container)) { throw 'GRADE_SOURCE_ROOT_REQUIRED' }
    if ($MaintenanceFiles.Count -ne 0) { throw 'GRADE_RELEASE_FILE_SET_IS_FIXED' }
    $MaintenanceFiles = $GRADE_FILES
    $SOURCE_ROOT = (Resolve-Path -LiteralPath $SourceRoot).Path
    foreach ($name in $GRADE_FILES) {
        if ((Sha (Join-Path $SOURCE_ROOT $name)) -ne $GRADE_NEW[$name]) { throw "GRADE_SOURCE_HASH_MISMATCH:$name" }
    }
    if ($Leverage30Release) { Write-Host 'COMMON_LEVERAGE_RELEASE_TARGET=30; SMALL_ENTRY_INCLUDED=YES' } else { Write-Host 'GRADE_RELEASE_ENABLES_PERFORMANCE_FUNDING_AND_ENTRY_BENEFITS=YES' }
}
elseif ($SourceRoot) { throw 'SOURCE_ROOT_REQUIRES_GRADE_RELEASE' }

# ================= 모드 선택 =================
if ($MaintenanceUnderHold) {
    if (-not (Test-Path $PUSH_HOLD)) {
        throw "MAINTENANCE_UNDER_HOLD_REQUIRES_HOLD"
    }
    if ($MaintenanceFiles.Count -eq 0) {
        throw "MAINTENANCE_FILES_REQUIRED"
    }

    $selected = @()
    foreach ($raw in $MaintenanceFiles) {
        $rel = ([string]$raw).Trim().Replace("/", "\")
        if (-not $rel -or $rel.Contains("..")) {
            throw "MAINTENANCE_FILE_INVALID:$rel"
        }
        if ($FROZEN.Contains($rel)) {
            throw "FROZEN_CORE_SYNC_FORBIDDEN:$rel"
        }
        if ($rel -notin $MAINTENANCE_ALLOWLIST) {
            throw "MAINTENANCE_FILE_FORBIDDEN:$rel"
        }
        if ($selected -notcontains $rel) { $selected += $rel }
    }
    $BASE_FILES = @($selected)
    Write-Host "BASE_CODE_SYNC_MODE=MANUAL_MAINTENANCE_UNDER_HOLD"
}
else {
    $BASE_FILES = @($NORMAL_BASE_FILES)
    Write-Host "BASE_CODE_SYNC_MODE=R4_DRIFT_PLAN_ONLY"
    Write-Host "BASE_CODE_AUTO_DEPLOY=FORBIDDEN_R4"
}

Write-Host "STRATEGY_PAYLOAD_GENERAL_SYNC=NO"
Write-Host "STRATEGY_CONFIG_GENERAL_SYNC=NO"
Write-Host "RESEARCH_TREE_SYNC=NO"
Write-Host "VERIFY_FILE_SYNC=NO"
Write-Host "FROZEN_CORE_SYNC=NO"

$existing = @($BASE_FILES | Where-Object { Test-Path (Join-Path $SOURCE_ROOT $_) })
if ($existing.Count -eq 0) { throw "BASE_SOURCE_EMPTY" }

# ================= Remote hash 읽기 =================
$remoteScript = ""
foreach ($rel in $existing) {
    $unix = RelUnix $rel
    $remoteScript += "if [ -f '$REMOTE_ROOT/$unix' ]; then sha256sum '$REMOTE_ROOT/$unix'; else echo 'MISSING $REMOTE_ROOT/$unix'; fi`n"
}
$remoteScriptB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($remoteScript -replace "`r", "")))
Write-Host "?? ?? ?? ?? ?"
$remoteRows = @(& ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -i $KEY $SSH "echo '$remoteScriptB64' | base64 -d | bash" 2>&1)
if ($LASTEXITCODE -ne 0) { throw "REMOTE_HASH_READ_FAIL" }

$remoteHash = @{}
foreach ($line in $remoteRows) {
    $text = [string]$line
    if ($text -match '^([0-9a-fA-F]{64})\s+(.+)$') {
        $rel = $Matches[2].Replace("$REMOTE_ROOT/", "")
        $remoteHash[$rel] = $Matches[1].ToLower()
    }
}

$changed = @()
foreach ($rel in $existing) {
    $unix = RelUnix $rel
    if ((Sha (Join-Path $SOURCE_ROOT $rel)) -ne $remoteHash[$unix]) {
        $changed += $rel
    }
}

if ($TYPED_RELEASE) {
    foreach ($name in $GRADE_FILES) {
        $actual = [string]$remoteHash[$name]
        if ($actual -ne $GRADE_OLD[$name] -and $actual -ne $GRADE_NEW[$name]) { throw "AWS_GRADE_BASELINE_CHANGED:$name" }
    }
    # 설정만 비활성인 재시도도 같은 고정 파일 집합으로 처리한다.
    $changed = @($GRADE_FILES)
}
if ($changed.Count -eq 0) {
    Write-Host "BASE_CODE_CHANGED=0"
    Write-Host "BASE_CODE_SYNC=NO_CHANGE"
    Write-Host "SERVER_WRITE=0"
    Write-Host "SERVICE_RESTART=0"
    exit 0
}

# 일반 R4 base sync는 항상 계획만 출력한다. -PlanOnly 유무와 무관하게 write 금지.
if (-not $MaintenanceUnderHold) {
    Write-Host "BASE_CODE_SYNC_PLAN_ONLY=YES"
    Write-Host "BASE_CODE_CHANGED=$($changed.Count)"
    $changed | ForEach-Object { Write-Host "BASE_SYNC_PLAN $_" }
    Write-Host "SERVER_WRITE=0"
    Write-Host "SERVICE_RESTART=0"
    exit 0
}

if ($PlanOnly) {
    Write-Host "BASE_CODE_SYNC_PLAN_ONLY=YES"
    Write-Host "BASE_CODE_CHANGED=$($changed.Count)"
    $changed | ForEach-Object { Write-Host "BASE_SYNC_PLAN $_" }
    Write-Host "SERVER_WRITE=0"
    Write-Host "SERVICE_RESTART=0"
    exit 0
}

# ================= 수동 Maintenance 적용 =================
$stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
$stageLocal = Join-Path $env:TEMP "bbot_base_maintenance_$stamp"
$archive = Join-Path $env:TEMP "bbot_base_maintenance_$stamp.tar.gz"
$remoteStage = "/tmp/bbot_base_maintenance_$stamp"
Remove-Item $stageLocal -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $archive -Force -ErrorAction SilentlyContinue
New-Item $stageLocal -ItemType Directory -Force | Out-Null

try {
    foreach ($rel in $changed) {
        $src = Join-Path $SOURCE_ROOT $rel
        $dst = Join-Path $stageLocal $rel
        New-Item (Split-Path $dst) -ItemType Directory -Force | Out-Null
        Copy-Item $src $dst -Force
    }

    Write-Host "?? ?? ?? ?"
    & tar.exe -czf $archive -C $stageLocal .
    if ($LASTEXITCODE -ne 0) { throw "LOCAL_TAR_FAIL" }
    & ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -i $KEY $SSH "rm -rf '$remoteStage' && mkdir -p '$remoteStage'"
    if ($LASTEXITCODE -ne 0) { throw "REMOTE_STAGE_CREATE_FAIL" }
    Write-Host "?? ?? ??? ?? ?"
    & scp -q -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -i $KEY $archive "${SSH}:$remoteStage/payload.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw "BASE_PAYLOAD_UPLOAD_FAIL" }

    $manifest = (@($changed | ForEach-Object { RelUnix $_ }) -join "`n") + "`n"
    $manifestB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($manifest))
    $remote = @"
set -euo pipefail
cd '$remoteStage'
tar -xzf payload.tar.gz
printf '%s' '$manifestB64' | base64 -d > manifest.txt
if grep -Eq '(^|/)(runner.py|order.py|risk_engine.py|candidate.py|strategy_config.json|strategy_lab|results|verify_[^/]*)(`$|/)' manifest.txt; then
  echo FORBIDDEN_MAINTENANCE_PAYLOAD >&2
  exit 31
fi
while IFS= read -r f; do
  [ -z "`$f" ] && continue
  case "`$f" in *.py) /home/ec2-user/bot/venv/bin/python -m py_compile "$remoteStage/`$f" ;; esac
done < manifest.txt
(
  flock -x 9
  backup="$remoteStage/backup"
  backup_zip="$remoteStage/B_BOT_PRE_R4_MAINTENANCE_BACKUP.zip"
  persistent_backup='$REMOTE_ROOT/B_BOT_PRE_R4_MAINTENANCE_BACKUP.zip'
  mkdir -p "`$backup"
  : > "$remoteStage/new_files.txt"
  while IFS= read -r f; do
    [ -z "`$f" ] && continue
    mkdir -p "`$backup/`$(dirname "`$f")"
    if [ -f '$REMOTE_ROOT/'"`$f" ]; then
      cp -a '$REMOTE_ROOT/'"`$f" "`$backup/`$f"
    else
      printf '%s\n' "`$f" >> "$remoteStage/new_files.txt"
    fi
  done < manifest.txt

  BACKUP_DIR="`$backup" BACKUP_ZIP="`$backup_zip" /home/ec2-user/bot/venv/bin/python - <<'PY_BACKUP'
import os, zipfile
from pathlib import Path
base=Path(os.environ['BACKUP_DIR'])
out=Path(os.environ['BACKUP_ZIP'])
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for path in sorted(base.rglob('*')):
        if path.is_file():
            z.write(path, path.relative_to(base).as_posix())
PY_BACKUP

  rollback() {
    while IFS= read -r f; do
      [ -z "`$f" ] && continue
      if [ -f "`$backup/`$f" ]; then
        mkdir -p '$REMOTE_ROOT/'"`$(dirname "`$f")"
        cp -a "`$backup/`$f" '$REMOTE_ROOT/'"`$f"
      else
        rm -f '$REMOTE_ROOT/'"`$f"
      fi
    done < manifest.txt
  }
  trap rollback ERR

  while IFS= read -r f; do
    [ -z "`$f" ] && continue
    mkdir -p '$REMOTE_ROOT/'"`$(dirname "`$f")"
    cp "$remoteStage/`$f" '$REMOTE_ROOT/'"`$f.tmp.r4maint"
    mv -f '$REMOTE_ROOT/'"`$f.tmp.r4maint" '$REMOTE_ROOT/'"`$f"
  done < manifest.txt

  mv -f "`$backup_zip" "`$persistent_backup"
  trap - ERR
) 9>'$LOCK'
echo BASE_MAINTENANCE_SYNC=PASS
echo BASE_MAINTENANCE_BACKUP='$REMOTE_ROOT/B_BOT_PRE_R4_MAINTENANCE_BACKUP.zip'
echo BASE_MAINTENANCE_BACKUP_COUNT=1
"@

    if ($TYPED_RELEASE) {
        $initialHashes = @{}
        foreach ($name in $GRADE_FILES) { $initialHashes[$name] = [string]$remoteHash[$name] }
        $releasePlan = @{
            kind = $(if ($Leverage30Release) { "LEVERAGE30" } else { "PERFORMANCE_TIER" })
            root = $REMOTE_ROOT
            stage = $remoteStage
            old = $GRADE_OLD
            new = $GRADE_NEW
            initial = $initialHashes
            frozen = $FROZEN
        }
        $planJson = $releasePlan | ConvertTo-Json -Depth 10 -Compress
        $planB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($planJson))
        $program = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('IyDtjIzsnbzrqoU6IHN5bmNfYmFzZV90b19zZXJ2ZXIucHMxIOuCtOu2gCDrk7HquIkg67Cw7Y+sIOuzuOusuAppbXBvcnQgYmFzZTY0LGNvcHksaGFzaGxpYixpbXBvcnRsaWIsanNvbixsb2dnaW5nLG9zLHNodXRpbCxzcWxpdGUzLHN1YnByb2Nlc3Msc3lzLHRpbWUsemlwZmlsZQpmcm9tIGNvbnRleHRsaWIgaW1wb3J0IGNsb3NpbmcKZnJvbSBwYXRobGliIGltcG9ydCBQYXRoCgpwbGFuPWpzb24ubG9hZHMoYmFzZTY0LmI2NGRlY29kZSgnUkVMRUFTRV9QTEFOX0I2NCcpKQpyb290PVBhdGgocGxhblsncm9vdCddKTtzdGFnZT1QYXRoKHBsYW5bJ3N0YWdlJ10pO2JhY2t1cD1zdGFnZS8nYmFja3VwJwpmaWxlcz1wbGFuWyduZXcnXTtvbGQ9cGxhblsnb2xkJ107aW5pdGlhbD1wbGFuWydpbml0aWFsJ10KbGV2ZXJhZ2VfcmVsZWFzZT1wbGFuLmdldCgna2luZCcpPT0nTEVWRVJBR0UzMCcKc2VydmljZT0ncjQtcnVudGltZS5zZXJ2aWNlJztjb25maWc9cm9vdC8nc3RyYXRlZ3lfY29uZmlnLmpzb24nO2RhdGFiYXNlPXJvb3QvJ3N0YXRlL2hpc3RvcnkuZGInCmxvZz1yb290Lydsb2dzL3J1bm5lci5sb2cnO3BlcnNpc3RlbnQ9cm9vdC8nQl9CT1RfUFJFX1I0X01BSU5URU5BTkNFX0JBQ0tVUC56aXAnCnN5cy5kb250X3dyaXRlX2J5dGVjb2RlPVRydWUKb3MuZW52aXJvblsnQk9UX0JBU0VfRElSJ109c3RyKHJvb3QpCm9zLmVudmlyb25bJ1NUUkFURUdZX0NPTkZJR19GSUxFJ109c3RyKGNvbmZpZykKc3lzLnBhdGhbOjBdPVtzdHIoc3RhZ2UpLHN0cihyb290KV0KCmRlZiBzaGEocGF0aCk6CiAgICByZXR1cm4gaGFzaGxpYi5zaGEyNTYocGF0aC5yZWFkX2J5dGVzKCkpLmhleGRpZ2VzdCgpIGlmIHBhdGguaXNfZmlsZSgpIGVsc2UgJycKCmRlZiBzeXN0ZW1jdGwoKmFyZ3MsY2hlY2s9VHJ1ZSk6CiAgICByZXR1cm4gc3VicHJvY2Vzcy5ydW4oWydzdWRvJywnLW4nLCdzeXN0ZW1jdGwnLCphcmdzXSxjYXB0dXJlX291dHB1dD1UcnVlLHRleHQ9VHJ1ZSxjaGVjaz1jaGVjaykKCmRlZiBhdG9taWMocGF0aCxyYXcpOgogICAgdGVtcG9yYXJ5PXBhdGgud2l0aF9uYW1lKHBhdGgubmFtZSsnLnRtcC5yNG1haW50JykKICAgIHRyeToKICAgICAgICB0ZW1wb3Jhcnkud3JpdGVfYnl0ZXMocmF3KQogICAgICAgIGlmIHBhdGguZXhpc3RzKCk6c2h1dGlsLmNvcHltb2RlKHBhdGgsdGVtcG9yYXJ5KQogICAgICAgIG9zLnJlcGxhY2UodGVtcG9yYXJ5LHBhdGgpCiAgICBmaW5hbGx5OgogICAgICAgIGlmIHRlbXBvcmFyeS5leGlzdHMoKTp0ZW1wb3JhcnkudW5saW5rKCkKCmRlZiBjYXB0dXJlKCk6CiAgICByZXN1bHQ9e30KICAgIHdpdGggY2xvc2luZyhzcWxpdGUzLmNvbm5lY3QoZGF0YWJhc2UucmVzb2x2ZSgpLmFzX3VyaSgpKyc/bW9kZT1ybycsdXJpPVRydWUpKSBhcyBjb246CiAgICAgICAgY29uLnJvd19mYWN0b3J5PXNxbGl0ZTMuUm93CiAgICAgICAgaWYgY29uLmV4ZWN1dGUoJ1BSQUdNQSBxdWlja19jaGVjaycpLmZldGNob25lKClbMF0hPSdvayc6cmFpc2UgUnVudGltZUVycm9yKCdEQVRBQkFTRV9JTlRFR1JJVFlfRkFJTEVEJykKICAgICAgICBmb3IgdGFibGUsa2V5IGluIFsoJ3I0X3N0cmF0ZWd5X2luc3RhbmNlcycsJ2luc3RhbmNlX2lkJyksKCdyNF9hY3RpdmVfbGVncycsJ2xlZ19pZCcpLCgncjRfbGVnX3RyYWlsaW5nJywnbGVnX2lkJyldOgogICAgICAgICAgICByZXN1bHRbdGFibGVdPVtkaWN0KHJvdykgZm9yIHJvdyBpbiBjb24uZXhlY3V0ZSgnU0VMRUNUICogRlJPTSAnK3RhYmxlKycgT1JERVIgQlkgJytrZXkpXQogICAgcmV0dXJuIHJlc3VsdAoKZGVmIHByZXNlcnZlKGJlZm9yZSxhZnRlcik6CiAgICBmb3IgdGFibGUscm93cyBpbiBiZWZvcmUuaXRlbXMoKToKICAgICAgICBjdXJyZW50PWFmdGVyW3RhYmxlXQogICAgICAgIGlmIGxlbihyb3dzKSE9bGVuKGN1cnJlbnQpOnJhaXNlIFJ1bnRpbWVFcnJvcignTUlHUkFUSU9OX1JPV19DT1VOVF9DSEFOR0VEOicrdGFibGUpCiAgICAgICAgZm9yIGxlZnQscmlnaHQgaW4gemlwKHJvd3MsY3VycmVudCk6CiAgICAgICAgICAgIGlmIGFueShyaWdodC5nZXQoa2V5KSE9dmFsdWUgZm9yIGtleSx2YWx1ZSBpbiBsZWZ0Lml0ZW1zKCkpOnJhaXNlIFJ1bnRpbWVFcnJvcignTUlHUkFUSU9OX0VYSVNUSU5HX1JPV19DSEFOR0VEOicrdGFibGUpCgpkZWYgY29udHJvbGxlcl9maWVsZHMoY2ZnKToKICAgIGlmIGxldmVyYWdlX3JlbGVhc2U6CiAgICAgICAgcmV0dXJuIHsKICAgICAgICAgICAgJ21pbic6Y2ZnWydsZXZlcmFnZV9wb2xpY3knXVsnbWluJ10sCiAgICAgICAgICAgICdhYnNvbHV0ZV9tYXgnOmNmZ1snbGV2ZXJhZ2VfcG9saWN5J11bJ2Fic29sdXRlX21heCddLAogICAgICAgICAgICAnb3duZXJfbGV2ZXJhZ2VfaW5jcmVtZW50X3gnOmNmZ1sncjRfcnVudGltZSddWydjb21tb25fbGV2ZXJhZ2VfcG9saWN5J11bJ293bmVyX2xldmVyYWdlX2luY3JlbWVudF94J10sCiAgICAgICAgICAgICdlZmZlY3RpdmVfbGl2ZV9leGNoYW5nZV9sZXZlcmFnZV94JzpjZmdbJ3I0X3J1bnRpbWUnXVsnY29tbW9uX2xldmVyYWdlX3BvbGljeSddWydlZmZlY3RpdmVfbGl2ZV9leGNoYW5nZV9sZXZlcmFnZV94J10sCiAgICAgICAgfQogICAgY29udHJvbGxlcj1jZmdbJ3I0X3J1bnRpbWUnXVsndGllcl9jb250cm9sbGVyJ10KICAgIHJldHVybiB7a2V5OmNvcHkuZGVlcGNvcHkoY29udHJvbGxlcltrZXldKSBmb3Iga2V5IGluICgncGVyZm9ybWFuY2VfYXV0bycsJ2Z1bmRpbmdfYXV0bycsJ3RpZXJfZW50cnlfYmVuZWZpdHMnKSBpZiBrZXkgaW4gY29udHJvbGxlcn0KCmRlZiBzZXRfZmllbGRzKGNmZyxmaWVsZHMpOgogICAgaWYgbGV2ZXJhZ2VfcmVsZWFzZToKICAgICAgICBjZmdbJ2xldmVyYWdlX3BvbGljeSddWydhYnNvbHV0ZV9tYXgnXT1maWVsZHNbJ2Fic29sdXRlX21heCddCiAgICAgICAgY2ZnWydsZXZlcmFnZV9wb2xpY3knXVsnbWluJ109ZmllbGRzWydtaW4nXQogICAgICAgIGNvbW1vbj1jZmdbJ3I0X3J1bnRpbWUnXVsnY29tbW9uX2xldmVyYWdlX3BvbGljeSddCiAgICAgICAgZm9yIGtleSBpbiAoJ293bmVyX2xldmVyYWdlX2luY3JlbWVudF94JywnZWZmZWN0aXZlX2xpdmVfZXhjaGFuZ2VfbGV2ZXJhZ2VfeCcpOmNvbW1vbltrZXldPWZpZWxkc1trZXldCiAgICAgICAgcmV0dXJuCiAgICBjb250cm9sbGVyPWNmZ1sncjRfcnVudGltZSddWyd0aWVyX2NvbnRyb2xsZXInXQogICAgZm9yIGtleSBpbiAoJ3BlcmZvcm1hbmNlX2F1dG8nLCdmdW5kaW5nX2F1dG8nLCd0aWVyX2VudHJ5X2JlbmVmaXRzJyk6CiAgICAgICAgaWYga2V5IGluIGZpZWxkczpjb250cm9sbGVyW2tleV09Y29weS5kZWVwY29weShmaWVsZHNba2V5XSkKICAgICAgICBlbHNlOmNvbnRyb2xsZXIucG9wKGtleSxOb25lKQoKZm9yIG5hbWUsd2FudGVkIGluIGZpbGVzLml0ZW1zKCk6CiAgICBpZiBuYW1lIG5vdCBpbiBvbGQgb3IgJy8nIGluIG5hbWUgb3IgJ1xcJyBpbiBuYW1lOnJhaXNlIFJ1bnRpbWVFcnJvcignVU5FWFBFQ1RFRF9SRUxFQVNFX0ZJTEUnKQogICAgaWYgc2hhKHN0YWdlL25hbWUpIT13YW50ZWQ6cmFpc2UgUnVudGltZUVycm9yKCdTVEFHRURfU09VUkNFX0hBU0hfTUlTTUFUQ0g6JytuYW1lKQogICAgY29tcGlsZSgoc3RhZ2UvbmFtZSkucmVhZF90ZXh0KGVuY29kaW5nPSd1dGYtOC1zaWcnKSxuYW1lLCdleGVjJykKICAgIGFjdHVhbD1zaGEocm9vdC9uYW1lKQogICAgaWYgYWN0dWFsIG5vdCBpbiAob2xkW25hbWVdLHdhbnRlZCkgb3IgYWN0dWFsIT1pbml0aWFsW25hbWVdOnJhaXNlIFJ1bnRpbWVFcnJvcignQVdTX1NPVVJDRV9DSEFOR0VEOicrbmFtZSkKZm9yIG5hbWUsd2FudGVkIGluIHBsYW5bJ2Zyb3plbiddLml0ZW1zKCk6CiAgICBpZiBzaGEocm9vdC9uYW1lKSE9d2FudGVkOnJhaXNlIFJ1bnRpbWVFcnJvcignQVdTX0ZST1pFTl9DT1JFX0NIQU5HRUQ6JytuYW1lKQppZiBzeXN0ZW1jdGwoJ2lzLWFjdGl2ZScsJy0tcXVpZXQnLHNlcnZpY2UsY2hlY2s9RmFsc2UpLnJldHVybmNvZGU6cmFpc2UgUnVudGltZUVycm9yKCdSNF9TRVJWSUNFX05PVF9BQ1RJVkUnKQpleGVjc3RhcnQ9c3lzdGVtY3RsKCdzaG93JyxzZXJ2aWNlLCctcCcsJ0V4ZWNTdGFydCcsJy0tdmFsdWUnKS5zdGRvdXQKaWYgc3RyKHJvb3QvJ3I0X3J1bnRpbWVfc2VydmljZS5weScpIG5vdCBpbiBleGVjc3RhcnQ6cmFpc2UgUnVudGltZUVycm9yKCdVTkVYUEVDVEVEX1I0X1NFUlZJQ0VfRVhFQ1VUQUJMRScpCgppZiBub3QgbGV2ZXJhZ2VfcmVsZWFzZToKICAgICMg7Iuk7KCcIOq4sOyhtCDslrTrjJHthLAv7JWI7KCEIO2YuOy2nOydhCDsgqzsmqntlZwg7KGw7ZqM66eMIOyImO2Wie2VnOuLpC4g7KO866y4IO2VqOyImOuKlCDtmLjstpztlZjsp4Ag7JWK64qU64ukLgogICAgbG9nZ2luZy5iYXNpY0NvbmZpZyhmaWxlbmFtZT1zdHIobG9nKSxlbmNvZGluZz0ndXRmLTgnLGxldmVsPWxvZ2dpbmcuSU5GTyxmb3JtYXQ9JyUoYXNjdGltZSlzIFslKGxldmVsbmFtZSlzXSAlKG1lc3NhZ2UpcycpCiAgICBmcm9tIHI0X2Z1bmRpbmdfZmVlZCBpbXBvcnQgZmV0Y2hfZnVuZGluZ19pbmNvbWUKICAgIGZyb20gcjRfZXhjaGFuZ2VfYWRhcHRlciBpbXBvcnQgQmluYW5jZVI0RXhjaGFuZ2UKICAgIHRyeToKICAgICAgICBleGNoYW5nZT1CaW5hbmNlUjRFeGNoYW5nZSgpCiAgICAgICAgZW5kPWludCh0aW1lLnRpbWUoKSoxMDAwKQogICAgICAgIGZ1bmRpbmc9ZmV0Y2hfZnVuZGluZ19pbmNvbWUoZXhjaGFuZ2UsZW5kLTI0KjYwKjYwKjEwMDAsZW5kKQogICAgICAgIHByaW50KCdSRUFMX0ZVTkRJTkdfSElTVE9SWV9SRUFEX09LIHJvd3M9JytzdHIobGVuKGZ1bmRpbmcpKSxmbHVzaD1UcnVlKQogICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBleGM6CiAgICAgICAgcmFpc2UgUnVudGltZUVycm9yKCdGVU5ESU5HX1JFQURfUFJFRkxJR0hUX0ZBSUxFRDonK3R5cGUoZXhjKS5fX25hbWVfXykgZnJvbSBOb25lCgp3YXNfcGlkPXN5c3RlbWN0bCgnc2hvdycsc2VydmljZSwnLXAnLCdNYWluUElEJywnLS12YWx1ZScpLnN0ZG91dC5zdHJpcCgpCnByZXZpb3VzX2ZpZWxkcz1Ob25lO2NoYW5nZWQ9RmFsc2U7c3RvcHBlZD1GYWxzZQp0cnk6CiAgICBzeXN0ZW1jdGwoJ3N0b3AnLHNlcnZpY2UpO3N0b3BwZWQ9VHJ1ZQogICAgaWYgc3lzdGVtY3RsKCdpcy1hY3RpdmUnLCctLXF1aWV0JyxzZXJ2aWNlLGNoZWNrPUZhbHNlKS5yZXR1cm5jb2RlPT0wOnJhaXNlIFJ1bnRpbWVFcnJvcignUjRfU0VSVklDRV9TVE9QX0ZBSUxFRCcpCiAgICAjIOykkeyngCDtm4Qg7LWc7IugIOuTseq4iS/slYjsoIQg7J6g6riIL+yghOuetSDshKTsoJXsnYQg7J2964qU64ukLiDroZzsu6wg7ISk7KCVIOyghOyytOulvCDrja7slrTsk7Dsp4Ag7JWK64qU64ukLgogICAgY2ZnPWpzb24ubG9hZHMoY29uZmlnLnJlYWRfdGV4dChlbmNvZGluZz0ndXRmLTgtc2lnJykpCiAgICBjb250cm9sbGVyPWNmZ1sncjRfcnVudGltZSddWyd0aWVyX2NvbnRyb2xsZXInXQogICAgaWYgY29udHJvbGxlci5nZXQoJ2VuYWJsZWQnKSBpcyBub3QgVHJ1ZTpyYWlzZSBSdW50aW1lRXJyb3IoJ0VYSVNUSU5HX1RJRVJfQ09OVFJPTExFUl9ESVNBQkxFRCcpCiAgICBwcmV2aW91c19maWVsZHM9Y29udHJvbGxlcl9maWVsZHMoY2ZnKQogICAgdGFyZ2V0PWNvcHkuZGVlcGNvcHkoY2ZnKQogICAgaWYgbGV2ZXJhZ2VfcmVsZWFzZToKICAgICAgICBjb21tb249dGFyZ2V0WydyNF9ydW50aW1lJ11bJ2NvbW1vbl9sZXZlcmFnZV9wb2xpY3knXQogICAgICAgIGlmIGNvbW1vbi5nZXQoJ3RhcmdldCcpIT0xMCBvciBjb21tb24uZ2V0KCdvcGVuX2Jhc2tldF9jaGFuZ2UnKSE9J0RFRkVSX1VOVElMX0ZMQVQnOnJhaXNlIFJ1bnRpbWVFcnJvcignVU5FWFBFQ1RFRF9MRVZFUkFHRV9CQVNFTElORV9PUl9PUEVOX0JBU0tFVF9QT0xJQ1knKQogICAgICAgIGlmIHtrOnYgZm9yIGssdiBpbiBwcmV2aW91c19maWVsZHMuaXRlbXMoKSBpZiBrIT0nbWluJ30gbm90IGluICgKICAgICAgICAgICAgeydhYnNvbHV0ZV9tYXgnOjI1LCdvd25lcl9sZXZlcmFnZV9pbmNyZW1lbnRfeCc6MTUsJ2VmZmVjdGl2ZV9saXZlX2V4Y2hhbmdlX2xldmVyYWdlX3gnOjI1fSwKICAgICAgICAgICAgeydhYnNvbHV0ZV9tYXgnOjMwLCdvd25lcl9sZXZlcmFnZV9pbmNyZW1lbnRfeCc6MjAsJ2VmZmVjdGl2ZV9saXZlX2V4Y2hhbmdlX2xldmVyYWdlX3gnOjMwfSwKICAgICAgICApOnJhaXNlIFJ1bnRpbWVFcnJvcignTElWRV9MRVZFUkFHRV9QT0xJQ1lfQ0hBTkdFRCcpCiAgICAgICAgc2V0X2ZpZWxkcyh0YXJnZXQseydtaW4nOjIwLCdhYnNvbHV0ZV9tYXgnOjMwLCdvd25lcl9sZXZlcmFnZV9pbmNyZW1lbnRfeCc6MjAsJ2VmZmVjdGl2ZV9saXZlX2V4Y2hhbmdlX2xldmVyYWdlX3gnOjMwfSkKICAgIGVsc2U6CiAgICAgICAgdGM9dGFyZ2V0WydyNF9ydW50aW1lJ11bJ3RpZXJfY29udHJvbGxlciddCiAgICAgICAgcGVyZm9ybWFuY2U9dGMuc2V0ZGVmYXVsdCgncGVyZm9ybWFuY2VfYXV0bycse30pCiAgICAgICAgcGVyZm9ybWFuY2UudXBkYXRlKGVuYWJsZWQ9VHJ1ZSxhcHBseV9lbmFibGVkPVRydWUpCiAgICAgICAgcGVyZm9ybWFuY2Uuc2V0ZGVmYXVsdCgnZXZhbHVhdGlvbl9pbnRlcnZhbF9zZWNvbmRzJywzMDApCiAgICAgICAgcGVyZm9ybWFuY2Uuc2V0ZGVmYXVsdCgndHJhbnNpdGlvbl9jb29sZG93bl9zZWNvbmRzJywzNjAwKQogICAgICAgIGZ1bmRpbmdfY29uZmlnPXRjLnNldGRlZmF1bHQoJ2Z1bmRpbmdfYXV0bycse30pCiAgICAgICAgZnVuZGluZ19jb25maWdbJ2VuYWJsZWQnXT1UcnVlO2Z1bmRpbmdfY29uZmlnLnNldGRlZmF1bHQoJ3BvbGxfaW50ZXJ2YWxfc2Vjb25kcycsMzAwKQogICAgICAgIGJlbmVmaXRzPXRjLnNldGRlZmF1bHQoJ3RpZXJfZW50cnlfYmVuZWZpdHMnLHt9KQogICAgICAgIGJlbmVmaXRzWydlbmFibGVkJ109VHJ1ZQogICAgICAgIGJlbmVmaXRzLnNldGRlZmF1bHQoJ2NvbmN1cnJlbnRfbGVnX2xpbWl0cycseydDQU5BUlknOjMsJ1NNQUxMJzo0LCdOT1JNQUwnOjV9KQogICAgICAgIGJlbmVmaXRzLnNldGRlZmF1bHQoJ3ByaW9yaXR5X29yZGVyJyxbJ05PUk1BTCcsJ1NNQUxMJywnQ0FOQVJZJ10pCiAgICAgICAgZm9yIGtleSBpbiAoJ2V2YWx1YXRpb25faW50ZXJ2YWxfc2Vjb25kcycsJ3RyYW5zaXRpb25fY29vbGRvd25fc2Vjb25kcycpOgogICAgICAgICAgICBpZiB0eXBlKHBlcmZvcm1hbmNlW2tleV0pIGlzIG5vdCBpbnQgb3IgcGVyZm9ybWFuY2Vba2V5XTw9MDpyYWlzZSBSdW50aW1lRXJyb3IoJ0lOVkFMSURfUEVSRk9STUFOQ0VfVElNSU5HJykKICAgICAgICBpZiB0eXBlKGZ1bmRpbmdfY29uZmlnWydwb2xsX2ludGVydmFsX3NlY29uZHMnXSkgaXMgbm90IGludCBvciBmdW5kaW5nX2NvbmZpZ1sncG9sbF9pbnRlcnZhbF9zZWNvbmRzJ108PTA6cmFpc2UgUnVudGltZUVycm9yKCdJTlZBTElEX0ZVTkRJTkdfSU5URVJWQUwnKQogICAgICAgIGlmIGJlbmVmaXRzWydjb25jdXJyZW50X2xlZ19saW1pdHMnXSE9eydDQU5BUlknOjMsJ1NNQUxMJzo0LCdOT1JNQUwnOjV9IG9yIGJlbmVmaXRzWydwcmlvcml0eV9vcmRlciddIT1bJ05PUk1BTCcsJ1NNQUxMJywnQ0FOQVJZJ106cmFpc2UgUnVudGltZUVycm9yKCdVTkVYUEVDVEVEX1RJRVJfRU5UUllfQkVORUZJVFMnKQogICAgY2hlY2s9Y29weS5kZWVwY29weSh0YXJnZXQpO3NldF9maWVsZHMoY2hlY2sscHJldmlvdXNfZmllbGRzKQogICAgaWYgY2hlY2shPWNmZzpyYWlzZSBSdW50aW1lRXJyb3IoJ1VOUkVMQVRFRF9DT05GSUdfQ0hBTkdFJykKICAgIGJlZm9yZT1jYXB0dXJlKCkKICAgIGJhY2t1cC5ta2RpcihleGlzdF9vaz1UcnVlKQogICAgZm9yIG5hbWUgaW4gZmlsZXM6CiAgICAgICAgaWYgKHJvb3QvbmFtZSkuZXhpc3RzKCk6c2h1dGlsLmNvcHkyKHJvb3QvbmFtZSxiYWNrdXAvbmFtZSkKICAgIHNodXRpbC5jb3B5Mihjb25maWcsYmFja3VwLydzdHJhdGVneV9jb25maWcuanNvbicpCiAgICAoYmFja3VwLyduZXdfZmlsZXMuanNvbicpLndyaXRlX3RleHQoanNvbi5kdW1wcyhbbmFtZSBmb3IgbmFtZSBpbiBmaWxlcyBpZiBub3QgKHJvb3QvbmFtZSkuZXhpc3RzKCldKSkKICAgIHdpdGggY2xvc2luZyhzcWxpdGUzLmNvbm5lY3QoZGF0YWJhc2UucmVzb2x2ZSgpLmFzX3VyaSgpKyc/bW9kZT1ybycsdXJpPVRydWUpKSBhcyBzb3VyY2U6CiAgICAgICAgd2l0aCBjbG9zaW5nKHNxbGl0ZTMuY29ubmVjdChiYWNrdXAvJ2hpc3RvcnkuZGInKSkgYXMgZGVzdDpzb3VyY2UuYmFja3VwKGRlc3QpCiAgICB3aXRoIHppcGZpbGUuWmlwRmlsZShzdGFnZS8nYmFja3VwLnppcCcsJ3cnLHppcGZpbGUuWklQX0RFRkxBVEVEKSBhcyBhcmNoaXZlOgogICAgICAgIGZvciBwYXRoIGluIHNvcnRlZChiYWNrdXAuaXRlcmRpcigpKTphcmNoaXZlLndyaXRlKHBhdGgscGF0aC5uYW1lKQogICAgYXRvbWljKHBlcnNpc3RlbnQsKHN0YWdlLydiYWNrdXAuemlwJykucmVhZF9ieXRlcygpKQogICAgY2hhbmdlZD1UcnVlCiAgICBmb3IgbmFtZSBpbiBmaWxlczphdG9taWMocm9vdC9uYW1lLChzdGFnZS9uYW1lKS5yZWFkX2J5dGVzKCkpCiAgICBpZiBub3QgbGV2ZXJhZ2VfcmVsZWFzZToKICAgICAgICBmcm9tIHN0cmF0ZWd5X2xlZ19sZWRnZXIgaW1wb3J0IFN0cmF0ZWd5TGVnTGVkZ2VyCiAgICAgICAgU3RyYXRlZ3lMZWdMZWRnZXIoZGF0YWJhc2UpCiAgICBwcmVzZXJ2ZShiZWZvcmUsY2FwdHVyZSgpKQogICAgYXRvbWljKGNvbmZpZywoanNvbi5kdW1wcyh0YXJnZXQsZW5zdXJlX2FzY2lpPUZhbHNlLGluZGVudD0yKSsnXG4nKS5lbmNvZGUoKSkKICAgIGltcG9ydCBzdHJhdGVneV9ydW50aW1lCiAgICBzdHJhdGVneV9ydW50aW1lLnJlbG9hZCgpCiAgICB2YWxpZGF0aW9uPXN0cmF0ZWd5X3J1bnRpbWUudmFsaWRhdGUoKQogICAgaWYgbm90IHZhbGlkYXRpb24uZ2V0KCdvaycpOnJhaXNlIFJ1bnRpbWVFcnJvcignU1RSQVRFR1lfQ09ORklHX1ZBTElEQVRJT05fRkFJTEVEJykKICAgICMg7KCV7KCBIOyEpOyglSDqsoDspp3qs7wgREIg67O07KG0IO2ZleyduOydhCDrqqjrkZAg7Ya16rO87ZWcIOuSpCDquLDsobQg7ISc67mE7Iqk66W8IOyerOyLnOyeke2VnOuLpC4KICAgIG9mZnNldD1sb2cuc3RhdCgpLnN0X3NpemUgaWYgbG9nLmV4aXN0cygpIGVsc2UgMAogICAgaW5vZGU9bG9nLnN0YXQoKS5zdF9pbm8gaWYgbG9nLmV4aXN0cygpIGVsc2UgTm9uZQogICAgc3lzdGVtY3RsKCdzdGFydCcsc2VydmljZSkKICAgIGRlYWRsaW5lPXRpbWUubW9ub3RvbmljKCkrNjA7cmVhZHk9RmFsc2U7cmVjb25jaWxlZF9hdD1Ob25lCiAgICB3aGlsZSB0aW1lLm1vbm90b25pYygpPGRlYWRsaW5lOgogICAgICAgIGlmIHN5c3RlbWN0bCgnaXMtYWN0aXZlJywnLS1xdWlldCcsc2VydmljZSxjaGVjaz1GYWxzZSkucmV0dXJuY29kZTpyYWlzZSBSdW50aW1lRXJyb3IoJ1I0X1NFUlZJQ0VfU1RBUlRfRkFJTEVEJykKICAgICAgICB3aXRoIGxvZy5vcGVuKCdyYicpIGFzIHN0cmVhbToKICAgICAgICAgICAgaWYgbG9nLnN0YXQoKS5zdF9pbm89PWlub2RlIGFuZCBsb2cuc3RhdCgpLnN0X3NpemU+PW9mZnNldDpzdHJlYW0uc2VlayhvZmZzZXQpCiAgICAgICAgICAgIHRleHQ9c3RyZWFtLnJlYWQoKS5kZWNvZGUoJ3V0Zi04JyxlcnJvcnM9J3JlcGxhY2UnKQogICAgICAgIGlmICdzdGFydHVwIHJlY29uY2lsZSBQQVNTJyBpbiB0ZXh0OgogICAgICAgICAgICBpZiByZWNvbmNpbGVkX2F0IGlzIE5vbmU6cmVjb25jaWxlZF9hdD10aW1lLm1vbm90b25pYygpCiAgICAgICAgICAgIGlmIHRpbWUubW9ub3RvbmljKCktcmVjb25jaWxlZF9hdD49MTA6cmVhZHk9VHJ1ZTticmVhawogICAgICAgIGlmICdyZWFzb249U1RBUlRVUF9SRUNPTkNJTEVfTk9UX1BBU1MnIGluIHRleHQ6cmFpc2UgUnVudGltZUVycm9yKCdSNF9TVEFSVFVQX1JFQ09OQ0lMRV9CTE9DS0VEJykKICAgICAgICB0aW1lLnNsZWVwKDEpCiAgICBpZiBub3QgcmVhZHk6cmFpc2UgUnVudGltZUVycm9yKCdSNF9TVEFSVFVQX1JFQ09OQ0lMRV9OT1RfQ09ORklSTUVEJykKICAgIG5ld19waWQ9c3lzdGVtY3RsKCdzaG93JyxzZXJ2aWNlLCctcCcsJ01haW5QSUQnLCctLXZhbHVlJykuc3Rkb3V0LnN0cmlwKCkKICAgIGlmIG5ld19waWQgaW4gKCcnLCcwJyx3YXNfcGlkKTpyYWlzZSBSdW50aW1lRXJyb3IoJ1I0X05FV19QUk9DRVNTX05PVF9DT05GSVJNRUQnKQogICAgZm9yIG5hbWUsd2FudGVkIGluIGZpbGVzLml0ZW1zKCk6CiAgICAgICAgaWYgc2hhKHJvb3QvbmFtZSkhPXdhbnRlZDpyYWlzZSBSdW50aW1lRXJyb3IoJ0RFUExPWUVEX0hBU0hfTUlTTUFUQ0g6JytuYW1lKQogICAgY3VycmVudD1qc29uLmxvYWRzKGNvbmZpZy5yZWFkX3RleHQoZW5jb2Rpbmc9J3V0Zi04LXNpZycpKQogICAgaWYgbGV2ZXJhZ2VfcmVsZWFzZToKICAgICAgICBjb21tb249Y3VycmVudFsncjRfcnVudGltZSddWydjb21tb25fbGV2ZXJhZ2VfcG9saWN5J10KICAgICAgICBpZiBjb250cm9sbGVyX2ZpZWxkcyhjdXJyZW50KSE9eydtaW4nOjIwLCdhYnNvbHV0ZV9tYXgnOjMwLCdvd25lcl9sZXZlcmFnZV9pbmNyZW1lbnRfeCc6MjAsJ2VmZmVjdGl2ZV9saXZlX2V4Y2hhbmdlX2xldmVyYWdlX3gnOjMwfTpyYWlzZSBSdW50aW1lRXJyb3IoJ0xFVkVSQUdFMzBfTk9UX0VOQUJMRUQnKQogICAgICAgIGlmIGN1cnJlbnRbJ3I0X3J1bnRpbWUnXVsndGllcl9jb250cm9sbGVyJ10hPWNmZ1sncjRfcnVudGltZSddWyd0aWVyX2NvbnRyb2xsZXInXTpyYWlzZSBSdW50aW1lRXJyb3IoJ1RJRVJfQVVUT01BVElPTl9TRVRUSU5HU19DSEFOR0VEJykKICAgICAgICBwcmludChqc29uLmR1bXBzKHsnbWluaW11bV9sZXZlcmFnZV94JzoyMCwnZGVwbG95bWVudCc6J0FQUExJRUQnLCdwaWQnOmludChuZXdfcGlkKSwnc3RhcnR1cF9yZWNvbmNpbGUnOidQQVNTJywnY29tbW9uX2xldmVyYWdlX3gnOjMwLCdhYnNvbHV0ZV9tYXgnOjMwLCdzbWFsbF9lbnRyeV9pbmNsdWRlZCc6VHJ1ZSwnb3Blbl9iYXNrZXRfY2hhbmdlJzpjb21tb25bJ29wZW5fYmFza2V0X2NoYW5nZSddLCd0aWVyX2F1dG9tYXRpb25fcHJlc2VydmVkJzpUcnVlLCdleGlzdGluZ19yb3dzX3ByZXNlcnZlZF9iZWZvcmVfcmVzdGFydCc6VHJ1ZSwnZGF0YWJhc2VfcmVzdG9yZWQnOkZhbHNlLCdiYWNrdXBfY291bnQnOjF9KSxmbHVzaD1UcnVlKQogICAgZWxzZToKICAgICAgICB0Yz1jdXJyZW50WydyNF9ydW50aW1lJ11bJ3RpZXJfY29udHJvbGxlciddCiAgICAgICAgaWYgbm90ICh0Y1sncGVyZm9ybWFuY2VfYXV0byddWydlbmFibGVkJ10gaXMgVHJ1ZSBhbmQgdGNbJ3BlcmZvcm1hbmNlX2F1dG8nXVsnYXBwbHlfZW5hYmxlZCddIGlzIFRydWUgYW5kIHRjWydmdW5kaW5nX2F1dG8nXVsnZW5hYmxlZCddIGlzIFRydWUgYW5kIHRjWyd0aWVyX2VudHJ5X2JlbmVmaXRzJ11bJ2VuYWJsZWQnXSBpcyBUcnVlKTpyYWlzZSBSdW50aW1lRXJyb3IoJ1RJRVJfQVVUT01BVElPTl9OT1RfRU5BQkxFRCcpCiAgICAgICAgcHJpbnQoanNvbi5kdW1wcyh7J2RlcGxveW1lbnQnOidBUFBMSUVEJywncGlkJzppbnQobmV3X3BpZCksJ3N0YXJ0dXBfcmVjb25jaWxlJzonUEFTUycsJ21pZ3JhdGlvbl9leGlzdGluZ19yb3dzX3ByZXNlcnZlZCc6VHJ1ZSwnYWN0aXZlX2xlZ3NfYmVmb3JlX3Jlc3RhcnQnOnN1bShyb3cuZ2V0KCdzdGF0dXMnKT09J0FDVElWRScgZm9yIHJvdyBpbiBiZWZvcmVbJ3I0X2FjdGl2ZV9sZWdzJ10pLCdwZXJmb3JtYW5jZV9hdXRvJzpUcnVlLCdmdW5kaW5nX2F1dG8nOlRydWUsJ3RpZXJfZW50cnlfYmVuZWZpdHMnOlRydWUsJ2RhdGFiYXNlX3Jlc3RvcmVkJzpGYWxzZSwnYmFja3VwX2NvdW50JzoxfSksZmx1c2g9VHJ1ZSkKCmV4Y2VwdCBCYXNlRXhjZXB0aW9uIGFzIGV4YzoKICAgIHJvbGxiYWNrX2Vycm9ycz1bXQogICAgaWYgc3RvcHBlZDoKICAgICAgICB0cnk6c3lzdGVtY3RsKCdzdG9wJyxzZXJ2aWNlKQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb246cm9sbGJhY2tfZXJyb3JzLmFwcGVuZCgnU0VSVklDRV9TVE9QX0ZBSUxFRCcpCiAgICAgICAgaWYgY2hhbmdlZDoKICAgICAgICAgICAgZm9yIG5hbWUgaW4gZmlsZXM6CiAgICAgICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICAgICAgaWYgKGJhY2t1cC9uYW1lKS5leGlzdHMoKTphdG9taWMocm9vdC9uYW1lLChiYWNrdXAvbmFtZSkucmVhZF9ieXRlcygpKQogICAgICAgICAgICAgICAgICAgIGVsaWYgKHJvb3QvbmFtZSkuZXhpc3RzKCk6KHJvb3QvbmFtZSkudW5saW5rKCkKICAgICAgICAgICAgICAgIGV4Y2VwdCBFeGNlcHRpb246cm9sbGJhY2tfZXJyb3JzLmFwcGVuZCgnU09VUkNFX1JFU1RPUkVfRkFJTEVEOicrbmFtZSkKICAgICAgICAgICAgdHJ5OgogICAgICAgICAgICAgICAgY3VycmVudD1qc29uLmxvYWRzKGNvbmZpZy5yZWFkX3RleHQoZW5jb2Rpbmc9J3V0Zi04LXNpZycpKQogICAgICAgICAgICAgICAgc2V0X2ZpZWxkcyhjdXJyZW50LHByZXZpb3VzX2ZpZWxkcykKICAgICAgICAgICAgICAgIGF0b21pYyhjb25maWcsKGpzb24uZHVtcHMoY3VycmVudCxlbnN1cmVfYXNjaWk9RmFsc2UsaW5kZW50PTIpKydcbicpLmVuY29kZSgpKQogICAgICAgICAgICBleGNlcHQgRXhjZXB0aW9uOnJvbGxiYWNrX2Vycm9ycy5hcHBlbmQoJ0FVVE9NQVRJT05fQ09ORklHX1JFU1RPUkVfRkFJTEVEJykKICAgICAgICB0cnk6c3lzdGVtY3RsKCdzdGFydCcsc2VydmljZSkKICAgICAgICBleGNlcHQgRXhjZXB0aW9uOnJvbGxiYWNrX2Vycm9ycy5hcHBlbmQoJ1BSRVZJT1VTX1NFUlZJQ0VfU1RBUlRfRkFJTEVEJykKICAgIHByaW50KGpzb24uZHVtcHMoeydkZXBsb3ltZW50JzonRkFJTEVEJywncmVhc29uJzpzdHIoZXhjKSBpZiBpc2luc3RhbmNlKGV4YywoUnVudGltZUVycm9yLE9TRXJyb3IpKSBlbHNlIHR5cGUoZXhjKS5fX25hbWVfXywncm9sbGJhY2tfZXJyb3JzJzpyb2xsYmFja19lcnJvcnMsJ2RhdGFiYXNlX3Jlc3RvcmVkJzpGYWxzZSwnYWRkaXRpdmVfc2NoZW1hX3JldGFpbmVkJzpjaGFuZ2VkIGFuZCBub3QgbGV2ZXJhZ2VfcmVsZWFzZSwnYmFja3VwJzpzdHIocGVyc2lzdGVudCl9KSxmbHVzaD1UcnVlKQogICAgcmFpc2UgU3lzdGVtRXhpdCgxKQo='))
        $program = $program.Replace('RELEASE_PLAN_B64', $planB64)
        $programB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($program))
        $remote = @"
set -euo pipefail
cd '$remoteStage'
tar -xzf payload.tar.gz
(
  flock -x 9
  printf '%s' '$programB64' | base64 -d | /home/ec2-user/bot/venv/bin/python -B -
) 9>'$LOCK'
"@
    }

    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($remote -replace "`r", "")))
    Write-Host "?? ?? ? ?? ??"
    & ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -i $KEY $SSH "echo '$b64' | base64 -d | bash"
    if ($LASTEXITCODE -ne 0) { throw "BASE_MAINTENANCE_REMOTE_FAIL" }

    Write-Host "BASE_CODE_CHANGED=$($changed.Count)"
    $changed | ForEach-Object { Write-Host "BASE_MAINTENANCE $_" }
    if ($TYPED_RELEASE) { Write-Host "SERVICE_RESTART=VERIFIED" } else { Write-Host "SERVICE_RESTART=0" }
}
finally {
    Remove-Item $stageLocal -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $archive -Force -ErrorAction SilentlyContinue
    $deployExitCode = $LASTEXITCODE
    & ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -i $KEY $SSH "rm -rf '$remoteStage'" 2>$null | Out-Null
    $global:LASTEXITCODE = $deployExitCode
}

