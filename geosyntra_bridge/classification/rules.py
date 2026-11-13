"""
Multi-signal scoring functions. Each returns (weight_contribution, evidence_string|None).

Weights are deliberately modest and additive (nothing alone reaches HIGH
confidence) so the engine never confidently classifies on a single weak
signal — it takes corroborating evidence, consistent with "never silently
assign uncertain features to an incorrect Feature Class."
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..schema.models import LayerDef


def score_geometry_type(group_geom_type: str, layer: LayerDef) -> Tuple[float, Optional[str]]:
    hints = layer.classification_hints
    if not hints.geometry_types:
        return 0.0, None
    if group_geom_type in hints.geometry_types:
        return 0.30, f"geometry type '{group_geom_type}' matches expected for {layer.name}"
    # Wrong geometry type is a hard mismatch signal.
    return -0.50, f"geometry type '{group_geom_type}' does not match {layer.name} ({hints.geometry_types})"


def score_layer_name(source_layer_name: str, layer: LayerDef) -> Tuple[float, Optional[str]]:
    hints = layer.classification_hints
    for pattern in hints.layer_name_patterns:
        try:
            if re.search(pattern, source_layer_name, re.IGNORECASE):
                return 0.25, f"CAD layer name '{source_layer_name}' matches pattern /{pattern}/"
        except re.error:
            continue
    return 0.0, None


def score_text_keywords(texts: List[str], layer: LayerDef) -> Tuple[float, Optional[str]]:
    hints = layer.classification_hints
    if not hints.text_keywords or not texts:
        return 0.0, None
    joined = " ".join(t.lower() for t in texts)
    for kw in hints.text_keywords:
        if kw.lower() in joined:
            return 0.20, f"nearby label text contains keyword '{kw}'"
    return 0.0, None


def score_block_names(block_names: List[str], layer: LayerDef) -> Tuple[float, Optional[str]]:
    hints = layer.classification_hints
    if not hints.block_name_patterns or not block_names:
        return 0.0, None
    for bn in block_names:
        for pattern in hints.block_name_patterns:
            try:
                if re.search(pattern, bn, re.IGNORECASE):
                    return 0.20, f"block reference name '{bn}' matches pattern /{pattern}/"
            except re.error:
                continue
    return 0.0, None


def score_typical_length(avg_length: Optional[float], layer: LayerDef) -> Tuple[float, Optional[str]]:
    hints = layer.classification_hints
    if avg_length is None or not hints.typical_length_range:
        return 0.0, None
    lo, hi = hints.typical_length_range
    if lo <= avg_length <= hi:
        return 0.10, f"average segment length {avg_length:.1f}m within expected range [{lo}, {hi}]"
    return 0.0, None


def score_learned_rule(matched: bool) -> Tuple[float, Optional[str]]:
    if matched:
        return 0.45, "matches a previously approved mapping rule"
    return 0.0, None
