#!/usr/bin/env python3
"""
gossip.py: Raspberry Pis that gossip with each other over MQTT.

How it works
  * Conversation (MQTT): every Pi publishes a "hello" every 2 s listing the
    rumors it knows. When a friend is missing a rumor, it gets whispered to
    them on their own topic. A Pi that joins late gets caught up, the same way
    gossip spreads in real life (and how "gossip protocols" work in
    distributed systems).
  * Every Pi has a VCNL4040 proximity sensor, and how long something stays
    close decides what happens:
      - quick tap (hand in and out in under 1.5 s): the Pi comes up with a new
        rumor, which then spreads to the others.
      - something stays close (1.5 s or more): an eavesdropper! The Pi hisses
        "shh, someone's listening" and tells its friends. The whole group makes
        small talk until the person backs off.
  * Where rumors come from: a tap first uses the STARTER_RUMORS list below.
    Once this Pi has heard all of those, it asks the local Qwen model (Ollama)
    to make up a new one. Typing a line also starts a rumor.
  * Screen: each Pi's MiniPiTFT shows the latest thing it said, with a top bar
    colored by mood (green = gossiping, gray = alone, red = someone's
    listening). Stop the screen service first: sudo systemctl stop piscreen.service
  * Optional Bluetooth proximity (--ble): only Pis physically close to each
    other (by signal strength) count as "nearby" and gossip.

MQTT topics (broker farlab.infosci.cornell.edu, all under IDD/mamaya/gossip/)
  hello/<pi>    {"known": [rumor ids], "hush": bool}   every 2 s from every Pi
  rumor/<to>    {"from", "rid", "text", "origin"}     a rumor whispered to one Pi
  hush/<pi>     "1" / "0"                              eavesdropper seen / gone
  say/<pi>      "shh! someone's listening."            everything a Pi says, so all screens show
                                                       the whole conversation (rumor text is
                                                       hidden until the rumor reaches you)
  bye/<pi>      ""                                     sent by the broker if a Pi drops off

Run:   python3 gossip.py --calibrate               # print VCNL4040 readings to pick a threshold
       python3 gossip.py --listen-threshold 40     # normal run, with the threshold you picked
       python3 gossip.py --speak --voice en+f3     # also say lines out loud (needs espeak-ng)
       python3 gossip.py --no-sensor               # no VCNL4040 (e.g. testing on a laptop)
       python3 gossip.py --rumor "Pi #2 has a crush on the toaster"
       python3 gossip.py --ble                     # also use Bluetooth signal strength for "nearby"

While it's running, type a line and press Enter to start a new rumor.
"""

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

import paho.mqtt.client as mqtt

MQTT_BROKER = "farlab.infosci.cornell.edu"
MQTT_PORT = 1883
MQTT_USERNAME = "idd"
MQTT_PASSWORD = "device@theFarm"
BASE = os.environ.get("GOSSIP_TOPIC", "IDD/mamaya/gossip")  # change it to test without joining the real group

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")  # use whichever Qwen you pulled
OLLAMA_TIMEOUT = 90  # seconds; the first request is slow while the model loads

SERVICE_UUID ="6a1f0001-9c3e-4b7a-8f2d-60551905a1e0"
CHAR_UUID = "6a1f0002-9c3e-4b7a-8f2d-60551905a1e0"

HELLO_EVERY = 2.0  # seconds between "hello" messages
FORGET_AFTER = 8.0  # seconds without hearing from a peer => they've left
LISTENER_GONE_AFTER = 2.5  # seconds with nobody close before gossip resumes
HUSH_AFTER = 1.5  # something close this long = eavesdropper; shorter = a tap that starts a rumor
MIN_TAP = 0.1  # ignore single noisy readings shorter than this
LEFT_AFTER = 0.3  # below threshold this long = the hand/person has really moved away
CHATTER_CHANCE = 0.03  # chance per second of an idle reaction ("no way!"); was 0.08, lowered after user testing

STARTER_RUMORS = [
    "the router has been talking to someone else's laptop",
    "the kettle in the kitchen is secretly a smart kettle",
    "someone saw the printer jam on purpose",
    "the motion sensor by the door is faking it",
    "the old Pi in the drawer is coming back",
    "the smart fridge has been reading everyone's grocery lists",
    "the Wi-Fi only drops when the TA walks in",
    "the speaker has been practicing its singing at night",
    "the projector pretends to be broken on Mondays",
    "the thermostat and the window are not speaking anymore",
]

