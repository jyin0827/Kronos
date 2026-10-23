# 포트폴리오 추적기 (DB증권, 조회 전용)

DB증권 Open API로 해외주식 잔고를 주기적으로 조회해서 종목별 손익, 목표 비중(`fund/weights.csv`)과의 차이, 고점 대비 낙폭을 보여 줍니다.
잔고 조회 API 한 가지만 호출하며, 주문 API는 프로그램 안에서 막혀 있습니다.

## 처음 한 번

1. **API 신청:** DB증권 [Open API 사용신청](https://www.dbsec.co.kr/online/accservice/on_OpenApi_wrk00.do)에서 앱키(app key)와 시크릿(app secret)을 받습니다.
2. **실시간 시세 신청:** DB증권 앱(MTS)이나 HTS에서 해외주식 실시간 시세를 신청합니다(무료). 신청하지 않으면 지연 시세가 나올 수 있습니다.
3. **설정 파일 만들기:** `dbsec_config.yaml.example`을 복사해 같은 폴더에 `dbsec_config.yaml`로 저장하고, 앱키와 시크릿을 따옴표 안에 넣습니다.
   - `dbsec_config.yaml`은 GitHub에 올라가지 않습니다. 다른 사람과 공유하지 마세요.
   - 모의투자 계좌로 먼저 해 보려면 `vtl_` 칸에 모의투자 키를 넣고 `mode: "demo"`로 바꿉니다.

## 실행

- **계속 추적:** `run_tracker.cmd`를 더블클릭합니다. 30초마다 갱신되고, 끄려면 `Ctrl+C`를 누릅니다.
- **한 번만 조회:** 터미널에서 `tracker\run_tracker.cmd --once`
- **옵션:**
  - `--interval 60`: 갱신 간격(초). 5초 이상이어야 합니다.
  - `--drift 0.03`: 목표 비중과 3%p 이상 벌어지면 경고합니다. 기본값은 5%p입니다.
  - `--mock`: 키 없이 가짜 잔고로 화면만 확인합니다.

## 기록

조회할 때마다 `tracker/history/` 폴더에 기록이 쌓입니다. 이 폴더는 GitHub에 올라가지 않습니다.
- `equity.csv`: 시각, 총자산, 현금, 투자 비중
- `positions.csv`: 시각별 종목, 수량, 가격, 평가금액, 비중, 목표 비중

## 알아 둘 점

- **추정치:** 잔고 금액과 손익은 DB증권이 계산한 추정치라 실제와 조금 다를 수 있습니다(공식 문서 안내).
- **장 마감 후:** 미국 장이 닫혀 있으면 마지막 체결가로 계산합니다.
- **인증 토큰:** 토큰은 SDK 폴더(`%USERPROFILE%\.dbsec\sdk\.dbsec_token.json`)에 저장되고, 24시간마다 자동으로 다시 발급됩니다.
- **필요한 환경:** DB증권 공식 SDK(`DBsecurities/dbsec-open-api`)가 설치된 `%USERPROFILE%\.venvs\broker` 가상환경이 필요합니다.
