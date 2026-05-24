#!/usr/bin/env python3
"""
test_display.py — Quick OLED display test.

Run this directly to check if the display is wired and working:
  python3 test_display.py
  # or with the venv:
  /opt/pz-nfc-reader/venv/bin/python test_display.py

What it does:
  1. Checks I2C bus for a device at 0x3C
  2. Initialises the SSD1306 display
  3. Shows a test pattern for 5 seconds, then clears
"""

import sys
import time

# ── 1. Check for luma.oled ────────────────────────────────────────────────────
try:
    from luma.core.interface.serial import i2c as luma_i2c
    from luma.oled.device import ssd1306
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    print(f"\n  ERROR: missing library — {e}")
    print("\n  luma.oled is not installed in this Python environment.")
    print("  Fix: run  sudo bash /opt/pz-nfc-reader/install.sh")
    print("  Then retry with:  /opt/pz-nfc-reader/venv/bin/python test_display.py\n")
    sys.exit(1)

# ── 2. Initialise display ─────────────────────────────────────────────────────
I2C_ADDRESS = 0x3C
I2C_PORT    = 1

print(f"Connecting to SSD1306 at I²C 0x{I2C_ADDRESS:02X} on bus {I2C_PORT}...")
try:
    serial = luma_i2c(port=I2C_PORT, address=I2C_ADDRESS)
    device = ssd1306(serial, width=128, height=64)
    print("  Display initialised OK.")
except Exception as e:
    print(f"\n  ERROR: could not open display — {e}")
    print("\n  Check:")
    print("    • I²C is enabled  (sudo raspi-config → Interface Options → I2C)")
    print("    • OLED VCC → Pin 17 (3.3V), GND → Pin 20, SDA → Pin 3, SCL → Pin 5")
    print("    • i2cdetect -y 1  shows 3c in the grid")
    sys.exit(1)

# ── 3. Render test screen ─────────────────────────────────────────────────────
font_paths = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
font = None
for p in font_paths:
    try:
        font = ImageFont.truetype(p, 14)
        break
    except OSError:
        continue
if font is None:
    font = ImageFont.load_default()

def render(lines, invert=False):
    img  = Image.new("1", (128, 64), "black")
    draw = ImageDraw.Draw(img)
    for text, y in lines:
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            w = bbox[2] - bbox[0]
        except AttributeError:
            w, _ = draw.textsize(text, font=font)
        x = max(0, (128 - w) // 2)
        draw.text((x, y), text, font=font, fill="white")
    if invert:
        img = img.point(lambda p: 255 - p)
    device.display(img)

print("Showing test pattern for 5 seconds...")
render([("PZTrack NFC", 8), ("Display OK", 30), ("Test passed", 48)])
time.sleep(5)

print("Showing inverted screen (ON WATER style)...")
render([("VESSEL NAME", 8), ("ON WATER", 30), ("Press to toggle", 48)], invert=True)
time.sleep(3)

device.clear()
print("\nTest complete — display is working correctly.\n")
