"""
Entry point for the Nori dual-camera preview/capture application on RK3588.

Two Nori Xvision cameras are driven via the ``norisrc`` GStreamer element
in hardware-trigger mode.  A single PWM (pwmchip3/pwm0) fans its pulse
train out to both cameras' trigger inputs so that every frame pair is
captured synchronously.

Usage:
    python main.py [--device-indices auto|0,1] [--no-overlay] [--trigger-fps N]

Flags:
    --device-indices IDS   Comma-separated Nori device indices, or 'auto' (default: auto)
    --no-overlay           Force appsink-to-QImage fallback (skips VideoOverlay)
    --trigger-fps N        PWM frequency in Hz for the hardware fsync (default: 27)
    --pts-filename         Include buffer PTS in captured filenames
"""

import os
import signal
import sys
import argparse
from pathlib import Path

# Force Qt to use X11 (xcb) backend — xvimagesink requires X11 window IDs,
# which are unavailable under the native Wayland Qt backend.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst
from loguru import logger

from PySide6.QtWidgets import QApplication

from camera_config import CameraConfig, CameraSettings, load_camera_config
from camera_pipeline import detect_nori_cameras, is_rk3588
from dual_camera_manager import DualCameraManager
from main_window import MainWindow


SRC_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SRC_DIR / "camera_config.yaml"
DEFAULT_LOCAL_CONFIG = SRC_DIR / "camera_config.local.yaml"


def _resolve_indices_by_role(
    cameras, left_role: str, right_role: str,
) -> list[int] | None:
    """Return [left_idx, right_idx] matched by nori-ctl tag, or None if not resolvable.

    Falls back to None (caller uses enumeration order) when either role
    isn't found or matches nothing — mis-matching would silently assign
    the wrong physical camera to each slot.
    """
    by_tag: dict[str, int] = {}
    for c in cameras:
        if c.tag:
            by_tag.setdefault(c.tag, c.index)
    left_idx = by_tag.get(left_role)
    right_idx = by_tag.get(right_role)
    if left_idx is None or right_idx is None or left_idx == right_idx:
        return None
    return [left_idx, right_idx]


def _setup_signals(app: QApplication) -> None:
    """Route SIGINT/SIGTERM through Qt's event loop so closeEvent runs cleanly."""
    def _handler(signum, _frame):
        logger.info("Signal {} received — quitting", signal.Signals(signum).name)
        app.quit()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _parse_indices(spec: str) -> list[int]:
    """Parse a '0,1' style string into a list of ints."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise SystemExit(f"Invalid device index: {part!r}")
    return out


def main():
    Gst.init(None)
    logger.info("GStreamer initialized")

    if not Gst.ElementFactory.find("norisrc"):
        logger.error("GStreamer element 'norisrc' not found. Is gst-nori installed?")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Nori dual-camera preview/capture (RK3588)")
    parser.add_argument(
        "--device-indices",
        default="auto",
        help="Comma-separated Nori device indices, or 'auto' to scan (default: auto)",
    )
    parser.add_argument(
        "--no-overlay",
        action="store_true",
        help="Force appsink-to-QImage preview instead of VideoOverlay",
    )
    parser.add_argument(
        "--trigger-fps",
        type=int,
        default=27,
        help="Hardware fsync PWM frequency in Hz (default: 27)",
    )
    parser.add_argument(
        "--pts-filename",
        action="store_true",
        help="Include GStreamer buffer PTS in captured filenames for sync debugging",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to base camera config YAML (default: {DEFAULT_CONFIG.name})",
    )
    parser.add_argument(
        "--local-config",
        type=Path,
        default=DEFAULT_LOCAL_CONFIG,
        help=f"Path to local override YAML, optional (default: {DEFAULT_LOCAL_CONFIG.name})",
    )
    args = parser.parse_args()

    cfg: CameraConfig = load_camera_config(args.config, args.local_config)

    # Resolve device indices.  Prefer config-driven role→tag matching; fall
    # back to USB enumeration order when tags are missing.
    if args.device_indices == "auto":
        cameras = detect_nori_cameras()
        if not cameras:
            logger.error(
                "No Nori cameras found. Check SDK installation or pass "
                "--device-indices 0,1 explicitly."
            )
            sys.exit(1)
        for c in cameras:
            logger.info(
                "  idx={} tag={} product='{}' loc={}",
                c.index, c.tag or "(untagged)", c.product, c.location,
            )
        resolved = _resolve_indices_by_role(cameras, cfg.left.role, cfg.right.role)
        if resolved is not None:
            indices = resolved
            logger.info(
                "Camera mapping by role: left={}→idx{} right={}→idx{}",
                cfg.left.role, indices[0], cfg.right.role, indices[1],
            )
        else:
            indices = [c.index for c in cameras][:2]
            logger.warning(
                "Could not resolve both roles ({}/{}) from camera tags — "
                "falling back to USB enumeration order {}. "
                "Assign tags with `nori-ctl tag set <idx> LEFT|RIGHT`.",
                cfg.left.role, cfg.right.role, indices,
            )
    else:
        indices = _parse_indices(args.device_indices)
        logger.info(
            "Explicit --device-indices override: {} (left={}, right={})",
            indices, cfg.left.role, cfg.right.role,
        )

    # VideoOverlay is the preferred path on RK3588; on dev machines fall back
    use_overlay = is_rk3588() and not args.no_overlay
    logger.info(
        "Config | device-indices={} preview={} trigger-fps={}",
        indices,
        "VideoOverlay" if use_overlay else "appsink fallback",
        args.trigger_fps,
    )

    app = QApplication(sys.argv)
    _setup_signals(app)

    manager = DualCameraManager(
        device_indices=indices,
        use_overlay=use_overlay,
        trigger_fps=args.trigger_fps,
        camera_settings=[cfg.left, cfg.right],
    )

    logger.info("Launching MainWindow")
    window = MainWindow(manager=manager, pts_filename=args.pts_filename)

    # Safety net: also stop pipelines on any quit path that bypasses closeEvent
    app.aboutToQuit.connect(manager.stop)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
