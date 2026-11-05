#!/usr/bin/env python3
"""
gossip.py: Raspberry Pis that gossip when they're near each other.

How it works
  * Proximity (Bluetooth LE): every Pi advertises a shared "gossip" service and
    scans for the others. Signal strength (RSSI) decides who counts as "nearby".
  * Conversation (Wi-Fi/UDP): every Pi broadcasts a small "hello" listing the
    rumors it knows. When a nearby friend is missing a rumor, it gets whispered
    to them. When a third Pi walks up, whoever's there catches it up, the same
    way gossip spreads in real life (and how "gossip protocols" work in
    distributed systems).
  * Eavesdropper detector (optional VCNL4040): if a person leans in close to a
    Pi that has the sensor, it hisses "shh, someone's listening" and tells its
    friends. The whole group stops gossiping until the person backs off.

All Pis must be on the same Wi-Fi network (a phone hotspot works well).

Run:   python3 gossip.py                   # BLE proximity + Wi-Fi chat
       python3 gossip.py --wifi-only       # no Bluetooth, anyone on the network counts as nearby
       python3 gossip.py --speak           # also say things out loud (needs espeak-ng)
       python3 gossip.py --vcnl            # this Pi has the VCNL4040 eavesdropper sensor
       python3 gossip.py --calibrate       # just print VCNL4040 readings, to pick a threshold
       python3 gossip.py --rumor "Pi #2 has a crush on the toaster"

While it's running, type a line and press Enter to start a new rumor.
"""

import argparse
import asyncio
import json
import random
import shutil
import socket
import subprocess
import sys
import time
import uuid

PORT = 50505
SERVICE_UUID = "6a1f0001-9c3e-4b7a-8f2d-60551905a1e0"
CHAR_UUID = "6a1f0002-9c3e-4b7a-8f2d-60551905a1e0"

HELLO_EVERY = 2.0  # seconds between "hello" broadcasts
FORGET_AFTER = 8.0  # seconds without hearing from a peer => they've left
LISTENER_GONE_AFTER = 2.5  # seconds with nobody close before gossip resumes

