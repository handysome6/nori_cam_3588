#!/usr/bin/env python3
"""Single-camera launcher using Nori SDK source (norisrc) on RK3588.

Uses the Nori Xvision camera SDK via the ``norisrc`` GStreamer element
instead of the kernel v4l2/UVC driver.  The downstream decode pipeline
is identical to the v4l2src variant:

  norisrc -> jpegparse -> mppjpegdec (HW decode + resize) -> xvimagesink
"""

import argparse
import shutil
import subprocess
import sys
from fractions import Fraction
from typing import Dict, List, Optional, Sequence

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from gst_common import preview_dims_for, run_preview  # noqa: E402

# ---------------------------------------------------------------------------
# Default capture mode — Nori Xvision 20MP sensor
# ---------------------------------------------------------------------------
DEFAULT_WIDTH = 5120
DEFAULT_HEIGHT = 3840
# NOTE: norisrc get_caps truncates the SDK's float fps to int, so 27.5 fps
# becomes 27/1 in GstCaps.  We omit framerate from the caps filter by default
# and let GStreamer negotiate from the element's advertised modes.


def parse_fps(value: str) -> Fraction:
    """Parse '27', '27.5', or '55/2' into a Fraction."""
    if "/" in value:
        num, den = value.split("/", 1)
        return Fraction(int(num), int(den))
    return Fraction(value).limit_denominator(1000)


NORI_CTL_COLUMNS = ("#", "VID:PID", "Product", "Serial", "Bus:Dev", "Location", "Tag")


