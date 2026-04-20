import base64
import io
import logging
from dataclasses import dataclass

import mss
from PIL import Image

from config import MAX_AI_WIDTH, MAX_DISPLAY_WIDTH, MONITOR_INDEX

logger = logging.getLogger(__name__)


@dataclass
class ScreenCapture:
    raw_png: bytes       # Resized PNG for AI analysis (capped at MAX_AI_WIDTH)
    display_b64: str     # Compressed JPEG base64 for browser display
    width: int
    height: int


def capture_screenshot(monitor_index: int = MONITOR_INDEX) -> ScreenCapture:
    """Capture the screen. Returns capped PNG for AI and compressed JPEG for display."""
    with mss.mss() as sct:
        monitors = sct.monitors
        idx = monitor_index if monitor_index < len(monitors) else 1
        shot = sct.grab(monitors[idx])

        # BGRA → RGBA → RGB (shot.bgra is already bytes-like; no copy needed)
        img = Image.frombytes(
            "RGBA", (shot.width, shot.height), shot.bgra, "raw", "BGRA"
        ).convert("RGB")

        # Downscaled PNG for AI (cap at MAX_AI_WIDTH to stay within API limits)
        ai_img = img
        if img.width > MAX_AI_WIDTH:
            ratio = MAX_AI_WIDTH / img.width
            ai_img = img.resize(
                (MAX_AI_WIDTH, int(img.height * ratio)), Image.LANCZOS
            )
        png_buf = io.BytesIO()
        ai_img.save(png_buf, format="PNG")
        raw_png = png_buf.getvalue()

        # Downscaled JPEG for WebSocket transmission
        display_img = img
        if img.width > MAX_DISPLAY_WIDTH:
            ratio = MAX_DISPLAY_WIDTH / img.width
            display_img = img.resize(
                (MAX_DISPLAY_WIDTH, int(img.height * ratio)), Image.LANCZOS
            )
        jpeg_buf = io.BytesIO()
        display_img.save(jpeg_buf, format="JPEG", quality=82, optimize=True)
        display_b64 = base64.b64encode(jpeg_buf.getvalue()).decode()

        logger.info(
            "Captured %dx%d — ai_png=%dKB display=%dKB",
            img.width, img.height,
            len(raw_png) // 1024,
            len(jpeg_buf.getvalue()) // 1024,
        )
        return ScreenCapture(raw_png=raw_png, display_b64=display_b64,
                             width=img.width, height=img.height)
