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
