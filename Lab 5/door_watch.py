#!/usr/bin/env python3
"""
Door Watch, the overnight half.

Before bed: make a Quiet Coyote at the camera and hold it for 2 seconds.
The screen counts down, then goes dark. From then on the Pi takes one look
every few seconds, notes when someone arrives and leaves, and takes ONE photo
only if they stay longer than --snap-after seconds. Stop with Ctrl-C in the morning.

    tmux new -s night
    python door_watch.py                 # real night
    python door_watch.py --no-arm        # skip the gesture (for testing)
    python door_watch.py --minutes 1     # fake night: walk past for a minute

Same structure as Part E (yolo_to_qwen.py):
    look()      one frame -> YOLO -> how many people, best confidence
    interpret() reading -> "someone" / "nobody"   (coarse on purpose)
    watch()     only acts on CHANGES: arrival, departure, photo, log line
"""
import argparse
import json
import math
import os
import time
from datetime import datetime

import cv2
from ultralytics import YOLO

import pitft

LOG_TXT = "night_log.txt"      # human-readable, same style as watch_log.txt
LOG_JSON = "visits.json"       # what door_morning.py reads
SNAP_DIR = "snapshots"


# ------------------------------------------------------------------ camera + YOLO

class Eye:
    def __init__(self, model_path, conf):
        self.cap = cv2.VideoCapture(0)
        self.model = YOLO(model_path, task="detect")
        self.conf = conf

    def frame(self):
        # The webcam keeps a few old frames in a buffer. When we only look
        # every few seconds, throw those away so we see "now", not 5 s ago.
        for _ in range(4):
            self.cap.grab()
        ok, frame = self.cap.read()
        return frame if ok else None

    def look(self):
        """One look. Returns (frame, number of people, best confidence)."""
        frame = self.frame()
        if frame is None:
            return None, 0, 0.0
        r = self.model(frame, classes=[0], conf=self.conf, verbose=False)[0]
        confs = [float(c) for c in r.boxes.conf] if r.boxes is not None else []
        return frame, len(confs), max(confs, default=0.0)


def interpret(people):
    """Part E logged left/center/right and close/far, which made the log noisy.
    At a door the only thing that matters is: is someone there?"""
    return "someone" if people > 0 else "nobody"


# ------------------------------------------------------------------ bedtime gesture

def coyote_parts(lm):
    """Quiet Coyote: index and pinky up (the ears), middle and ring
    fingers touching the thumb (the snout). lm = 21 MediaPipe landmarks.
    Returns each check separately so --debug can show which one fails."""
    def d(a, b):
        return math.dist((lm[a].x, lm[a].y), (lm[b].x, lm[b].y))

    hand = d(0, 9) or 1e-6                      # wrist to middle knuckle = hand size
    return {
        "index_up": lm[8].y < lm[6].y,
        # The pinky gets dragged down when the ring finger bends, so only ask
        # for its tip to be above its base knuckle (17), not its middle joint.
        "pinky_up": lm[20].y < lm[17].y,
        "middle_to_thumb": round(d(12, 4) / hand, 2),   # needs < 0.45
        "ring_to_thumb": round(d(16, 4) / hand, 2),     # needs < 0.45
    }


def is_quiet_coyote(lm):
    p = coyote_parts(lm)
    return p["index_up"] and p["pinky_up"] and p["middle_to_thumb"] < 0.45 and p["ring_to_thumb"] < 0.45


def enter_pressed():
    """Backup for testing over ssh: pressing Enter in the terminal also arms it."""
    import select
    import sys
    if select.select([sys.stdin], [], [], 0)[0]:
        sys.stdin.readline()
        return True
    return False


def wait_for_coyote(eye, hold=2.0, debug=False):
    """Bedtime hand-off. Uses the camera at full speed for these few
    seconds only; the all-night sampling starts after."""
    import mediapipe as mp

    hands = mp.solutions.hands.Hands(max_num_hands=2, min_detection_confidence=0.6)
    print(f"PiTFT found: {pitft.HAVE_SCREEN}")
    print("Waiting for a quiet coyote (or press A, or Enter here)...", flush=True)
    last_print = last_ok = 0
    since = None
    pitft.light(True)
    pitft.show(pitft.card([("Night shift", 24, (235, 235, 245)),
                           ("show a quiet coyote", 16, (170, 170, 200)),
                           ("to start watching", 16, (170, 170, 200))]))
    since = None
    while True:
        frame = eye.frame()
        if frame is None:
            continue
        res = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        found = res.multi_hand_landmarks or []
        # Part C lesson: with two hands we can't tell whose command it is. Wait.
        ok = len(found) == 1 and is_quiet_coyote(found[0].landmark)
        if debug and time.time() - last_print > 0.7:
            last_print = time.time()
            if not found:
                print("  no hand seen", flush=True)
            elif len(found) > 1:
                print(f"  {len(found)} hands: waiting for just one", flush=True)
            else:
                print(f"  {'COYOTE' if ok else 'not yet'}  {coyote_parts(found[0].landmark)}", flush=True)
        if ok:
            last_ok = time.time()
            if since is None:
                print("Coyote seen, hold it...", flush=True)
            since = since or time.time()
            held = time.time() - since
            pitft.show(pitft.card([("Hold it...", 24, (235, 235, 245)),
                                   ("●" * int(held / hold * 5 + 1), 18, (120, 200, 160))]))
            if held >= hold:
                break
        elif since and time.time() - last_ok > 0.5:
            # MediaPipe drops the hand for a frame now and then; only start
            # over if the coyote has really been gone for half a second
            since = None
            pitft.show(pitft.card([("Night shift", 24, (235, 235, 245)),
                                   ("show a quiet coyote", 16, (170, 170, 200))]))
        if pitft.pressed("A") or enter_pressed():   # backups: button A or Enter
            print("Armed by button/Enter.")
            break
    print("Armed. Screen off in 10s.", flush=True)
    hands.close()


