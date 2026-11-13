"""
Matching by drawing style — engineers recognise objects by how they are drawn, not by the layer name:
a main line is drawn continuous and heavy, a lateral dashed and thin; a manhole and a valve are
different symbols even when both sit on one "IRR-FITTINGS" layer.

A layer whose entities of one geometry kind are drawn in clearly different styles is split into one
mapping group per style:
  lines    : linetype + lineweight + colour
  points   : block symbol (variants of the same name — "Boulder Rock 1..5" — stay together)
  polygons : hatch pattern + colour
Group key:  "<layer>::<kind>"  or  "<layer>::<kind>@<style>"
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from .model import FEATURE_KINDS, CadDocument, CadEntity

MIN_SHARE = 0.10   # a style must hold >= 10 % of the group (and >= 2 entities) to get its own group


def _block_family(name: str) -> str:
    """'Boulder Rock 3' / 'TREE-02' -> 'boulder rock' / 'tree' (variants of one symbol)."""
    n = re.sub(r"[\s_\-]*\(?\d+\)?$", "", (name or "").strip().lower())
    return re.sub(r"[\s_\-]+", " ", n) or (name or "").lower()


def style_of(e: CadEntity) -> Tuple[str, str]:
    """(machine key, human label)"""
    if e.kind == "point":
        if e.block_name:
            fam = _block_family(e.block_name)
            return f"blk:{fam}", f"block {e.block_name if fam == e.block_name.lower() else fam}"
        if e.dxftype == "CIRCLE":
            return f"circle:{round((e.radius or 0) * 2, 2)}", f"circle Ø{round((e.radius or 0) * 2, 2)}"
        return f"pt:{e.dxftype}", e.dxftype.lower()
    if e.kind == "line":
        lt = (e.linetype or "Continuous").upper()
        lw = e.lineweight
        return f"ln:{lt}:{lw}:{e.color}", f"{lt.title()}{f' {lw / 100:.2f}mm' if lw else ''}{f' c{e.color}' if e.color is not None else ''}"
    if e.dxftype == "HATCH":
        return f"h:{(e.pattern or 'SOLID').upper()}:{e.color}", f"hatch {e.pattern or 'SOLID'}{f' c{e.color}' if e.color is not None else ''}"
    return f"pg:{e.color}:{(e.linetype or '').upper()}", f"outline{f' c{e.color}' if e.color is not None else ''}"


def assign_groups(cad: CadDocument) -> Dict[str, str]:
    by: Dict[Tuple[str, str], List[CadEntity]] = defaultdict(list)
    for e in cad.entities:
        if e.kind in FEATURE_KINDS:
            by[(e.layer, e.kind)].append(e)
    labels: Dict[str, str] = {}
    for (layer, kind), ents in by.items():
        base = f"{layer}::{kind}"
        styled = [(style_of(e), e) for e in ents]
        counts = Counter(s[0] for s, _ in styled)
        major = {k for k, n in counts.items() if n >= max(2, MIN_SHARE * len(ents))}
        if kind == "line":
            # one long joined main among many short laterals is still its own object: weigh by drawn length
            total = sum(e.length() for e in ents) or 1.0
            share = Counter()
            for (k, _), e in styled:
                share[k] += e.length() / total
            major |= {k for k, v in share.items() if v >= 0.15}
        elif kind == "point":
            # a different symbol is a different object (valve vs controller), even when drawn once
            major = {k for k in counts if k.startswith("blk:")} if sum(k.startswith("blk:") for k in counts) > 1 else major
        if kind == "polygon":
            # hatch fills and closed outlines of one layer are the same thing drawn two ways
            major = {k for k in major if k.startswith("h:")} if len({k for k in major if k.startswith("h:")}) > 1 else set()
        if len(major) <= 1:
            for e in ents:
                e.group = base
            continue
        label_of = {s[0]: s[1] for s, _ in styled}
        for (k, _), e in styled:
            if k in major:
                e.group = f"{base}@{label_of[k]}"
                labels[e.group] = label_of[k]
            else:
                e.group = base
    cad.styles = labels
    return labels


def split_group(group: str) -> Tuple[str, str, str]:
    """'L::line@Dashed 0.18mm c3' -> ('L', 'line', 'Dashed 0.18mm c3')"""
    layer, _, rest = group.rpartition("::")
    kind, _, style = rest.partition("@")
    return layer, kind, style
