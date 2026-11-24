# 열화상(RAW16) + RGB 동시 촬영기

포스터·연차보고서용 추가 데이터(빈 장면, 고온 물체, 새 장소·거리별 소화기) 촬영용입니다.

## 설치 (PC, Ubuntu)
```bash
pip install opencv-python numpy
```

## 1. 장치 확인
```bash
python3 dual_capture.py --list
```
`Y16 시도 성공`이 뜨는 번호가 열화상(Boson), 나머지가 RGB입니다. `v4l2-ctl --list-devices`로 이름도 확인할 수 있습니다.

## 2. 촬영
```bash
python3 dual_capture.py --thermal 0 --rgb 2 --location 공학관3층 --note "고온 물체 시험"
```
| 키 | 동작 |
|---|---|
| `r` | 녹화 시작/정지 (누를 때마다 새 구간) |
| `1` | 빈 장면 (소화기 없음) |
| `2`~`5` | 고온 물체 없음 / 전기히터 / 전기포트 / 헤어드라이어 |
| `6`~`8` | 소화기 1 m / 3 m / 5 m |
| `9` | 새 건물 일반 (사람·문·계단 포함) |
| `m` | 마커 (손뼉 칠 때 눌러 두면 동기 확인용) |
| `q` | 종료 |

장면 키를 먼저 누르고 `r`로 녹화하세요. 저장 용량: 열화상 30 fps 전부 저장 시 1분에 약 0.6~1 GB (`--save-every 3`이면 1/3).

## 3. 점검
```bash
python3 check_session.py captures/<세션폴더> --preview
```
RAW16 형식(uint16, 640×512), 장면별 프레임 수, RGB 짝 시간차, FFC 정지 프레임 수를 출력하고 장면별 미리보기(열화상 | RGB)를 저장합니다.

## 저장 구조
```
captures/<날짜_시간>_<장소>/
├─ thermal/000000.png ...   16비트 PNG (Y16 원본, 계조 변환 없음)
├─ rgb/000000.jpg ...
├─ thermal_frames.csv       파일, 타임스탬프, 장면, 구간, min/max, 정지 여부
├─ rgb_frames.csv
├─ pairs.csv                열화상 ↔ 가장 가까운 RGB (50 ms 이내 = ok)
├─ markers.csv
└─ meta.json                장치·설정·측정 fps·저장 누락 수
```

## 주의
- 열화상이 8비트로 들어오면 오류로 멈춥니다(원본 손실 방지). 장치 번호와 Y16 지원을 확인하세요.
- 온도 환산(°C = count/100 − 273.15)은 Boson TLinear 고이득 설정일 때만 맞습니다.
- 두 카메라는 PC 시계로 맞춥니다(하드웨어 동기 아님). 빠르게 움직이는 장면은 피하고, 시작할 때 손뼉 + `m`을 남겨 두세요.
- 고온 물체 시험에서 실제 불은 사용하지 마세요.
- 카메라 없이 동작 시험: `python3 dual_capture.py --fake --frames 60 --headless`