def list_nori_cameras() -> List[Dict[str, str]]:
    """Return camera rows parsed from ``nori-ctl list``.

    Each row is a dict with the keys in :data:`NORI_CTL_COLUMNS` plus an
    ``index`` integer. Column boundaries are derived from the header line
    since Product names may contain spaces.
    """
    if shutil.which("nori-ctl") is None:
        raise SystemExit(
            "'nori-ctl' not found on PATH. Install the latest gst-nori package."
        )
    result = subprocess.run(
        ["nori-ctl", "list"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(
            f"'nori-ctl list' failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return []

    header = lines[0]
    positions = []
    for col in NORI_CTL_COLUMNS:
        idx = header.find(col)
        if idx < 0:
            raise SystemExit(
                f"Unexpected 'nori-ctl list' format: missing column '{col}'."
            )
        positions.append(idx)
    positions.append(None)  # sentinel: read to end of line

    cameras: List[Dict[str, str]] = []
    for line in lines[1:]:
        row: Dict[str, str] = {}
        for i, col in enumerate(NORI_CTL_COLUMNS):
            start = positions[i]
            end = positions[i + 1]
            row[col] = line[start:end].strip() if end is not None else line[start:].strip()
        try:
            row["index"] = int(row["#"])
        except ValueError:
            continue
        cameras.append(row)
    return cameras


def find_camera_by_role(cameras: List[Dict[str, str]], role: str) -> Optional[Dict[str, str]]:
    """Return the first camera whose Tag matches *role* (case-insensitive)."""
    target = role.strip().casefold()
    for cam in cameras:
        if cam.get("Tag", "").casefold() == target:
            return cam
    return None


def format_camera_row(cam: Dict[str, str]) -> str:
    """One-line human-readable summary of a camera row."""
    tag = cam.get("Tag") or "(untagged)"
    return (
        f"device-index {cam['index']}  "
        f"tag={tag}  "
        f"product='{cam.get('Product', '')}'  "
        f"loc={cam.get('Location', '')}"
    )


def select_camera(cameras: List[Dict[str, str]]) -> Dict[str, str]:
    """Let the user interactively pick a camera row."""
    if not cameras:
        raise SystemExit("No Nori cameras detected.")

    if len(cameras) == 1:
        print(f"One camera detected: {format_camera_row(cameras[0])}", flush=True)
        return cameras[0]

    print("Available Nori cameras:")
    for i, cam in enumerate(cameras, start=1):
        print(f"  [{i}] {format_camera_row(cam)}")

    if not sys.stdin.isatty():
        raise SystemExit(
            "Interactive camera selection requires a terminal. "
            "Pass --device-index or --role explicitly."
        )

    prompt = f"Select a camera [1-{len(cameras)}] (default 1): "
    while True:
        selection = input(prompt).strip()
        if not selection:
            return cameras[0]
        if selection.isdigit():
            sel = int(selection)
            if 1 <= sel <= len(cameras):
                return cameras[sel - 1]
        print(
            f"Invalid selection. Enter a number between 1 and {len(cameras)}.",
            file=sys.stderr,
        )


def build_pipeline_desc(
    width: int,
    height: int,
    fps: Optional[Fraction],
    sink: str,
    device_index: Optional[int] = None,
    role: Optional[str] = None,
    trigger_mode: Optional[str] = None,
    sensor_shutter: Optional[int] = None,
    sensor_gain: Optional[int] = None,
) -> str:
    """Build a GStreamer pipeline string for norisrc on RK3588.

    Exactly one of *device_index* or *role* should be provided; *role*
    takes precedence on the element itself when both are set.
    """
    if role is None and device_index is None:
        raise ValueError("build_pipeline_desc requires device_index or role")

    src_parts = ["norisrc", "auto-exposure=true"]
    if role is not None:
        src_parts.append(f'role="{role}"')
    if device_index is not None:
        src_parts.append(f"device-index={device_index}")
    if trigger_mode is not None:
        src_parts.append(f"trigger-mode={trigger_mode}")
    if sensor_shutter is not None:
        src_parts.append(f"sensor-shutter={sensor_shutter}")
    if sensor_gain is not None:
        src_parts.append(f"sensor-gain={sensor_gain}")
    src = " ".join(src_parts)

    # Caps filter — framerate included only when explicitly requested
    caps = f"image/jpeg,width={width},height={height}"
    if fps is not None:
        limited = fps.limit_denominator(1000)
        caps += f",framerate={limited.numerator}/{limited.denominator}"

    preview_w, preview_h = preview_dims_for(width, height)

    return (
        f"{src} "
        f"! {caps} "
        f"! jpegparse name=parser "
        f"! mppjpegdec width={preview_w} height={preview_h} format=NV12 "
        f"! {sink} sync=false"
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a Nori SDK camera preview pipeline on RK3588.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=None,
        help="Nori camera index. Omit to auto-detect and select interactively.",
    )
    parser.add_argument(
        "--role",
        type=str,
        default=None,
        help="Select camera by NORICAM tag (e.g. LEFT, RIGHT). "
        "Tag cameras first with `nori-ctl tag set <idx> <role>`. "
        "Takes precedence over --device-index.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List detected Nori cameras (with tags) and exit.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help=f"Capture width (default: {DEFAULT_WIDTH}).",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help=f"Capture height (default: {DEFAULT_HEIGHT}).",
    )
    parser.add_argument(
        "--fps",
        type=str,
        default=None,
        help="Framerate as integer, decimal, or fraction e.g. '30', '27.5', '55/2'. "
        "Omitted by default (auto-negotiated from SDK).",
    )
    parser.add_argument(
        "--trigger-mode",
        choices=["none", "software", "hardware", "command"],
        default=None,
        help="Camera trigger mode (default: free-running).",
    )
    parser.add_argument(
        "--hardware-trigger",
        action="store_true",
        help="Shorthand for --trigger-mode hardware.",
    )
    parser.add_argument(
        "--sensor-shutter",
        type=int,
        default=None,
        help="Sensor shutter / exposure time in microseconds.",
    )
    parser.add_argument(
        "--sensor-gain",
        type=int,
        default=None,
        help="Sensor analogue gain multiplier.",
    )
    parser.add_argument(
        "--sink",
        default="xvimagesink",
        help="GStreamer video sink element (default: xvimagesink).",
    )
    parser.add_argument(
        "--no-check-soi",
        action="store_true",
        help="Disable SOI (0xFFD8) check at frame start.",
    )
    parser.add_argument(
        "--no-check-eoi",
        action="store_true",
        help="Disable EOI (0xFFD9) check at frame end.",
    )
    parser.add_argument(
        "--no-check-walk",
        action="store_true",
        default=True,
        help="Disable marker segment walk (disabled by default; use --check-walk to enable).",
    )
    parser.add_argument(
        "--check-walk",
        action="store_true",
        help="Enable marker segment walk (header structure validation).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv[1:])

    Gst.init(None)

    if not Gst.ElementFactory.find("norisrc"):
        raise SystemExit(
            "GStreamer element 'norisrc' not found. Is gst-nori installed?"
        )

    # Resolve --hardware-trigger shorthand
    trigger_mode = args.trigger_mode
    if args.hardware_trigger:
        if trigger_mode is not None and trigger_mode != "hardware":
            raise SystemExit(
                "--hardware-trigger conflicts with "
                f"--trigger-mode {trigger_mode}"
            )
        trigger_mode = "hardware"

    # --list: print cameras and exit
    if args.list:
        cameras = list_nori_cameras()
        if not cameras:
            print("No Nori cameras detected.")
            return 0
        print("Available Nori cameras:")
        for cam in cameras:
            print(f"  {format_camera_row(cam)}")
        return 0

    if args.role is not None and args.device_index is not None:
        raise SystemExit("--role and --device-index are mutually exclusive.")

    role: Optional[str] = args.role
    device_index: Optional[int] = args.device_index

    if role is not None:
        # Resolve role->index for display only; norisrc itself also resolves it.
        cameras = list_nori_cameras()
        match = find_camera_by_role(cameras, role)
        if match is None:
            tags = sorted({c.get("Tag") for c in cameras if c.get("Tag")})
            tag_list = ", ".join(tags) if tags else "(none)"
            raise SystemExit(
                f"No Nori camera with tag '{role}' found. "
                f"Known tags: {tag_list}. "
                f"Use `nori-ctl tag set <idx> {role}` to assign."
            )
        resolved_index = match["index"]
    elif device_index is None:
        chosen = select_camera(list_nori_cameras())
        device_index = chosen["index"]
        resolved_index = device_index
    else:
        resolved_index = device_index

    fps = parse_fps(args.fps) if args.fps else None

    if role is not None:
        print(
            f"Role: {role} (device-index {resolved_index})",
            flush=True,
        )
    else:
        print(f"Device index: {resolved_index}", flush=True)
    fps_display = f" @ {float(fps):.1f} fps" if fps else " (fps auto)"
    print(
        f"Capture mode: {args.width}x{args.height}{fps_display}",
        flush=True,
    )
    if trigger_mode:
        print(f"Trigger mode: {trigger_mode}", flush=True)

    pipeline_desc = build_pipeline_desc(
        width=args.width,
        height=args.height,
        fps=fps,
        sink=args.sink,
        device_index=device_index,
        role=role,
        trigger_mode=trigger_mode,
        sensor_shutter=args.sensor_shutter,
        sensor_gain=args.sensor_gain,
    )

    return run_preview(
        pipeline_desc,
        check_soi=not args.no_check_soi,
        check_eoi=not args.no_check_eoi,
        check_walk=args.check_walk,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except KeyboardInterrupt:
        raise SystemExit(130)
