# 파일명: LOCAL_V3_TEST.ps1
param([bool]$RunOneResearchTrial=$true,[bool]$PullServerRuntime=$true)
$ErrorActionPreference="Stop"
$ROOT="C:\my\bottest"; $PY="$ROOT\.venv\Scripts\python.exe"; $REPORT="$ROOT\LOCAL_V3_TEST_REPORT.json"
if (-not (Test-Path "$ROOT\SERVER_PUSH_HOLD.flag")) { throw "SERVER_PUSH_HOLD.flag MISSING - LOCAL TEST REFUSED" }
if (-not (Test-Path "$ROOT\LOCAL_TEST_MODE.flag")) { New-Item "$ROOT\LOCAL_TEST_MODE.flag" -ItemType File -Force | Out-Null }
$env:BBOT_LOCAL_ROOT=$ROOT; $env:BOT_BASE_DIR=$ROOT; $env:RUNNER_DRY_RUN="1"
Write-Host "SERVER_PUSH_HOLD=ON"
Write-Host "REAL_ORDER_OFF=ON"

# PowerShell syntax check for sync/release scripts.
$psFiles=@("sync_from_server.ps1","sync_base_to_server.ps1","strategy_runtime_sync.ps1","LOCAL_V3_TEST.ps1","ENABLE_SERVER_SYNC_AFTER_LOCAL_PASS.ps1")
foreach($rel in $psFiles){
  $p=Join-Path $ROOT $rel; if(-not (Test-Path $p)){throw "MISSING $rel"}
  $tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$tokens,[ref]$errors)|Out-Null
  if($errors.Count -gt 0){ throw "POWERSHELL_PARSE_FAIL $rel : $($errors[0].Message)" }
}
Write-Host "POWERSHELL_PARSE=PASS"

# Pull current SERVER runtime read-only while LOCAL->SERVER writes remain held.
# sync_from_server.ps1 calls both push scripts first, but SERVER_PUSH_HOLD makes them no-op.
if($PullServerRuntime){
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$ROOT\sync_from_server.ps1"
  if($LASTEXITCODE -ne 0){throw "READ_ONLY_RUNTIME_SYNC_FAIL"}
  if(-not (Test-Path "$ROOT\SERVER_PUSH_HOLD.flag")){throw "SERVER_PUSH_HOLD_LOST_DURING_RUNTIME_PULL"}
  Write-Host "SERVER_RUNTIME_READ_ONLY_PULL=PASS"
}

# If current LIVE positions exist, every cached position must retain original owner facts.
$ownerState="$ROOT\data\server_runtime\state\position_cache.json"
$currentPositionOwner=@{checked=$false;open_positions=0;missing_owner=@()}
if(Test-Path $ownerState){
  $pos=Get-Content $ownerState -Raw -Encoding UTF8 | ConvertFrom-Json
  $currentPositionOwner.checked=$true
  foreach($prop in $pos.PSObject.Properties){
    $row=$prop.Value; $amt=0.0; try{$amt=[double]$row.positionAmt}catch{$amt=0.0}
    if([math]::Abs($amt) -le 0){continue}
    $currentPositionOwner.open_positions++
    if(-not ([string]$row.entry_tf).Trim() -or -not ([string]$row.entry_strategy).Trim()){ $currentPositionOwner.missing_owner += $prop.Name }
  }
  if($currentPositionOwner.missing_owner.Count -gt 0){throw "CURRENT_POSITION_OWNER_MISSING: $($currentPositionOwner.missing_owner -join ',')"}
  Write-Host "CURRENT_POSITION_OWNER_GUARD=PASS open=$($currentPositionOwner.open_positions)"
}else{
  Write-Host "CURRENT_POSITION_OWNER_GUARD=WARN_NO_RUNTIME_CACHE"
}

# Compile all Python without execution.
& $PY -m compileall -q $ROOT
if($LASTEXITCODE -ne 0){throw "PY_COMPILEALL_FAIL"}
Write-Host "PY_COMPILEALL=PASS"

# Pure V3 safety/contract verification.
$verifyOut=& $PY "$ROOT\verify_v3.py" 2>&1
if($LASTEXITCODE -ne 0){ $verifyOut|Write-Host; throw "VERIFY_V3_FAIL" }
$verifyText=($verifyOut -join "`n")
$verify=$verifyText | ConvertFrom-Json
if(-not $verify.ok){throw "VERIFY_V3_NOT_OK"}
Write-Host "VERIFY_V3=PASS"

# Roadmap Constitution 93 clauses + governance 12.
$roadmapOut=& $PY "$ROOT\verify_roadmap_constitution.py" 2>&1
if($LASTEXITCODE -ne 0){$roadmapOut|Write-Host;throw "ROADMAP_93_VERIFY_FAIL"}
$roadmap=($roadmapOut -join "`n")|ConvertFrom-Json
if(-not $roadmap.ok -or [int]$roadmap.chapter_checks_passed -ne 93){throw "ROADMAP_93_NOT_OK"}
Write-Host "ROADMAP_93=PASS 93/93"

