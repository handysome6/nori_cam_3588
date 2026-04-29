# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PySide6 GUI application for dual 20MP Nori Xvision camera preview and capture on **Rockchip RK3588**. Two cameras stream MJPEG at 5120x3840 via the **Nori SDK GStreamer source (`norisrc`)** in **hardware-trigger** mode. A single onboard PWM (default `pwmchip3/pwm0` @ 27 Hz, 100 µs pulse) fans out to both cameras' trigger inputs so every frame pair is exposed simultaneously. The goal is dual 720p live previews with single-click full-resolution synchronized capture (saving raw MJPEG frames as .jpg without re-encoding).

## Reference Implementation

The sibling project `/home/cat/workspace/uvc_cam_jetson/src/` is the NVIDIA Jetson Orin reference (UVC `v4l2src` + `nvv4l2decoder`). This project replicates its architecture for RK3588 hardware, replacing UVC with `norisrc` and NVIDIA decoders with Rockchip MPP.

## Running the Application

```bash
cd src
python main.py                                          # auto-detect via nori-ctl
python main.py --device-indices 0,1                     # explicit Nori device indices
python main.py --trigger-fps 27                         # PWM frequency in Hz (default 27)
python main.py --no-overlay                             # appsink fallback (no VideoOverlay)
python main.py --pts-filename                           # embed probe timestamps in filenames
python main.py --config path/to/base.yaml --local-config path/to/local.yaml
```

One-time PWM setup (non-root sysfs access for `FsyncTrigger`):

```bash
sudo ./scirpts/setup_pwm.sh                             # default pwmchip3/pwm0
sudo ./scirpts/setup_pwm.sh --chip pwmchip0 --channel 1 # other boards
sudo ./scirpts/setup_pwm.sh --uninstall
```

Single-camera pan/zoom GUI (live `videocrop` mutation against the native 5120×3840 MJPEG):

```bash
python src/single_cam_pan_zoom.py                       # interactive picker / first cam
python src/single_cam_pan_zoom.py --device-index 0
python src/single_cam_pan_zoom.py --role LEFT
```

Image pair viewer:

```bash
python src/image_compare.py <directory-with-A_/D_-pairs>
```

## Running Tests

```bash
# Headless dual camera sync test (no display needed)
# Note: folder name has a typo ("scirpts" not "scripts")
python3 scirpts/headless_sync_test.py                                  # auto-detect, 20 captures
python3 scirpts/headless_sync_test.py --captures 5 --interval 2.0
python3 scirpts/headless_sync_test.py --framerate 10/1                 # 10 Hz mode

# Single-camera GStreamer pipeline tests
python3 scirpts/gst_nori_single_cam.py                                 # norisrc-based
python3 scirpts/gst_uvc_single_cam.py                                  # generic UVC v4l2src
```

## Target Platform

- Rockchip RK3588 SoC (aarch64, Linux 6.1.84)
- GStreamer 1.20.3 with Rockchip MPP HW-accelerated plugins
- X11 display server (Wayland is not supported for the video sink)
- **Nori SDK GStreamer plugin** providing `norisrc` (`gst-inspect-1.0 norisrc`) and `nori-ctl` CLI
- Hardware PWM at `pwmchip3/pwm0` driving the cameras' shared trigger input
- Two cameras attached to separate USB host controllers

## RK3588 GStreamer Element Mapping

| Stage       | Jetson (NVIDIA)                 | RK3588 (Rockchip + Nori)         |
|-------------|--------------------------------|-----------------------------------|
| Source      | `v4l2src` (UVC)                | `norisrc trigger-mode=hardware`   |
| JPEG decode | `nvv4l2decoder mjpeg=1`        | `mppjpegdec`                      |
| Resize      | `nvvidconv` + caps filter       | `mppjpegdec width=W height=H` (built-in) |
| Color fmt   | `video/x-raw(memory:NVMM),NV12`| `video/x-raw,NV12`                |
| Display     | `nveglglessink`                | `xvimagesink`                     |

Key difference: `mppjpegdec` has built-in `width`/`height` properties that do HW-accelerated downscale inside the decoder itself — no separate resize element needed.

## Architecture

Each camera runs an independent GStreamer pipeline with a `tee` splitting into a preview branch and a capture branch:

```
norisrc device-index=N trigger-mode=hardware <settings>
  ! image/jpeg,width=5120,height=3840
  ! tee name=t  --[pad probe stamps CLOCK_MONOTONIC here]
     ├─ Preview: queue(leaky) -> jpegparse (JPEG-validate probe)
     │                        -> mppjpegdec width=1280 height=720 format=NV12
     │                        -> xvimagesink (VideoOverlay into Qt widget)
     └─ Capture: queue(leaky) -> appsink (latest raw MJPEG frame only)
```

