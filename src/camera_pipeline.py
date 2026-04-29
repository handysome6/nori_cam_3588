"""
CameraPipeline — GStreamer pipeline for one Nori SDK camera on RK3588.

Pipeline topology (tee-split):

    norisrc (MJPEG 5120x3840, trigger-mode=hardware)
      -> tee
         ├─ Preview branch: jpegparse -> mppjpegdec (HW decode + resize) -> preview sink
         └─ Capture branch: queue(leaky) -> appsink (raw MJPEG, latest only)

Two preview modes controlled by `use_overlay`:
  True  — VideoOverlay: GStreamer renders directly into a Qt widget window handle
  False — appsink fallback: decoded NV12 (RK3588) / RGB (dev) frames are
          converted via cv2 / direct wrap and emitted as QImage via signal

Platform selection:
  RK3588  — mppjpegdec + xvimagesink (HW accelerated, built-in resize)
  Dev     — jpegdec + videoconvert + autovideosink (software, test source)
"""

import shutil
import subprocess
import threading
import time
from collections import deque
from typing import NamedTuple

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstVideo
from loguru import logger

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from camera_config import CameraSettings


# ---------------------------------------------------------------------------
# Nori camera auto-detection
# ---------------------------------------------------------------------------

MAX_PROBE_INDEX = 8  # probe device-index 0..7

_NORI_CTL_COLUMNS = ("#", "VID:PID", "Product", "Serial", "Bus:Dev", "Location", "Tag")


class NoriCamera(NamedTuple):
    """A detected Nori camera.  Empty strings are used for unknown fields
    (e.g. the probe-fallback path knows the index but nothing else)."""
    index: int
    tag: str
    product: str
    location: str


def _list_nori_cameras_via_ctl() -> list[NoriCamera]:
    """Run ``nori-ctl list`` and parse rows.

    Raises ``FileNotFoundError`` if ``nori-ctl`` is not on PATH, or
    ``RuntimeError`` if the command fails or output is unparseable.
    """
    exe = shutil.which("nori-ctl")
    if exe is None:
        raise FileNotFoundError("nori-ctl")

    result = subprocess.run(
        [exe, "list"], capture_output=True, text=True, check=False, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"'nori-ctl list' exited {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()}"
        )

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return []

    header = lines[0]
    positions: list[int] = []
    for col in _NORI_CTL_COLUMNS:
        pos = header.find(col)
        if pos < 0:
            raise RuntimeError(
                f"Unexpected 'nori-ctl list' format: missing column '{col}'"
            )
        positions.append(pos)

    def field(line: str, i: int) -> str:
        start = positions[i]
        end = positions[i + 1] if i + 1 < len(positions) else None
        return (line[start:end] if end is not None else line[start:]).strip()

    cameras: list[NoriCamera] = []
    for line in lines[1:]:
        try:
            idx = int(field(line, 0))
        except ValueError:
            continue
        cameras.append(NoriCamera(
            index=idx,
            tag=field(line, _NORI_CTL_COLUMNS.index("Tag")),
            product=field(line, _NORI_CTL_COLUMNS.index("Product")),
            location=field(line, _NORI_CTL_COLUMNS.index("Location")),
        ))
    return cameras


def _probe_nori_cameras() -> list[int]:
    """Fallback: probe norisrc device indices by pushing to PAUSED.

    READY only initialises SDK state — it does not open the device.  We
    must go to PAUSED (which calls basesrc ``start()`` -> SDK device open)
    to find out whether a physical camera is actually present.
    """
    available: list[int] = []
    for idx in range(MAX_PROBE_INDEX):
        elem = Gst.ElementFactory.make("norisrc", None)
        if elem is None:
            break
        elem.set_property("device-index", idx)
        ret = elem.set_state(Gst.State.PAUSED)
        if ret == Gst.StateChangeReturn.FAILURE:
            elem.set_state(Gst.State.NULL)
            continue
        if ret == Gst.StateChangeReturn.ASYNC:
            ret, _, _ = elem.get_state(2 * Gst.SECOND)
        if ret != Gst.StateChangeReturn.FAILURE:
            available.append(idx)
            logger.info("Nori camera found: device-index {}", idx)
        elem.set_state(Gst.State.NULL)
    return available


