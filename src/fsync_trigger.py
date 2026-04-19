"""
PWM-based external trigger (fsync) manager for Nori cameras.

Drives a hardware PWM output to generate synchronisation pulses that
trigger both cameras simultaneously.  The default configuration uses
pwmchip3/pwm0 at 27 Hz with a 100 us pulse width (matching the RK3588
Nori board).  Pass *pwm_chip* / *pwm_channel* to the constructor to
target a different PWM on another board.
"""

import os
import time

from loguru import logger


# Defaults match RK3588 Nori board — override via constructor args.
DEFAULT_PWM_CHIP = "pwmchip3"
DEFAULT_PWM_CHANNEL = 0


def _write_sysfs(path: str, value: str) -> None:
    with open(path, "w") as f:
        f.write(value)


class FsyncTrigger:
    """Manage a PWM-based frame-sync trigger signal."""

    def __init__(
        self,
        fps: int = 27,
        pwm_chip: str = DEFAULT_PWM_CHIP,
        pwm_channel: int = DEFAULT_PWM_CHANNEL,
    ):
        self._fps = fps
        self._running = False
        self._chip = f"/sys/class/pwm/{pwm_chip}"
        self._channel = f"{self._chip}/pwm{pwm_channel}"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def fps(self) -> int:
        return self._fps

    def start(self, fps: int | None = None) -> bool:
        """Export the PWM channel (if needed) and start pulsing.

        Returns True on success, False if sysfs writes fail.
        """
        if fps is not None:
            self._fps = fps

        period_ns = int(1e9 / self._fps)
        duty_ns = 100_000  # 100 us pulse width

        try:
            if not os.path.exists(self._channel):
                _write_sysfs(self._chip + "/export", "0")
                time.sleep(0.1)

            _write_sysfs(self._channel + "/period", str(period_ns))
            _write_sysfs(self._channel + "/duty_cycle", str(duty_ns))
            _write_sysfs(self._channel + "/enable", "1")

            self._running = True
            logger.success(
                "FSYNC started: {} Hz (period={} ns, duty={} ns)",
                self._fps, period_ns, duty_ns,
            )
            return True

        except PermissionError as exc:
            logger.error("Failed to start FSYNC: {}", exc)
            logger.error(
                "Permission denied accessing PWM sysfs. "
                "Run 'sudo ./nori/setup_pwm.sh' to configure "
                "udev rules and systemd service for non-root PWM access."
            )
            self._running = False
            return False
        except OSError as exc:
            logger.error("Failed to start FSYNC: {}", exc)
            self._running = False
            return False

    def stop(self) -> None:
        """Disable the PWM output."""
        try:
            if os.path.exists(self._channel):
                _write_sysfs(self._channel + "/enable", "0")
                logger.info("FSYNC stopped")
        except OSError as exc:
            logger.warning("Failed to stop FSYNC cleanly: {}", exc)
        finally:
            self._running = False
