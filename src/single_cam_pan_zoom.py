"""Single-camera live preview with pipeline-driven pan/zoom.

Mouse wheel and click-drag in the preview widget mutate a ``videocrop``
crop region in real time, so zooming pulls fresh pixels from the native
5120x3840 MJPEG stream rather than upscaling a pre-decoded preview.

Pipeline:

    norisrc -> jpegparse -> mppjpegdec (HW decode, optional HW downscale)
            -> videocrop name=crop  (live-mutable in PLAYING)
            -> videoscale -> NV12 preview_w x preview_h
            -> videoconvert -> RGB
            -> appsink (emits QImage)

The widget pins the crop's aspect ratio to the source aspect so videoscale
never distorts.  Crop properties (top/bottom/left/right) are documented as
mutable in PLAYING state by ``gst-inspect-1.0 videocrop``.

Usage:

    python single_cam_pan_zoom.py                    # interactive picker / first cam
    python single_cam_pan_zoom.py --device-index 0
    python single_cam_pan_zoom.py --role LEFT
    python single_cam_pan_zoom.py --decode-height 1920   # lower memory pressure
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from pathlib import Path

# xvimagesink is not used here, but xcb keeps Qt consistent with the main app
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402
from loguru import logger  # noqa: E402

from PySide6.QtCore import (  # noqa: E402
    QObject, QPointF, QRect, Qt, QTimer, Signal, Slot,
)
from PySide6.QtGui import QImage, QKeySequence, QPainter, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QSizePolicy, QStatusBar, QVBoxLayout, QWidget,
)

# Reuse helpers from the main project
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
from camera_pipeline import (  # noqa: E402
    NoriCamera,
    _make_jpeg_probe,
    detect_nori_cameras,
    is_rk3588,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

CAPTURE_W_DEFAULT = 5120
CAPTURE_H_DEFAULT = 3840
PREVIEW_H_DEFAULT = 720          # output height fed into the widget
DECODE_H_DEFAULT = 0             # 0 = full capture height (max detail)

ZOOM_FACTOR = 1.25               # per wheel notch
MIN_CROP_PX = 8                  # NV12 chroma alignment + sanity floor
FPS_AVG_WINDOW = 30


def _round_even(n: int) -> int:
    """Round up to the nearest even pixel — videocrop / NV12 need even dims."""
    return n + 1 & ~1 if n % 2 else n


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class PreviewPipeline(QObject):
    """norisrc -> mppjpegdec -> videocrop -> videoscale -> RGB appsink.

    ``set_crop_rect`` mutates the videocrop element while playing so the
    next decoded frame carries only the requested region.
    """

    frame_ready = Signal(QImage)
    pipeline_error = Signal(str)
    pipeline_eos = Signal()

    def __init__(
        self,
        device_index: int,
        capture_w: int,
        capture_h: int,
        decode_w: int,
        decode_h: int,
        preview_w: int,
        preview_h: int,
        trigger_mode: str | None = None,
        validate_jpeg: bool = True,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._device_index = device_index
        self._capture_w = capture_w
        self._capture_h = capture_h
        self._decode_w = decode_w
        self._decode_h = decode_h
        self._preview_w = preview_w
        self._preview_h = preview_h
        self._trigger_mode = trigger_mode
        self._validate_jpeg = validate_jpeg
        self._on_rk3588 = is_rk3588()

        self._pipeline: Gst.Pipeline | None = None
        self._videocrop: Gst.Element | None = None
        self._appsink: Gst.Element | None = None

        self._state = "stopped"
        self._error: str | None = None

        self._bus_timer = QTimer(self)
        self._bus_timer.setInterval(50)
        self._bus_timer.timeout.connect(self._poll_bus)

    # --- properties --------------------------------------------------------

    @property
    def decode_size(self) -> tuple[int, int]:
        return self._decode_w, self._decode_h

    @property
    def preview_size(self) -> tuple[int, int]:
        return self._preview_w, self._preview_h

    @property
    def error_message(self) -> str | None:
        return self._error

    # --- pipeline string ---------------------------------------------------

    def _build_desc(self) -> str:
        if self._on_rk3588:
            return self._build_desc_rk3588()
        return self._build_desc_dev()

    def _build_desc_rk3588(self) -> str:
        src = [
            "norisrc",
            f"device-index={self._device_index}",
            "auto-exposure=true",
            "auto-white-balance=true",
        ]
        if self._trigger_mode:
            src.append(f"trigger-mode={self._trigger_mode}")
        src_str = " ".join(src)

        # Only ask mppjpegdec to scale if decode != capture; otherwise the
        # default (width=0 height=0 = original) gives full-resolution output.
        decode_props = "format=NV12"
        if (self._decode_w, self._decode_h) != (self._capture_w, self._capture_h):
            decode_props += f" width={self._decode_w} height={self._decode_h}"

        return (
            f"{src_str} "
            f"! image/jpeg,width={self._capture_w},height={self._capture_h} "
            f"! jpegparse name=parser "
            f"! mppjpegdec {decode_props} "
            f"! video/x-raw,format=NV12,width={self._decode_w},height={self._decode_h} "
            f"! videocrop name=crop top=0 bottom=0 left=0 right=0 "
            f"! videoscale "
            f"! video/x-raw,format=NV12,width={self._preview_w},height={self._preview_h} "
            f"! videoconvert "
            f"! video/x-raw,format=RGB,width={self._preview_w},height={self._preview_h} "
            f"! appsink name=sink emit-signals=true sync=false drop=true max-buffers=1"
        )

    def _build_desc_dev(self) -> str:
        # Dev pattern: simulate a high-resolution source so the crop logic
        # exercises the same code paths as on RK3588.
        return (
            f"videotestsrc is-live=true pattern=ball "
            f"! video/x-raw,format=NV12,width={self._decode_w},height={self._decode_h},framerate=27/1 "
            f"! videocrop name=crop top=0 bottom=0 left=0 right=0 "
            f"! videoscale "
            f"! video/x-raw,format=NV12,width={self._preview_w},height={self._preview_h} "
            f"! videoconvert "
            f"! video/x-raw,format=RGB,width={self._preview_w},height={self._preview_h} "
            f"! appsink name=sink emit-signals=true sync=false drop=true max-buffers=1"
        )

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        desc = self._build_desc()
        logger.info("Pipeline: {}", desc)
        try:
            self._pipeline = Gst.parse_launch(desc)
        except Exception as exc:
            self._error = str(exc)
            self.pipeline_error.emit(self._error)
            return False

        self._videocrop = self._pipeline.get_by_name("crop")
        self._appsink = self._pipeline.get_by_name("sink")
        if self._appsink is None or self._videocrop is None:
            self._error = "pipeline missing 'sink' or 'crop' element"
            self.pipeline_error.emit(self._error)
            return False

        self._appsink.connect("new-sample", self._on_new_sample)

        if self._on_rk3588 and self._validate_jpeg:
            parser = self._pipeline.get_by_name("parser")
            if parser is not None:
                parser.get_static_pad("sink").add_probe(
                    Gst.PadProbeType.BUFFER, _make_jpeg_probe(self._device_index),
                )

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._error = "set_state(PLAYING) failed"
            self.pipeline_error.emit(self._error)
            return False

        self._state = "playing"
        self._bus_timer.start()
        logger.success("Pipeline playing | dev_idx={}", self._device_index)
        return True

    def stop(self) -> None:
        if self._state == "stopped":
            return
        self._bus_timer.stop()
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline.get_state(3 * Gst.SECOND)
            self._pipeline = None
        self._videocrop = None
        self._appsink = None
        self._state = "stopped"
        logger.info("Pipeline stopped")

    # --- crop control ------------------------------------------------------

    def set_crop_rect(self, cx: int, cy: int, cw: int, ch: int) -> None:
        """Update videocrop in decode-space coordinates.

        ``cx, cy`` is the top-left corner; ``cw, ch`` the size.  Values are
        clamped to the decode dimensions and rounded to even pixels for
        NV12 chroma alignment.
        """
        if self._videocrop is None:
            return

        cx = max(0, min(self._decode_w - MIN_CROP_PX, int(cx))) & ~1
        cy = max(0, min(self._decode_h - MIN_CROP_PX, int(cy))) & ~1
        cw = max(MIN_CROP_PX, min(self._decode_w - cx, int(cw))) & ~1
        ch = max(MIN_CROP_PX, min(self._decode_h - cy, int(ch))) & ~1

        right = self._decode_w - (cx + cw)
        bottom = self._decode_h - (cy + ch)

        # videocrop properties are mutable in PLAYING (per gst-inspect).
        self._videocrop.set_property("left", cx)
        self._videocrop.set_property("top", cy)
        self._videocrop.set_property("right", right)
        self._videocrop.set_property("bottom", bottom)

    # --- appsink callback (streaming thread) -------------------------------

    def _on_new_sample(self, appsink: Gst.Element) -> Gst.FlowReturn:
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        caps = sample.get_caps()
        s = caps.get_structure(0)
        w = s.get_value("width")
        h = s.get_value("height")
        buf = sample.get_buffer()
        ok, mi = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            # .copy() is essential — QImage must own the bytes after unmap.
            img = QImage(
                bytes(mi.data), w, h, w * 3, QImage.Format.Format_RGB888,
            ).copy()
        finally:
            buf.unmap(mi)
        self.frame_ready.emit(img)
        return Gst.FlowReturn.OK

    # --- bus polling (Qt main thread) --------------------------------------

    @Slot()
    def _poll_bus(self) -> None:
        if self._pipeline is None:
            return
        bus = self._pipeline.get_bus()
        while True:
            msg = bus.pop()
            if msg is None:
                break
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                self._error = f"{err.message} | {debug}"
                logger.error("Bus error: {}", self._error)
                self.pipeline_error.emit(self._error)
                self._bus_timer.stop()
            elif msg.type == Gst.MessageType.EOS:
                logger.warning("Bus EOS")
                self.pipeline_eos.emit()
                self._bus_timer.stop()


# ---------------------------------------------------------------------------
# Live preview widget — renders frames + drives the crop region
# ---------------------------------------------------------------------------

class LivePreviewWidget(QWidget):
    """Displays the latest QImage and translates wheel/drag into crop changes.

    Crop is in source (decode-space) pixel coordinates and aspect-locked to
    the source aspect so videoscale never distorts.
    """

    crop_changed = Signal(int, int, int, int)  # cx, cy, cw, ch

    def __init__(self, source_w: int, source_h: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._source_w = source_w
        self._source_h = source_h
        self._aspect = source_w / source_h

        # Crop state (source coords) — start at full image
        self._cx = 0.0
        self._cy = 0.0
        self._cw = float(source_w)
        self._ch = float(source_h)

        self._image: QImage | None = None
        self._drag_origin: QPointF | None = None
        self._drag_anchor_cx = 0.0
        self._drag_anchor_cy = 0.0

        self.setMinimumSize(640, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background: black;")

    # --- public API --------------------------------------------------------

    @property
    def crop(self) -> tuple[int, int, int, int]:
        return int(self._cx), int(self._cy), int(self._cw), int(self._ch)

    @property
    def zoom_factor(self) -> float:
        return self._source_w / self._cw if self._cw > 0 else 1.0

    def set_frame(self, img: QImage) -> None:
        self._image = img
        self.update()

    def reset_zoom(self) -> None:
        self._set_crop(0.0, 0.0, float(self._source_w), float(self._source_h))

    def set_zoom_1to1(self) -> None:
        """Center a crop sized to the widget's pixels (source 1:1)."""
        target_w = float(min(self.width(), self._source_w))
        target_h = float(min(self.height(), self._source_h))
        # Pin to source aspect so the crop never distorts.
        if target_w / target_h > self._aspect:
            target_w = target_h * self._aspect
        else:
            target_h = target_w / self._aspect
        cx = (self._source_w - target_w) / 2
        cy = (self._source_h - target_h) / 2
        self._set_crop(cx, cy, target_w, target_h)

    # --- painting ----------------------------------------------------------

    def _display_rect(self) -> QRect:
        """Compute on-screen rect for the cropped image (KeepAspectRatio)."""
        if self.height() == 0 or self._ch == 0:
            return QRect(0, 0, self.width(), self.height())
        wr = self.width() / self.height()
        ir = self._cw / self._ch
        if wr > ir:
            h = self.height()
            w = int(h * ir)
            x = (self.width() - w) // 2
            y = 0
        else:
            w = self.width()
            h = int(w / ir)
            x = 0
            y = (self.height() - h) // 2
        return QRect(x, y, w, h)

    def paintEvent(self, ev) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self._image is None:
            painter.setPen(Qt.gray)
            painter.drawText(self.rect(), Qt.AlignCenter, "Waiting for frames…")
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(self._display_rect(), self._image)

    # --- crop math ---------------------------------------------------------

    def _widget_to_source(self, mx: float, my: float) -> tuple[float, float]:
        d = self._display_rect()
        if d.width() == 0 or d.height() == 0:
            return self._cx + self._cw / 2, self._cy + self._ch / 2
        rx = max(0.0, min(float(d.width()), mx - d.x()))
        ry = max(0.0, min(float(d.height()), my - d.y()))
        sx = self._cx + rx * self._cw / d.width()
        sy = self._cy + ry * self._ch / d.height()
        return sx, sy

    def _set_crop(self, cx: float, cy: float, cw: float, ch: float) -> None:
        # Pin aspect to source aspect (so videoscale never distorts).
        if ch <= 0:
            return
        if cw / ch > self._aspect:
            cw = ch * self._aspect
        else:
            ch = cw / self._aspect

        # Clamp size: at least MIN_CROP_PX, at most the source dims.
        cw = min(max(float(MIN_CROP_PX), cw), float(self._source_w))
        ch = min(max(float(MIN_CROP_PX), ch), float(self._source_h))
        # Re-pin aspect after clamps (in case clamping broke it).
        if cw / ch > self._aspect:
            cw = ch * self._aspect
        else:
            ch = cw / self._aspect

        # Clamp origin.
        cx = max(0.0, min(self._source_w - cw, cx))
        cy = max(0.0, min(self._source_h - ch, cy))

        self._cx, self._cy, self._cw, self._ch = cx, cy, cw, ch
        self.crop_changed.emit(int(self._cx), int(self._cy),
                               int(self._cw), int(self._ch))
        self.update()

    # --- mouse / wheel -----------------------------------------------------

    def wheelEvent(self, ev) -> None:  # noqa: N802
        delta = ev.angleDelta().y()
        if delta == 0:
            ev.ignore()
            return
        factor = ZOOM_FACTOR if delta > 0 else 1.0 / ZOOM_FACTOR

        mx = ev.position().x()
        my = ev.position().y()
        sx, sy = self._widget_to_source(mx, my)

        new_cw = self._cw / factor
        new_ch = self._ch / factor

        # Pre-clamp size to compute origin around anchor correctly.
        if new_cw > self._source_w:
            new_cw = float(self._source_w)
            new_ch = float(self._source_h)
        if new_cw < MIN_CROP_PX:
            new_cw = float(MIN_CROP_PX)
            new_ch = MIN_CROP_PX / self._aspect

        d = self._display_rect()
        if d.width() > 0 and d.height() > 0:
            rx = max(0.0, min(float(d.width()), mx - d.x()))
            ry = max(0.0, min(float(d.height()), my - d.y()))
            new_cx = sx - rx * new_cw / d.width()
            new_cy = sy - ry * new_ch / d.height()
        else:
            new_cx = sx - new_cw / 2
            new_cy = sy - new_ch / 2

        self._set_crop(new_cx, new_cy, new_cw, new_ch)
        ev.accept()

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._drag_origin = QPointF(ev.position())
            self._drag_anchor_cx = self._cx
            self._drag_anchor_cy = self._cy
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._drag_origin is None:
            return
        dx = ev.position().x() - self._drag_origin.x()
        dy = ev.position().y() - self._drag_origin.y()
        d = self._display_rect()
        if d.width() == 0 or d.height() == 0:
            return
        spp_x = self._cw / d.width()
        spp_y = self._ch / d.height()
        # Drag right -> show what was on the left -> crop origin moves left.
        new_cx = self._drag_anchor_cx - dx * spp_x
        new_cy = self._drag_anchor_cy - dy * spp_y
        self._set_crop(new_cx, new_cy, self._cw, self._ch)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._drag_origin = None
            self.setCursor(Qt.OpenHandCursor)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(
        self,
        pipeline: PreviewPipeline,
        decode_w: int,
        decode_h: int,
        camera_label: str,
    ):
        super().__init__()
        self.setWindowTitle(f"Nori Single-Cam Pan/Zoom — {camera_label}")
        self._pipeline = pipeline
        self._frame_times: deque[float] = deque(maxlen=FPS_AVG_WINDOW)

        self._preview = LivePreviewWidget(decode_w, decode_h)
        self._preview.crop_changed.connect(self._on_crop_changed)

        # Toolbar
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        btn_fit = QPushButton("Fit (R)")
        btn_fit.setMinimumHeight(32)
        btn_fit.clicked.connect(self._preview.reset_zoom)
        btn_one = QPushButton("1:1 (1)")
        btn_one.setMinimumHeight(32)
        btn_one.clicked.connect(self._preview.set_zoom_1to1)
        self._zoom_lbl = QLabel("Zoom: 1.00×")
        self._crop_lbl = QLabel(f"Crop: {decode_w}×{decode_h} @ (0,0)")
        self._fps_lbl = QLabel("FPS: –")
        for lbl in (self._zoom_lbl, self._crop_lbl, self._fps_lbl):
            lbl.setStyleSheet("font-family: monospace;")
        bar.addWidget(btn_fit)
        bar.addWidget(btn_one)
        bar.addStretch(1)
        bar.addWidget(self._zoom_lbl)
        bar.addSpacing(16)
        bar.addWidget(self._crop_lbl)
        bar.addSpacing(16)
        bar.addWidget(self._fps_lbl)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(bar)
        layout.addWidget(self._preview, stretch=1)
        self.setCentralWidget(central)

        sb = QStatusBar()
        self.setStatusBar(sb)
        sb.showMessage("Wheel: zoom · Drag: pan · R: fit · 1: 1:1 · Esc/Q: quit")

        # Pipeline signals
        pipeline.frame_ready.connect(self._on_frame_ready)
        pipeline.pipeline_error.connect(self._on_pipeline_error)
        pipeline.pipeline_eos.connect(self._on_pipeline_eos)

        # Shortcuts
        for seq, fn in (
            ("R", self._preview.reset_zoom),
            ("1", self._preview.set_zoom_1to1),
            ("Q", self.close),
            ("Esc", self.close),
        ):
            QShortcut(QKeySequence(seq), self).activated.connect(fn)

        self.resize(1280, 800)

    @Slot(QImage)
    def _on_frame_ready(self, img: QImage) -> None:
        self._preview.set_frame(img)
        now = time.monotonic()
        self._frame_times.append(now)
        if len(self._frame_times) >= 2:
            dur = self._frame_times[-1] - self._frame_times[0]
            if dur > 0:
                fps = (len(self._frame_times) - 1) / dur
                self._fps_lbl.setText(f"FPS: {fps:5.1f}")

    @Slot(int, int, int, int)
    def _on_crop_changed(self, cx: int, cy: int, cw: int, ch: int) -> None:
        self._pipeline.set_crop_rect(cx, cy, cw, ch)
        self._zoom_lbl.setText(f"Zoom: {self._preview.zoom_factor:5.2f}×")
        self._crop_lbl.setText(f"Crop: {cw}×{ch} @ ({cx},{cy})")

    @Slot(str)
    def _on_pipeline_error(self, msg: str) -> None:
        self.statusBar().showMessage(f"Pipeline error: {msg}")

    @Slot()
    def _on_pipeline_eos(self) -> None:
        self.statusBar().showMessage("Pipeline EOS — stream ended")

    def closeEvent(self, ev) -> None:  # noqa: N802
        logger.info("Window closing — stopping pipeline")
        self._pipeline.stop()
        super().closeEvent(ev)


