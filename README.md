# B-Bot 연구소

기존 V3 연구 파이프라인에 학습 전용 유전 탐색과 Optuna 후보 제안을 연결하는 준비본입니다.
실거래 수익성 통과 또는 운영 배포 완료를 뜻하지 않습니다.

## 후보와 실행 전략의 경계

1. 명시된 과거 학습 OHLCV에서 기존 `run_strategy.simulate`로 유한 후보를 탐색합니다.
2. 후보 목록·파라미터·소스 SHA-256을 동결합니다. 같은 run_id의 조건 변경과 소스 변경은 거부합니다.
3. 기존 스케줄러가 동결 후보를 기존 공식 연구 함수로 평가합니다.
4. 최종 검증·블라인드·섀도 및 기존 소액거래 자격 게이트를 통과한 버전만 기존 배포 경로의 대상이 됩니다.

생성 전략은 NumPy/Pandas 기반 독립 Python 파일입니다. 연구소나 Optuna를 서버에 설치해서 탐색을 실행하지 않습니다.
현재 구조 탐색 범위는 기존 CHANNEL_Z/TREND_Z와 확인 조건의 조합입니다. 임의의 새 지표나 Python 코드를 생성하는 기능은 포함하지 않습니다.

## 현재 검증 범위

- 실제 Optuna를 이용한 합성 데이터 탐색, 탐색 횟수 제한과 재현성
- 미래 캔들 거부, 후보 봉인과 소스 변경 감지
- 생성 전략과 공식 신호 배열 및 봉 접두 구간 신호 일치
- Linux 프로세스 간 배타 연구 잠금

EC2 아티팩트 수신과 운영 등록은 별도 검증이 필요합니다.
이 작업본에는 거래소 키·SSH 키·운영 DB·최신 연구 체크포인트·시세 데이터가 포함되지 않습니다.

## GitHub 연구 실행

`research-run.yml`은 기존 `research_controller.py`를 단일 워커로 실행합니다. 수동 실행은 1·4·8회, 매시간 예약 실행은 최대 8회이며 작업 제한은 55분입니다. 로컬의 진행 중인 연구 큐는 가져오지 않고 새 연구를 시작합니다.

비공개 `research-input-v1` 자료 브랜치의 고정 커밋을 읽어 조각별·전체 압축·내부 파일 SHA-256을 검증합니다. 시작 프레임은 장기 자료가 준비된 1H·6H·12H·1D·1W입니다. 30M은 적합한 장기 자료 연결 후 추가하며, 최근 캔들로 대체하지 않습니다. 이 범위는 연구소 설정에 명시하고 기본 후보·구조 후보·변이 후보가 같은 범위를 사용합니다.

매 작업은 전체 연구 상태를 비공개 `research-state` 브랜치에 저장하고 다음 작업이 복원합니다. 각 커밋에는 누적 연구 이력이 들어 있으며 이전 커밋도 보존합니다. 아티팩트 용량 한도에 의존하지 않습니다. 단일 압축이 90 MiB를 넘으면 저장을 중단하므로 자료가 커지기 전에 저장 방식을 확장해야 합니다. 이전 성공 작업은 있는데 체크포인트가 없으면 조용히 초기화하지 않고 중단합니다. 입력·헌법·정식 엔진 해시가 바뀌어도 복원을 중단합니다.

GitHub 연구 실행에는 AWS 또는 거래소 자격증명을 넣지 않습니다. 실서버 전략 공급과 실거래 등록은 기존 최종 검증·블라인드·섀도·배포 자격 절차의 별도 연결 대상입니다.

## 소스 기준

2026-10-14 연구 업로드의 기존 연구 모듈을 바탕으로, 10월 15일 확보한 컨트롤러 및 연결 수정본을 통합했습니다. 현재 Windows 전체 최신본과 일괄 동기화된 상태는 아닙니다. 운영 설정과 거래소 스냅샷은 가져오지 않았습니다.

## 최신 로컬 입력 확인

