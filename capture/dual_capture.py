#!/usr/bin/env python3
"""열화상(FLIR Boson, RAW16) + RGB 카메라 동시 촬영기.

- 열화상: UVC Y16(16비트) 원본을 그대로 16비트 PNG로 저장 (계조 변환 없음)
- RGB: JPG로 저장 (정답 확인·라벨 보조용)
- 두 카메라 모두 같은 PC 시계(time.time_ns)로 타임스탬프를 찍고,
  종료 시 열화상 프레임마다 가장 가까운 RGB 프레임을 짝지어 pairs.csv 생성
- 장면 태그(빈 장면, 고온 물체 종류, 소화기 거리 등)를 키보드로 바꿔 가며 구간별로 기록

사용 예:
  python3 dual_capture.py --list                      # 카메라 장치 확인
  python3 dual_capture.py --thermal 0 --rgb 2 --location 공학관3층
  python3 dual_capture.py --fake --frames 120          # 카메라 없이 동작 시험

조작 키 (미리보기 창):
  r : 녹화 시작/정지 (현재 장면 태그로 구간 기록)
  1~9 : 장면 태그 선택 (아래 SCENES 참고)
  m : 마커 기록 (손뼉 등 동기 확인용)
  q / ESC : 종료
"""
import argparse, csv, json, os, queue, sys, threading, time
from datetime import datetime

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    sys.exit("opencv-python 이 필요합니다: pip install opencv-python numpy")

SCENES = {
    "1": "empty_no_object",      # B1 빈 장면 (소화기 없음)
    "2": "hot_none",             # B2 고온 물체 없음
    "3": "hot_heater",           # B2 전기히터
    "4": "hot_kettle",           # B2 전기포트
    "5": "hot_dryer",            # B2 헤어드라이어
    "6": "ext_1m",               # B3 소화기 1 m
    "7": "ext_3m",               # B3 소화기 3 m
    "8": "ext_5m",               # B3 소화기 5 m
    "9": "general",              # 새 건물 일반 (사람·문·계단 포함)
}

TH_W, TH_H = 640, 512


# ----------------------------------------------------------------- cameras
def open_thermal(dev, fps):
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2) if sys.platform.startswith("linux") else cv2.VideoCapture(dev)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"Y16 "))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, TH_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, TH_H)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)  # 8비트 변환 끄기 → 16비트 원본
    return cap


def to_raw16(frame):
    """OpenCV가 돌려준 Y16 프레임을 (512, 640) uint16 배열로 정리."""
    if frame is None:
        return None
    a = np.asarray(frame)
    if a.dtype == np.uint16 and a.shape[:2] == (TH_H, TH_W):
        return a.reshape(TH_H, TH_W)
    b = a.reshape(-1).view(np.uint8) if a.dtype != np.uint8 else a.reshape(-1)
    if b.size == TH_W * TH_H * 2:
        return b.view("<u2").reshape(TH_H, TH_W).copy()
    if a.dtype == np.uint8 and a.ndim == 3:
        raise RuntimeError("열화상이 8비트 컬러로 들어옵니다. Y16 설정이 적용되지 않았습니다 (장치 번호·드라이버 확인).")
    raise RuntimeError(f"예상하지 못한 열화상 프레임 형식: dtype={a.dtype}, shape={a.shape}")


def open_rgb(dev, w, h, fps):
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2) if sys.platform.startswith("linux") else cv2.VideoCapture(dev)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap


class FakeThermal:
    """카메라 없이 시험할 때 쓰는 가짜 RAW16 (약 22°C 배경 + 움직이는 고온 원)."""
    def __init__(self):
        self.t = 0
    def read(self):
        time.sleep(1 / 30)
        self.t += 1
        img = np.full((TH_H, TH_W), int((22 + 273.15) * 100), np.uint16)
        img += np.random.randint(0, 30, img.shape, dtype=np.uint16)
        y, x = np.ogrid[:TH_H, :TH_W]
        cx = 100 + (self.t * 5) % 440
        img[(x - cx) ** 2 + (y - 256) ** 2 < 900] = int((80 + 273.15) * 100)
        return True, img
    def release(self):
        pass
    def isOpened(self):
        return True