# ---------------------------------------------------------------------------
# Camera resolution / CLI plumbing
# ---------------------------------------------------------------------------

def _resolve_camera(
    args: argparse.Namespace,
    cameras: list[NoriCamera],
) -> NoriCamera | None:
    if args.role:
        for c in cameras:
            if c.tag and c.tag.casefold() == args.role.casefold():
                return c
        return None
    if args.device_index is not None:
        for c in cameras:
            if c.index == args.device_index:
                return c
        return NoriCamera(index=args.device_index, tag="", product="", location="")
    if not cameras:
        return None
    if len(cameras) == 1:
        return cameras[0]
    print("Available Nori cameras:")
    for i, c in enumerate(cameras, 1):
        print(f"  [{i}] idx={c.index} tag={c.tag or '(untagged)'} loc={c.location}")
    if not sys.stdin.isatty():
        return cameras[0]
    while True:
        try:
            sel = input(f"Select [1-{len(cameras)}] (default 1): ").strip()
        except EOFError:
            return cameras[0]
        if not sel:
            return cameras[0]
        if sel.isdigit() and 1 <= int(sel) <= len(cameras):
            return cameras[int(sel) - 1]
        print("Invalid selection.", file=sys.stderr)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Single-camera live preview with pipeline-driven pan/zoom.",
    )
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--device-index", type=int, default=None,
                   help="Nori device index (default: auto-detect / first)")
    g.add_argument("--role", type=str, default=None,
                   help="Select camera by nori-ctl tag (e.g. LEFT, RIGHT)")
    parser.add_argument("--list", action="store_true",
                        help="List detected Nori cameras and exit")
    parser.add_argument("--capture-width", type=int, default=CAPTURE_W_DEFAULT,
                        help=f"Source width (default {CAPTURE_W_DEFAULT})")
    parser.add_argument("--capture-height", type=int, default=CAPTURE_H_DEFAULT,
                        help=f"Source height (default {CAPTURE_H_DEFAULT})")
    parser.add_argument("--decode-height", type=int, default=DECODE_H_DEFAULT,
                        help="HW decode output height in pixels "
                             "(0 = full capture height; preserves max sensor detail "
                             "but increases videoscale memory load at fit-to-window)")
    parser.add_argument("--preview-height", type=int, default=PREVIEW_H_DEFAULT,
                        help=f"Output preview height in pixels (default {PREVIEW_H_DEFAULT})")
    parser.add_argument("--trigger-mode",
                        choices=["none", "software", "hardware", "command"],
                        default=None,
                        help="Camera trigger mode (default: free-run)")
    parser.add_argument("--no-validate-jpeg", action="store_true",
                        help="Disable SOI/EOI JPEG validation probe")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv[1:])

    Gst.init(None)

    if is_rk3588():
        if not Gst.ElementFactory.find("norisrc"):
            logger.error("'norisrc' element not found — is gst-nori installed?")
            return 1
        cameras = detect_nori_cameras()
        if args.list:
            print("Detected Nori cameras:")
            for c in cameras:
                print(f"  idx={c.index}  tag={c.tag or '(untagged)':10}  loc={c.location}")
            return 0
        camera = _resolve_camera(args, cameras)
        if camera is None:
            logger.error("No matching camera found (role={}, idx={}, available={})",
                         args.role, args.device_index, [c.index for c in cameras])
            return 2
    else:
        if args.list:
            print("Dev machine — videotestsrc dev path (no real cameras)")
            return 0
        logger.warning("Not running on RK3588 — using videotestsrc dev path")
        camera = NoriCamera(index=0, tag="DEV", product="testsrc", location="-")

    # Resolve decode + preview dims (aspect-locked to capture aspect).
    decode_h = args.decode_height if args.decode_height else args.capture_height
    decode_w = _round_even(decode_h * args.capture_width // args.capture_height)
    preview_h = args.preview_height
    preview_w = _round_even(preview_h * args.capture_width // args.capture_height)

    logger.info(
        "Camera: idx={} tag={} | capture={}×{} decode={}×{} preview={}×{}",
        camera.index, camera.tag or "(untagged)",
        args.capture_width, args.capture_height,
        decode_w, decode_h, preview_w, preview_h,
    )

    pipeline = PreviewPipeline(
        device_index=camera.index,
        capture_w=args.capture_width,
        capture_h=args.capture_height,
        decode_w=decode_w,
        decode_h=decode_h,
        preview_w=preview_w,
        preview_h=preview_h,
        trigger_mode=args.trigger_mode,
        validate_jpeg=not args.no_validate_jpeg,
    )

    app = QApplication(sys.argv[:1])
    label = f"idx={camera.index} {camera.tag or '(untagged)'}"
    window = MainWindow(pipeline, decode_w, decode_h, label)

    if not pipeline.start():
        logger.error("Pipeline failed to start: {}", pipeline.error_message or "(unknown)")
        return 1

    app.aboutToQuit.connect(pipeline.stop)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