기존 `research_controller.py --mode managed-dry-run --check-inputs`는 정식 엔진·장기 캐시·연구 원장·큐·시세 파일의 존재와 용량, 대기 전략 소스 해시를 읽기 전용으로 확인합니다. 누락이나 해시 불일치 시 종료 코드는 2입니다. 파일이 모두 있어도 실제 데이터 적합성이나 수익성 통과를 뜻하지 않습니다.

이 검사는 파일을 복사하거나 공개 업로드하지 않습니다. 윈도우 경로가 남은 기록은 플랫폼 경로 검토 대상으로 표시합니다. 실제 연구 실행 이전에 최신 소스·체크포인트 인계와 경로 호환 검증이 필요합니다.

## 비공개 인계 묶음 생성

기존 컨트롤러의 `--mode managed-dry-run --export-inputs <연구 폴더 밖의 새 zip 경로>`는 연구 잠금을 확보한 뒤 위 입력 검사와 로컬 압축 생성을 수행합니다. 거래소 상태 스냅샷·운영 DB·루트 운영 설정·환경 파일·개인키는 수집 대상에 포함하지 않습니다. 연구소 소스·연구 기록·시세와 필수 연구 입력을 포함하며 파일별 SHA-256은 `HANDOFF.json`에 기록합니다.

이 묶음에는 상세 연구 이력이 있으므로 공개 업로드 대상으로 사용하지 않습니다. 자동 업로드·서버 접속·주문 활성화는 수행하지 않습니다. 연구 원본과 기존 압축은 덮어쓰지 않습니다. 잠금 사용 중이면 종료 코드 3과 SKIP을 반환합니다. 연구 기록이 생성 도중 바뀌거나 알려진 자격증명 패턴이 발견되면 임시 압축을 제거하고 중단합니다. 수집기가 갱신하는 시세는 파일별 읽기 전후 상태와 압축에 담긴 바이트 해시로 고정합니다. 패턴 검사는 완전한 비밀정보 감사를 대신하지 않습니다.

기본 분할 단위는 200 MiB입니다. 이를 넘는 ZIP은 같은 이름의 폴더 안에 `.zip.001`, `.zip.002` 등의 바이트 조각과 `TRANSFER.json`으로 생성합니다. 조각을 순서대로 결합하면 원래 ZIP 전체가 복원되며 조각별·전체 ZIP·내부 파일별 SHA-256을 확인할 수 있습니다. 연구 파일을 줄이거나 삭제하지 않습니다. 압축 수준 1을 사용합니다. 원본 합계 4 GiB와 단일 파일 512 MiB 상한은 유지합니다. 작은 ZIP의 최종 파일 생성은 표준 `os.link`를 사용하므로 하드 링크를 지원하는 파일시스템이 필요합니다.

R4 피드백의 `exit_fill_ids` 안에서 원장 생성식 `EXTERNAL_TRADE:종목:체결번호:LEG_32자리해시` 전체 형식과 같은 기록의 `symbol`, `leg_id`가 모두 일치하는 체결 ID만 자격증명 검사 뷰에서 구분합니다. 원본 JSON·압축 바이트·해시는 그대로 보존합니다. 같은 필드의 토큰 모양 문자열과 다른 필드의 자격증명 패턴은 계속 차단합니다.

자격증명 검사는 압축 생성 전에 수행합니다. 검사한 텍스트 입력의 SHA-256을 보존하고 압축 시 다시 대조하여 검사 후 바뀐 파일을 포함하지 않습니다. 외부 체결 ID 형식은 기존 원장의 `confirm_external_reduction` 생성식과 같은 기록의 종목·LEG를 함께 확인합니다.

## 기존 실거래 배포 작업자 연결

`v3_auto_runtime_release.py --connect-github --code-ref <검증된 고정 커밋>`은 기존 Windows 배포 호스트에서 실행합니다. SSH·거래소 자격증명은 GitHub에 올리지 않습니다. 기존 배포 작업자가 비공개 연구 상태를 60초 간격으로 확인하고, 최종 검증 및 봉인 블라인드를 통과한 정확한 버전의 소스와 증거만 기존 로컬 파이프라인에 추가합니다. 원본 연구 이력·운영 설정·기존 포지션은 보존합니다. 같은 버전이 로컬에 다른 근거로 이미 있으면 덮어쓰지 않고 중단합니다.

