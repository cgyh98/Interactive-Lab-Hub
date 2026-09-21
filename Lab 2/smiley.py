import time
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

# Turn on the backlight
backlight = digitalio.DigitalInOut(board.D22)
backlight.switch_to_output()
backlight.value = True

FACE_COLOR = (242, 201, 76)
FACE_RADIUS = 45
INK = (0, 0, 0)

cx = width // 2
cy = height // 2

# The weekend face from jail.py, on its own.
draw.rectangle((0, 0, width, height), outline=0, fill=(0, 0, 0))

draw.ellipse(
    (cx - FACE_RADIUS, cy - FACE_RADIUS, cx + FACE_RADIUS, cy + FACE_RADIUS),
    fill=FACE_COLOR,
)

for eye_x in (cx - 16, cx + 16):
    draw.ellipse((eye_x - 5, cy - 18, eye_x + 5, cy - 8), fill=INK)

# PIL angles run clockwise from 3 o'clock, so 20-160 is the lower half of the
# arc box and curves up at the ends into a smile.
draw.arc((cx - 24, cy - 2, cx + 24, cy + 30), 20, 160, fill=INK, width=4)

disp.image(image, rotation)

# Nothing changes, so just hold the picture on screen until ctrl-c.
while True:
    time.sleep(1)