REACTIONS = ["no way!", "stop it.", "I KNEW it.", "who told you that?", "mm-hmm, mm-hmm.",
             "you didn't hear it from me."]
EAVESDROP_LINES = ["shh! someone's listening.", "psst, we have company.", "wait, wait. don't look now.",
                   "zip it, there's a human right here."]
NERVOUS_LINES = ["so... nice weather today.", "anyway, I love processing data.", "la la la.",
                 "we were just talking about firmware updates."]
RESUME_LINES = ["okay, they're gone. where was I?", "coast is clear.", "phew. anyway..."]


class GossipNode:
    def __init__(self, args):
        self.id = args.name
        self.ble = args.ble
        self.rssi_enter = args.rssi
        self.rssi_leave = args.rssi - 8  # hysteresis so friends don't flicker in and out
        self.voice = args.voice
        self.speak_enabled = bool(args.speak and shutil.which("espeak-ng"))
        if args.speak and not self.speak_enabled:
            print("(espeak-ng not found, so staying quiet. Install it with: sudo apt install espeak-ng)")

        self.screen = None
        if not args.no_screen:
            try:
                self.screen = Screen()
            except Exception as e:  # noqa: BLE001
                print(f"(no screen found ({e}), so lines only print here)")

        self.use_ollama = not args.no_ollama
        self.ollama_model = args.ollama_model
        self.generating = False  # True while waiting for Ollama to make up a rumor
        self.background = set()  # running background tasks (kept so they aren't garbage-collected)

        self.use_vcnl = not args.no_sensor
        self.listen_threshold = args.listen_threshold
        self.chatter = args.chatter
        self.someone_listening = False  # our own sensor sees a person close
        self.peer_hush = {}  # peer_id -> True if their sensor sees a person
        self.prev_peer_hush = False

        self.rumors = {}  # rumor_id -> {"text", "origin"}
        self.hello_seen = {}  # peer_id -> last time we heard from them
        self.rssi = {}  # peer_id -> (last_time, smoothed_rssi)
        self.together = set()  # peers currently considered "nearby"
        self.last_new_rumor = time.time()

        self.add_rumor(args.rumor or random.choice(STARTER_RUMORS), origin=self.id)

    # ---------------------------------------------------------------- rumors
    def add_rumor(self, text, origin, rumor_id=None):
        rumor_id = rumor_id or uuid.uuid4().hex[:8]
        if rumor_id in self.rumors:
            return False
        already_heard = any(r["text"] == text for r in self.rumors.values())
        self.rumors[rumor_id] = {"text": text, "origin": origin}  # remember the id so it isn't sent again
        if already_heard:
            return False  # same story, different teller: old news

        self.last_new_rumor = time.time()
        return True

    def start_rumor(self, text=None):
        """Start a new rumor: typed text, else one from the list, else one made up by Ollama."""
        if text:
            self.add_rumor(text, origin=self.id)
            self.say(f"ooh, I just heard {text}", public="*heard something juicy...*")
            return
        known = {r["text"] for r in self.rumors.values()}
        fresh = [t for t in STARTER_RUMORS if t not in known]
        if fresh:
            text = random.choice(fresh)
            self.add_rumor(text, origin=self.id)
            self.say(f"ooh, I just heard {text}", public="*heard something juicy...*")
        elif not self.use_ollama:
            self.say("I've run out of gossip! type one in for me.")
        elif not self.generating:  # ignore extra taps while the model is thinking
            self.generating = True  # set now, not inside the task, so the next tap sees it
            task = asyncio.get_running_loop().create_task(self.make_up_rumor())
            self.background.add(task)
            task.add_done_callback(self.background.discard)

    async def make_up_rumor(self):
        """Ask the local Qwen model for a brand-new rumor (runs in a thread so gossip keeps flowing)."""
        self.say("ooh wait, I'm hearing something...")
        known = [r["text"] for r in self.rumors.values()]
        try:
            text = await asyncio.to_thread(ask_ollama_for_rumor, known, self.ollama_model)
        except Exception as e:  # noqa: BLE001
            print(f"(Ollama didn't come up with a rumor: {e})", flush=True)
            text = None
        finally:
            self.generating = False
        if text and self.add_rumor(text, origin=self.id):
            self.say(f"ooh, I just heard {text}", public="*heard something juicy...*")
        else:
            self.say("hmm, never mind. I forgot what I heard.")

    def say(self, line, public=None):
        """Show (and speak) one of this Pi's lines, and share it on say/<me> so the others show it too.

        public: what the other Pis see instead (so rumor text stays a whisper until it
        actually reaches them), or False to keep the line to this Pi only.
        """
        self.show(self.id, line)
        if self.speak_enabled:
            subprocess.Popen(["espeak-ng", "-v", self.voice, line],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if public is not False and getattr(self, "client", None):
            self.publish(f"say/{self.id}", line if public is None else public)

    def show(self, speaker, line):
        """Print a line and put it on the screen. Other Pis' lines are prefixed with their name."""
        print(f"[{time.strftime('%H:%M:%S')}] {speaker}: {line}", flush=True)
        if self.screen:
            friends = len(self.together)
            header = f"{self.id} · {friends} friend{'' if friends == 1 else 's'}"
            mood = "hush" if self.is_quiet() else ("chat" if friends else "alone")
            text = line if speaker == self.id else f"{speaker}: {line}"
            try:
                self.screen.show(header, text, mood)
            except Exception as e:  # noqa: BLE001
                print(f"(screen error: {e})")

    def is_quiet(self):
        """True when anyone in the group has spotted an eavesdropper."""
        return self.someone_listening or any(self.peer_hush.get(p) for p in self.together)

    # ------------------------------------------------------------- proximity
    def is_near(self, peer):
        now = time.time()
        seen = self.hello_seen.get(peer)
        if not seen or now - seen > FORGET_AFTER:
            return False  # not online / went quiet
        if not self.ble:
            return True  # without Bluetooth, everyone online counts as nearby
        r = self.rssi.get(peer)
        if not r or now - r[0] > FORGET_AFTER:
            return False
        threshold = self.rssi_leave if peer in self.together else self.rssi_enter
        return r[1] >= threshold

    def on_advertisement(self, device, adv):
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if SERVICE_UUID not in uuids:
            return
        name = adv.local_name or device.name
        if not name or name == self.id:
            return
        prev = self.rssi.get(name)
        smoothed = adv.rssi if prev is None else 0.7 * prev[1] + 0.3 * adv.rssi
        self.rssi[name] = (time.time(), smoothed)

    async def start_ble(self):
        from bleak import BleakScanner
        from bless import BlessServer, GATTAttributePermissions, GATTCharacteristicProperties

        self.server = BlessServer(name=self.id, loop=asyncio.get_running_loop())
        await self.server.add_new_service(SERVICE_UUID)
        await self.server.add_new_characteristic(
            SERVICE_UUID, CHAR_UUID,
            GATTCharacteristicProperties.read, self.id.encode(),
            GATTAttributePermissions.readable,
        )
        await self.server.start()  # starts advertising
        self.scanner = BleakScanner(detection_callback=self.on_advertisement)
        await self.scanner.start()

    # ------------------------------------- tap = new rumor, linger = eavesdropper (VCNL4040)
    async def vcnl_loop(self, sensor):
        close_since = None  # when the current "something is close" started
        last_close = 0.0  # last time a reading was above the threshold
        while True:
            try:
                prox = await asyncio.to_thread(lambda: sensor.proximity)
            except OSError:
                await asyncio.sleep(0.5)  # loose cable / I2C hiccup, so try again
                continue
            now = time.time()
            # once someone is detected, they have to back off a bit further to count as gone
            threshold = self.listen_threshold * (0.6 if self.someone_listening else 1.0)

            if prox >= threshold:
                if close_since is None:
                    close_since = now
                last_close = now
                if not self.someone_listening and now - close_since >= HUSH_AFTER:
                    self.someone_listening = True
                    self.say(random.choice(EAVESDROP_LINES))
                    self.publish_hush()  # warn friends right away
            elif close_since is not None and now - last_close > LEFT_AFTER:
                # the hand/person moved away: if it was only a quick tap, start a rumor
                held = last_close - close_since
                if not self.someone_listening and MIN_TAP <= held < HUSH_AFTER:
                    self.start_rumor()
                close_since = None

            if self.someone_listening and now - last_close > LISTENER_GONE_AFTER:
                self.someone_listening = False
                self.say(random.choice(RESUME_LINES))
                self.publish_hush()
            await asyncio.sleep(0.1)

    # ------------------------------------------------------------------ MQTT
    def publish(self, subtopic, payload):
        self.client.publish(f"{BASE}/{subtopic}", payload)

    def send_hello(self):
        self.publish(f"hello/{self.id}", json.dumps({"known": list(self.rumors),
                                                      "hush": self.someone_listening}))

    def publish_hush(self):
        self.publish(f"hush/{self.id}", "1" if self.someone_listening else "0")

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(f"{BASE}/#")
            print(f"(connected to MQTT, listening on {BASE}/#)", flush=True)
        else:
            print(f"(MQTT connection refused, code {rc})", flush=True)

    def on_mqtt(self, topic, payload):
        """Runs on the asyncio loop for every message under IDD/mamaya/gossip/."""
        parts = topic.split("/")
        if len(parts) != 5:
            return
        kind, who = parts[3], parts[4]
        text = payload.decode(errors="replace")

        if kind == "hello" and who != self.id:
            try:
                msg = json.loads(text)
            except ValueError:
                return
            self.hello_seen[who] = time.time()
            self.peer_hush[who] = bool(msg.get("hush"))
            if self.is_near(who) and not self.is_quiet():
                missing = [rid for rid in self.rumors if rid not in set(msg.get("known", []))]
                if missing:
                    # one rumor per hello, so it trickles out like a conversation
                    rid = random.choice(missing)
                    self.publish(f"rumor/{who}", json.dumps({"from": self.id, "rid": rid, **self.rumors[rid]}))

        elif kind == "rumor" and who == self.id:
            try:
                msg = json.loads(text)
                peer, rid, rumor = msg["from"], msg["rid"], msg["text"]
            except (ValueError, KeyError, TypeError):
                return
            origin = msg.get("origin", peer)
            if not self.is_near(peer):
                return
            if peer not in self.together:  # say hi before gossiping
                self.say(f"oh hi {peer}! come here, I have to tell you something")
                self.together.add(peer)
            if self.add_rumor(rumor, origin, rid):
                source = "" if origin == peer else f" (they heard it from {origin})"
                self.say(f"psst, {peer} just told me {rumor}{source}", public=False)  # too chatty to share

        elif kind == "hush" and who != self.id:
            self.peer_hush[who] = text == "1"

        elif kind == "say" and who != self.id:
            self.show(who, text)  # a friend said something: show it here too (not spoken)

        elif kind == "bye" and who != self.id:
            self.hello_seen.pop(who, None)  # presence_loop will say goodbye

    async def hello_loop(self):
        while True:
            self.send_hello()
            await asyncio.sleep(HELLO_EVERY + random.uniform(-0.3, 0.3))

    async def presence_loop(self):
        while True:
            peers = set(self.hello_seen) | set(self.rssi)
            near_now = {p for p in peers if self.is_near(p)}
            arrived, left = sorted(near_now - self.together), sorted(self.together - near_now)
            self.together = near_now  # update first so the screen shows the right friend count
            for p in arrived:
                self.say(f"oh hi {p}! come here, I have to tell you something")
            for p in left:
                self.say(f"bye {p}... (don't tell anyone what I said)")

            # a friend spotted an eavesdropper, so act natural
            peer_hush_now = any(self.peer_hush.get(p) for p in self.together)
            if not self.someone_listening:
                if peer_hush_now and not self.prev_peer_hush:
                    self.say(random.choice(NERVOUS_LINES))
                elif self.prev_peer_hush and not peer_hush_now:
                    self.say(random.choice(RESUME_LINES))
            self.prev_peer_hush = peer_hush_now

            # idle chatter when everyone's caught up and nobody's listening
            if (self.together and not self.is_quiet()
                    and time.time() - self.last_new_rumor > 15 and random.random() < self.chatter):
                self.say(random.choice(REACTIONS))
            await asyncio.sleep(1)

    def on_stdin(self):
        line = sys.stdin.readline()
        if not line:  # end of input (no terminal), so stop listening for typed rumors
            asyncio.get_running_loop().remove_reader(sys.stdin)
            return
        line = line.strip()
        if line:
            self.start_rumor(line)

    async def run(self):
        loop = asyncio.get_running_loop()

        self.client = make_client(self.id)
        self.client.on_connect = self.on_connect
        # paho runs callbacks on its own thread, so hand each message to the asyncio loop
        self.client.on_message = lambda c, u, m: loop.call_soon_threadsafe(self.on_mqtt, m.topic, m.payload)
        # if this Pi drops off without saying goodbye, the broker tells the others
        self.client.will_set(f"{BASE}/bye/{self.id}", "")
        self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=10)
        self.client.loop_start()

        try:
            loop.add_reader(sys.stdin, self.on_stdin)
        except (ValueError, OSError):
            pass  # no terminal (e.g. running as a service)

        if self.ble:
            try:
                await self.start_ble()
            except Exception as e:  # noqa: BLE001
                print(f"Bluetooth didn't start ({e}). Everyone online counts as nearby.")
                self.ble = False

        tasks = [self.hello_loop(), self.presence_loop()]
        if self.use_vcnl:
            try:
                tasks.append(self.vcnl_loop(open_vcnl()))
            except Exception as e:  # noqa: BLE001
                print(f"VCNL4040 not found ({e}). Running without the sensor: "
                      "type a line and press Enter to start rumors.")
                self.use_vcnl = False

        mode = f"Bluetooth proximity, RSSI >= {self.rssi_enter} dBm" if self.ble else "everyone online is nearby"
        if self.use_vcnl:
            mode += f", tap the sensor for a rumor, threshold {self.listen_threshold}"
        self.say(f"awake ({mode}). I know {len(self.rumors)} rumor(s).")
        await asyncio.gather(*tasks)


