"""
Auto PTS calibration GUI — adapted from AutoCamCalib for the Nori dual-camera
RK3588 stack.

GUI layout reused verbatim from ``auto_gui_ui.py`` (graphicsView_left/right
preview panels, camera-settings group, PTS-settings group, four-corner
positioning buttons, Start/Stop scan).  Camera backend swapped from
``HikSyncedCameras`` to ``DualCameraManager`` (Nori SDK + hardware-PWM-triggered
synchronous capture).
"""

import os
import sys
from pathlib import Path

# Force Qt to use X11 (xcb) backend for parity with the main app.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst

from loguru import logger
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QMainWindow,
    QMessageBox,
)

from auto_gui_ui import Ui_MainWIndow
from camera_config import load_camera_config
from camera_pipeline import CameraPipeline, detect_nori_cameras, is_rk3588
from dual_camera_manager import DualCameraManager
from pts.auto_pts import scan_positions, PTSPositionGenerator
from pts.pts_controller import PTSController


SRC_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SRC_DIR / "camera_config.yaml"
DEFAULT_LOCAL_CONFIG = SRC_DIR / "camera_config.local.yaml"

ROOT_DIR = Path.home() / "DCIM"
DEFAULT_EXP = 5000      # microseconds (norisrc sensor-shutter)
DEFAULT_GAIN = 1        # norisrc sensor-gain

DEFAULT_H_FOV = 55
DEFAULT_H_COUNT = 10
DEFAULT_V_FOV = 35
DEFAULT_V_COUNT = 10
DEFAULT_PORT = "/dev/ttyUSB0"

# Delay between PTS arrival and capture so the cameras settle on a fresh
# trigger pulse, mirroring the timing in the original AutoCamCalib scan.
SETTLE_MS = 1500
CAPTURE_HOLD_MS = 1000


def _set_norisrc_property(pipeline: CameraPipeline, prop: str, value):
    """Set a property on the running norisrc element inside ``pipeline``.

    norisrc warns if ``sensor-shutter``/``sensor-gain`` are set while AE is
    on, so this is paired with ``auto-exposure=false`` at construction time.
    """
    gst_pipeline = getattr(pipeline, "_pipeline", None)
    if gst_pipeline is None:
        return False
    it = gst_pipeline.iterate_elements()
    while True:
        result, elem = it.next()
        if result == Gst.IteratorResult.OK:
            factory = elem.get_factory()
            if factory and factory.get_name() == "norisrc":
                try:
                    elem.set_property(prop, value)
                    logger.debug("norisrc {} = {}", prop, value)
                    return True
                except Exception as e:
                    logger.warning("Failed setting norisrc {} = {}: {}", prop, value, e)
                    return False
        elif result == Gst.IteratorResult.RESYNC:
            it.resync()
        else:
            break
    return False


class ScanThread(QThread):
    """Drive a PTS sweep and trigger paired captures at each grid point.

    Signals:
        position_reached(dict) — emitted before each capture, carries the
            PTS controller's reported pose.
        scan_finished()        — terminal signal, emitted on normal completion
            and on error.
    """

    position_reached = Signal(dict)
    scan_finished = Signal()

    def __init__(
        self,
        manager: DualCameraManager,
        save_dir: str,
        port: str = DEFAULT_PORT,
        h_fov: float = DEFAULT_H_FOV,
        v_fov: float = DEFAULT_V_FOV,
        h_count: int = DEFAULT_H_COUNT,
        v_count: int = DEFAULT_V_COUNT,
    ):
        super().__init__()
        self.manager = manager
        self.save_dir = save_dir
        self.port = port
        self.h_fov = h_fov
        self.v_fov = v_fov
        self.h_count = h_count
        self.v_count = v_count
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        try:
            for position_info in scan_positions(
                h_fov=self.h_fov,
                v_fov=self.v_fov,
                h_count=self.h_count,
                v_count=self.v_count,
                port=self.port,
            ):
                if not self._is_running:
                    break

                self.position_reached.emit(position_info)
                QThread.msleep(SETTLE_MS)
                # DualCameraManager.capture() snapshots the ring buffers and
                # schedules file writes on the QThreadPool — safe to call
                # off the GUI thread.
                self.manager.capture(self.save_dir, pts_in_filename=False)
                QThread.msleep(CAPTURE_HOLD_MS)

            self.scan_finished.emit()
        except Exception as e:
            logger.error("Scan process error: {}", e)
            self.scan_finished.emit()