class FakeRGB:
    def __init__(self, w, h):
        self.w, self.h, self.t = w, h, 0
    def read(self):
        time.sleep(1 / 30)
        self.t += 1
        img = np.full((self.h, self.w, 3), 90, np.uint8)
        cv2.putText(img, f"fake rgb {self.t}", (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
        return True, img
    def release(self):
        pass
    def isOpened(self):
        return True


def list_devices(max_idx=10):
    print("장치 확인 (열화상은 640x512 + Y16 이 정상):")
    for i in range(max_idx):
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2) if sys.platform.startswith("linux") else cv2.VideoCapture(i)
        if not cap.isOpened():
            continue
        ok, fr = cap.read()
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"  [{i}] 열림  {w}x{h}  첫 프레임: {'있음 '+str(np.asarray(fr).shape) if ok else '없음'}")
        cap.release()
        try:
            t = open_thermal(i, 30)
            ok, fr = t.read()
            if ok:
                r = to_raw16(fr)
                print(f"       → Y16 시도 성공: min {r.min()} / max {r.max()} (열화상으로 보임)")
            t.release()
        except Exception as e:  # noqa: BLE001
            print(f"       → Y16 아님 ({e.__class__.__name__})")
    print("v4l2-ctl --list-devices 로도 이름(Boson 등)을 확인할 수 있습니다.")