def ask_ollama_for_rumor(known, model):
    """Ask the local model for one short, harmless rumor that isn't one we already know."""
    examples = random.sample(STARTER_RUMORS, 3)
    prompt = (
        "You are a Raspberry Pi gossiping with other gadgets. Make up ONE new, silly, harmless rumor "
        "about a household gadget, appliance or computer. Write it so it finishes the sentence "
        "'I just heard that ...'. Lowercase, under 15 words, no quotes, no real people.\n"
        f"Examples: {'; '.join(examples)}\n"
        f"Don't repeat any of these: {'; '.join(known[-8:])}\n"
        "Reply with only the rumor."
    )
    body = {"model": model, "stream": False, "options": {"temperature": 1.0},
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(OLLAMA_URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        reply = json.loads(resp.read())["message"]["content"]
    return clean_rumor(reply)


def clean_rumor(reply):
    """Small models add extras ("Rumor: ...", quotes, several lines). Keep just the rumor."""
    lines = [l.strip() for l in reply.splitlines() if l.strip()]
    if not lines:
        return None
    text = lines[0]
    text = re.sub(r"^(\d+[.)]|[-*•])\s*", "", text)  # list markers
    text = re.sub(r"^(rumou?r|gossip)\s*:\s*", "", text, flags=re.I)  # "Rumor:" labels
    text = re.sub(r"^(i (just )?heard( that)?|psst,?)\s*", "", text, flags=re.I)  # repeated lead-in
    text = text.strip(" \"'“”‘’.!")
    if not text:
        return None
    text = text[0].lower() + text[1:]
    if len(text) > 120:
        text = text[:120].rsplit(" ", 1)[0] + "..."
    return text


class Screen:
    """The Pi's 240x135 MiniPiTFT (same setup as pixel_grid_publisher.py).

    Top bar: this Pi's name and how many friends are around, colored by mood
    (green = gossiping, gray = alone, red = someone's listening).
    Below it: the latest thing this Pi said, as big as it fits.
    """
    W, H = 240, 135
    BAR = 26
    MOOD_COLORS = {"chat": (20, 120, 60), "alone": (80, 80, 90), "hush": (180, 25, 25)}

    def __init__(self):
        import adafruit_rgb_display.st7789 as st7789
        import board
        import digitalio
        from PIL import Image, ImageDraw, ImageFont
        self.Image, self.ImageDraw, self.ImageFont = Image, ImageDraw, ImageFont

        backlight = digitalio.DigitalInOut(board.D22)
        backlight.switch_to_output()
        backlight.value = True
        self.disp = st7789.ST7789(
            board.SPI(),
            cs=digitalio.DigitalInOut(board.D5),
            dc=digitalio.DigitalInOut(board.D25),
            rst=None,
            baudrate=64000000,
            width=135, height=240, x_offset=53, y_offset=40,
            rotation=90,
        )
        self.fonts = {}

    def font(self, size, bold=False):
        if (size, bold) not in self.fonts:
            path = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
            try:
                self.fonts[size, bold] = self.ImageFont.truetype(path, size)
            except OSError:
                self.fonts[size, bold] = self.ImageFont.load_default()
        return self.fonts[size, bold]

    @staticmethod
    def wrap(draw, text, font, width):
        lines, current = [], ""
        for word in text.split():
            trial = f"{current} {word}".strip()
            if not current or draw.textlength(trial, font=font) <= width:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def show(self, header, line, mood):
        img = self.Image.new("RGB", (self.W, self.H), (15, 15, 25))
        draw = self.ImageDraw.Draw(img)
        draw.rectangle((0, 0, self.W, self.BAR), fill=self.MOOD_COLORS.get(mood, (80, 80, 90)))
        draw.text((6, 4), header, font=self.font(16, bold=True), fill="white")

        # use the biggest font that fits the whole line
        room = self.H - self.BAR - 6
        for size in (24, 20, 17, 14, 12):
            font = self.font(size)
            lines = self.wrap(draw, line, font, self.W - 12)
            line_h = size + 4
            if len(lines) * line_h <= room:
                break
        y = self.BAR + 4
        for text in lines:
            if y + line_h > self.H:
                break
            draw.text((6, y), text, font=font, fill="white")
            y += line_h
        self.disp.image(img)


def make_client(name):
    client_id = f"gossip-{name}-{uuid.uuid4().hex[:6]}"
    try:  # paho-mqtt 2.x
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:  # paho-mqtt 1.x
        client = mqtt.Client(client_id=client_id)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    return client


def open_vcnl():
    import adafruit_vcnl4040
    import board
    return adafruit_vcnl4040.VCNL4040(board.I2C())


def calibrate():
    """Print live readings so you can pick --listen-threshold for your space."""
    sensor = open_vcnl()
    print("Move your face/hand toward the sensor. Ctrl+C to stop.")
    print("Hold your hand about 5 cm away and note the number. Use a value a bit below it as --listen-threshold")
    print("(it should be well above the 'nobody there' value).")
    while True:
        prox = sensor.proximity
        print(f"proximity {prox:6d}  {'#' * min(prox // 5, 60):60s}  lux {sensor.lux:7.1f}", flush=True)
        time.sleep(0.2)


def main():
    p = argparse.ArgumentParser(description="Raspberry Pis that gossip over MQTT.")
    p.add_argument("--name", default=socket.gethostname(), help="this Pi's name (default: hostname)")
    p.add_argument("--rumor", help="the rumor this Pi starts out knowing")
    p.add_argument("--no-sensor", action="store_true", help="run without a VCNL4040 (type rumors instead)")
    p.add_argument("--no-screen", action="store_true", help="don't use the MiniPiTFT screen")
    p.add_argument("--no-ollama", action="store_true",
                   help="never ask the local model for new rumors (only use the built-in list)")
    p.add_argument("--ollama-model", default=OLLAMA_MODEL,
                   help=f"Ollama model for new rumors (default {OLLAMA_MODEL}, or set OLLAMA_MODEL)")
    p.add_argument("--listen-threshold", type=int, default=30,
                   help="VCNL4040 proximity reading that counts as 'something is close' (see --calibrate)")
    p.add_argument("--chatter", type=float, default=CHATTER_CHANCE,
                   help=f"chance per second of an idle reaction (default {CHATTER_CHANCE}; the original was 0.08)")
    p.add_argument("--calibrate", action="store_true", help="print VCNL4040 readings and exit")
    p.add_argument("--speak", action="store_true", help="say lines out loud with espeak-ng")
    p.add_argument("--voice", default="en", help="espeak-ng voice, e.g. en+f3, en+m2, es")
    p.add_argument("--ble", action="store_true", help="only gossip with Pis that are physically close (Bluetooth)")
    p.add_argument("--rssi", type=int, default=-65, help="with --ble: how close counts as 'nearby' (closer to 0 = closer)")
    args = p.parse_args()
    if "/" in args.name or "+" in args.name or "#" in args.name:
        p.error("--name can't contain '/', '+' or '#' (they have special meaning in MQTT topics)")
    try:
        if args.calibrate:
            calibrate()
        else:
            asyncio.run(GossipNode(args).run())
    except KeyboardInterrupt:
        print("\n(gossip over)")


if __name__ == "__main__":
    main()