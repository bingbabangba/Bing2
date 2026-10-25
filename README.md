# 붕괴: 스타레일 PC 리세마라 도우미 (`hsr_reroll`)

Windows PC 클라이언트용 리세마라 자동화/기록 도구입니다.

- **결과 자동 기록**: 게임 웹캐시에서 워프 기록 URL(authkey)을 찾아 HoYoverse 기록 API로 실제 뽑기 결과를 가져와 계정 장부에 저장 → 점수순 `report.html` / `report.csv`
- **시나리오 자동화**: 이미지 인식(템플릿 매칭) + 녹화 매크로로 튜토리얼 대사 스킵·자동전투·우편·10연차·기록 열기·로그아웃을 반복
- **경우의 수 시뮬레이터**: 확률(0.6%, 74부터 소프트 천장, 90 천장, 출발 워프 50 확정)로 "계정 몇 개면 원하는 결과가 나오나" 계산
- **추천 캐릭터 정리**: [`docs/추천캐릭터.md`](docs/추천캐릭터.md)

## 설치 (Windows)
1. Python 3.10+ 설치 (설치 시 "Add to PATH" 체크)
2. 이 폴더에서 `pip install -r requirements.txt`
3. 게임은 관리자 권한으로 실행되므로 명령은 `run_admin.bat <명령>` 으로 실행

## 사용 순서
```bat
:: 0) 경우의 수 확인 (게임 없이도 동작)
run_admin.bat simulate --departure 50 --limited 60 --name "Mortenax Blade" --goal 15

:: 1) 가장 확실한 반자동 모드: 사람이 플레이, 도구가 결과 기록
run_admin.bat run scenarios\fetch_only.json --accounts 20

:: 2) 자동 모드: 템플릿 캡처 후 (docs\템플릿_캡처_가이드.md)
run_admin.bat check
run_admin.bat run scenarios\reroll_cycle.json --accounts 30 --dry-run   :: 클릭 없이 인식만 점검
run_admin.bat run scenarios\reroll_cycle.json --accounts 30

:: 결과
run_admin.bat refresh      :: 기록 반영 지연(~1시간)으로 비어 있던 계정 재조회
run_admin.bat report       :: reroll_data\report.html
run_admin.bat mark acc007 keep --note "블레이드+브로냐"
```
수동으로 하나만 기록하려면 게임에서 **워프 → 상세 정보 → 기록** 화면을 연 뒤 `run_admin.bat fetch --label acc001`.

중단: **F12**. 인식 실패 시 스크린샷(`reroll_data\shots\<계정>`)을 남기고 사람에게 넘깁니다.

## 이 PC 환경에서 가능한 방법 조사 결과
| 방법 | 가능 여부 | 비고 |
|---|---|---|
| PC 클라이언트 1개 + 계정 교체 반복 | **채택** | 클라이언트는 동시에 1개만 실행 가능 → 순차 반복 |
| 결과 확인 = 기록 API | **채택** | 화면 OCR보다 정확. 단, 기록 반영이 최대 ~1시간 늦어 `refresh` 제공 |
| 튜토리얼 자동 진행 | 부분 가능 | 대사/전투/버튼은 이미지 인식, 이동 구간은 녹화 매크로. 패치로 화면이 바뀌면 재캡처 필요 |
| 계정 가입 자동화 | **미포함(의도적)** | 이메일 인증코드·캡차는 사람 입력 단계(`prompt`)로 둠. 캡차 우회·대량 가입 자동화는 만들지 않음 |
| 안드로이드 에뮬레이터 다중 실행 | 대안 | 여러 인스턴스 병렬은 가능하지만 사양 부담 큼. 이 도구의 시뮬레이터/장부는 그대로 사용 가능 |

리세 1회 소요 시간(가입~뽑기): 커뮤니티 기준 약 20~30분. 자동 모드는 사람이 가입 1~2분만 개입.

## 주의
- HoYoverse 이용약관은 서드파티 자동화 프로그램을 금지합니다. 매크로 사용은 계정 제재 위험이 있으니 **본인 책임**으로, 버릴 리세 계정에서만 사용하세요. 기록 조회 기능은 공개 워프 트래커들과 같은 방식(읽기 전용)입니다.
- `reroll_data/accounts.json` 에는 이메일·authkey(24시간 유효)가 저장되므로 공유하지 마세요 (`.gitignore` 처리됨).

## 개발
```
pip install opencv-python-headless numpy pytest
python -m pytest -q tests
```
