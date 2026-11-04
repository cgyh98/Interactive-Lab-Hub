"""
Tiny helper for the Mini PiTFT (240x135) and its two buttons.

Pins follow the Lab 2 clock (screen_clock.py). If your Lab 2 code uses
different pins, change them here once and both scripts pick it up.

Without the screen attached (e.g. testing on a laptop), every call still
works: images are saved to pitft_preview.png and buttons read as "not pressed".
"""
import os

W, H = 240, 135

try:
    import board
    import digitalio
    from adafruit_rgb_display import st7789

    _disp = st7789.ST7789(
        board.SPI(),
        cs=digitalio.DigitalInOut(board.D5),
        dc=digitalio.DigitalInOut(board.D25),
        rst=None,
        baudrate=64000000,
        width=135, height=240, x_offset=53, y_offset=40,
    )
    _back = digitalio.DigitalInOut(board.D22)
    _back.switch_to_output()
    _back.value = True
    _btn = {}
    for name, pin in (("A", board.D23), ("B", board.D24)):
        b = digitalio.DigitalInOut(pin)
        b.switch_to_input()
        _btn[name] = b
    HAVE_SCREEN = True
except Exception as e:  # no screen, no Blinka, or not on a Pi
    HAVE_SCREEN = False
    print(f"[pitft] screen not available: {type(e).__name__}: {e}")


def show(img):
    """Show a 240x135 PIL image."""
    if HAVE_SCREEN:
        try:
            _disp.image(img.convert("RGB").resize((W, H)), 90)
        except Exception as e:
            print(f"[pitft] could not draw: {type(e).__name__}: {e}", flush=True)
    else:
        img.save("pitft_preview.png")


def light(on=True):
    """Backlight on/off. Off at night so the hallway stays dark."""
    if HAVE_SCREEN:
        _back.value = on


def pressed(name):
    """True while button 'A' (top) or 'B' (bottom) is held down."""
    return HAVE_SCREEN and not _btn[name].value


def font(size, bold=False):
    from PIL import ImageFont
    for path in (
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def card(lines, bg=(12, 14, 30)):
    """Quick text screen. lines = [(text, size, color), ...] top to bottom."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    total = sum(s + 6 for _, s, _ in lines) - 6
    y = (H - total) // 2
    for text, size, color in lines:
        d.text((W // 2, y), text, font=font(size, size >= 20), fill=color, anchor="ma")
        y += size + 6
    return img