def countdown(seconds):
    for s in range(seconds, 0, -1):
        pitft.show(pitft.card([("Armed", 26, (120, 200, 160)),
                               (f"screen off in {s}", 16, (170, 170, 200))]))
        time.sleep(1)
    pitft.show(pitft.card([("", 10, (0, 0, 0))], bg=(0, 0, 0)))
    pitft.light(False)


# ------------------------------------------------------------------ the night

class Night:
    def __init__(self, every, snap_after, leave_after):
        self.every = every
        self.snap_after = snap_after
        self.leave_after = leave_after          # misses in a row before we call it "left"
        self.armed = datetime.now()
        self.visits = []
        self.current = None                     # the visit in progress
        self.misses = 0
        os.makedirs(SNAP_DIR, exist_ok=True)
        self.log(f"armed, one look every {every}s")
        self.save()

    def log(self, text, when=None):
        line = f"{(when or datetime.now()):%H:%M:%S} {text}"
        print(line, flush=True)
        with open(LOG_TXT, "a") as f:
            f.write(line + "\n")

    def save(self, ended=None):
        with open(LOG_JSON, "w") as f:
            json.dump({"armed": self.armed.isoformat(timespec="seconds"),
                       "ended": ended.isoformat(timespec="seconds") if ended else None,
                       "every": self.every,
                       "snap_after": self.snap_after,
                       "visits": self.visits}, f, indent=1)

    def update(self, frame, reading, conf):
        now = datetime.now()
        if reading == "someone":
            self.misses = 0
            if self.current is None:                       # arrival
                self.current = {"start": now, "last": now, "peak": conf, "photo": None}
                self.log(f"someone arrived ({conf:.2f})", now)
            c = self.current
            c["last"] = now
            c["peak"] = max(c["peak"], conf)
            stayed = (now - c["start"]).total_seconds()
            if c["photo"] is None and stayed >= self.snap_after:   # one photo, only if they stay
                name = f"{SNAP_DIR}/{now:%Y%m%d_%H%M%S}.jpg"
                cv2.imwrite(name, frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                c["photo"] = name
                self.log(f"still here after {stayed:.0f}s, took {name}", now)
        elif self.current is not None:
            self.misses += 1
            if self.misses >= self.leave_after:           # departure
                self.close()

    def close(self):
        c = self.current
        if c is None:
            return
        stayed = (c["last"] - c["start"]).total_seconds()
        self.visits.append({"start": c["start"].isoformat(timespec="seconds"),
                            "end": c["last"].isoformat(timespec="seconds"),
                            "seconds": round(stayed),
                            "peak_conf": round(c["peak"], 2),
                            "photo": c["photo"]})
        self.log(f"left after {stayed:.0f}s", c["last"])
        self.current, self.misses = None, 0
        self.save()                             # save after every visit, so a crash loses nothing


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="yolo26n_ncnn_model", help="the NCNN folder from Part A")
    p.add_argument("--conf", type=float, default=0.6,
                   help="Part B: 0.5 let a teddy-bear photo through, so start higher")
    p.add_argument("--every", type=float, default=5, help="seconds between looks (house rule: 5 to 30)")
    p.add_argument("--snap-after", type=float, default=10, help="photo only if someone stays this long")
    p.add_argument("--leave-after", type=int, default=2, help="empty looks in a row = they left")
    p.add_argument("--minutes", type=float, help="stop by itself after this long (fake night)")
    p.add_argument("--no-arm", action="store_true", help="skip the gesture")
    p.add_argument("--debug", action="store_true", help="print what MediaPipe sees while waiting")
    a = p.parse_args()

    for f in (LOG_TXT, LOG_JSON):               # every night starts clean
        if os.path.exists(f):
            os.remove(f)

    eye = Eye(a.model, a.conf)
    if not a.no_arm:
        wait_for_coyote(eye, debug=a.debug)
        countdown(10)                           # time to close the door and walk away
    night = Night(a.every, a.snap_after, a.leave_after)
    stop_at = time.time() + a.minutes * 60 if a.minutes else None

    by_button = False
    try:
        while not by_button and (stop_at is None or time.time() < stop_at):
            t = time.time()
            frame, people, conf = eye.look()
            if frame is not None:
                night.update(frame, interpret(people), conf)
            # wait for the next look, but keep an ear on button A (the morning hand-back)
            while time.time() - t < a.every:
                if pitft.pressed("A"):
                    by_button = True
                    break
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    if by_button and night.current is not None and \
            (datetime.now() - night.current["start"]).total_seconds() < 15:
        # That "visitor" is you, walking up to press A. Don't count it.
        night.log("ignored the last few seconds: that was you pressing A")
        night.current = None
    night.close()                               # someone still at the door when we stop
    end = datetime.now()
    night.log("stopped" + (" by button A" if by_button else ""))
    night.save(ended=end)
    eye.cap.release()
    pitft.light(True)
    pitft.show(pitft.card([("Good morning", 24, (235, 235, 245)),
                           ("reading the night...", 15, (170, 170, 200))]))
    if by_button:
        # Morning hand-back: go straight to the report on the screen.
        # Run it inside this program, because this program already owns the
        # screen; a second program asking for it gets "Device or resource busy".
        import sys
        import door_morning
        sys.argv = ["door_morning.py"]
        door_morning.main()
    else:
        print("\nGood morning. Now run: python door_morning.py")


if __name__ == "__main__":
    main()