STARTER_RUMORS = [
    "the router has been talking to someone else's laptop",
    "the kettle in the kitchen is secretly a smart kettle",
    "someone saw the printer jam on purpose",
    "the motion sensor by the door is faking it",
    "the old Pi in the drawer is coming back",
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
        self.ble = not args.wifi_only
        self.rssi_enter = args.rssi
        self.rssi_leave = args.rssi - 8  # hysteresis so friends don't flicker in and out
        self.voice = args.voice
        self.speak_enabled = bool(args.speak and shutil.which("espeak-ng"))
        if args.speak and not self.speak_enabled:
            print("(espeak-ng not found, so staying quiet. Install it with: sudo apt install espeak-ng)")

        self.use_vcnl = args.vcnl
        self.listen_threshold = args.listen_threshold
        self.someone_listening = False  # our own sensor sees a person close
        self.peer_hush = {}  # peer_id -> True if their sensor sees a person
        self.prev_peer_hush = False

        self.rumors = {}  # rumor_id -> {"text", "origin"}
        self.hello_seen = {}  # peer_id -> (last_time, ip)
        self.rssi = {}  # peer_id -> (last_time, smoothed_rssi)
        self.together = set()  # peers currently considered "nearby"
        self.last_new_rumor = time.time()

        self.add_rumor(args.rumor or random.choice(STARTER_RUMORS), origin=self.id)

    # ---------------------------------------------------------------- rumors
    def add_rumor(self, text, origin, rumor_id=None):
        rumor_id = rumor_id or uuid.uuid4().hex[:8]
        if rumor_id in self.rumors:
            return False
        self.rumors[rumor_id] = {"text": text, "origin": origin}
        self.last_new_rumor = time.time()
        return True

    def say(self, line):
        print(f"[{time.strftime('%H:%M:%S')}] {self.id}: {line}", flush=True)
        if self.speak_enabled:
            subprocess.Popen(["espeak-ng", "-v", self.voice, line],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def is_quiet(self):
        """True when anyone in the group has spotted an eavesdropper."""
        return self.someone_listening or any(self.peer_hush.get(p) for p in self.together)

    # ------------------------------------------------------------- proximity
    def is_near(self, peer):
        now = time.time()
        seen = self.hello_seen.get(peer)
        if not seen or now - seen[0] > FORGET_AFTER:
            return False  # not on the network / went quiet
        if not self.ble:
            return True
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

    # ------------------------------------------------- eavesdropper (VCNL4040)
    async def sensor_loop(self, sensor):
        last_close = 0.0
        close_streak = 0
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
                close_streak += 1
                last_close = now
            else:
                close_streak = 0

            if not self.someone_listening and close_streak >= 2:
                self.someone_listening = True
                self.say(random.choice(EAVESDROP_LINES))
                self.send_hello()  # warn friends right away
            elif self.someone_listening and now - last_close > LISTENER_GONE_AFTER:
                self.someone_listening = False
                self.say(random.choice(RESUME_LINES))
                self.send_hello()
            await asyncio.sleep(0.2)

    # ---------------------------------------------------------------- network
    def send(self, msg, addr):
        self.sock.sendto(json.dumps(msg).encode(), (addr, PORT))

    def send_hello(self):
        self.send({"type": "hello", "id": self.id, "known": list(self.rumors),
                   "hush": self.someone_listening}, "255.255.255.255")

    def on_datagram(self, data, addr):
        try:
            msg = json.loads(data.decode())
        except (ValueError, UnicodeDecodeError):
            return
        peer = msg.get("id")
        if not peer or peer == self.id:
            return
        ip = addr[0]
        self.hello_seen[peer] = (time.time(), ip)

        if msg.get("type") == "hello":
            self.peer_hush[peer] = bool(msg.get("hush"))
            if self.is_near(peer) and not self.is_quiet():
                missing = [rid for rid in self.rumors if rid not in set(msg.get("known", []))]
                if missing:
                    # one rumor per hello, so it trickles out like a conversation
                    rid = random.choice(missing)
                    self.send({"type": "rumor", "id": self.id, "rid": rid, **self.rumors[rid]}, ip)

        elif msg.get("type") == "rumor" and self.is_near(peer):
            if self.add_rumor(msg["text"], msg.get("origin", peer), msg["rid"]):
                source = "" if msg.get("origin") == peer else f" (they heard it from {msg.get('origin')})"
                self.say(f"psst, {peer} just told me {msg['text']}{source}")

    async def hello_loop(self):
        while True:
            self.send_hello()
            await asyncio.sleep(HELLO_EVERY + random.uniform(-0.3, 0.3))

    async def presence_loop(self):
        while True:
            peers = set(self.hello_seen) | set(self.rssi)
            near_now = {p for p in peers if self.is_near(p)}
            for p in sorted(near_now - self.together):
                self.say(f"oh hi {p}! come here, I have to tell you something")
            for p in sorted(self.together - near_now):
                self.say(f"bye {p}... (don't tell anyone what I said)")
            self.together = near_now

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
                    and time.time() - self.last_new_rumor > 15 and random.random() < 0.08):
                self.say(random.choice(REACTIONS))
            await asyncio.sleep(1)

    def on_stdin(self):
        line = sys.stdin.readline().strip()
        if line:
            self.add_rumor(line, origin=self.id)
            self.say(f"(new rumor ready to share: {line})")

    async def run(self):
        loop = asyncio.get_running_loop()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", PORT))
        self.sock.setblocking(False)
        loop.add_reader(self.sock, lambda: self.on_datagram(*self.sock.recvfrom(4096)))
        try:
            loop.add_reader(sys.stdin, self.on_stdin)
        except (ValueError, OSError):
            pass  # no terminal (e.g. running as a service)

        if self.ble:
            try:
                await self.start_ble()
            except Exception as e:  # noqa: BLE001
                print(f"Bluetooth didn't start ({e}). Falling back to Wi-Fi only.")
                self.ble = False

        tasks = [self.hello_loop(), self.presence_loop()]
        if self.use_vcnl:
            try:
                tasks.append(self.sensor_loop(open_vcnl()))
            except Exception as e:  # noqa: BLE001
                print(f"VCNL4040 not found ({e}). Running without the eavesdropper detector.")
                self.use_vcnl = False

        mode = f"BLE proximity, RSSI >= {self.rssi_enter} dBm" if self.ble else "Wi-Fi only"
        if self.use_vcnl:
            mode += ", watching for eavesdroppers"
        self.say(f"awake ({mode}). I know {len(self.rumors)} rumor(s).")
        await asyncio.gather(*tasks)


def open_vcnl():
    import adafruit_vcnl4040
    import board
    return adafruit_vcnl4040.VCNL4040(board.I2C())


def calibrate():
    """Print live readings so you can pick --listen-threshold for your space."""
    sensor = open_vcnl()
    print("Move your face/hand toward the sensor. Ctrl+C to stop.")
    print("Pick a threshold a bit above the 'nobody there' value. Use the distance you want to count as leaning in.")
    while True:
        prox = sensor.proximity
        print(f"proximity {prox:6d}  {'#' * min(prox // 5, 60):60s}  lux {sensor.lux:7.1f}", flush=True)
        time.sleep(0.2)


def main():
    p = argparse.ArgumentParser(description="Raspberry Pis that gossip when nearby.")
    p.add_argument("--name", default=socket.gethostname(), help="this Pi's name (default: hostname)")
    p.add_argument("--rumor", help="the rumor this Pi starts out knowing")
    p.add_argument("--rssi", type=int, default=-65, help="how close counts as 'nearby' (closer to 0 = closer)")
    p.add_argument("--wifi-only", action="store_true", help="skip Bluetooth; anyone on the network is nearby")
    p.add_argument("--speak", action="store_true", help="say lines out loud with espeak-ng")
    p.add_argument("--voice", default="en", help="espeak-ng voice, e.g. en+f3, en+m2, es")
    p.add_argument("--vcnl", action="store_true", help="use a VCNL4040 on this Pi as the eavesdropper detector")
    p.add_argument("--listen-threshold", type=int, default=30,
                   help="VCNL4040 proximity reading that counts as 'someone is listening' (see --calibrate)")
    p.add_argument("--calibrate", action="store_true", help="print VCNL4040 readings and exit")
    args = p.parse_args()
    try:
        if args.calibrate:
            calibrate()
        else:
            asyncio.run(GossipNode(args).run())
    except KeyboardInterrupt:
        print("\n(gossip over)")


if __name__ == "__main__":
    main()
