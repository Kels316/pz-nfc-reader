#!/usr/bin/env python3
"""
display_manager.py — SSD1306 OLED display driver for the NFC reader.

Uses luma.oled for hardware I/O and Pillow for rendering.
All text is horizontally centred on a 128×64 pixel display.

If the display cannot be initialised (wrong address, hardware absent, etc.)
every method becomes a no-op so the rest of the module keeps running.
"""

import logging

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Fallback font paths — the first one found is used.
_BOLD_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_REGULAR_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _load_font(paths: list[str], size: int) -> ImageFont.ImageFont:
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    logger.warning("TrueType fonts not found; using PIL default font.")
    return ImageFont.load_default()


class DisplayManager:
    """Manages a 128×64 SSD1306 OLED connected via I²C."""

    def __init__(
        self,
        i2c_address: int = 0x3C,
        width: int = 128,
        height: int = 64,
        i2c_port: int = 1,
    ):
        self.width = width
        self.height = height
        self.device = None

        try:
            from luma.core.interface.serial import i2c as luma_i2c
            from luma.oled.device import ssd1306

            serial = luma_i2c(port=i2c_port, address=i2c_address)
            self.device = ssd1306(serial, width=width, height=height)
            logger.info(
                "OLED display (SSD1306) initialised at I²C 0x%02X.", i2c_address
            )
        except Exception as exc:
            logger.warning(
                "OLED display could not be initialised (%s). "
                "Display output will be suppressed.",
                exc,
            )

        self.font_large = _load_font(_BOLD_FONT_PATHS, 14)
        self.font_small = _load_font(_REGULAR_FONT_PATHS, 11)

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------

    def _render(
        self,
        lines: list[tuple[str, ImageFont.ImageFont, int]],
        invert: bool = False,
    ) -> None:
        """
        Render text lines to the display.

        Each element of *lines* is (text, font, y_pixel).
        Text is horizontally centred.  Pass invert=True to swap black/white
        (used for "ON WATER" confirmation so it stands out visually).
        """
        if not self.device:
            return

        mode = self.device.mode  # typically "1" (1-bit)
        image = Image.new(mode, (self.width, self.height), "black")
        draw = ImageDraw.Draw(image)

        for text, font, y in lines:
            try:
                # Pillow ≥ 9.2
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
            except AttributeError:
                # Older Pillow
                text_width, _ = draw.textsize(text, font=font)
            x = max(0, (self.width - text_width) // 2)
            draw.text((x, y), text, font=font, fill="white")

        if invert:
            image = image.point(lambda p: 255 - p)

        self.device.display(image)

    # ------------------------------------------------------------------
    # Public screen states
    # ------------------------------------------------------------------

    def show_startup(self) -> None:
        """Shown briefly while the application is initialising."""
        self._render(
            [
                ("PZTrack NFC", self.font_large, 16),
                ("Starting up...", self.font_small, 38),
            ]
        )

    def show_ready(self) -> None:
        """Idle state — waiting for a tag scan."""
        self._render(
            [
                ("Ready", self.font_large, 8),
                ("Scan vessel tag", self.font_small, 32),
                ("to check in/out", self.font_small, 47),
            ]
        )

    def show_scanning(self, uid_hex: str) -> None:
        """Displayed immediately after a tag is detected, while the API is queried."""
        short = uid_hex.upper()[:8]  # show first 4 bytes
        self._render(
            [
                ("Tag Detected", self.font_large, 8),
                (short, self.font_small, 32),
                ("Looking up...", self.font_small, 47),
            ]
        )

    def show_current_status(self, craft_name: str, current_state: str | None) -> None:
        """
        Shown after a tag scan, while waiting for the button press.
        Displays the vessel name, its current state, and a prompt.
        """
        name = craft_name[:14] if len(craft_name) > 14 else craft_name
        if current_state == "checked_in":
            state_label = "ON WATER"
        elif current_state == "checked_out":
            state_label = "OFF WATER"
        else:
            state_label = "UNKNOWN"
        self._render(
            [
                (name, self.font_large, 4),
                (state_label, self.font_large, 24),
                ("Press to toggle", self.font_small, 50),
            ],
            invert=(current_state == "checked_in"),
        )

    def show_success(self, craft_name: str, new_state: str) -> None:
        """
        Confirmation screen after a successful state toggle.

        "ON WATER"  is shown inverted (white background) to make it
        immediately obvious that the vessel is now active on the water.
        "OFF WATER" is shown normally.
        """
        # Truncate to avoid overflow on a 128 px wide display at 14 pt
        name = craft_name[:14] if len(craft_name) > 14 else craft_name
        state_label = "ON WATER" if new_state == "checked_in" else "OFF WATER"
        self._render(
            [
                (name, self.font_large, 6),
                (state_label, self.font_large, 28),
                ("Updated OK", self.font_small, 50),
            ],
            invert=(new_state == "checked_in"),
        )

    def show_unknown_tag(self, uid_hex: str) -> None:
        """Tag UID has no matching t_rfid entry in the database."""
        short = uid_hex.upper()[:8]
        self._render(
            [
                ("Unknown Tag", self.font_large, 8),
                (short, self.font_small, 32),
                ("Not registered", self.font_small, 47),
            ]
        )

    def show_error(self, message: str) -> None:
        """
        Generic error screen (inverted so it grabs attention).
        Long messages are split across two lines.
        """
        lines: list[tuple[str, ImageFont.ImageFont, int]] = [
            ("ERROR", self.font_large, 4),
        ]
        if len(message) > 16:
            lines.append((message[:16].strip(), self.font_small, 28))
            lines.append((message[16:32].strip(), self.font_small, 42))
        else:
            lines.append((message, self.font_small, 34))
        self._render(lines, invert=True)

    def show_shutdown(self) -> None:
        """Shown briefly during a clean shutdown."""
        self._render(
            [
                ("Shutting down", self.font_large, 20),
            ]
        )

    def clear(self) -> None:
        """Blank the display."""
        if self.device:
            self.device.clear()
