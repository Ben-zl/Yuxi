"""Unity Profiler Analyzer - Threshold Definitions"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Thresholds:
    """Performance evaluation thresholds

    Supports both mobile and PC game platforms with different thresholds.
    Use platform parameter to select appropriate thresholds.
    """

    platform: Literal["mobile", "pc"] = "mobile"

    # FPS Thresholds
    fps_excellent: int = field(default=60)
    fps_good: int = field(default=45)
    fps_acceptable: int = field(default=30)
    fps_poor: int = field(default=20)

    # Memory Thresholds (MB)
    memory_total_warning: int = field(default=500)
    memory_total_critical: int = field(default=1000)
    memory_total_emergency: int = field(default=1500)
    memory_mono_warning: int = field(default=200)
    memory_mono_critical: int = field(default=400)

    # Rendering Thresholds
    drawcall_good: int = field(default=100)
    drawcall_warning: int = field(default=300)
    drawcall_critical: int = field(default=500)
    triangles_good: int = field(default=50000)
    triangles_warning: int = field(default=100000)
    triangles_critical: int = field(default=200000)

    # Module Time Thresholds (ms)
    script_warning: int = field(default=5)
    script_critical: int = field(default=10)
    physics_warning: int = field(default=3)
    physics_critical: int = field(default=5)
    rendering_warning: int = field(default=8)
    rendering_critical: int = field(default=12)
    animation_warning: int = field(default=2)
    animation_critical: int = field(default=3)

    # GC Thresholds
    gc_frequency_warning: int = field(default=10)
    gc_frequency_critical: int = field(default=30)
    gc_time_warning: int = field(default=5)
    gc_time_critical: int = field(default=10)

    # Jank Thresholds
    jank_rate_acceptable: float = field(default=0.01)
    jank_rate_poor: float = field(default=0.05)
    jank_rate_terrible: float = field(default=0.10)

    def __post_init__(self):
        """Apply platform-specific thresholds after initialization"""
        if self.platform == "pc":
            # PC/Console Game Thresholds
            # FPS: Higher standards for high refresh rate monitors
            object.__setattr__(self, 'fps_excellent', 144)
            object.__setattr__(self, 'fps_good', 60)
            object.__setattr__(self, 'fps_acceptable', 30)
            object.__setattr__(self, 'fps_poor', 20)

            # Memory: PC has more memory available (8GB-32GB typical)
            object.__setattr__(self, 'memory_total_warning', 2048)      # 2 GB
            object.__setattr__(self, 'memory_total_critical', 4096)     # 4 GB
            object.__setattr__(self, 'memory_total_emergency', 6144)    # 6 GB
            object.__setattr__(self, 'memory_mono_warning', 512)        # 512 MB
            object.__setattr__(self, 'memory_mono_critical', 1024)      # 1 GB

            # Rendering: PC can handle more draw calls
            object.__setattr__(self, 'drawcall_good', 500)
            object.__setattr__(self, 'drawcall_warning', 1000)
            object.__setattr__(self, 'drawcall_critical', 2000)
            object.__setattr__(self, 'triangles_good', 200000)      # 200K triangles
            object.__setattr__(self, 'triangles_warning', 500000)   # 500K triangles
            object.__setattr__(self, 'triangles_critical', 1000000)  # 1M triangles

            # Module Time: More CPU power available
            object.__setattr__(self, 'script_warning', 8)
            object.__setattr__(self, 'script_critical', 16)
            object.__setattr__(self, 'physics_warning', 5)
            object.__setattr__(self, 'physics_critical', 10)
            object.__setattr__(self, 'rendering_warning', 12)
            object.__setattr__(self, 'rendering_critical', 20)
            object.__setattr__(self, 'animation_warning', 3)
            object.__setattr__(self, 'animation_critical', 5)

        else:  # mobile
            # Mobile Game Thresholds (default values already set)
            pass


def create_thresholds(platform: str = "mobile") -> Thresholds:
    """Factory function to create thresholds for a specific platform

    Args:
        platform: 'mobile' or 'pc'

    Returns:
        Thresholds instance with platform-specific values
    """
    return Thresholds(platform=platform)


# Convenience instances for quick access
MOBILE_THRESHOLDS = Thresholds(platform="mobile")
PC_THRESHOLDS = Thresholds(platform="pc")
