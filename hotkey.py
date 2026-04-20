import logging
from typing import Callable

from pynput import keyboard

logger = logging.getLogger(__name__)


def start_hotkey_listener(callback: Callable[[], None], hotkey_str: str) -> None:
    """Start a blocking global hotkey listener. Returns only on KeyboardInterrupt."""
    logger.info("Registering global hotkey: %s", hotkey_str)
    with keyboard.GlobalHotKeys({hotkey_str: callback}) as listener:
        listener.join()
