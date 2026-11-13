"""
CAD Key Reader — reads the drawing's key (legend / keynotes) the way an engineer does.

Sources (all read-only):
  1. Keynotes in model space: TEXT / MTEXT / MULTILEADER starting with a code
     ("HS.09 Natural Crushed Gravel Aggregate (Dark Grey)"); the leader arrow head
     shows which entity the note describes.
  2. Legend on a layout sheet (paper space): rows of  [symbol] [code] description,
     where the symbol is a hatch swatch, block or line sample. The symbol's layer,
     block name or hatch pattern links the row to model-space layers.
  3. Legend drawn in model space outside the plan (same row structure).

Every key entry is linked to CAD layers by combining independent evidence:
  name similarity (description vs layer name), leader votes, symbol layer,
  block name and hatch pattern + colour.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from .model import FEATURE_KINDS, TEXT, CadDocument, CadEntity

CODE_RX = re.compile(r"^\s*(?P<code>[A-Z]{1,5}[.\-_ ]?\d{1,3}[A-Z]?(?:\.\d{1,2})?)\s*[:\-–]?\s+(?P<desc>[A-Za-z(].{2,})$")


@dataclass
class KeyEntry:
    code: str
    description: str
    source: str                                   # keynote | legend
    occurrences: int = 0
    layers: Dict[str, float] = field(default_factory=dict)   # layer -> link score 0..1
    evidence: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def best_layer(self) -> Optional[str]:
        return max(self.layers, key=self.layers.get) if self.layers else None

    def to_dict(self):
        return {"code": self.code, "description": self.description, "source": self.source,
                "occurrences": self.occurrences, "layers": dict(sorted(self.layers.items(), key=lambda x: -x[1])),
                "best_layer": self.best_layer, "evidence": self.evidence}


_STOP = {"hs", "ss", "the", "of", "and", "with", "type", "hatch", "layer", "new", "existing", "prop", "proposed",
         "mm", "m", "x", "to", "for", "in", "on"}


def words(s: str) -> List[str]:
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s or "")
    out = []
    for w in re.findall(r"[a-zA-Z]+", s.lower()):
        if len(w) < 2 or w in _STOP:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def name_similarity(a: str, b: str) -> float:
    wa, wb = set(words(a)), set(words(b))
    if not wa or not wb:
        return 0.0
    inter = len(wa & wb)
    return inter / min(len(wa), len(wb)) if inter else 0.0


def _parse_key_text(t: CadEntity) -> Optional[Tuple[str, str]]:
    txt = (t.text or "").replace("\n", " ").strip()
    m = CODE_RX.match(txt)
    if not m:
        return None
    return m.group("code").replace(" ", "").upper(), " ".join(m.group("desc").split())


def _geom(e: CadEntity):
    try:
        if e.kind == "point":
            return Point(e.center[:2])
        if e.kind == "polygon" and len(e.coords) >= 4:
            p = Polygon([c[:2] for c in e.coords])
            return p if p.is_valid else p.buffer(0)
        if len(e.coords) >= 2:
            return LineString([c[:2] for c in e.coords])
    except Exception:  # noqa: BLE001
        return None
    return None


LEGEND_TITLE = re.compile(r"^\s*((hard|soft)?\s*(scape|landscape)?\s*(material|materials|planting|plant)?\s*"
                          r"(legend|key|schedule)s?|مفتاح|مفتاح الرسم|دليل)\b", re.I)
NOT_LEGEND = re.compile(r"key\s*plan|location\s*plan", re.I)

LEVELS = [
    ("layout_legend", "Legend on a layout sheet"),
    ("drawing_legend", "Legend drawn on the drawing sheet (model space)"),
    ("drawing_keynotes", "Coded keynotes on the drawing sheet"),
    ("layers", "No key found — CAD layers, blocks and geometry interpreted"),
]


class _SymbolIndex:
    """Candidate legend swatches sorted by their centre height: a text only looks at the symbols of its own row
    (the old scan of every symbol for every text took minutes on large plans)."""

    def __init__(self, symbols: List[CadEntity], max_size: float):
        import numpy as np
        items = []
        for s in symbols:
            bb = s.bbox()
            if not bb or max(bb[2] - bb[0], bb[3] - bb[1]) > max_size:
                continue
            items.append((s, bb))
        self.syms = [s for s, _ in items]
        B = np.array([bb for _, bb in items], dtype=float).reshape(-1, 4)
        cy = (B[:, 1] + B[:, 3]) / 2
        order = np.argsort(cy, kind="stable")
        self.B, self.cy, self.idx = B[order], cy[order], order
        self.np = np

    def best(self, t: CadEntity) -> Optional[CadEntity]:
        np = self.np
        if not len(self.cy):
            return None
        h = max(t.text_height or 0.0, 0.25)
        tx, ty = t.center[0], t.center[1]
        lo = np.searchsorted(self.cy, ty - h - 1.6 * h, "left")
        hi = np.searchsorted(self.cy, ty + 1.6 * h, "right")
        if hi <= lo:
            return None
        B, cy = self.B[lo:hi], self.cy[lo:hi]
        dy = np.minimum(np.minimum(np.abs(cy - ty), np.abs(cy - (ty - h / 2))), np.abs(cy - (ty - h)))
        ok = (B[:, 2] <= tx + h * 0.5) & (dy <= 1.6 * h) & (tx - B[:, 2] <= 25 * h)
        if not ok.any():
            return None
        d = np.where(ok, (tx - B[:, 2]) + 3 * dy, np.inf)
        k = int(np.argmin(d))           # first minimum, in the original order of equal rows
        return self.syms[int(self.idx[lo + k])]


def _symbol_for(t: CadEntity, symbols: List[CadEntity], max_size: float) -> Optional[CadEntity]:
    """Legend swatch: entity left of the text, on the same row, small compared to the plan."""
    return _SymbolIndex(symbols, max_size).best(t)


def _legend_rows(texts: List[CadEntity], symbols: List[CadEntity], need_structure: bool,
                 max_size: float) -> List[Tuple[CadEntity, Optional[CadEntity]]]:
    """Rows of [symbol] description. On the drawing sheet a legend must look like one:
    under a LEGEND/KEY title, or ≥3 left-aligned rows each with its own swatch."""
    cands = []
    for t in texts:
        if t.leader_point is not None or not (t.text or "").strip() or LEGEND_TITLE.match(t.text) or NOT_LEGEND.search(t.text):
            continue
        cands.append(t)
    index = _SymbolIndex(symbols, max_size)
    rows = [(t, index.best(t)) for t in cands]
    if not need_structure:
        return [(t, s) for t, s in rows if s is not None or _parse_key_text(t)]
    picked: Dict[int, Tuple[CadEntity, CadEntity]] = {}
    # (a) rows inside the area under a legend title
    for title in (x for x in texts if LEGEND_TITLE.match(x.text or "") and not NOT_LEGEND.search(x.text or "")):
        h = max(title.text_height or 0.0, 0.25)
        x0, y0 = title.center[0] - 10 * h, title.center[1]
        for t, s in rows:
            if s is not None and x0 <= t.center[0] <= x0 + 80 * h and y0 - 120 * h <= t.center[1] < y0:
                picked[id(t)] = (t, s)
    # (b) left-aligned columns of >=3 rows that each have a swatch
    with_sym = [(t, s) for t, s in rows if s is not None]
    cols: Dict[int, List[Tuple[CadEntity, CadEntity]]] = defaultdict(list)
    for t, s in with_sym:
        h = max(t.text_height or 0.0, 0.25)
        cols[round(t.center[0] / (2 * h))].append((t, s))
    for col in cols.values():
        if len(col) < 3:
            continue
        # a legend is a stack of rows: ≥3 consecutive rows close under each other (texts that merely share an x
        # position across a large plan are labels, not a legend)
        col.sort(key=lambda ts: -ts[0].center[1])
        run = [col[0]]
        for prev, cur in zip(col, col[1:]):
            h = max(prev[0].text_height or 0.0, 0.25)
            if prev[0].center[1] - cur[0].center[1] <= 4 * h:
                run.append(cur)
                continue
            if len(run) >= 3:
                for t, s in run:
                    picked[id(t)] = (t, s)
            run = [cur]
        if len(run) >= 3:
            for t, s in run:
                picked[id(t)] = (t, s)
    return list(picked.values())


def _add_symbol_evidence(ke: "KeyEntry", sym: CadEntity, feats: List[CadEntity], feat_layers: List[str]):
    from .styles import style_of
    if sym.layer in feat_layers:
        ke.evidence.setdefault("symbol_layer", []).append(sym.layer)
        ke.evidence.setdefault("symbol_style", []).append(f"{sym.layer}|{style_of(sym)[0]}")
    if sym.block_name:
        for e in feats:
            if e.block_name == sym.block_name:
                ke.evidence.setdefault("symbol_block", []).append(e.layer)
    if sym.pattern:
        for e in feats:
            if e.pattern and e.pattern == sym.pattern and (sym.color is None or e.color == sym.color):
                ke.evidence.setdefault("symbol_hatch", []).append(e.layer)


def read_keys(cad: CadDocument) -> List[KeyEntry]:
    """Find the drawing key the way an engineer does:
       1. legend on a layout sheet;  2. if none, legend / keynotes on the drawing sheet;
       3. if none, interpret layers, blocks and geometry (the semantic matcher does that).
    The level actually used is recorded in cad.key_info. Computed once per drawing (cached on the document,
    because legend swatches found on the drawing sheet are turned into artefacts)."""
    if cad.keys is not None:
        return cad.keys
    tried: List[Dict[str, object]] = []
    feats_all = [e for e in cad.entities if e.kind in FEATURE_KINDS]
    ext = cad.extent()
    plan_size = max(ext[2] - ext[0], ext[3] - ext[1]) if ext else 1e9
    entries: Dict[str, KeyEntry] = {}

    def entry_for(t: CadEntity, source: str) -> KeyEntry:
        parsed = _parse_key_text(t)
        code, desc = parsed if parsed else (None, " ".join((t.text or "").split()))
        if not code:
            code = f"LEG-{sum(1 for k in entries.values() if k.code.startswith('LEG-')) + 1:02d}"
        ke = entries.setdefault(code, KeyEntry(code, desc, source))
        if len(desc) > len(ke.description):
            ke.description = desc
        ke.occurrences += 1
        return ke

    # ---------------------------------------------------------- 1. layout sheet legend
    paper_texts = [e for e in cad.paper if e.kind == TEXT and e.text]
    paper_syms = [e for e in cad.paper if e.kind in FEATURE_KINDS]
    rows = _legend_rows(paper_texts, paper_syms, need_structure=False, max_size=1e9) if paper_texts else []
    for t, sym in rows:
        ke = entry_for(t, "layout_legend")
        if sym is not None:
            _add_symbol_evidence(ke, sym, feats_all, sorted({e.layer for e in feats_all}))
    tried.append({"level": "layout_legend", "found": len(rows),
                  "sheets": sorted({e.space for e in cad.paper}) or []})
    level = "layout_legend" if rows else None

    # ---------------------------------------------------------- 2. legend on the drawing sheet
    model_texts = [e for e in cad.entities if e.kind == TEXT and e.text]
    if level is None:
        mrows = _legend_rows(model_texts, feats_all, need_structure=True, max_size=max(0.03 * plan_size, 3.0))
        symbols_used = set()
        for t, sym in mrows:
            ke = entry_for(t, "drawing_legend")
            symbols_used.add(id(sym))
            _add_symbol_evidence(ke, sym, [e for e in feats_all if id(e) not in symbols_used],
                                 sorted({e.layer for e in feats_all}))
        # legend swatches are drawing furniture, never assets
        for e in feats_all:
            if id(e) in symbols_used:
                e.kind, e.artifact = "artifact", "legend symbols"
                cad.artifacts["legend symbols"] = cad.artifacts.get("legend symbols", 0) + 1
        tried.append({"level": "drawing_legend", "found": len(mrows)})
        if mrows:
            level = "drawing_legend"

    feats = [e for e in cad.entities if e.kind in FEATURE_KINDS]
    feat_layers = sorted({e.layer for e in feats})

    # ---------------------------------------------------------- 3. coded keynotes on the drawing sheet
    geoms = [(e, _geom(e)) for e in feats]
    geoms = [(e, g) for e, g in geoms if g is not None and not g.is_empty]
    tree = STRtree([g for _, g in geoms]) if geoms else None
    n_kn = 0
    for t in model_texts:
        if not _parse_key_text(t):
            continue
        n_kn += 1
        ke = entry_for(t, "drawing_keynotes")
        if t.leader_point and tree is not None:
            p = Point(t.leader_point[:2])
            hits = [geoms[int(i)] for i in tree.query(p.buffer(1.5))]
            pts = sorted(((g.distance(p), id(e), e) for e, g in hits if e.kind == "point" and g.distance(p) <= 1.5))
            lns = sorted(((g.distance(p), id(e), e) for e, g in hits if e.kind == "line" and g.distance(p) <= 0.3))
            pls = sorted(((g.area, id(e), e) for e, g in hits if e.kind == "polygon" and g.distance(p) <= 0.05))
            pick = (pts or lns or pls or [None])[0]
            if pick is not None:
                ke.evidence.setdefault("leader", []).append(pick[2].layer)
                ke.evidence.setdefault("leader_group", []).append(pick[2].group)
    tried.append({"level": "drawing_keynotes", "found": n_kn})
    if level is None and n_kn:
        level = "drawing_keynotes"

    # block names that describe a key entry ("PLANTER - ROUND" for "... Planter (Round)")
    blocks_by_layer: Dict[str, set] = defaultdict(set)
    for e in feats:
        if e.block_name:
            blocks_by_layer[e.layer].add(e.block_name)
    for ke in entries.values():
        for lay, names in blocks_by_layer.items():
            if any(name_similarity(ke.description, b) >= 0.5 for b in names):
                ke.evidence.setdefault("block_name", []).append(lay)

    # ---------------------------------------------------------- link scoring
    for ke in entries.values():
        scores: Dict[str, float] = defaultdict(float)
        for lay in feat_layers:
            sim = name_similarity(ke.description, lay)
            if sim:
                scores[lay] += 0.6 * sim
        # a legend swatch drawn on the layer is the legend literally saying "this layer is this item"
        for kind, weight in (("leader", 0.35), ("block_name", 0.3), ("symbol_layer", 0.9), ("symbol_block", 0.6),
                             ("symbol_hatch", 0.4)):
            votes = Counter(ke.evidence.get(kind, []))
            total = sum(votes.values())
            for lay, n in votes.items():
                scores[lay] += weight * n / total
        ke.evidence = {k: sorted(set(v)) for k, v in ke.evidence.items()}
        ke.layers = {k: round(min(v, 1.0), 3) for k, v in scores.items() if v >= 0.25}

    level = level or "layers"
    linked = sum(1 for k in entries.values() if k.layers)
    cad.key_info = {"level": level, "label": dict(LEVELS)[level], "tried": tried,
                    "entries": len(entries), "linked_entries": linked}
    cad.keys = sorted(entries.values(), key=lambda k: k.code)
    return cad.keys


def keys_by_layer(keys: List[KeyEntry], min_score: float = 0.45) -> Dict[str, List[KeyEntry]]:
    out: Dict[str, List[KeyEntry]] = defaultdict(list)
    for k in keys:
        if not k.layers:
            continue
        top = max(k.layers.values())
        for lay, sc in k.layers.items():
            if sc >= min_score and sc >= top - 0.15:
                out[lay].append(k)
    return out
