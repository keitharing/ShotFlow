from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class QCThresholds:
    # FFmpeg filters
    black_min_s: float = 0.1
    black_pixel_th: float = 0.10
    edge_fade_max_s: float = 0.5  # black only at start/end and shorter than this = fade (info)
    freeze_noise_db: float = -60.0
    freeze_min_s: float = 0.5
    freeze_warning_s: float = 1.0  # shorter freezes are usually intentional static product holds
    silence_noise_db: float = -50.0
    silence_min_s: float = 0.5
    true_peak_max_dbfs: float = -0.1
    loudness_tolerance_lu: float = 3.0
    # Frame analysis (on downscaled frames)
    analysis_width: int = 320
    flicker_min_jump: float = 6.0  # luma levels (0-255) up and back down within 2 frames
    flicker_sigma: float = 6.0
    # Cut = frame difference far above its neighbours. Calibrated on v0.4 (decision D-002):
    # 12/12 real cuts had diff >= 31 and ratio >= 5.7; non-cuts peaked at diff 10.2, ratio 4.2.
    cut_min_motion: float = 15.0
    cut_min_ratio: float = 5.0
    cut_window: int = 6
    dark_luma: float = 16.0  # frames this dark are fades, not cuts
    # Boundary sheet
    boundary_delta_e_info: float = 15.0

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


DEFAULT = QCThresholds()