설정 후 로컬 Controller의 연구 실행은 `GITHUB_RESEARCH_OWNER`로 건너뜁니다. 기존 배포 작업자의 섀도 검사·서버 사전 동기화·검증기·CANARY 배포 절차는 유지합니다. 초기 연구 통과만으로 주문이나 배포 요청을 만들지 않습니다. 이 연결은 기존 PC를 배포 중계로 사용하므로 연구 계산은 GitHub에서 실행되지만 자동 서버 전달에는 중계 PC가 켜져 있어야 합니다.

## PC 없는 AWS 직접 연결 준비

목표 경로는 GitHub 연구 → AWS 직접 수신이며 PC 상시 중계는 사용하지 않습니다. `v3_auto_runtime_release.py --prepare-server`는 기존 SSH 호스트에서 한 번 실행하는 준비 명령입니다. AWS 전용 Ed25519 개인키는 AWS 밖으로 내보내지 않고 해당 비공개 저장소의 읽기 전용 배포키로 공개키만 등록합니다. GitHub 호스트 키는 공식 Ed25519 지문에 고정합니다. 필요한 경우 서버 Git 패키지를 설치합니다.

서버에서 비공개 저장소를 직접 읽는 데 성공하면 허용된 Python 소스 여덟 개와 해시만 `aws-runtime-source` 브랜치에 보존합니다. 운영 설정·개인키·DB는 수집하지 않습니다. 운영 서비스 재시작이나 전략 자동등록은 이 준비 단계에서 실행하지 않습니다. 직접 수신·실행 검증은 이 최신 서버 기준을 확인한 후 진행합니다.

### AWS direct reception

`--prepare-server` establishes a read-only GitHub deploy key and captures the current runtime source. `--install-server-receiver --code-ref <commit>` installs the appended Runtime reception command and a 60-second AWS timer. It preserves the R4 PID, config, ledger and order path. Final-gated portable candidates are published to the private `runtime-candidates` branch and received into a bounded staging inbox. First-gate passes are excluded. Reception does not authorize registration: the server shadow and existing release validation gates remain required. The one-time installer captures the existing local release verifiers on private `release-checks` for Linux portability review; no keys, DB or config are exported.

`--prepare-release-context` reads the current AWS Python dependency closure, verifies the four frozen kernel hashes, and captures the three remaining local maintenance gate scripts on private `release-context`. It reads `strategy_config.json` for compatibility verification after credential scanning; credential files, API configuration, databases and private keys are excluded; the Config loader source is included only if credential scanning passes. It never imports the collected runtime modules or changes AWS files, services or registration settings. Missing dependencies are reported for review rather than converted into passing checks.

The release context also follows the active strategy registry and verifies all seven legacy strategy hashes using the existing verifier baseline. The six existing release verifiers are retained at the repository root. Open-position verification accepts the same explicit workspace environment as the other verifiers; the autonomous guard permits only a read-only file collection loop owned by server preparation and still rejects base deployment execution. These changes do not enable registration.

`--verify-context` runs all six existing release verifiers against an isolated, hash-bound server snapshot. The engineering workflow checks out the pinned source and original release-script receipts, constructs the workspace outside the checkout, and verifies all 37 runtime files remain unchanged. It never uses overlay inheritance, API credentials, server writes or live orders, and does not approve a candidate release. Linux uses the current Python interpreter; the existing Windows hidden-worker interpreter is preserved.

### 서버 시세의 GitHub 검증 입력 전달

`--install-server-receiver --enable-feedback --code-ref <검증된 커밋>`은 기존 수신 타이머에 비공개 `runtime-feedback` 브랜치 전송을 추가합니다. 별도 GitHub 쓰기 배포키를 서버에 만들며 거래소 키는 전송하지 않습니다. 배포키 자체는 저장소 쓰기 권한이고, 프로그램의 전송 대상은 이 고정 브랜치로 제한합니다. 실제 수집기 heartbeat, 최근 확정봉, 자격증명 검사를 통과한 전략 설정만 해시와 함께 전달합니다. 운영 DB와 주문 API는 사용하지 않습니다. 최신 스냅샷만 유지하고 같은 봉의 시세 객체는 재사용합니다.

