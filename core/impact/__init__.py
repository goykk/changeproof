"""Diff 映射与影响传播。"""

from .mapper import map_changes
from .propagation import ImpactOutput, compute_impact

__all__ = ["ImpactOutput", "compute_impact", "map_changes"]