def detect_nori_cameras() -> list[NoriCamera]:
    """Detect Nori cameras, preferring ``nori-ctl list`` over index probing.

    Falls back to probing when ``nori-ctl`` is missing or fails; in that
    case a warning is logged and ``tag``/``product``/``location`` are
    empty strings.  Cameras that exist but have no tag yet are returned
    normally with ``tag=""`` — callers decide how to handle them.
    """
    try:
        cameras = _list_nori_cameras_via_ctl()
        logger.info("Detected {} Nori camera(s) via nori-ctl", len(cameras))
        return cameras
    except FileNotFoundError:
        logger.warning(
            "'nori-ctl' not on PATH; falling back to GStreamer probe "
            "(no tag/location info, slower)"
        )
    except Exception as e:
        logger.warning(
            "'nori-ctl list' failed ({}); falling back to GStreamer probe", e
        )

    indices = _probe_nori_cameras()
    if not indices:
        logger.warning("No Nori cameras detected (probed indices 0..{})", MAX_PROBE_INDEX - 1)
    return [NoriCamera(index=i, tag="", product="", location="") for i in indices]


def scan_nori_cameras() -> list[int]:
    """Return available Nori device indices (thin wrapper for legacy callers).

    Prefer :func:`detect_nori_cameras` when tag/location info is useful.
    """
    return [c.index for c in detect_nori_cameras()]


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def is_rk3588() -> bool:
    """
    Return True if running on RK3588 with Rockchip MPP HW-accelerated GStreamer elements.

    Detection: check for mppjpegdec element factory — the specific element we
    use for HW JPEG decode + resize.
    """
    result = Gst.ElementFactory.find("mppjpegdec") is not None
    logger.info("Platform: {}", "RK3588 (HW accelerated)" if result else "Dev machine (SW path)")
    return result


# ---------------------------------------------------------------------------
# JPEG validation probe — drop corrupted frames before jpegparse
# ---------------------------------------------------------------------------

def _validate_jpeg(data) -> bool:
    """Check SOI (0xFFD8) at start and EOI (0xFFD9) at end."""
    size = len(data)
    if size < 4:
        return False
    if data[0] != 0xFF or data[1] != 0xD8:
        return False
    if data[size - 2] != 0xFF or data[size - 1] != 0xD9:
        return False
    return True


def _make_jpeg_probe(device_index: int):
    """Create a pad probe callback that drops invalid JPEG buffers."""
    drop_count = 0

    def probe(pad, info):
        nonlocal drop_count
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.DROP
        ok, map_info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.PadProbeReturn.DROP
        try:
            if _validate_jpeg(map_info.data):
                return Gst.PadProbeReturn.OK
            drop_count += 1
            logger.debug(
                "Dropped corrupted JPEG #{} ({} bytes) on device-index {}",
                drop_count, buf.get_size(), device_index,
            )
            return Gst.PadProbeReturn.DROP
        finally:
            buf.unmap(map_info)

    return probe


# ---------------------------------------------------------------------------
# Timestamped sample for ring buffer
# ---------------------------------------------------------------------------

class StampedSample(NamedTuple):
    """A GStreamer sample tagged with a CLOCK_MONOTONIC timestamp for matching.

    timestamp_ns is the best available CLOCK_MONOTONIC time for this frame:
      - Preferred: pad-probe wall-clock (time.clock_gettime_ns stamped on
        norisrc's streaming thread, before the tee fans out to preview/capture
        branches).  Immune to preview decode contention and independent of
        GStreamer base_time / PTS computation internals.
      - Fallback:  wall-clock at appsink callback time, used only when the
        probe timestamp lookup fails (e.g. PTS is CLOCK_TIME_NONE).
    """
    timestamp_ns: int     # CLOCK_MONOTONIC ns — pad-probe wall-clock preferred
    sample: object        # Gst.Sample (typed as object for NamedTuple compat)


# Ring buffer depth: at 27 Hz this holds ~180 ms of history, enough to
# cover the 1–2 frame USB delivery skew between cameras.
RING_BUFFER_SIZE = 5


# ---------------------------------------------------------------------------
# File-write worker (runs on QThreadPool, never on the UI thread)
# ---------------------------------------------------------------------------

