#!/usr/bin/env python3
"""
Door Watch, the morning half.

    python door_morning.py              # report + timeline image + PiTFT
    python door_morning.py --no-qwen    # skip Qwen, use the plain sentence
    python door_morning.py --delete     # house rule: delete the snapshots after the demo

The counting is done in code. Qwen only turns the counted facts into a
friendlier sentence, and its answer is thrown away if it mentions a time or
a number that isn't in the facts (Part D: a 0.8B model invents details).
"""
import argparse
import glob
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

import pitft

LOG_JSON = "visits.json"
SNAP_DIR = "snapshots"
REPORT = "night_report.png"


# ------------------------------------------------------------------ facts (code, not Qwen)

def load():
    with open(LOG_JSON) as f:
        night = json.load(f)
    night["armed"] = datetime.fromisoformat(night["armed"])
    crashed = night["ended"] is None
    if crashed:  # the watch died before Ctrl-C. Be honest about the gap.
        night["ended"] = datetime.fromtimestamp(os.path.getmtime(LOG_JSON))
    else:
        night["ended"] = datetime.fromisoformat(night["ended"])
    night["crashed"] = crashed
    for v in night["visits"]:
        v["start"] = datetime.fromisoformat(v["start"])
        v["end"] = datetime.fromisoformat(v["end"])
        v["stayed"] = v["seconds"] >= night["snap_after"]
    return night


