"""Camera configuration via YAML (omegaconf).

Loads a committed base ``camera_config.yaml`` merged with an optional,
gitignored ``camera_config.local.yaml`` override.  The schema covers the
norisrc attributes we expose for per-camera tuning (left / right).

Precedence: structured-schema defaults -> base yaml -> local yaml.

Field names use underscores here; they are translated to the GStreamer
property names (with dashes) where ``norisrc`` is actually configured.
See ``gst-inspect-1.0 norisrc`` for the source of truth.
"""

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from omegaconf import MISSING, OmegaConf


MIRROR_FLIP_VALUES = ("normal", "mirror", "flip", "mirror-flip")


@dataclass
class CameraSettings:
    role: str = MISSING            # nori-ctl tag, e.g. "LEFT" / "RIGHT"
    auto_exposure: bool = True
    auto_white_balance: bool = True
    sensor_shutter: int = 5000     # microseconds, used only when auto_exposure=false
    sensor_gain: int = 1           # analog multiplier, used only when auto_exposure=false
    mirror_flip: str = "normal"    # one of MIRROR_FLIP_VALUES


@dataclass
class CameraConfig:
    left: CameraSettings = field(default_factory=CameraSettings)
    right: CameraSettings = field(default_factory=CameraSettings)


def _validate(cfg: CameraConfig) -> None:
    for side in ("left", "right"):
        s: CameraSettings = getattr(cfg, side)
        if s.mirror_flip not in MIRROR_FLIP_VALUES:
            raise ValueError(
                f"{side}.mirror_flip={s.mirror_flip!r} not in {list(MIRROR_FLIP_VALUES)}"
            )
        if not s.role:
            raise ValueError(f"{side}.role must be set (got empty string)")


def load_camera_config(
    base_path: Path,
    local_path: Path | None = None,
) -> CameraConfig:
    """Load and merge camera config.

    ``base_path`` must exist.  ``local_path`` is optional; if provided and
    present on disk its keys are deep-merged on top of the base.
    """
    base_path = Path(base_path)
    if not base_path.exists():
        raise FileNotFoundError(f"Camera config not found: {base_path}")

    schema = OmegaConf.structured(CameraConfig)
    layers = [schema, OmegaConf.load(base_path)]
    local_applied: Path | None = None
    if local_path is not None:
        local_path = Path(local_path)
        if local_path.exists():
            layers.append(OmegaConf.load(local_path))
            local_applied = local_path

    merged = OmegaConf.merge(*layers)
    cfg: CameraConfig = OmegaConf.to_object(merged)
    _validate(cfg)

    logger.info(
        "Camera config: base={} local={}",
        base_path,
        local_applied if local_applied else "(none)",
    )
    for side in ("left", "right"):
        s = getattr(cfg, side)
        logger.info(
            "  {:5s} role={} AE={} AWB={} sensor-shutter={}us sensor-gain={} mirror-flip={}",
            side, s.role, s.auto_exposure, s.auto_white_balance,
            s.sensor_shutter, s.sensor_gain, s.mirror_flip,
        )
    return cfg
