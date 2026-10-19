# R4 텔레그램 중대 장애 독립 알림기 — 단계별 적용 안내

**현재 상태:** GitHub 연구실에 코드·테스트·서비스 템플릿만 저장한다. AWS 자동 설치/실행/cron 변경을 수행하지 않는다.

### 적용 목적
- \`server_status_telegram.py\`는 AWS ec2-user crontab의 \`15,45 * * * *\`에서 일반 상태를 30분마다 보고 중이다.
- \`r4-runtime.service\`는 실행 중이고, 구형 \`runner.service\`는 중지 상태다. \`monitor.py\`는 서버에서 삭제된 것으로 확인되었다.
- 별도 \`telegram-critical-watch.timer\`를 도입하여 R4 서비스, 구형 writer, 컬렉터, 컬렉터 heartbeat, SQLite 원장 불일치만 **읽기 전용**으로 확인한다.
- 현존하는 텔레그램 토큰/채팅 ID는 서버 내부의 기존 환경변수 또는 /etc/bbot/runner.env, .env에서 조회한다. 대화나 GitHub에 값을 올리지 않는다.
- 웹소켓 실제 연결 상태는 직접 심박 증거가 없으므로 **중대 장애 확정 판정에서 제외**한다. 향후 직접 신호 계측을 별도로 검증해야 한다.

### 1. 설치 전 필수 확인 (민감 설정 비출력)
1. 원격에서 \`r4-runtime.service\` 활성화, \`runner.service\` 비활성화, 컬렉터 최신성, 임시 결함이 없는지 확인한다.
2. 기존 알림 코드와 GitHub 새 코드의 SHA256을 로컬에서 대조한다. \`server_status_telegram.py\`·기존 crontab·거래 코드 자체는 수정하지 않는다.
3. 시스템에 \`telegram-critical-watch.service\` / \`.timer\`가 기존 존재한다면 설치를 중지하고 검토한다.
4. 기존 Telegram 봇 채팅 ID가 양의 정수로 된 본인 개인 채팅인지 확인한다. 그룹·채널에는 중대 장애 알림을 보내지 않는다.
5. 독립 쿼리 봇은 기존 Telegram webhook이나 다른 getUpdates 소비자와 충돌할 수 있다. 적용 전 충돌 검사가 필요하다.

### 2. 독립 알림 코드 점검 및 시험
- 고정 커밋의 \`runtime/telegram_critical_watch.py\` 및 \`ops/telegram-critical-watch.{service,timer}\`를 로컬로 내려받아 해시를 확인한 뒤 AWS로 **새 파일만** 복사한다. 기존 \`runner.py\`, \`order.py\`, \`risk_engine.py\` 및 R4 서비스 파일을 덮어쓰지 않는다.
- 첫 시험에는 \`--send-test\`를 **명시적으로** 한 번 실행하고 개인 Telegram에 메시지가 도착했는지 확인한다.
- \`--check\`는 읽기 전용 점검이나 중대 장애 메시지를 전송할 수 있다. 초기에 시스템이 불안정하면 의도치 않은 알림을 막도록 시험 시간과 사용자 동의를 확인한다.
- 문제 코드는 45초 이상 간격의 관찰 **2회 연속** 발생해야 전송하며, 6시간 이내 같은 장애 중복 전송을 억제한다. 전송 실패 시 전달 완료로 기록하지 않는다.
- \`--send-test\` 성공과 2회 연속 정상 검사 및 보조 기록을 확인한 후에만 systemd service/timer를 **별도 수동 등록**한다.

### 3. 정기 보고에서 요청 시 조회로 전환
1. 중대 장애 타이머의 서비스 실행 상태, 정기 재검사, 수신 테스트를 확인한다.
2. 대화형 \`telegram_readonly_query.py\`는 본인 개인 Telegram chat/user ID를 각각 승인하고 webhook/경쟁 poller가 없는지 확인한 후 **별도 활성화**한다. 아직 원장 기반 거래 건수·전략·위험 정보만 제공한다.
3. 실시간 현금·총자산·검증된 순손익 승률은 신뢰할 수 있는 계좌 스냅샷/완결 체결 근거를 확보하기 전까지 조회 불가로 표시한다.
4. 모든 조건 충족 시에만 \`15,45 * * * *\`의 **정확히 해당 \`server_status_telegram.py\` cron 한 줄만** 비활성화한다. 기존 crontab은 백업하고 diff를 확인한다. 다른 일정은 그대로 둔다.
5. R4 실거래 서비스를 재시작하지 않고, 거래 DB/거래 API/보유 포지션을 변경하지 않는다.

### 제외 사항
- 자동 장애 복구 또는 R4/runner 재시작, 강제 청산, 계좌 자금 이동.
- 과거 로그만으로 웹소켓 정상/불량을 확정하는 것.
- GitHub에 계좌 잔고, API 키, 개인 채팅 ID, 전체 로그 및 주문 식별자를 게시하는 것.

### 회귀 테스트
\`python -B -m unittest tests.test_telegram_critical_watch tests.test_telegram_readonly_query -v\`

전체 기존 CI 테스트·canonical 6 검증기 통과 전 병합하지 않는다.


## 5분 정상 수신 reservation.py를 기준으로 통일 (승인·실서버 확인 전)

사용자가 AWS에서 실행 중으로 추정되는 5분 보고 모듈 예약 파일 reservation.py를 제공했다. 이 파일의 기존 인터페이스에 5분 주기 전송, 기존 TELEGRAM_TOKEN/TELEGRAM_CHAT_ID, 바이낸스 계좌 조회, 보유 포지션, exit_history 통계, 전략별 성과, CYCLE 비교가 있다. **업로드 파일과 AWS 실제 파일의 동일성은 배포 전 해시 대조가 필요하다.**

- 새로운 runtime/telegram_reservation_bridge.py가 서버의 reservation.py를 고정 경로에서 import하여, 실제 그 보고기가 로딩한 TELEGRAM_TOKEN/TELEGRAM_CHAT_ID를 그대로 사용한다. import는 기존 예약 main()과 5분 발송 반복문을 실행하지 않는다.
- 별도 runtime/telegram_readonly_query.py --poll-once --use-reservation은 **명시적 선택**이 필요하다. private chat의 chat ID == 전송자 user ID 여부를 확인하며 getUpdates를 통해 질문에만 응답한다. 설치만으로 시작하지 않는다.
- 서버 조회는 기존 reservation.account_summary / live_positions / build_strategy_stats / load_cycle_report 등에서 데이터를 읽는다. 예약 trade_stats()는 누적 통계 파일을 갱신하므로 호출하지 않는다. 대신 기존 읽기 전용 exit_history 및 통계 보조 함수를 사용하고 최근 최대 5000행 범위, 불완전 기록 건수를 표시한다.
- 계좌 현황은 totalWalletBalance(지갑 잔고), availableBalance(가용 잔고), totalUnrealizedProfit(미실현 PnL)을 각각 명시한다. 지갑잔고+미실현손익은 잔고 계산값으로 제공하며 모든 계정 손익이나 실제 인출 가능 현금으로 혼동하지 않는다.
- 전략 승률·청산 수익은 기존 예약 보고 기준이며 R4의 경제적 체결 증거·펀딩비·수수료 완결성을 재검증한 수치는 아니다. 데이터 없는 날 0%가 아니라 집계 대상 없음으로 표시한다.
- 새 runtime/telegram_critical_watch.py는 reservation.py가 **존재하면 그와 같은 봇/개인 수신 대상**을 우선하여 사용하고, 없으면 기존 환경설정 전송 경로만 사용한다. 기존 배포된 Watch 코드를 변경하거나 테스트를 재전송하려면 **서버 실제 파일 해시 일치, 검증된 배포 커밋, 개인 수신 확인**이 선행되어야 한다.
- 기존 reservation.py, server_status_telegram.py, 기존 cron 및 모든 R4 거래 모듈을 **절대 자동 변경하지 않는다**. 현재 AWS에 테스트된 초기 Watch 버전만 저장돼 있으며 타이머는 아직 활성화되지 않았다.
- 배포 전 사전 점검: reservation.py 실제 존재 여부와 SHA256, import 성공 여부, getUpdates webhook 또는 기존 소비자 존재 여부를 비밀정보 출력 없이 확인. 기존 5분 발신 메시지와 새 테스트가 동일 채팅에서 실제로 도착하는지 반드시 확인. 그 전엔 5분/30분 보고를 중단하지 않는다.


## 대화형 질문 폴러: opt-in, 5분 알림 별도 유지

- ops/telegram-readonly-query.service 와 ops/telegram-readonly-query.timer 는 자동 배포되지 않으며 기본 비활성이다. 타이머는 서비스 완료 후 15초가 지나 다음 폴링을 시작하고 getUpdates는 최대 20초 롱폴링한다. 정상적인 답변에는 수십 초 걸릴 수 있다.
- 서비스는 runtime/telegram_readonly_query.py --poll-once --use-reservation 만 실행하며, 실제 AWS에 설치된 reservation.py 에서 봇 토큰 및 private chat을 확인한다. Telegram 질문은 private 채팅 및 허용 chat ID == 실제 user ID인 메시지만 답변한다. 사용자 ID와 토큰은 절대 출력하지 않는다.
- 예약 모듈을 import해 가져오는 잔고·포지션·전략·승률은 기존 보고 모듈의 조회 로직을 따르며, 새로운 거래 명령은 추가하지 않는다. 원장 조회는 SQLite 읽기 전용으로만 수행한다. reservation.py 원본, R4 트레이더, watchdog, 기존 5분 보고와 30분 cron은 유지한다.
- 활성화 선행 조건: AWS 설치 스크립트 해시 검증, R4 active, runner.service inactive, telegram-critical-watch.timer active, 현재 5분 알림 정상, Telegram getWebhookInfo의 webhook URL 비어있음, 기존 메시지 소비 프로그램이 없다는 운영자 확인. 같은 봇에서 webhook 또는 다른 getUpdates 폴러가 있으면 활성화 금지. Telegram API로 외부/숨겨진 getUpdates 소비자를 완벽히 감지할 수 없다.
- 첫 폴링은 현재까지 쌓인 오래된 메시지를 회신하지 않도록 cursor 파일을 새로 생성한다. 이는 Telegram 서버의 대기열을 정리할 수 있으므로 다른 기존 소비자가 없다는 확인 없이는 절대 첫 폴링을 실행하지 않는다.
- service ProtectSystem=strict, 허용 쓰기 디렉터리는 독립 상태 cursor 및 예약 logger가 사용하는 state/logs뿐이다. service는 oneshot이며 자동 트레이더나 예약 5분 sender를 시작/중단하지 않는다.
- 활성화 후 사용자 명시적 테스트로 개인 채팅에서 현금, 포지션, 오늘 승률, 전략별 성과, 서버 상태 질문하고 답변이 실제 도착하는지 확인한다. 실패 시 우선 새 query timer만 비활성화하고, 원인을 조사한다. 기존 알림과 R4를 멈추지 않는다.