def dur(s):
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}s" if s % 60 else f"{s // 60} min"


def facts(night):
    """Everything the morning says comes from here."""
    v = night["visits"]
    stayed = [x for x in v if x["stayed"]]
    f = {
        "watched_from": f"{night['armed']:%H:%M}",
        "watched_until": f"{night['ended']:%H:%M}",
        "visits": len(v),
        "passed_by": len(v) - len(stayed),
        "stayed": len(stayed),
        "stopped_early": night["crashed"],
    }
    if v:
        longest = max(v, key=lambda x: x["seconds"])
        f["longest_at"] = f"{longest['start']:%H:%M}"
        f["longest_for"] = dur(longest["seconds"])
        f["first_at"] = f"{v[0]['start']:%H:%M}"
    return f


def plain_sentence(f):
    window = f"{f['watched_from']} to {f['watched_until']}"
    if f["visits"] == 0:
        s = f"No one came to the door last night ({window})."
    else:
        # we can't tell people apart, so count visits, not people
        times = "once" if f["visits"] == 1 else f"{f['visits']} times"
        s = f"Someone came to the door {times} last night. "
        if f["stayed"] and f["passed_by"]:
            s += f"{f['passed_by']} just passed by, {f['stayed']} stayed. "
        elif f["stayed"]:
            s += "Each time, they stayed a while. " if f["stayed"] > 1 else ""
        else:
            s += "Nobody stopped. "
        if f["visits"] == 1:
            s += f"They stayed {f['longest_for']}, at {f['longest_at']}."
        else:
            s += f"The longest was {f['longest_for']} at {f['longest_at']}."
    if f["stopped_early"]:
        s += f" The watch stopped early, at {f['watched_until']}, so the rest of the night is unknown."
    return s.strip()


# ------------------------------------------------------------------ Qwen (phrasing only)

def ask_qwen(f, model, timeout=90):
    prompt = (
        "You are a door camera giving a morning report to the person who lives here. "
        "Write at most two short, calm sentences using ONLY these facts. "
        "Always say how many visits there were and the time of the longest one. "
        "Do not add any time, number, person or detail that is not listed. "
        "visits counts visits, not different people. If visits is 0, say plainly that nobody came.\n\n"
        + json.dumps(f)
    )
    body = json.dumps({"model": model, "stream": False, "think": False,
                       "messages": [{"role": "user", "content": prompt}],
                       "options": {"temperature": 0.2}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["message"]["content"].strip()


WORDS = {1: "once", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
         7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def trustworthy(text, f):
    """Reject the answer if it adds a time or number we didn't give it,
    or if it leaves out what matters (how many visits, when the longest was).
    Part D/E: a 0.8B model invents details AND blurs facts into vague phrases."""
    low = text.lower()
    if f["visits"] == 0:
        if not any(w in low for w in ("no one", "nobody", "no visit", "no-one")):
            return False
    else:
        n = f["visits"]
        # take the times out first: "21:52" contains a "1", which once fooled this check
        no_times = re.sub(r"\b\d{1,2}:\d{2}\b", " ", low)
        said = {int(x) for x in re.findall(r"\b\d+\b", no_times)}
        said |= {k for k, w in WORDS.items() if re.search(rf"\b{w}\b", no_times)}
        if re.search(r"\bone\b", no_times):
            said.add(1)
        if n not in said or f["longest_at"] not in text:
            return False
        # a different count word ("two" when there was one visit) is a wrong count
        wrong = {k for k, w in WORDS.items() if k != n and k != 1 and re.search(rf"\b{w}\b", no_times)}
        if wrong:
            return False
    if "hour" in low and "hours" not in str(f):   # e.g. "during the hour": made up
        return False
    if f["visits"] > 1:
        # "Two visits occurred at 21:40" makes it sound like every visit was then.
        # The longest one's time must sit in a sentence that says "longest".
        for sentence in re.split(r"[.!?]", text):
            if f["longest_at"] in sentence and "longest" not in sentence.lower():
                return False
    allowed_times = {v for v in f.values() if isinstance(v, str) and re.fullmatch(r"\d\d:\d\d", v)}
    for t in re.findall(r"\b\d{1,2}:\d{2}\b", text):
        if t.zfill(5) not in allowed_times:
            return False
    allowed_nums = {str(v) for v in f.values() if isinstance(v, int) and not isinstance(v, bool)}
    allowed_nums |= set(re.findall(r"\d+", " ".join(str(v) for v in f.values())))
    text_wo_times = re.sub(r"\b\d{1,2}:\d{2}\b", "", text)
    return all(n in allowed_nums for n in re.findall(r"\d+", text_wo_times))


# ------------------------------------------------------------------ timeline image

BG, FG, DIM = (10, 11, 22), (236, 234, 244), (125, 127, 156)
PASS, STAY = (110, 130, 200), (255, 176, 72)


def font(size, bold=False):
    return pitft.font(size, bold)


def wrap(d, text, fnt, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=fnt) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + [cur] if cur else lines


def thumb(path, size):
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        im = Image.new("RGB", size, (40, 40, 50))
    return ImageOps.fit(im, size)


def timeline(night, sentence, path=REPORT):
    W, H = 1600, 940
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    L, R, Y = 110, W - 110, 640
    t0, t1 = night["armed"], night["ended"]
    span = max(1, (t1 - t0).total_seconds())
    xt = lambda t: L + (R - L) * (t - t0).total_seconds() / span

    # header
    d.text((L, 64), "DOOR WATCH", font=font(22, True), fill=DIM)
    d.text((L, 94), f"{t0:%a %b %-d}, overnight", font=font(48), fill=FG)
    d.text((R, 104), f"{t0:%H:%M} \u2013 {t1:%H:%M}", font=font(28), fill=DIM, anchor="ra")
    d.text((R, 142), f"one look every {night['every']:g}s", font=font(20), fill=DIM, anchor="ra")

    # the night as a rail
    d.rounded_rectangle([L, Y - 3, R, Y + 3], radius=3, fill=(44, 46, 74))
    hr = t0.replace(minute=0, second=0) + timedelta(hours=1)
    step = 1 if span <= 14 * 3600 else 2
    while hr < t1:
        x = xt(hr)
        d.line([(x, Y + 16), (x, Y + 26)], fill=DIM, width=2)
        d.text((x, Y + 34), f"{hr:%H}:00", font=font(18), fill=DIM, anchor="ma")
        hr += timedelta(hours=step)
    if span < 3600:  # a short fake night: label the ends instead
        d.text((L, Y + 34), f"{t0:%H:%M:%S}", font=font(18), fill=DIM)
        d.text((R, Y + 34), f"{t1:%H:%M:%S}", font=font(18), fill=DIM, anchor="ra")

    visits = night["visits"]
    if not visits:
        d.text((W // 2, Y - 150), "No one came to the door.", font=font(44), fill=FG, anchor="mm")

    # each visit: a glowing mark, wider the longer they stayed
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for v in visits:
        x0, x1 = xt(v["start"]), xt(v["end"])
        x1 = max(x1, x0 + 6)
        col = STAY if v["stayed"] else PASS
        gd.rounded_rectangle([x0 - 4, Y - 16, x1 + 4, Y + 16], radius=10, fill=col)
        d.rounded_rectangle([x0, Y - 9, x1, Y + 9], radius=6, fill=col)
    img = ImageChops.screen(img, glow.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.55)))
    d = ImageDraw.Draw(img)
    for v in visits:  # redraw crisp marks on top of the glow
        x0, x1 = xt(v["start"]), max(xt(v["end"]), xt(v["start"]) + 6)
        d.rounded_rectangle([x0, Y - 9, x1, Y + 9], radius=6, fill=STAY if v["stayed"] else PASS)

    # photos for the visits that stayed (longest 5 if there are many), placed above in time order
    shots = sorted([v for v in visits if v.get("photo")], key=lambda v: -v["seconds"])[:5]
    shots.sort(key=lambda v: v["start"])
    if shots:
        tw, th, gap = 236, 177, 24
        total = len(shots) * tw + (len(shots) - 1) * gap
        x = max(L, min(R - total, int(sum(xt(v["start"]) for v in shots) / len(shots) - total / 2)))
        top = 215
        for v in shots:
            cx = x + tw // 2
            mx = xt(v["start"])
            d.line([(cx, top + th + 44), (cx, Y - 70), (mx, Y - 22)], fill=(90, 90, 120), width=2)
            mask = Image.new("L", (tw, th), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, tw, th], radius=14, fill=255)
            img.paste(thumb(v["photo"], (tw, th)), (x, top), mask)
            d.rounded_rectangle([x, top, x + tw, top + th], radius=14, outline=STAY, width=2)
            d.text((x, top + th + 12), f"{v['start']:%H:%M}", font=font(22, True), fill=FG)
            d.text((x + tw, top + th + 14), f"stayed {dur(v['seconds'])}", font=font(19),
                   fill=DIM, anchor="ra")
            x += tw + gap

    # legend
    lx = L
    for col, label in ((PASS, "passed by"), (STAY, f"stayed \u2265 {night['snap_after']:g}s, photo taken")):
        d.rounded_rectangle([lx, Y + 82, lx + 26, Y + 94], radius=5, fill=col)
        d.text((lx + 36, Y + 78), label, font=font(18), fill=DIM)
        lx += 70 + d.textlength(label, font=font(18))

    # the sentence
    f = font(28)
    for i, line in enumerate(wrap(d, sentence, f, W - 2 * L)):
        d.text((W // 2, H - 110 + i * 38), line, font=f, fill=FG, anchor="ma")
    img.save(path)
    return path


# ------------------------------------------------------------------ Mini PiTFT

def summary_card(night, f):
    if f["visits"] == 0:
        return pitft.card([("All quiet", 26, (235, 235, 245)),
                           ("no one came to the door", 15, (170, 170, 200)),
                           (f"{f['watched_from']} \u2013 {f['watched_until']}", 14, (120, 122, 150))])
    n = f["visits"]
    lines = [(f"{n} visit{'s' * (n != 1)}", 28, (255, 190, 100)),
             (f"longest {f['longest_for']} at {f['longest_at']}", 15, (235, 235, 245))]
    if any(v.get("photo") for v in night["visits"]):
        lines.append(("B: photos   A: back", 13, (120, 122, 150)))
    return pitft.card(lines)


def photo_card(v):
    img = thumb(v["photo"], (pitft.W, pitft.H))
    d = ImageDraw.Draw(img)
    d.rectangle([0, pitft.H - 24, pitft.W, pitft.H], fill=(0, 0, 0))
    d.text((6, pitft.H - 21), f"{v['start']:%H:%M}  stayed {dur(v['seconds'])}",
           font=font(15, True), fill=(255, 255, 255))
    return img


def browse(night, f, minutes=3):
    """Button B steps through the photos, A goes back to the summary."""
    shots = [v for v in night["visits"] if v.get("photo")]
    pitft.light(True)
    print("[screen] summary", flush=True)
    pitft.show(summary_card(night, f))
    if not (pitft.HAVE_SCREEN and shots):
        print("[screen] no photos to browse, done", flush=True)
        return
    print(f"[screen] B = next photo ({len(shots)}), A = summary. Ctrl-C to quit.", flush=True)
    i, end = -1, time.time() + minutes * 60
    try:
        while time.time() < end:
            if pitft.pressed("B"):
                i = (i + 1) % len(shots)
                print(f"[screen] photo {i + 1}/{len(shots)}", flush=True)
                pitft.show(photo_card(shots[i]))
                while pitft.pressed("B"):       # wait for release so one press = one photo
                    time.sleep(0.02)
            elif pitft.pressed("A"):
                i = -1
                print("[screen] summary", flush=True)
                pitft.show(summary_card(night, f))
                while pitft.pressed("A"):
                    time.sleep(0.02)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    print("[screen] done", flush=True)


# ------------------------------------------------------------------ main

def delete_snapshots():
    files = glob.glob(f"{SNAP_DIR}/*.jpg")
    for p in files:
        os.remove(p)
    print(f"Deleted {len(files)} snapshot(s).")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="qwen3.5:0.8b")
    p.add_argument("--no-qwen", action="store_true")
    p.add_argument("--delete", action="store_true", help="delete all snapshots and exit")
    a = p.parse_args()

    if a.delete:
        return delete_snapshots()

    night = load()
    f = facts(night)
    sentence = plain_sentence(f)
    print("Facts:", json.dumps(f))
    print("Plain:", sentence)

    if not a.no_qwen:
        try:
            t = time.time()
            q = ask_qwen(f, a.model)
            ok = trustworthy(q, f)
            print(f"Qwen ({time.time() - t:.1f}s): {q}")
            if ok:
                sentence = q
            else:
                print("  rejected: Qwen used a time or number that isn't in the facts")
        except Exception as e:
            print(f"Qwen unavailable ({e}); using the plain sentence")

    for v in night["visits"]:
        print(f"  {v['start']:%H:%M:%S}  {'stayed' if v['stayed'] else 'passed'}  "
              f"{dur(v['seconds']):>6}  conf {v['peak_conf']:.2f}  {v.get('photo') or ''}")
    print("Saved", timeline(night, sentence))
    browse(night, f)


if __name__ == "__main__":
    main()