GitHub 연구는 `--prepare-context`로 기존 서버 소스와 여섯 검증기를 격리 작업공간에 구성하고, `--pull-runtime-feedback`으로 서버 원본 시각을 보존한 입력을 읽습니다. heartbeat가 오래됐거나 후보 수신 커밋이 다르면 가져오지 않습니다. 수신기 설치는 R4 재시작·운영 설정 변경·자동등록 활성화를 수행하지 않습니다. 최종 후보의 섀도 검증과 실거래 적용 연결은 별도 완료 확인이 필요합니다.

### 최종 후보의 카나리 자동등록

GitHub 연구 실행은 기존 `tick(cloud_validation=True)`에서 기존 후보 선택·인스턴스 등록·섀도·카나리 요청 생성·배포 검증기 여섯 개를 그대로 실행합니다. 새 승인 조건을 만들거나 섀도 통과를 합성하지 않습니다. `VALIDATED_FOR_SERVER` 요청만 기존 순수 병합기로 실제 서버 설정에 병합해 `runtime-candidates`에 전달합니다. 원본 연구 엔진·헌법·정확한 소스·요청·설정·산출물 해시를 묶고, 시세 원본 heartbeat와 검증 시각을 확인합니다.

서버의 `--enable-registration`은 명시적 설치 옵션이며 기본은 꺼짐입니다. 활성화하려면 시세 전달도 켜져 있어야 합니다. 설치 사전 검사는 자동등록을 끈 상태로 수행합니다. 수신기는 최종 연구 통과와 섀도 통과·여섯 검증기 증거가 모두 있는 단일 카나리 요청만 적용합니다. Frozen Core, 기존 대표 프레임, 다른 전략 인스턴스, 등급·레버리지 설정, 원장 DB는 보존합니다. 동일 전략의 이전 버전은 기존 순수 병합기 규칙대로 신규 진입만 중지하며 기존 포지션은 그대로 관리합니다. 기존 실행 파일의 내용이 다르면 덮어쓰지 않고 차단합니다.

기존 `/tmp/bbot_source_sync.lock`으로 수동 배포와 충돌을 막고 실제 운영 설정 해시가 달라지면 최신 시세 피드백을 기다립니다. 설정·전략 파일은 원자적으로 반영하고 R4 PID 생존을 확인합니다. 실패하면 원복합니다. 기존 서비스는 재시작하지 않습니다. 최근 원복 자료 한 묶음과 기존 수신함 상한을 유지하며 로그는 기존 runner.log에만 중복을 줄여 기록합니다. 서버 설정에서 정확한 인스턴스 등록을 다시 읽은 뒤에만 GitHub 연구 상태를 적용 완료로 기록합니다.

자동등록 설치는 PC에 미처리 배포 요청이나 실행 중인 Runtime 동기화가 있으면 중단합니다. 기존 PC 배포 작업자는 종료하고, 기존 설정의 배포 담당을 `AWS_DIRECT`로 바꿉니다. 이전 작업자 파일은 한 개만 백업합니다. 재부팅으로 로컬 작업자가 다시 시작돼도 기존 tick에서 AWS 담당임을 확인하고 배포하지 않습니다. 서버 설치가 실패하면 PC 배포는 안전하게 정지 상태로 남으며 서버 자동등록 성공으로 처리하지 않습니다.

## 진행 중인 연구 속도 개선 작업

작업 브랜치는 `research-speed`이다. 해당 브랜치 README의 작업 메모는 개발 재개용이며 중앙정책의 권위 있는 인계 파일 `B_BOT_MASTER_HANDOFF.json`을 대체하지 않는다. 3프레임 병렬·5시간 예산·중간 저장은 main에 반영했다. 3프레임 실제 실행은 확인했으며 장시간 예약 실행은 별도 관찰이 필요하다. 작업 세션이 끊겨도 main 예약 연구를 취소하거나 미검증 브랜치로 교체하지 않는다.