class _FrameWriter(QRunnable):
    """Writes raw GstBuffer bytes to a .jpg file on a worker thread."""

    def __init__(self, sample: Gst.Sample, path: str):
        super().__init__()
        self._sample = sample
        self._path = path

    def run(self):
        buf = self._sample.get_buffer()
        result, map_info = buf.map(Gst.MapFlags.READ)
        if not result:
            logger.error("Failed to map GstBuffer for capture write: {}", self._path)
            return
        try:
            with open(self._path, "wb") as f:
                f.write(bytes(map_info.data))
            logger.success("Frame saved → {} ({:.1f} KB)", self._path, len(map_info.data) / 1024)
        except OSError as exc:
            logger.error("Failed to write capture file {}: {}", self._path, exc)
        finally:
            buf.unmap(map_info)


# ---------------------------------------------------------------------------
# CameraPipeline
# ---------------------------------------------------------------------------

class CameraPipeline(QObject):
    """
    Manages one GStreamer camera pipeline.

    Signals:
        pipeline_error(str)   — emitted on GStreamer ERROR bus message
        pipeline_eos()        — emitted on EOS
        preview_frame(QImage) — emitted per frame in appsink fallback mode
    """

    pipeline_error = Signal(str)
    pipeline_eos = Signal()
    preview_frame = Signal(QImage)

    # Capture resolution (Nori 20MP sensor native mode)
    CAPTURE_W = 5120
    CAPTURE_H = 3840

    # Preview dimensions — 720 vertical, width derived from capture aspect
    # ratio so the 4:3 source isn't stretched into a 16:9 preview.
    PREVIEW_H = 720
    PREVIEW_W = (CAPTURE_W * PREVIEW_H // CAPTURE_H + 1) & ~1  # round up to even

    # Framerate presets: label -> GStreamer fraction string
    FRAMERATE_PRESETS = {
        "27 Hz": "55/2",
        "10 Hz": "10/1",
    }

    def __init__(
        self,
        device_index: int = 0,
        use_overlay: bool = True,
        framerate: str = "55/2",
        settings: CameraSettings | None = None,
        parent: QObject = None,
    ):
        super().__init__(parent)
        self._device_index = device_index
        self._use_overlay = use_overlay
        self._framerate = framerate
        self._settings = settings if settings is not None else CameraSettings(role="")
        self._on_rk3588 = is_rk3588()

        # Runtime state
        self._pipeline: Gst.Pipeline | None = None
        self._preview_sink: Gst.Element | None = None
        self._capture_sink: Gst.Element | None = None
        self._window_handle: int | None = None

        # Ring buffer of recent MJPEG samples — updated on GStreamer streaming thread.
        # Each entry is a StampedSample(timestamp_ns, sample) so that
        # DualCameraManager can match frames across cameras by pad-probe timestamp.
        self._sample_ring: deque[StampedSample] = deque(maxlen=RING_BUFFER_SIZE)
        self._sample_lock = threading.Lock()

        # Pad-probe timestamp side channel: maps buffer PTS → wall-clock ns.
        # The probe fires on the tee's sink pad (norisrc streaming thread,
        # before preview decode contention).  The appsink callback looks up
        # the probe timestamp by the buffer's PTS to get a jitter-free,
        # base_time-independent CLOCK_MONOTONIC timestamp.
        self._probe_timestamps: dict[int, int] = {}
        self._probe_lock = threading.Lock()

        # State: "stopped" | "playing" | "error"
        self._state = "stopped"
        self._error_message: str | None = None

        # Qt timer polls the GStreamer bus so we never run a GLib main loop
        self._bus_timer = QTimer(self)
        self._bus_timer.setInterval(50)  # 50 ms ≈ 20 polls/s
        self._bus_timer.timeout.connect(self._poll_bus)

    # ------------------------------------------------------------------
    # Pipeline string construction
    # ------------------------------------------------------------------

    def _norisrc_settings_props(self) -> str:
        """Format user-configurable norisrc properties as launch-line tokens.

        `sensor-shutter` / `sensor-gain` are only emitted when auto-exposure
        is off — norisrc logs a warning if they're set while AE is on, and
        AE=false resets the UVC exposure/gain registers to defaults before
        applying these values.
        """
        s = self._settings
        parts = [
            f"auto-exposure={'true' if s.auto_exposure else 'false'}",
            f"auto-white-balance={'true' if s.auto_white_balance else 'false'}",
            f"mirror-flip={s.mirror_flip}",
        ]
        if not s.auto_exposure:
            parts.append(f"sensor-shutter={s.sensor_shutter}")
            parts.append(f"sensor-gain={s.sensor_gain}")
        return " ".join(parts)

    def _build_pipeline_string(self) -> str:
        W, H = self.PREVIEW_W, self.PREVIEW_H

        if self._on_rk3588:
            # RK3588: norisrc with hardware trigger, mppjpegdec does HW decode + resize.
            # In hardware trigger mode, framerate is driven by the external PWM
            # signal — omit framerate from caps to let GStreamer negotiate from
            # the element's advertised modes.
            src = (
                f"norisrc device-index={self._device_index} trigger-mode=hardware "
                f"{self._norisrc_settings_props()} ! "
                f"image/jpeg,width={self.CAPTURE_W},height={self.CAPTURE_H} ! "
                "tee name=t "
            )
            capture_branch = (
                "t. ! queue leaky=downstream max-size-buffers=1 ! "
                "appsink name=capture_sink drop=true max-buffers=1 emit-signals=true"
            )
            if self._use_overlay:
                # HW decode + resize -> VideoOverlay sink (xvimagesink)
                # Preview queue must be leaky to prevent backpressure from
                # mppjpegdec blocking norisrc's thread — that would add
                # variable jitter to the pad-probe timestamps used for
                # cross-camera frame matching.
                preview_branch = (
                    f"t. ! queue leaky=downstream max-size-buffers=2 ! "
                    f"jpegparse name=parser ! "
                    f"mppjpegdec width={W} height={H} format=NV12 ! "
                    "xvimagesink name=preview_sink sync=false "
                )
            else:
                # HW decode + resize -> NV12 -> appsink.  Inserting
                # `videoconvert ! video/x-raw,format=RGB` here triggers a
                # `not-negotiated` failure: mppjpegdec only outputs NV12 (or
                # RK-tiled equivalents) and refuses the RGB caps filter.  Push
                # raw NV12 into the appsink and convert to BGR with cv2 in
                # `_on_new_preview_sample` instead.
                preview_branch = (
                    f"t. ! queue leaky=downstream max-size-buffers=2 ! "
                    f"jpegparse name=parser ! "
                    f"mppjpegdec width={W} height={H} format=NV12 ! "
                    "appsink name=preview_sink drop=true max-buffers=1 "
                    "emit-signals=true sync=false "
                )
        else:
            # Dev machine: software decode with a test source
            src = (
                "videotestsrc is-live=true pattern=ball ! "
                f"video/x-raw,width={W},height={H},framerate=27/1 ! "
                "jpegenc ! image/jpeg ! "
                "tee name=t "
            )
            capture_branch = (
                "t. ! queue leaky=downstream max-size-buffers=1 ! "
                "appsink name=capture_sink drop=true max-buffers=1 emit-signals=true"
            )
            if self._use_overlay:
                preview_branch = (
                    f"t. ! queue leaky=downstream max-size-buffers=2 ! "
                    f"jpegdec ! videoconvert ! "
                    f"video/x-raw,width={W},height={H} ! "
                    "autovideosink name=preview_sink sync=false "
                )
            else:
                preview_branch = (
                    f"t. ! queue leaky=downstream max-size-buffers=2 ! "
                    f"jpegdec ! videoconvert ! "
                    f"video/x-raw,format=RGB,width={W},height={H} ! "
                    "appsink name=preview_sink drop=true max-buffers=1 "
                    "emit-signals=true sync=false "
                )

        pipeline_str = src + preview_branch + capture_branch
        logger.debug("Pipeline string: {}", pipeline_str)
        return pipeline_str

    # ------------------------------------------------------------------
    # Lifecycle: start / stop
    # ------------------------------------------------------------------

    def start(self, window_handle: int | None = None) -> bool:
        """
        Build and start the pipeline.

        Args:
            window_handle: Native window ID (winId()) of the preview widget.
                           Required when use_overlay=True.
        Returns:
            True if the pipeline reached PLAYING state (or ASYNC).
        """
        self._window_handle = window_handle
        logger.info(
            "Starting pipeline | device-index={} overlay={} rk3588={}",
            self._device_index, self._use_overlay, self._on_rk3588,
        )

        pipeline_str = self._build_pipeline_string()
        try:
            self._pipeline = Gst.parse_launch(pipeline_str)
        except Exception as exc:
            self._state = "error"
            self._error_message = str(exc)
            logger.error("Pipeline parse failed: {}", exc)
            self.pipeline_error.emit(self._error_message)
            return False

        if self._pipeline is None:
            self._state = "error"
            self._error_message = "Gst.parse_launch returned None"
            logger.error(self._error_message)
            self.pipeline_error.emit(self._error_message)
            return False

        # Named element references
        self._capture_sink = self._pipeline.get_by_name("capture_sink")
        self._preview_sink = self._pipeline.get_by_name("preview_sink")

        if self._capture_sink is None:
            self._state = "error"
            self._error_message = "capture_sink element not found in pipeline"
            logger.error(self._error_message)
            self.pipeline_error.emit(self._error_message)
            return False

        # Attach JPEG validation probe on jpegparse sink pad (RK3588 only)
        if self._on_rk3588:
            parser = self._pipeline.get_by_name("parser")
            if parser is not None:
                sink_pad = parser.get_static_pad("sink")
                sink_pad.add_probe(
                    Gst.PadProbeType.BUFFER, _make_jpeg_probe(self._device_index)
                )
                logger.info("JPEG validation probe attached | device-index={}", self._device_index)

        # Attach timestamp probe on tee's sink pad.
        # This runs on norisrc's streaming thread (before the tee fans out
        # to preview/capture branches), so wall-clock stamps here have
        # sub-ms accuracy with no preview decode contention.
        tee = self._pipeline.get_by_name("t")
        if tee is not None:
            tee_sink_pad = tee.get_static_pad("sink")
            tee_sink_pad.add_probe(
                Gst.PadProbeType.BUFFER, self._stamp_probe,
            )
            logger.info("Timestamp probe attached on tee sink pad | device-index={}", self._device_index)

        # Connect capture appsink new-sample
        self._capture_sink.connect("new-sample", self._on_new_capture_sample)

        # Connect preview appsink new-sample in fallback mode
        if not self._use_overlay and self._preview_sink is not None:
            self._preview_sink.connect("new-sample", self._on_new_preview_sample)

        # Bus setup
        bus = self._pipeline.get_bus()
        if self._use_overlay:
            # sync-message needed so we can call set_window_handle()
            # before the first frame is rendered
            bus.enable_sync_message_emission()
            bus.connect("sync-message::element", self._on_sync_message)

        # Start pipeline
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            # Pull the actual error from the bus
            bus_msg = bus.pop_filtered(Gst.MessageType.ERROR)
            if bus_msg:
                err, debug = bus_msg.parse_error()
                self._error_message = f"{err.message} | {debug}"
            else:
                self._error_message = "Failed to set pipeline to PLAYING"
            self._state = "error"
            logger.error("Pipeline start failed: {}", self._error_message)
            self.pipeline_error.emit(self._error_message)
            return False

        self._state = "playing"
        self._bus_timer.start()

        logger.success("Pipeline playing | device-index={} overlay={}", self._device_index, self._use_overlay)
        return True

    def stop(self):
        """Stop the pipeline and release all resources.

        Blocks until:
          - GStreamer reaches NULL state (up to 3 s)
          - Any in-flight QThreadPool file-write tasks finish (up to 2 s)
        Safe to call multiple times or from a signal handler.
        """
        if self._state == "stopped" and self._pipeline is None:
            return
        logger.info("Stopping pipeline | device-index={}", self._device_index)
        self._bus_timer.stop()
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline.get_state(3 * Gst.SECOND)
            self._pipeline = None
        self._preview_sink = None
        self._capture_sink = None
        with self._sample_lock:
            self._sample_ring.clear()
        with self._probe_lock:
            self._probe_timestamps.clear()
        self._state = "stopped"
        QThreadPool.globalInstance().waitForDone(2000)
        logger.info("Pipeline stopped")

    # ------------------------------------------------------------------
    # Pad probe — norisrc streaming thread (before tee fan-out)
    # ------------------------------------------------------------------

    def _stamp_probe(self, pad: Gst.Pad, info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
        """Record wall-clock timestamp for each buffer, keyed by PTS.

        Runs on norisrc's streaming thread (the tee's sink pad), before the
        buffer is pushed to preview/capture branches.  No preview decode
        contention at this point, so wall-clock accuracy is sub-ms.
        """
        buf = info.get_buffer()
        if buf is not None:
            pts = buf.pts
            if pts != Gst.CLOCK_TIME_NONE:
                ts = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                with self._probe_lock:
                    self._probe_timestamps[pts] = ts
                    # Prevent unbounded growth from frames dropped by the
                    # leaky capture queue (never consumed by appsink).
                    if len(self._probe_timestamps) > 50:
                        keys = list(self._probe_timestamps.keys())
                        for k in keys[:25]:
                            del self._probe_timestamps[k]
        return Gst.PadProbeReturn.OK

    # ------------------------------------------------------------------
    # Capture appsink callback — capture branch streaming thread
    # ------------------------------------------------------------------

    def _on_new_capture_sample(self, appsink: Gst.Element) -> Gst.FlowReturn:
        """Cache MJPEG sample with pad-probe timestamp; called on capture streaming thread."""
        sample = appsink.emit("pull-sample")
        if sample is not None:
            buf = sample.get_buffer()
            pts = buf.pts
            ts = None
            # Look up the probe timestamp (stamped on norisrc's thread,
            # before decode contention, no base_time dependency).
            if pts != Gst.CLOCK_TIME_NONE:
                with self._probe_lock:
                    ts = self._probe_timestamps.pop(pts, None)
            # Fall back to wall-clock if probe lookup fails.
            if ts is None:
                ts = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
            with self._sample_lock:
                self._sample_ring.append(StampedSample(ts, sample))
        return Gst.FlowReturn.OK

    # ------------------------------------------------------------------
    # Preview appsink callback — appsink fallback mode
    # ------------------------------------------------------------------

    def _on_new_preview_sample(self, appsink: Gst.Element) -> Gst.FlowReturn:
        """Pull decoded preview frame, wrap in QImage, emit signal.

        Two formats are supported:
          - NV12 (RK3588 path): mppjpegdec outputs NV12 directly because
            attempting `videoconvert ! video/x-raw,format=RGB` after it
            triggers a not-negotiated stream error.  Convert NV12 → BGR
            with cv2 here and wrap in a Format_BGR888 QImage.
          - RGB  (dev path): jpegdec → videoconvert produces packed RGB,
            wrap directly in a Format_RGB888 QImage.
        """
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        caps = sample.get_caps()
        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        fmt = structure.get_value("format")

        buf = sample.get_buffer()
        result, map_info = buf.map(Gst.MapFlags.READ)
        if not result:
            return Gst.FlowReturn.OK
        try:
            if fmt == "NV12":
                import cv2
                import numpy as np
                # NV12: Y plane (H×W) followed by interleaved UV (H/2 × W).
                # Total = W × H × 3/2 bytes.
                yuv = np.frombuffer(map_info.data, dtype=np.uint8).reshape(
                    height * 3 // 2, width
                )
                bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_NV12)
                # bgr.tobytes() detaches from the GStreamer-owned buffer so
                # the QImage stays valid after buf.unmap().
                image = QImage(
                    bgr.tobytes(),
                    width,
                    height,
                    width * 3,
                    QImage.Format.Format_BGR888,
                )
            else:
                image = QImage(
                    bytes(map_info.data),
                    width,
                    height,
                    width * 3,
                    QImage.Format.Format_RGB888,
                ).copy()
        finally:
            buf.unmap(map_info)

        self.preview_frame.emit(image)
        return Gst.FlowReturn.OK

    # ------------------------------------------------------------------
    # VideoOverlay sync-message handler
    # ------------------------------------------------------------------

    def _on_sync_message(self, bus: Gst.Bus, message: Gst.Message):
        """Set the native window handle the moment GStreamer asks for it."""
        structure = message.get_structure()
        if structure is None:
            return
        if structure.get_name() == "prepare-window-handle":
            if self._window_handle is not None:
                logger.info("VideoOverlay: setting window handle 0x{:x}", self._window_handle)
                GstVideo.VideoOverlay.set_window_handle(message.src, self._window_handle)
            else:
                logger.warning("VideoOverlay: prepare-window-handle received but no window handle set")

    # ------------------------------------------------------------------
    # GStreamer bus polling — Qt main thread
    # ------------------------------------------------------------------

    @Slot()
    def _poll_bus(self):
        """Poll the GStreamer bus for error / EOS messages (no GLib main loop)."""
        if self._pipeline is None:
            return
        bus = self._pipeline.get_bus()
        while True:
            msg = bus.pop()
            if msg is None:
                break
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                self._state = "error"
                self._error_message = str(err)
                logger.error("GStreamer error: {} | debug: {}", err, debug)
                self.pipeline_error.emit(self._error_message)
                self._bus_timer.stop()
            elif msg.type == Gst.MessageType.EOS:
                self._state = "stopped"
                logger.warning("GStreamer EOS — stream ended")
                self.pipeline_eos.emit()
                self._bus_timer.stop()

    # ------------------------------------------------------------------
    # VideoOverlay expose on resize
    # ------------------------------------------------------------------

    def expose(self):
        """
        Call when the preview widget is resized (VideoOverlay mode only).
        Tells xvimagesink to repaint to the new widget dimensions.
        """
        if self._use_overlay and self._preview_sink is not None:
            try:
                self._preview_sink.expose()
            except Exception:
                pass  # not all sinks implement expose; safe to ignore

    def set_window_handle(self, window_handle: int | None):
        """
        Dynamically change the window handle for VideoOverlay rendering.
        Note: xvimagesink does not reliably support this while running;
        stop the pipeline first, then restart with the new handle.
        """
        if self._use_overlay and self._preview_sink is not None and window_handle is not None:
            try:
                self._preview_sink.set_window_handle(window_handle)
                self._window_handle = window_handle
                self._preview_sink.expose()
            except Exception as e:
                logger.warning("Failed to set window handle: {}", e)

    # ------------------------------------------------------------------
    # Frame capture
    # ------------------------------------------------------------------

    def snapshot_sample(self) -> "Gst.Sample | None":
        """Atomically read and return the latest cached MJPEG sample.

        Used by DualCameraManager to snapshot both cameras back-to-back
        before scheduling any file writes, minimising the race window.
        """
        with self._sample_lock:
            return self._sample_ring[-1].sample if self._sample_ring else None

    def snapshot_ring(self) -> list[StampedSample]:
        """Return a snapshot of the ring buffer (list copy, newest last).

        Each entry is a StampedSample(timestamp_ns, sample) where timestamp_ns
        is the pad-probe wall-clock CLOCK_MONOTONIC timestamp (stamped on
        norisrc's streaming thread before the tee), or a wall-clock fallback
        at appsink time if the probe lookup failed.  DualCameraManager uses
        these timestamps to match frames across cameras — same-trigger frames
        have probe timestamps within ~1 ms.
        """
        with self._sample_lock:
            return list(self._sample_ring)

    def write_sample_to_file(self, sample: "Gst.Sample", path: str):
        """Schedule a write of a pre-snapshot sample to *path* on a worker thread."""
        logger.info("Capture queued → {}", path)
        QThreadPool.globalInstance().start(_FrameWriter(sample, path))

    def capture_to_file(self, path: str) -> bool:
        """
        Schedule a write of the latest cached MJPEG frame to `path`.

        The write happens on QThreadPool (never blocks the UI thread).
        Returns True if a cached sample was available, False otherwise.
        """
        sample = self.snapshot_sample()
        if sample is None:
            logger.warning("capture_to_file: no cached sample available yet")
            return False

        self.write_sample_to_file(sample, path)
        return True

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        """One of: 'stopped', 'playing', 'error'."""
        return self._state

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def use_overlay(self) -> bool:
        return self._use_overlay

    @property
    def framerate(self) -> str:
        return self._framerate

