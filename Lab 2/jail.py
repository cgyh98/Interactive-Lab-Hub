import time
from datetime import datetime
import digitalio
import board
from PIL import Image, ImageDraw
import adafruit_rgb_display.st7789 as st7789

# Configuration for CS and DC pins (these are FeatherWing defaults on M0/M4):
cs_pin = digitalio.DigitalInOut(board.D5)
dc_pin = digitalio.DigitalInOut(board.D25)
reset_pin = None

# Config for display baudrate (default max is 24mhz):
BAUDRATE = 64000000

# Setup SPI bus using hardware SPI:
spi = board.SPI()

# Create the ST7789 display:
disp = st7789.ST7789(
    spi,
    cs=cs_pin,
    dc=dc_pin,
    rst=reset_pin,
    baudrate=BAUDRATE,
    width=135,
    height=240,
    x_offset=53,
    y_offset=40,
)

# Create blank image for drawing.
# Make sure to create image with mode 'RGB' for full color.
height = disp.width  # we swap height/width to rotate it to landscape!
width = disp.height
image = Image.new("RGB", (width, height))
rotation = 90

# Get drawing object to draw on image.
draw = ImageDraw.Draw(image)

# Draw a black filled box to clear the image.
draw.rectangle((0, 0, width, height), outline=0, fill=(0, 0, 0))
disp.image(image, rotation)

# Turn on the backlight
backlight = digitalio.DigitalInOut(board.D22)
backlight.switch_to_output()
backlight.value = True

# Set to a datetime to freeze the display on one moment (handy for filming a
# state you would otherwise have to wait for); None uses the real clock.
FAKE_NOW = None

# One bar per weekday. All five stand at Monday 00:00 and one falls as each
# weekday ends, so the last one drops at Saturday 00:00 and stays down until
# the week starts over.
BAR_COUNT = 5
BAR_WIDTH = 12
BAR_COLOR = (150, 150, 150)

FACE_COLOR = (242, 201, 76)
FACE_RADIUS = 45
INK = (0, 0, 0)

cx = width // 2
cy = height // 2


def draw_face(happy):
    draw.ellipse(
        (cx - FACE_RADIUS, cy - FACE_RADIUS, cx + FACE_RADIUS, cy + FACE_RADIUS),
        fill=FACE_COLOR,
    )

    for eye_x in (cx - 16, cx + 16):
        draw.ellipse((eye_x - 5, cy - 18, eye_x + 5, cy - 8), fill=INK)

    # PIL angles run clockwise from 3 o'clock, so the lower half of the arc
    # box (20-160) curves up at the ends and the upper half (200-340) curves
    # down at the ends.
    mouth = (cx - 24, cy - 2, cx + 24, cy + 30)
    if happy:
        draw.arc(mouth, 20, 160, fill=INK, width=4)
    else:
        draw.arc((cx - 24, cy + 8, cx + 24, cy + 40), 200, 340, fill=INK, width=4)


def draw_bars(now):
    # Bar i belongs to weekday i (0 = Monday). Days already past are gone, days
    # still ahead are full height, and today's bar drains from the top as the
    # day goes by -- at noon on Wednesday, bar 2 is half gone. On Saturday and
    # Sunday weekday is 5 or 6, so every bar has been used up.
    weekday = now.weekday()
    into_today = now.hour * 3600 + now.minute * 60 + now.second
    left_of_today = 1 - into_today / 86400

    spacing = (width - BAR_WIDTH) / (BAR_COUNT - 1)
    for i in range(BAR_COUNT):
        if i < weekday:
            continue
        remaining = left_of_today if i == weekday else 1
        left = i * spacing
        draw.rectangle(
            (left, height - height * remaining, left + BAR_WIDTH, height),
            fill=BAR_COLOR,
        )


while True:
    now = FAKE_NOW if FAKE_NOW else datetime.now()
    weekday = now.weekday()  # Monday is 0, Sunday is 6
    free = weekday >= BAR_COUNT

    draw.rectangle((0, 0, width, height), outline=0, fill=(0, 0, 0))
    draw_face(happy=free)
    draw_bars(now)

    disp.image(image, rotation)
    time.sleep(1)