2026-10-18 예약 실행 `39097844817`에서 historical reentry의 `reserve_trial(exact_strategy_version=...)` 인자 불일치를 확인했다. 원장에 정확한 버전·소스 경로·해시를 보존하도록 수정했으며 완료된 동일 버전/동일 소스 및 신원 없는 과거 결과의 중복 차단을 유지한다. 로컬 공학 검사 209개 통과. 원격 실행 결과는 Actions에서 별도 확인해야 한다. 운영 주문·레버리지·등급·최종 등록 조건 변경 없음.

## 연구 실행과 작업 인계

최종 목표는 최종 검증과 블라인드를 통과해 실서버에 공급할 완성 전략이다. 단순 탐색 횟수나 유전 최적화 점수는 완료 기준이 아니다. 이후에는 서버에서 탈락·잠긴 전략의 오류 점검, 필요한 전략 수정, 재검증과 재공급을 진행한다. 임의 잠금 해제나 주문·레버리지·등급 변경은 하지 않는다.

예약 실행은 최대 5시간 예산에서 종료 여유 30분을 두고 새 연구 시작을 중단한다. 시작/수동 검증 실행은 지정 횟수만 수행한다. 기존 매시간 예약과 실행 중 취소 금지를 유지한다. GitHub 실행기 대기 때문에 무중단 실행을 보장하지 않는다.

사용자가 승인한 서로 다른 계열의 3프레임 동시 계산은 기본·조건 조합 연구에 적용한다. 한 컨트롤러가 예약·원장·결과 반영을 담당하며, 트레일링 이후 단계와 최종 검증·서버 등록은 기존 정식 흐름을 유지한다. 배포된 검증 정책의 지문과 서버 등록 조건은 변경하지 않는다.

첫 완료 주기와 이후 60초가 경과한 완료 경계에서 원격 체크포인트를 저장한다. 강제 종료 시 마지막 원격 저장 이후 계산은 다시 수행될 수 있다. 저장된 계산 영수증이 있으면 소스·입력·결과 해시를 검증하고 결과 반영을 재개한다. 다른 실행이 갱신한 원격 상태는 덮어쓰지 않는다.

공식 인계는 기존 `strategy_lab/master_handoff.py`가 생성하는 `B_BOT_MASTER_HANDOFF.json`의 `research_development` 노드다. 진행 상태와 남은 항목은 `strategy_lab/research_development_state.json`에 기록한다. 첫 실제 실행 `39187205002`에서 3프레임 계산 두 묶음과 총 8주기, 중간·최종 저장 및 AWS 수신을 확인했다. 공학 실행 `39187204941`은 검사 219개와 기존 검증기 6개를 통과했고 런타임 파일 37개가 유지됐다. 복원 기간 수정본 `08fa0b4`도 실제 연구 `39187460325`에서 8주기·3프레임 묶음 3개(9건)를 완료했다. 공학 검사 `39187460344`의 220개 테스트·6개 검증기 통과와 런타임 파일 37개 유지를 확인했다. 최종 후보는 0개이며 초기 통과를 실서버 자격으로 간주하지 않는다. 유전/메타 배분, 신규 청산·재진입 방식의 실거래 동일성 검증 및 서버 잠금 전략 분석은 아직 완료되지 않았다.

수정본의 후보 커밋 `e1c647d89ef1c34a5a7f863b9492f5bef11cf898`도 2026-10-19 10:59 KST AWS 피드백의 `received_commit`과 일치했다. 수집기 heartbeat와 설정 해시 유지를 확인했으며, 이는 연구 수신 경로 확인이다. 운영 주문·체결 전체 상태를 확인했다는 뜻은 아니다.

### 유한 유전·Optuna 후보 연구 연결