# ----------------------------------------------------------------- display
def agc_view(raw, size):
    lo, hi = np.percentile(raw, (1, 99))
    v = np.clip((raw.astype(np.float32) - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)
    v = cv2.cvtColor(v, cv2.COLOR_GRAY2BGR)
    return cv2.resize(v, size)


# ----------------------------------------------------------------- grabbers
class Grabber(threading.Thread):
    def __init__(self, cap, kind):
        super().__init__(daemon=True)
        self.cap, self.kind = cap, kind
        self.latest = None          # (ts_ns, frame)
        self.lock = threading.Lock()
        self.running = True
        self.count = 0
        self.t0 = time.time()
        self.error = None
    def run(self):
        while self.running:
            ok, fr = self.cap.read()
            ts = time.time_ns()
            if not ok:
                time.sleep(0.005)
                continue
            try:
                if self.kind == "thermal":
                    fr = to_raw16(fr)
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                self.running = False
                break
            with self.lock:
                self.latest = (ts, fr)
                self.count += 1
    def get(self):
        with self.lock:
            return self.latest
    def fps(self):
        return self.count / max(time.time() - self.t0, 1e-6)


class Writer(threading.Thread):
    """디스크 저장은 별도 스레드에서 (촬영이 저장 때문에 끊기지 않도록)."""
    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue(maxsize=600)
        self.dropped = 0
    def put(self, path, img, params):
        try:
            self.q.put_nowait((path, img, params))
        except queue.Full:
            self.dropped += 1
    def run(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            path, img, params = item
            cv2.imwrite(path, img, params)


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="열화상 RAW16 + RGB 동시 촬영")
    ap.add_argument("--list", action="store_true", help="카메라 장치 목록 확인")
    ap.add_argument("--thermal", default="0", help="열화상 장치 번호 또는 /dev/videoN")
    ap.add_argument("--rgb", default="2", help="RGB 장치 번호 또는 /dev/videoN (없으면 --no-rgb)")
    ap.add_argument("--no-rgb", action="store_true")
    ap.add_argument("--fps", type=int, default=30, help="열화상 요청 fps (Boson 30/60)")
    ap.add_argument("--rgb-size", default="1280x720")
    ap.add_argument("--save-every", type=int, default=1, help="열화상 N프레임마다 1장 저장 (1=전부)")
    ap.add_argument("--out", default="captures")
    ap.add_argument("--location", default="unknown", help="장소 이름 (폴더명에 포함)")
    ap.add_argument("--note", default="", help="세션 메모")
    ap.add_argument("--fake", action="store_true", help="카메라 없이 가짜 영상으로 시험")
    ap.add_argument("--frames", type=int, default=0, help="--fake 시 자동 녹화 후 종료할 열화상 프레임 수")
    ap.add_argument("--headless", action="store_true", help="미리보기 창 없이 (--fake 시험용)")
    args = ap.parse_args()

    if args.list:
        list_devices(); return

    dev = lambda s: int(s) if str(s).isdigit() else s  # noqa: E731
    rw, rh = map(int, args.rgb_size.lower().split("x"))
    use_rgb = not args.no_rgb

    if args.fake:
        tcap, rcap = FakeThermal(), (FakeRGB(rw, rh) if use_rgb else None)
    else:
        tcap = open_thermal(dev(args.thermal), args.fps)
        if not tcap.isOpened():
            sys.exit(f"열화상 장치를 열 수 없습니다: {args.thermal} (--list 로 확인)")
        rcap = open_rgb(dev(args.rgb), rw, rh, 30) if use_rgb else None
        if use_rgb and not rcap.isOpened():
            sys.exit(f"RGB 장치를 열 수 없습니다: {args.rgb} (--list 로 확인, 없으면 --no-rgb)")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sess = os.path.join(args.out, f"{stamp}_{args.location}")
    os.makedirs(os.path.join(sess, "thermal"), exist_ok=True)
    if use_rgb:
        os.makedirs(os.path.join(sess, "rgb"), exist_ok=True)

    meta = {"created": stamp, "location": args.location, "note": args.note,
            "thermal": {"device": args.thermal, "size": [TH_W, TH_H], "format": "Y16 RAW16 → 16-bit PNG (무변환)",
                        "requested_fps": args.fps, "save_every": args.save_every,
                        "temp_hint": "Boson TLinear 고이득이면 °C = count/100 - 273.15 (설정 확인 필요)"},
            "rgb": {"device": args.rgb if use_rgb else None, "size": [rw, rh] if use_rgb else None},
            "scenes": SCENES, "fake": args.fake}

    tg = Grabber(tcap, "thermal"); tg.start()
    rg = Grabber(rcap, "rgb") if use_rgb else None
    if rg: rg.start()
    wr = Writer(); wr.start()

    fth = open(os.path.join(sess, "thermal_frames.csv"), "w", newline="")
    frg = open(os.path.join(sess, "rgb_frames.csv"), "w", newline="")
    fmk = open(os.path.join(sess, "markers.csv"), "w", newline="")
    cth, crg, cmk = csv.writer(fth), csv.writer(frg), csv.writer(fmk)
    cth.writerow(["file", "ts_ns", "scene", "segment", "min", "max", "frozen"])
    crg.writerow(["file", "ts_ns", "scene", "segment"])
    cmk.writerow(["ts_ns", "scene", "segment", "label"])

    scene, recording, segment = "general", False, 0
    last_t = last_r = None
    prev_raw = None
    n_th = n_rg = 0
    seen = 0
    if args.fake and args.frames:
        recording, segment = True, 1
    win = "dual capture (r:rec 1-9:scene m:marker q:quit)"
    print(f"저장 위치: {sess}")
    try:
        while True:
            if tg.error:
                print("열화상 오류:", tg.error); break
            t = tg.get()
            r = rg.get() if rg else None
            if t and (last_t is None or t[0] != last_t):
                last_t = t[0]; seen += 1
                raw = t[1]
                if recording and seen % args.save_every == 0:
                    frozen = int(prev_raw is not None and np.array_equal(raw, prev_raw))  # FFC 정지 구간 추정
                    fn = f"thermal/{n_th:06d}.png"
                    wr.put(os.path.join(sess, fn), raw, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                    cth.writerow([fn, t[0], scene, segment, int(raw.min()), int(raw.max()), frozen])
                    n_th += 1
                prev_raw = raw
            if r and (last_r is None or r[0] != last_r):
                last_r = r[0]
                if recording:
                    fn = f"rgb/{n_rg:06d}.jpg"
                    wr.put(os.path.join(sess, fn), r[1], [cv2.IMWRITE_JPEG_QUALITY, 92])
                    crg.writerow([fn, r[0], scene, segment])
                    n_rg += 1

            if args.fake and args.frames and n_th >= args.frames:
                break

            if not args.headless and t:
                left = agc_view(t[1], (640, 512))
                right = cv2.resize(r[1], (int(512 * rw / rh), 512)) if r else np.zeros((512, 640, 3), np.uint8)
                view = np.hstack([left, right])
                col = (0, 0, 255) if recording else (200, 200, 200)
                info = f"{'REC' if recording else 'STOP'} seg{segment} scene={scene} | T {n_th} fr ({tg.fps():.0f}fps)"
                if rg: info += f" | RGB {n_rg} fr ({rg.fps():.0f}fps)"
                if wr.dropped: info += f" | DROP {wr.dropped}"
                cv2.rectangle(view, (0, 0), (view.shape[1], 34), (0, 0, 0), -1)
                cv2.putText(view, info, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, col, 2)
                cv2.imshow(win, view)
                k = cv2.waitKey(1) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k == ord("r"):
                    recording = not recording
                    if recording: segment += 1
                    print(("녹화 시작" if recording else "녹화 정지"), f"(구간 {segment}, 장면 {scene})")
                if chr(k) in SCENES:
                    scene = SCENES[chr(k)]; print("장면 태그:", scene)
                if k == ord("m"):
                    cmk.writerow([time.time_ns(), scene, segment, "marker"]); print("마커 기록")
            else:
                time.sleep(0.002)
    finally:
        tg.running = False
        if rg: rg.running = False
        time.sleep(0.1)
        wr.q.put(None); wr.join()
        tcap.release()
        if rcap: rcap.release()
        for f in (fth, frg, fmk): f.close()
        if not args.headless:
            cv2.destroyAllWindows()
        pairs = make_pairs(sess) if use_rgb else 0
        meta.update({"thermal_saved": n_th, "rgb_saved": n_rg, "segments": segment,
                     "writer_dropped": wr.dropped, "pairs": pairs,
                     "thermal_fps_measured": round(tg.fps(), 1),
                     "rgb_fps_measured": round(rg.fps(), 1) if rg else None})
        with open(os.path.join(sess, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"완료: 열화상 {n_th}장, RGB {n_rg}장, 짝 {pairs}개, 저장 누락 {wr.dropped} → {sess}")


def make_pairs(sess, max_dt_ms=50):
    """열화상 프레임마다 시간이 가장 가까운 RGB 프레임을 짝지음."""
    def load(fn):
        with open(os.path.join(sess, fn), newline="") as f:
            return list(csv.DictReader(f))
    th, rg = load("thermal_frames.csv"), load("rgb_frames.csv")
    if not th or not rg:
        return 0
    rts = np.array([int(x["ts_ns"]) for x in rg])
    n = 0
    with open(os.path.join(sess, "pairs.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["thermal", "rgb", "dt_ms", "scene", "segment", "ok"])
        for x in th:
            t = int(x["ts_ns"]); i = int(np.argmin(np.abs(rts - t)))
            dt = (int(rts[i]) - t) / 1e6
            ok = int(abs(dt) <= max_dt_ms)
            w.writerow([x["file"], rg[i]["file"], f"{dt:.1f}", x["scene"], x["segment"], ok])
            n += ok
    return n


if __name__ == "__main__":
    main()