Framerate is **not** specified in caps when `trigger-mode=hardware` — the rate is dictated by the external PWM signal and GStreamer negotiates from `norisrc`'s advertised modes.

**Key classes (`src/`):**
- `CameraPipeline` — one per camera; builds/starts/stops the GStreamer pipeline, pad probe on tee sink pad for frame timestamps, ring buffer of 5 stamped samples, JPEG validation probe on `jpegparse` sink pad. Per-camera `norisrc` settings (auto exposure / WB, sensor shutter, sensor gain, mirror-flip) injected from `CameraSettings`.
- `DualCameraManager` — manages two `CameraPipeline` instances and the shared `FsyncTrigger`. Coordinates synchronized capture by matching frames across cameras using minimum timestamp delta (O(N²) with N=5). Starts the PWM on a 2.5 s delay after both pipelines reach PLAYING so both cameras are armed before the first pulse arrives.
- `FsyncTrigger` (`fsync_trigger.py`) — wraps the `/sys/class/pwm/<chip>/<channel>` interface to start/stop the hardware trigger pulse train (default 27 Hz, 100 µs duty). Setup script `scirpts/setup_pwm.sh` installs udev + systemd to grant non-root access.
- `MainWindow` — two preview panels (`_PreviewWidget` with `WA_NativeWindow + WA_PaintOnScreen` for `xvimagesink` VideoOverlay), capture/auto-capture/swap buttons, save path editor, PTS filename toggle.
- `main.py` — entry point; resolves Nori device indices via role-based tag matching (`LEFT`/`RIGHT` from `nori-ctl list`) before falling back to enumeration order; loads YAML config; constructs `DualCameraManager`.
- `camera_config.py` — `omegaconf`-based `CameraConfig` dataclass schema; loads committed `camera_config.yaml` deep-merged with optional gitignored `camera_config.local.yaml`. Field names map 1:1 to `norisrc` properties (underscore ↔ dash).
- `single_cam_pan_zoom.py` — standalone single-camera GUI that mutates a `videocrop` element live in PLAYING for true zoom (fresh pixels from the 5120×3840 stream, not preview upscaling).
- `image_compare.py` — standalone side-by-side A/D image pair viewer with zoom, pan, and thumbnail navigation.

**Test scripts (`scirpts/`):**
- `headless_sync_test.py` — headless dual camera sync test (no display/Qt needed); minimal `*src→queue→appsink` pipelines with the same probe-timestamp matching as the GUI.
- `gst_nori_single_cam.py` — single-camera Nori SDK pipeline test.
- `gst_uvc_single_cam.py` — single-camera generic UVC pipeline test.
- `gst_common.py` — shared helpers used by the gst test scripts.
- `setup_pwm.sh` — udev + systemd setup for non-root PWM access.

## Camera Configuration

Per-camera knobs live in `src/camera_config.yaml` (committed defaults) and `src/camera_config.local.yaml` (gitignored override, deep-merged on top). Schema:

```yaml
left:
  role: LEFT              # nori-ctl tag used to identify the physical camera
  auto_exposure: true     # false resets UVC exposure/gain, then applies sensor_*
  auto_white_balance: false
  sensor_shutter: 5000    # microseconds, used only when auto_exposure=false
  sensor_gain: 1          # analog gain, used only when auto_exposure=false
  mirror_flip: normal     # normal | mirror | flip | mirror-flip
right:
  role: RIGHT
  ...
```

Physical-to-canvas mapping at startup uses `nori-ctl` tags: `left.role` → left canvas, `right.role` → right canvas. If either tag is missing or unmatched, `main.py` falls back to USB enumeration order and warns.

## Frame Sync Mechanism

Both cameras receive the same hardware PWM pulse, but USB delivery introduces 1–2 frame skew between branches. The sync mechanism:

1. **Pad probe** on the tee's sink pad stamps `clock_gettime_ns(CLOCK_MONOTONIC)` on `norisrc`'s streaming thread (before `tee` fans out to branches). Stored in a side-channel dict keyed by `buffer.pts`.
2. **Capture appsink callback** looks up the probe timestamp by PTS and appends `StampedSample(timestamp_ns, sample)` to a ring buffer (depth=5).
3. **On capture**, both ring buffers are snapshot and the pair with minimum timestamp delta is selected.

Same-trigger frames typically match within ~1–3 ms across separate USB controllers; adjacent triggers at 27 Hz are ~37 ms apart, so the closest-pair rule is unambiguous.

