"""
Semantic matcher — the "engineer's experience" layer.

Turns free text (CAD key descriptions, layer names, block names) and schema class
names into *concepts* using an editable thesaurus (knowledge/thesaurus.json):
  "Precast Concrete Heel Kerb"  -> {concrete, curb}
  "HL_CURBSTONE"                -> {curb}           (compound split + synonyms)
  "Aluminum Edger" / "EDGES"    -> {edge}
Class profiles also include the values of domains that belong to that class only
(e.g. PlanterBox_UnitType -> PLANTERBOX), weighted by how specific a concept is
across the schema (IDF), so generic words ("concrete") count less than decisive
ones ("curb", "planter").

It also proposes attribute values: a key "Precast Concrete Pavers" yields
MATERIAL_TYPE = "PCC-Pavers" because the template's domain contains exactly that.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from collections import Counter
from typing import Dict, List, Optional, Tuple

from ..config import settings
from ..schema.models import LayerDef, SchemaDefinition

_ADMIN_DOMAINS = re.compile(r"status|condition|owner|source|region|migration|boolean|assetcat|mainasset|subasset|"
                            r"paintcond|rating", re.I)
_NEUTRAL = {"n/a", "na", "other", "undefined", "unknown", "none"}
_BASE_WORDS = ["area", "box", "structure", "surface", "equipment", "machine", "board", "display", "track", "wall",
               "light", "lighting", "pole", "polygon", "centerline", "center", "line", "repair", "bicycle", "quality",
               "air", "storage", "tank", "pool", "swimming", "projector", "led", "speaker", "loud", "elec", "mech",
               "amenity", "picnic", "table", "flag", "skate", "bridge", "park", "train", "misting", "feeder",
               "pillar", "boundary", "retaining", "stone", "gravel", "concrete", "sandy", "riprap", "beach", "wadi",
               "dam", "lake", "statue", "publicart", "public", "art", "drinking", "cooler", "clock", "gate", "toilet",
               "bench", "bollard", "fence", "handrail", "curb", "edges", "tiles", "planter", "shade", "sign",
               "signboard", "litter", "bin", "bbq", "stage", "sport", "play", "playground", "grate", "tree",
               "fountain", "pedestrian", "building", "boulders", "cctv", "condition", "rock", "palme", "palm",
               "native", "ground", "cover", "seasonal", "flower", "forest", "shrub", "plants", "lawn", "hedge", "palme"]


def _singular(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and w[:-2].endswith(("sh", "ch", "x", "ss")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


class Thesaurus:
    _cache: Dict[str, "Thesaurus"] = {}
    _lock = threading.Lock()

    def __init__(self, path: str):
        self.path = path
        data = {"concepts": {}, "stopwords": []}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        self.phrases: Dict[str, str] = {}
        for concept, terms in data.get("concepts", {}).items():
            for t in terms + [concept]:
                self.phrases[self._norm(t)] = concept
        self.stop = {self._norm(s) for s in data.get("stopwords", [])}
        self.multi = sorted((p for p in self.phrases if " " in p), key=len, reverse=True)
        self.vocab = sorted({w for p in self.phrases for w in p.split() if len(w) >= 3} | set(_BASE_WORDS),
                            key=len, reverse=True)
        self.mtime = os.path.getmtime(path) if os.path.isfile(path) else 0

    @classmethod
    def get(cls, path: Optional[str] = None) -> "Thesaurus":
        path = path or os.path.join(settings.root, "knowledge", "thesaurus.json")
        with cls._lock:
            t = cls._cache.get(path)
            m = os.path.getmtime(path) if os.path.isfile(path) else 0
            if t is None or t.mtime != m:
                t = cls._cache[path] = Thesaurus(path)
            return t

    @staticmethod
    def _norm(s: str) -> str:
        s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s or "")
        s = re.sub(r"[_\-/\\().,:;+&]+", " ", s.lower())
        return " ".join(_singular(w) for w in s.split())

    def split_compound(self, word: str) -> List[str]:
        """'planterbox' -> ['planter','box'];  'stonegravelarea' -> ['stone','gravel','area']"""
        out: List[str] = []
        i = 0
        w = word
        while i < len(w):
            m = next((v for v in self.vocab if w.startswith(v, i)), None)
            if m is None:
                return [word]
            out.append(m)
            i += len(m)
        return out if len(out) > 1 else [word]

    def concepts(self, text: str, compound: bool = False) -> Counter:
        s = " " + self._norm(text) + " "
        found: Counter = Counter()
        for p in self.multi:
            if f" {p} " in s:
                found[self.phrases[p]] += 1
                s = s.replace(f" {p} ", " ")
        for w in s.split():
            parts = self.split_compound(w) if compound and len(w) > 6 and w not in self.phrases else [w]
            for part in parts:
                part = _singular(part)
                if part in self.stop or len(part) < 2 or part.isdigit():
                    continue
                if part in self.phrases:
                    found[self.phrases[part]] += 1
                elif len(part) >= 3:
                    found["w:" + part] += 1
        return found

    def raw_words(self, text: str) -> set:
        return {w for w in self._norm(text).split() if len(w) >= 3 and not w.isdigit()}


LEGACY_W = 0.95


def dict_class_info(name):
    try:
        from ..knowledge.dictionary import class_info
        return class_info(name)
    except Exception:  # noqa: BLE001
        return None


class SchemaSemantics:
    """Concept profiles of every feature class + IDF weights, cached per schema."""

    _cache: Dict[Tuple[str, str, float], "SchemaSemantics"] = {}

    def __init__(self, schema: SchemaDefinition, th: Thesaurus):
        self.schema, self.th = schema, th
        dom_use: Counter = Counter()
        for l in schema.layers:
            for d in {f.domain for f in l.fields if f.domain}:
                dom_use[d] += 1
        self.profiles: Dict[str, Counter] = {}
        for l in schema.layers:
            prof: Counter = Counter()
            for c in th.concepts(l.name, compound=True):
                prof[c] += 1.0
            for kw in l.classification_hints.text_keywords:
                for c in th.concepts(kw):
                    prof[c] = max(prof[c], 1.0)
            if l.alias and l.alias != l.name:
                for c in th.concepts(l.alias, compound=True):
                    prof[c] = max(prof[c], 1.0)
            # data dictionary: the names this asset has in other systems / older deliveries name the class
            # itself; its definition adds supporting (non-identity) meaning
            info = dict_class_info(l.name)
            if info:
                for n in info.get("legacy_names", []):
                    for c in th.concepts(n, compound=True):
                        prof[c] = max(prof[c], LEGACY_W)   # names the class, but does not dilute its core profile
                desc = info.get("description", "")
                desc = desc.split(":", 1)[1] if ":" in desc[:80] else desc
                for c in th.concepts(desc.split(".")[0]):
                    if not c.startswith("w:"):
                        prof[c] = max(prof[c], 0.3)
            for f in l.fields:
                d = schema.get_domain(f.domain)
                if not d or dom_use[d.name] > 2 or _ADMIN_DOMAINS.search(d.name) or d.domain_type != "coded":
                    continue
                for code, desc in d.coded_values.items():
                    if str(desc).lower() in _NEUTRAL:
                        continue
                    for c in th.concepts(f"{desc}"):
                        prof[c] = max(prof[c], 0.5)
            self.profiles[l.name] = prof
        n = max(1, len(self.profiles))
        df: Counter = Counter()
        for prof in self.profiles.values():
            for c in prof:
                df[c] += 1
        self.idf = {c: math.log(1 + n / k) for c, k in df.items()}

    @classmethod
    def get(cls, schema: SchemaDefinition, th: Optional[Thesaurus] = None) -> "SchemaSemantics":
        th = th or Thesaurus.get()
        key = (schema.schema_id, schema.version, th.mtime)
        s = cls._cache.get(key)
        if s is None:
            s = cls._cache[key] = SchemaSemantics(schema, th)
        return s

    def score(self, text: str, fc: str) -> Tuple[float, List[str]]:
        tc = self.th.concepts(text, compound=True)
        prof = self.profiles.get(fc, Counter())
        if not tc or not prof:
            return 0.0, []
        shared = [c for c in tc if c in prof]
        if not shared or max(prof[c] for c in shared) < 0.5:
            return 0.0, []      # words only found in the class definition never decide a match on their own
        num = sum(self.idf.get(c, 0.5) * prof[c] for c in shared)
        t_norm = sum(self.idf.get(c, 0.5) for c in tc)
        f_norm = sum(self.idf.get(c, 0.5) * w for c, w in prof.items() if w >= 1.0) or 1.0
        s = num / math.sqrt(t_norm * f_norm)
        return min(1.0, s), [c.replace("w:", "") for c in shared]

    def identity_match(self, text: str, fc: str) -> bool:
        """True when the text names the class itself (core concepts), not merely one of its domain values."""
        tc = self.th.concepts(text, compound=True)
        prof = self.profiles.get(fc, Counter())
        return any(prof.get(c, 0) >= LEGACY_W for c in tc)

    def rank(self, text: str, candidates: List[LayerDef], top: int = 5) -> List[Tuple[str, float, List[str]]]:
        out = []
        for l in candidates:
            s, why = self.score(text, l.name)
            if s > 0:
                out.append((l.name, round(s, 3), why))
        return sorted(out, key=lambda x: -x[1])[:top]

    def attributes_from_text(self, fc: str, texts: List[str],
                             word_df: Optional[Counter] = None) -> Dict[str, Dict[str, object]]:
        """Domain values of `fc` whose meaning is fully contained in the text.

        A value matches when all its words appear in the text, or all its concepts do
        (synonyms). Ranking: more matched words, then rarer words across the drawing's
        key (word_df: 'heel' beats 'precast' for a kerb), then overall similarity."""
        layer = self.schema.get_layer(fc)
        if not layer:
            return {}
        word_df = word_df or Counter()
        out: Dict[str, Dict[str, object]] = {}
        for text in texts:
            tc = set(self.th.concepts(text))
            tw = self.th.raw_words(text)
            for f in layer.fields:
                d = self.schema.get_domain(f.domain)
                if not d or d.domain_type != "coded" or _ADMIN_DOMAINS.search(d.name) or f.name in out:
                    continue
                best = None
                for code, desc in d.coded_values.items():
                    if str(desc).lower() in _NEUTRAL:
                        continue
                    vw = self.th.raw_words(str(desc)) or self.th.raw_words(code)
                    vc = set(self.th.concepts(str(desc)))
                    raw_ok = bool(vw) and vw <= tw
                    # a synonym-only match must add information beyond the class identity
                    # ("Date Palm" must not be inferred for any palm just because it is a palm)
                    core = {c for c, w in self.profiles.get(fc, {}).items() if w >= LEGACY_W}
                    con_ok = bool(vc) and vc <= tc and not vc <= core
                    if not (raw_ok or con_ok):
                        continue
                    n_words = len(vw & tw) if raw_ok else 0
                    rarity = -sum(word_df.get(w, 0) for w in (vw & tw)) if raw_ok else -99
                    jac = len(vw & tw) / max(1, len(vw | tw))
                    cand = (raw_ok, n_words, rarity, jac, len(vc), code, desc)
                    if best is None or cand[:5] > best[:5]:
                        best = cand
                if best:
                    out[f.name] = {"value": best[5], "label": best[6], "from": text,
                                   "match": "words" if best[0] else "synonyms"}
        return out