신규 연구 작업 4번 중 1번을 유전·Optuna 후보에 배정하고 1H·6H·12H를 순환한다. 기본 연구의 3프레임 동시 계산과 이후 단계 배분은 유지한다. 공식 확정봉 로더에서 BTC·ETH의 2020–2021년만 잘라 학습 입력 해시를 고정한다. 프레임마다 최대 32개 평가·12개 후보이며, 완료한 묶음을 자동으로 새로 만들어 반복하지 않는다. 후보는 기존 정식 검증을 모두 거쳐야 한다. 운영 설정 변경 없이 연구 작업공간에만 적용하며 로컬 검사 224개 통과, 실제 실행 확인 대기 상태다. 이는 전체 메타 학습이나 신규 청산 방식 연구의 완료를 의미하지 않는다.


## 서버 전체 거래 진단 (읽기 전용, 설치 전 검증 필요)

기존 `strategy_runtime.py`의 `publish_github_feedback`와 공식 `v3_auto_runtime_release.py`의 피드백 수신을 확장하여, 기존 비공개 `runtime-feedback` 브랜치로 `DIAGNOSTICS.json`을 전송할 수 있다. 기존 `CONFIG.json`, `HEARTBEAT.json`, `MARKET.json` 계약은 유지한다. 수신기는 이전 3종 전송도 허용한다.

- 수집 범위: 호스트 부하·가용 메모리·디스크, 서비스 실행 여부, 컬렉터 신선도·확정봉 상태, 전략 인스턴스 등급·진입 차단 상태, 주문 대기/체결·청산 대기/체결, 원장/거래소 수량 불일치, 안전 제한 및 최근 복구 이벤트, 경제적 체결 증거 완결 여부, 로그 오류 태그 빈도, 읽기 전용 DB 접근 상태.
- 증거가 없는 단계는 `NO_DIRECT_EVIDENCE`, 파일이 없거나 읽기 실패한 경우는 `MISSING`·`UNAVAILABLE`로 표시한다. 서비스 활성 상태와 오래된 이벤트만으로 실거래 정상이라고 결론 내리지 않는다.
- 원본 로그·거래 API 정보·주문/체결 식별자·DB 파일·잔고·수익금은 올리지 않는다. 각 DB 최근 96건 및 최근 로그 64 KiB 범위만 판독한다. 운영 주문 API와 전략 설정을 변경하지 않는다.
- 서버 측 수신기 갱신은 기존 고정 커밋 검증·설치 경로를 사용한다. 연구소 브랜치 변경만으로 AWS 설치가 완료된 것은 아니다. 운영 재시작/배포 전 기존 6개 검증기 및 실제 서버 수신 테스트가 필요하다.

### 웹소켓 · 전략 신호 · 텔레그램 보조 진단 (읽기 전용)

- 웹소켓: 기존 `runner.log`의 명시적으로 허용된 `WS_*` / `USER_STREAM_*` 연결·이벤트·오류 유형 및 건수와 로그 시각만 수집한다. 실제 `r4_user_stream.py`의 소켓 상태 또는 수신 하트비트를 직접 읽지 않는다. 로그 증거가 없으면 `NO_DIRECT_HEARTBEAT`, 오류만 관찰되면 `FAILURE_EVENTS_OBSERVED`로 표기한다. 단순 서비스 활성/과거 체결은 연결 정상으로 판정하지 않는다.
- 전략 신호: 기존 `ENTRY_RESULT`, `ENTRY_BLOCK`, `STRATEGY_EXIT`의 건수와 로그 시각, **이미 설정에 존재하는** 인스턴스의 진입 결과·차단 건수만 수집한다. `gate.evaluate_frame()` 전체 평가 횟수나 거절 이유별 통계는 아직 직접 계측하지 않아 `direct_evaluation_heartbeat=false`로 표시한다.
- 텔레그램: 서버의 `server_status_telegram.py`, `monitor.py` 파일 유무/해시와 `[주문][SERVER_STATUS_TELEGRAM] sent ok` 및 `fail err=`의 건수만 수집한다. 오류 원문, HTTP 요청 URL, 텔레그램 토큰, 채팅 ID 및 메시지 본문은 절대 전송하지 않는다. `sent ok`는 **서버 로그상의 요청 성공 기록**이며 사용자 단말 수신 확인은 아니다(`message_delivery_verified=false`). 송신 기록과 파일이 없어도 서비스 장애를 확정하지 않는다.
- 모든 보조 진단은 기존 최근 로그 64 KiB/400줄만 판독하여 과거 누적·추세·실제 프로세스 상태를 대표하지 않는다. 로그 타임스탬프가 없으면 최신 이벤트 시각을 `0`으로 표시한다. 동작 상태의 완전한 직접 계측은 서버 전용 웹소켓 모듈과 신호 평가 코드를 별도 검증한 뒤 **거래 코드 변경 심사와 승인**을 거쳐 진행한다.
- 진단 출력은 기존 `DIAGNOSTICS.json`만 확장하며 연구실 허용 stage에 `telegram`을 추가한다. 전략 변경, 신규 주문, 데이터베이스 쓰기, 알림 메시지 발송, 실거래 서비스 재시작을 하지 않는다.