**Critical**: Both preview and capture queues must be `leaky=downstream`. The preview queue being leaky prevents `mppjpegdec` backpressure from blocking `norisrc`'s thread, which would add jitter to the probe timestamps. See `docs/dual-camera-sync-investigation.md` for the full investigation and `docs/frame-sync-future-directions.md` for the evolution of the timestamp approach.

## Critical Design Constraints

- **No re-encoding on capture**: save raw MJPEG buffer bytes directly as .jpg — never use `cv::imwrite()` or PIL.
- **Both queues must be leaky**: preview `leaky=downstream max-size-buffers=2` (prevents backpressure jitter on probe timestamps), capture `leaky=downstream max-size-buffers=1` (only keep latest).
- **Always-cached latest frames**: continuously cache via appsink callback into a 5-deep ring buffer; match and write on button press.
- **File writes on worker thread**: never block the UI thread for disk I/O (use `QThreadPool` / `_FrameWriter`).
- **No GLib main loop**: Qt event loop only; poll the GStreamer bus via `QTimer`.
- **JPEG validation before jpegparse**: pad probe drops frames with corrupt SOI/EOI markers — prevents fatal decoder errors at high bitrate.
- **No dependency on GStreamer base_time or PTS computation**: frame matching uses pad-probe wall-clock timestamps, not `buffer.pts + pipeline.base_time` (fragile under pipeline restart, caps renegotiation, and leaky queues producing synthetic PTS).
- **Fsync starts after pipelines are PLAYING**: `DualCameraManager` defers PWM start by 2.5 s so both cameras are armed and waiting for the first pulse — firing earlier lets one camera miss the initial pulses and stay black.
- **`sensor-shutter`/`sensor-gain` only when AE is off**: `norisrc` warns otherwise, and toggling AE off resets UVC exposure/gain registers before applying the configured values.

## Platform-Specific Issues

- **`rkximagesink` is broken**: hardcodes `/dev/dri/card0` which is RKNPU on this board, not the display controller. Use `xvimagesink` instead.
- **`xvimagesink` supports `GstVideoOverlay`**: embed in Qt widgets via `set_window_handle()` — same pattern as Jetson's `nveglglessink`.
- **Qt under X11 only**: `main.py` forces `QT_QPA_PLATFORM=xcb` because `xvimagesink` needs an X11 window ID.
- **Platform detection**: check for `mppjpegdec` element factory via `Gst.ElementFactory.find("mppjpegdec")`. Without it, `CameraPipeline` falls back to a `videotestsrc`-based dev pipeline.
- **PWM sysfs requires permissions**: `chown` is silently ignored on sysfs attribute files; `setup_pwm.sh` uses `chmod a+rw` plus a udev rule + systemd oneshot to keep it usable across reboots.

## Quick GStreamer Test Commands

```bash
# Full resolution with HW downscale to 720p (Nori source, hardware trigger)
gst-launch-1.0 norisrc device-index=0 trigger-mode=hardware \
  ! image/jpeg,width=5120,height=3840 \
  ! jpegparse ! mppjpegdec width=1280 height=720 format=NV12 \
  ! xvimagesink sync=false

# Decode benchmark (no display)
gst-launch-1.0 norisrc device-index=0 trigger-mode=hardware \
  ! image/jpeg,width=5120,height=3840 \
  ! jpegparse ! mppjpegdec width=1280 height=720 format=NV12 \
  ! fakesink sync=false

# Generic UVC fallback (no Nori SDK)
gst-launch-1.0 v4l2src device=/dev/video0 io-mode=mmap \
  ! image/jpeg,width=5120,height=3840,framerate=55/2 \
  ! jpegparse ! mppjpegdec width=1280 height=720 format=NV12 \
  ! xvimagesink sync=false
```

When using `norisrc trigger-mode=hardware`, the PWM must be active for frames to flow — start it via `FsyncTrigger` or echo into `/sys/class/pwm/pwmchip3/pwm0/{period,duty_cycle,enable}`.

## Tech Stack

- **Python 3.10+** with **PySide6** (Qt 6)
- **GStreamer 1.20.3** via `gi.repository: Gst, GstVideo`
- **Nori SDK** providing `norisrc` GStreamer source and `nori-ctl` CLI
- **omegaconf** for layered YAML config
- **loguru** for structured logging
- Camera devices: identified by `nori-ctl` device index (not `/dev/videoN`); resolved by role tag at startup

## Documentation

- `docs/dual-camera-sync-investigation.md` — full investigation of the frame sync problem, experiments, root cause analysis, and the implemented fix.
- `docs/frame-sync-future-directions.md` — timestamp approaches evaluated (pad probe implemented, camera SDK for long-term).
- `docs/rk3588-gstreamer-pipeline.md` — GStreamer pipeline architecture notes.
