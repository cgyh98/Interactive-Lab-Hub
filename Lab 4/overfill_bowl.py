import sys
import time
import qwiic_proximity
import pi_servo_hat

# Overfill Bowl
# A Qwiic proximity sensor mounted above the bowl, facing down, watches
# how high the food is piled. Every time the bowl becomes overfilled,
# the servo performs its reaction once. It re-arms after the food level
# drops back down (someone ate or removed some food).
#
# Run with --calibrate to just print sensor readings:
#   python overfill_bowl.py --calibrate
#
# Run with --throw to do the throw once without using the sensor:
#   python overfill_bowl.py --throw

SERVO_CH = 0
SERVO_HOME = 60
# Flick: wind up to one end, then snap to the other end at full servo
# speed to throw the food. The library maps 0-90 to 1-2 ms pulses, but 9g
# servos accept about 0.5-2.5 ms, so roughly -45 to 135 is reachable. If the
# servo buzzes or strains at an end, move that angle back toward 60.
WINDUP_ANGLE = 130
FLICK_ANGLE = -45
WINDUP_PAUSE = 0.3     # seconds to settle at the wind-up before flicking
FLICK_HOLD = 0.4       # seconds to stay at the end of the flick
FLICK_REPEATS = 2

# Used only for the slow wind-up and return moves.
SERVO_STEP = 3
STEP_DELAY = 0.02

# Calibrate these for your own bowl and sensor height.
# Higher proximity values mean the food is closer to the sensor.
OVERFILL_THRESHOLD = 96    # normal bowl ~88, overfilled ~100
RESET_THRESHOLD = 92       # reading must drop below this to re-arm

# The reading must stay past a threshold this long before it counts,
# so a hand or spoon passing under the sensor does not trigger it.
HOLD_TIME = 1.0
POLL_DELAY = 0.05


servo = None


def setup_servo():
    global servo
    servo = pi_servo_hat.PiServoHat()
    servo.restart()
    servo.move_servo_position(SERVO_CH, SERVO_HOME)


def sweep(start, end):
    step = SERVO_STEP if end > start else -SERVO_STEP
    for angle in range(start, end, step):
        servo.move_servo_position(SERVO_CH, angle)
        time.sleep(STEP_DELAY)


def react():
    position = SERVO_HOME
    for _ in range(FLICK_REPEATS):
        sweep(position, WINDUP_ANGLE)
        servo.move_servo_position(SERVO_CH, WINDUP_ANGLE)
        time.sleep(WINDUP_PAUSE)
        # One jump instead of small steps, so the servo moves at full speed.
        servo.move_servo_position(SERVO_CH, FLICK_ANGLE)
        time.sleep(FLICK_HOLD)
        position = FLICK_ANGLE
    sweep(position, SERVO_HOME)
    servo.move_servo_position(SERVO_CH, SERVO_HOME)


if "--throw" in sys.argv:
    setup_servo()
    print("Force throw!")
    react()
    sys.exit(0)


proximity_sensor = qwiic_proximity.QwiicProximity()

if not proximity_sensor.connected:
    raise RuntimeError(
        "Qwiic proximity sensor not found. Check the Qwiic connection."
    )

proximity_sensor.begin()

if "--calibrate" in sys.argv:
    print("Calibration mode: compare readings with a normal and an overfilled bowl.")
    try:
        while True:
            print(f"Proximity: {proximity_sensor.get_proximity()}   ", end="\r")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()
        sys.exit(0)


setup_servo()

print("Overfill Bowl ready!")
print(f"Overfill threshold: {OVERFILL_THRESHOLD}, reset threshold: {RESET_THRESHOLD}")

overfilled = False
crossed_since = None

try:
    while True:
        proximity = proximity_sensor.get_proximity()
        print(f"Proximity: {proximity}   ", end="\r")

        if not overfilled:
            crossing = proximity >= OVERFILL_THRESHOLD
        else:
            crossing = proximity < RESET_THRESHOLD

        if not crossing:
            crossed_since = None
        elif crossed_since is None:
            crossed_since = time.monotonic()
        elif time.monotonic() - crossed_since >= HOLD_TIME:
            crossed_since = None
            overfilled = not overfilled
            if overfilled:
                print(f"\nBowl overfilled! Proximity = {proximity}")
                react()
            else:
                print(f"\nFood level back to normal. Proximity = {proximity}")

        time.sleep(POLL_DELAY)

except KeyboardInterrupt:
    print("\nStopping Overfill Bowl.")
    servo.move_servo_position(SERVO_CH, SERVO_HOME)
