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