# Broad V3 regression 147 checks.
$regOut=& $PY "$ROOT\verify_v3_regression.py" 2>&1
if($LASTEXITCODE -ne 0){$regOut|Write-Host;throw "V3_REGRESSION_147_FAIL"}
$reg=($regOut -join "`n")|ConvertFrom-Json
if(-not $reg.ok -or [int]$reg.passed -ne 147){throw "V3_REGRESSION_147_NOT_OK"}
Write-Host "V3_REGRESSION=PASS 147/147"

# Synthetic strategy contract matrix.
$famOut=& $PY "$ROOT\verify_strategy_family_matrix.py" 2>&1
if($LASTEXITCODE -ne 0){$famOut|Write-Host;throw "STRATEGY_FAMILY_90_FAIL"}
$fam=($famOut -join "`n")|ConvertFrom-Json
if(-not $fam.ok -or [int]$fam.passed -ne 90){throw "STRATEGY_FAMILY_90_NOT_OK"}
Write-Host "STRATEGY_FAMILY=PASS 90/90"

$grammarOut=& $PY "$ROOT\verify_safe_grammar_matrix.py" 2>&1
if($LASTEXITCODE -ne 0){$grammarOut|Write-Host;throw "SAFE_GRAMMAR_240_FAIL"}
$grammar=($grammarOut -join "`n")|ConvertFrom-Json
if(-not $grammar.ok -or [int]$grammar.passed -ne 240){throw "SAFE_GRAMMAR_240_NOT_OK"}
Write-Host "SAFE_GRAMMAR=PASS 240/240"

# Overfit / lookahead / bounded-search release gate.
$overfitOut=& $PY "$ROOT\verify_overfit_validation.py" 2>&1
if($LASTEXITCODE -ne 0){$overfitOut|Write-Host;throw "OVERFIT_VALIDATION_FAIL"}
$overfit=($overfitOut -join "`n")|ConvertFrom-Json
if(-not $overfit.ok){throw "OVERFIT_VALIDATION_NOT_OK"}
Write-Host "OVERFIT_VALIDATION=PASS $($overfit.passed)/$($overfit.total)"

# State-transition / responsibility / safety logic release gate.
$logicOut=& $PY "$ROOT\verify_logic_validation.py" 2>&1
if($LASTEXITCODE -ne 0){$logicOut|Write-Host;throw "LOGIC_VALIDATION_FAIL"}
$logic=($logicOut -join "`n")|ConvertFrom-Json
if(-not $logic.ok){throw "LOGIC_VALIDATION_NOT_OK"}
Write-Host "LOGIC_VALIDATION=PASS $($logic.passed)/$($logic.total)"

# Measure the current 12H MAIN with the same Exact engine before any new strategy trial.
# This is research-only and creates symbol-specific Expected Account Contribution evidence.
$baselineOut=& $PY "$ROOT\strategy_lab\v3_baseline_research.py" 12H 2>&1
if($LASTEXITCODE -ne 0){$baselineOut|Write-Host;throw "V3_12H_BASELINE_RESEARCH_FAIL"}
$baselineText=($baselineOut -join "`n")
try{$baseline=$baselineText|ConvertFrom-Json}catch{$baselineOut|Write-Host;throw "V3_12H_BASELINE_JSON_FAIL"}
if(([string]$baseline.status).ToUpper() -ne "MEASURED" -or [int]$baseline.symbol_evidence_written -le 0){$baselineOut|Write-Host;throw "V3_12H_BASELINE_EVIDENCE_INCOMPLETE"}
Write-Host "V3_12H_BASELINE_RESEARCH=PASS symbols=$($baseline.symbol_evidence_written)"

# Controller/read-only status.
$statusOut=& $PY "$ROOT\strategy_lab\research_controller.py" --mode selftest 2>&1
if($LASTEXITCODE -ne 0){$statusOut|Write-Host;throw "RESEARCH_SELFTEST_FAIL"}
Write-Host "RESEARCH_SELFTEST=PASS"

# One real LOCAL research trial validates Exact Cache / backtester path. It never performs exchange orders.
$research=$null
if($RunOneResearchTrial){
  $researchOut=& $PY "$ROOT\strategy_lab\research_controller.py" --mode once --enable-research 2>&1
  if($LASTEXITCODE -ne 0){$researchOut|Write-Host;throw "LOCAL_RESEARCH_TRIAL_FAIL"}
  $researchText=($researchOut -join "`n"); try{$research=$researchText|ConvertFrom-Json}catch{$research=@{raw=$researchText}}
  Write-Host "LOCAL_RESEARCH_ONE_TRIAL=PASS"
}

$report=@{
  schema="B_BOT_V3_LOCAL_TEST_REPORT_V1"; ok=$true; tested_at=(Get-Date).ToString("o");
  server_push_hold=$true; real_order_off=$true; verify=$verify; roadmap_93=$roadmap; regression_147=$reg; strategy_family_90=$fam; safe_grammar_240=$grammar; overfit_validation=$overfit; logic_validation=$logic; baseline=$baseline; research=$research; current_position_owner=$currentPositionOwner
}
$report|ConvertTo-Json -Depth 30|Set-Content $REPORT -Encoding UTF8
Write-Host "LOCAL_V3_TEST=PASS"
Write-Host "REPORT=$REPORT"
Write-Host "SERVER_SYNC_REMAINS_BLOCKED=YES"