## 대화형 텔레그램 조회 프로토타입 (배포 전·기본 비활성)

새 \`runtime/telegram_readonly_query.py\`는 특정 개인 채팅 ID와 사용자 ID를 **모두** 검증한 후 \`getUpdates\`/ \`sendMessage\`만 사용하는 조회 전용 모듈이다. 자연어는 고정 명령어로만 분류하며 거래 API, 주문, 서비스 재시작, DB 쓰기, 임의 SQL 실행은 제공하지 않는다. 자동 알림이나 현재 \`server_status_telegram.py\` 일정은 변경하지 않는다.

- 지원 질문: \`상태\`, \`거래 횟수\`, \`전략\`, \`위험\`, \`승률\`, \`현금 잔고\`, \`도움말\`. 평소 자동 보고를 줄이고 장애 알림을 유지하는 **운영 정책은 서버의 현재 예약 작업과 모니터를 확인한 후 별도 설치/활성화**한다.
- 원장 조회는 \`state/history.db\`를 SQLite \`mode=ro\` 및 \`PRAGMA query_only=ON\`으로 연다. '진입 주문 의도'와 '종료 Leg' 수는 실제 거래소 체결 횟수나 경제적 승률로 혼용하지 않는다.
- 현재 승률/현금/총자산에 대해 별도의 재현 가능한 비용·펀딩비 검증 또는 거래소 잔고 읽기 경로가 없으므로 **추정 금지, 미검증 상태로 응답**한다. 계좌 잔고/손익은 GitHub 공개·피드백 자료에 넣지 않으며, 이후 신뢰 가능한 서버 내부 읽기 경로를 독립 설계해야 한다.
- 실행 코드는 자동 시작하지 않으며 \`--poll-once\`에서만 Telegram에 연결된다. 환경변수 \`TELEGRAM_BOT_TOKEN\` (또는 \`TELEGRAM_TOKEN\`), \`TELEGRAM_ALLOWED_CHAT_ID\`, \`TELEGRAM_ALLOWED_USER_ID\`가 모두 있어야 한다. 외부 제공자와의 getUpdates 컨슈머 충돌, webhook 등록 여부, 로그 유출 및 소유자 접근 제어를 실서버 배포 전에 점검해야 한다.
- 조회 중의 유일한 쓰기 위치는 독립 \`state/telegram_query_offset.json\`이며 trading DB·config·order API에는 쓰지 않는다. 네트워크/토큰 오류 원문은 출력하지 않고 일반 상태코드로 처리한다.
- **라이브 적용 전 필수 점검**: 기존 \`monitor.py\`가 \`runner.service\`를 자동 재시작하는지, \`server_status_telegram.py\`의 실제 systemd timer 및 다른 Telegram getUpdates 소비자가 존재하는지 읽기 전용으로 확인한다. 기존 R4 단일 실행기와 충돌할 위험이 있다. 봇 토큰·채팅 ID·SSH 개인키는 GitHub, 대화 로그, PR, 테스트에 넣지 않는다.