class AutoGui(QMainWindow, Ui_MainWIndow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self._init_graphics_view()
        self._set_default_values()

        self.manager: DualCameraManager | None = None
        self.scan_thread: ScanThread | None = None

        self.actionConnnect_Cameras.triggered.connect(self.connect_camera)
        self.actionCapture_Camera.triggered.connect(self._on_capture_action)
        self.pushButton_start.clicked.connect(self.start_scan_process)
        self.pushButton_stop.clicked.connect(self.stop_scan_process)
        self.pushButton_selectFolder.clicked.connect(self._on_select_folder)

        self.lineEdit_savingPath.setText(str(ROOT_DIR))

    # ------------------------------------------------------------------
    # UI initial state
    # ------------------------------------------------------------------

    def _set_default_values(self):
        self.lineEdit_hFov.setText(str(DEFAULT_H_FOV))
        self.lineEdit_hCount.setText(str(DEFAULT_H_COUNT))
        self.lineEdit_vFov.setText(str(DEFAULT_V_FOV))
        self.lineEdit_vCount.setText(str(DEFAULT_V_COUNT))
        self.lineEdit_serialPort.setText(str(DEFAULT_PORT))
        self.lineEdit_expTime.setText(str(DEFAULT_EXP))
        self.lineEdit_gain.setText(str(DEFAULT_GAIN))

        self.position_generator = PTSPositionGenerator(
            center_pan=90,
            center_tilt=30,
            h_fov=DEFAULT_H_FOV,
            v_fov=DEFAULT_V_FOV,
            h_count=DEFAULT_H_COUNT,
            v_count=DEFAULT_V_COUNT,
        )

        self.lineEdit_hFov.textChanged.connect(self.update_position_generator)
        self.lineEdit_hCount.textChanged.connect(self.update_position_generator)
        self.lineEdit_vFov.textChanged.connect(self.update_position_generator)
        self.lineEdit_vCount.textChanged.connect(self.update_position_generator)

        self.pushButton_botLeft.clicked.connect(self.move_bot_left)
        self.pushButton_botRight.clicked.connect(self.move_bot_right)
        self.pushButton_topRight.clicked.connect(self.move_top_right)
        self.pushButton_topLeft.clicked.connect(self.move_top_left)

    def _init_graphics_view(self):
        self.graphicsView_left.setScene(QGraphicsScene(self))
        self.graphicsView_right.setScene(QGraphicsScene(self))
        self.left_pixmap = QGraphicsPixmapItem()
        self.right_pixmap = QGraphicsPixmapItem()
        self.graphicsView_left.scene().addItem(self.left_pixmap)
        self.graphicsView_right.scene().addItem(self.right_pixmap)

    # ------------------------------------------------------------------
    # PTS controls
    # ------------------------------------------------------------------

    def update_position_generator(self):
        try:
            self.position_generator.update_params(
                h_fov=float(self.lineEdit_hFov.text()),
                v_fov=float(self.lineEdit_vFov.text()),
                h_count=int(self.lineEdit_hCount.text()),
                v_count=int(self.lineEdit_vCount.text()),
            )
        except ValueError:
            # User mid-edit; ignore until inputs are parseable.
            pass

    def _move_to(self, position):
        logger.info("移动到位置: {}", position)
        try:
            pts_controller = PTSController(port=self.lineEdit_serialPort.text())
            pts_controller.set_pan_tilt(position[0], position[1])
            pts_controller.close()
        except Exception as e:
            logger.error("PTS move failed: {}", e)
            QMessageBox.warning(self, "PTS error", str(e))

    def move_bot_left(self):
        self._move_to(self.position_generator.bottom_left())

    def move_bot_right(self):
        self._move_to(self.position_generator.bottom_right())

    def move_top_right(self):
        self._move_to(self.position_generator.top_right())

    def move_top_left(self):
        self._move_to(self.position_generator.top_left())

    # ------------------------------------------------------------------
    # Save-folder picker
    # ------------------------------------------------------------------

    def _on_select_folder(self):
        current = self.lineEdit_savingPath.text() or str(ROOT_DIR)
        folder = QFileDialog.getExistingDirectory(
            self, "Select capture folder", current
        )
        if folder:
            self.lineEdit_savingPath.setText(folder)

    # ------------------------------------------------------------------
    # Camera connect / capture
    # ------------------------------------------------------------------

    def connect_camera(self):
        if self.manager is not None:
            QMessageBox.information(self, "Cameras", "Cameras already connected.")
            return

        cfg = load_camera_config(DEFAULT_CONFIG, DEFAULT_LOCAL_CONFIG)

        # Apply UI exp/gain into per-camera settings; force AE off so that
        # sensor-shutter / sensor-gain are honoured by norisrc.
        try:
            exp = int(self.lineEdit_expTime.text())
            gain = int(self.lineEdit_gain.text())
        except ValueError:
            exp, gain = DEFAULT_EXP, DEFAULT_GAIN

        for s in (cfg.left, cfg.right):
            s.auto_exposure = False
            s.sensor_shutter = exp
            s.sensor_gain = gain

        # Resolve Nori device indices by role tag, fall back to enumeration.
        cameras = detect_nori_cameras()
        if not cameras:
            QMessageBox.critical(
                self,
                "No cameras",
                "No Nori cameras detected. Check the SDK installation.",
            )
            return

        by_tag = {c.tag: c.index for c in cameras if c.tag}
        left_idx = by_tag.get(cfg.left.role)
        right_idx = by_tag.get(cfg.right.role)
        if left_idx is None or right_idx is None or left_idx == right_idx:
            indices = [c.index for c in cameras][:2]
            logger.warning(
                "Falling back to USB enumeration order {} (roles {}/{} not matched)",
                indices, cfg.left.role, cfg.right.role,
            )
        else:
            indices = [left_idx, right_idx]

        # Force appsink fallback so preview frames land in QGraphicsView via
        # the preview_frame(QImage) signal — VideoOverlay can't render into
        # QGraphicsView, and the original layout uses QGraphicsPixmapItem.
        self.manager = DualCameraManager(
            device_indices=indices,
            use_overlay=False,
            camera_settings=[cfg.left, cfg.right],
        )

        # Wire previews: cam0 -> graphicsView_left, cam1 -> graphicsView_right
        for canvas_pos in range(2):
            pipe = self.manager.pipeline_for_canvas(canvas_pos)
            if pipe is None:
                continue
            if canvas_pos == 0:
                pipe.preview_frame.connect(self.update_left_frame)
            else:
                pipe.preview_frame.connect(self.update_right_frame)

        # Live exp/gain editing — applied directly to the running norisrc element.
        self.lineEdit_expTime.textChanged.connect(self._apply_exp)
        self.lineEdit_gain.textChanged.connect(self._apply_gain)

        results = self.manager.start([None, None])
        started = sum(1 for r in results if r)
        logger.info("{}/2 pipelines started", started)
        if started == 0:
            QMessageBox.critical(self, "Cameras", "Failed to start any pipeline.")

    def _apply_exp(self):
        if self.manager is None:
            return
        try:
            value = int(self.lineEdit_expTime.text())
        except ValueError:
            return
        for i in range(2):
            pipe = self.manager.pipeline(i)
            if pipe is not None:
                _set_norisrc_property(pipe, "sensor-shutter", value)

    def _apply_gain(self):
        if self.manager is None:
            return
        try:
            value = int(self.lineEdit_gain.text())
        except ValueError:
            return
        for i in range(2):
            pipe = self.manager.pipeline(i)
            if pipe is not None:
                _set_norisrc_property(pipe, "sensor-gain", value)

    def _on_capture_action(self):
        if self.manager is None:
            QMessageBox.warning(self, "警告", "请先连接相机")
            return
        self.manager.capture(self.lineEdit_savingPath.text(), pts_in_filename=False)

    # ------------------------------------------------------------------
    # Scan thread lifecycle
    # ------------------------------------------------------------------

    def start_scan_process(self):
        if self.manager is None:
            QMessageBox.warning(self, "警告", "请先连接相机")
            return

        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.stop()
            self.scan_thread.wait()

        self.scan_thread = ScanThread(
            self.manager,
            save_dir=self.lineEdit_savingPath.text(),
            port=self.lineEdit_serialPort.text(),
            h_fov=float(self.lineEdit_hFov.text()),
            v_fov=float(self.lineEdit_vFov.text()),
            h_count=int(self.lineEdit_hCount.text()),
            v_count=int(self.lineEdit_vCount.text()),
        )
        self.scan_thread.position_reached.connect(self.on_position_reached)
        self.scan_thread.scan_finished.connect(self.on_scan_finished)
        self.scan_thread.start()

        self.pushButton_start.setEnabled(False)

    def stop_scan_process(self):
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.stop()
            logger.info("扫描已请求停止")

    def on_position_reached(self, position_info):
        logger.info("到达位置 {}", position_info.get('index'))

    def on_scan_finished(self):
        self.pushButton_start.setEnabled(True)
        logger.info("扫描过程完成")

    # ------------------------------------------------------------------
    # Preview rendering
    # ------------------------------------------------------------------

    def update_left_frame(self, image: QImage):
        self.left_pixmap.setPixmap(QPixmap.fromImage(image))
        self.graphicsView_left.fitInView(self.left_pixmap, Qt.KeepAspectRatio)

    def update_right_frame(self, image: QImage):
        self.right_pixmap.setPixmap(QPixmap.fromImage(image))
        self.graphicsView_right.fitInView(self.right_pixmap, Qt.KeepAspectRatio)

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.stop()
            self.scan_thread.wait(2000)
        if self.manager is not None:
            self.manager.stop()
        super().closeEvent(event)


def main():
    Gst.init(None)
    if is_rk3588() and not Gst.ElementFactory.find("norisrc"):
        logger.error("GStreamer element 'norisrc' not found. Is gst-nori installed?")
        sys.exit(1)

    app = QApplication(sys.argv)
    window = AutoGui()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
