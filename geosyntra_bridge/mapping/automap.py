"""
Intelligent source-to-target layer mapping.

For every (CAD layer, geometry kind) group the mapper scores every target
feature class of the selected schema from independent signals:

  * geometry compatibility (hard filter — incompatible classes are never proposed)
  * normalised name identity  (e.g. CAD "WATER_MAIN" == FC "WaterMain")
  * CAD-layer-name regexes from the schema (classification_hints / cad_mapping)
  * block-name regexes, block attribute tags matching target fields
  * label keywords found next to the group's entities
  * typical segment length
  * previously approved mapping rules (learned from user corrections)

A target is only proposed when at least one non-geometry signal supports it;
otherwise the group stays UNRESOLVED (never a silent guess).
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ..cad.discovery import group_key
from ..cad.model import CadDocument, CadEntity, FEATURE_KINDS, TEXT
from ..classification.learned_rules import LearnedRuleStore
from ..geometry.spatial import GridIndex
from ..schema.models import LayerDef, SchemaDefinition, SYSTEM_FIELDS

HIGH, MEDIUM = 0.75, 0.40


def norm_name(s: str) -> str:
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", s or "")
    s = re.sub(r"^(HL|SL|WTR|SWR|SD)[_\-]", "", s, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def fingerprint(layer: str, kind: str) -> str:
    return f"{norm_name(layer)}|{kind}"


def group_stats(ents: List[CadEntity]) -> Dict[str, Any]:
    """Shape statistics used for geometry-conversion decisions."""
    import statistics
    from shapely.geometry import Polygon as _P
    areas, widths = [], []
    for e in ents:
        if e.kind == "polygon" and len(e.coords) >= 4:
            try:
                p = _P([c[:2] for c in e.coords])
                a, per = abs(p.area), p.length
            except Exception:  # noqa: BLE001
                continue
            if per > 0:
                areas.append(a)
                widths.append(2 * a / per)
    return {"has_circles": any(e.dxftype == "CIRCLE" for e in ents),
            "median_area": statistics.median(areas) if areas else None,
            "median_width": statistics.median(widths) if widths else None}


def compatible(group_kind: str, stats: Any, target_geom: str) -> Tuple[bool, str]:
    if isinstance(stats, bool):  # backwards compatibility: has_circles
        stats = {"has_circles": stats}
    if group_kind == target_geom:
        return True, "direct"
    if target_geom == "point" and group_kind == "polygon":
        if stats.get("has_circles"):
            return True, "circle centre -> point"
        if stats.get("median_area") is not None and stats["median_area"] <= 30:
            return True, "small closed outline -> point (centroid)"
    if target_geom == "line" and group_kind == "polygon":
        if stats.get("median_width") is not None and stats["median_width"] <= 1.0:
            return True, "narrow outline -> centerline"
        return True, "closed polyline -> line"
    return False, ""


CONVERT_FOR = {"small closed outline -> point (centroid)": "centroid", "narrow outline -> centerline": "centerline"}


def _confidence(score: float) -> Tuple[str, str]:
    if score >= HIGH:
        return "HIGH", "AUTO"
    if score >= MEDIUM:
        return "MEDIUM", "SUGGESTED"
    return "LOW", "MANUAL"


def _search(patterns: List[str], value: str) -> Optional[str]:
    for p in patterns:
        try:
            if re.search(p, value, re.IGNORECASE):
                return p
        except re.error:
            continue
    return None


def _dictionary_name_hit(fc: str, names: List[str]) -> Optional[Tuple[str, str]]:
    from .semantics import dict_class_info
    info = dict_class_info(fc)
    if not info:
        return None
    srcs = info.get("legacy_sources", {})
    cand = [(n, norm_name(n)) for n in info.get("legacy_names", []) if norm_name(n) != norm_name(fc)]
    for nm in names:
        nn = norm_name(nm)
        if not nn:
            continue
        for raw, ln in cand:
            if len(ln) < 5:
                continue
            code_like = "_" in raw or raw.isupper() or re.search(r"[a-z][A-Z]", raw)
            if nn == ln or (code_like and len(ln) >= 7 and ln in nn):
                return (srcs.get(raw, "data dictionary"), raw)
    return None


def _nearby_texts(group_ents: List[CadEntity], text_index: GridIndex, radius: float, limit: int = 60) -> List[str]:
    found: List[str] = []
    step = max(1, len(group_ents) // limit)
    seen = set()
    for e in group_ents[::step]:
        pts = []
        if e.kind == "line" and len(e.coords) >= 2:
            # labels sit anywhere along a pipe: sample the whole run, not only its middle
            L = e.length()
            n = min(40, max(1, int(L / max(radius, 1e-6))))
            try:
                from shapely.geometry import LineString
                ls = LineString([c[:2] for c in e.coords])
                pts = [ls.interpolate(i / n, normalized=True).coords[0] for i in range(n + 1)]
            except Exception:
                pts = []
        if not pts:
            p = e.rep_point()
            pts = [p] if p else []
        for p in pts:
            for t in text_index.query(p[0], p[1], radius):
                if id(t) not in seen:
                    seen.add(id(t))
                    found.append(t.text)
    return found


def score_group(schema: SchemaDefinition, layer_name: str, kind: str, ents: List[CadEntity],
                nearby: List[str], learned: Optional[str], keys: Optional[List[Any]] = None,
                style: str = "", ctx: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    from .semantics import SchemaSemantics
    sem = SchemaSemantics.get(schema)
    stats = group_stats(ents)
    keys = keys or []
    compat_layers = [l for l in schema.layers if compatible(kind, stats, l.geometry_type)[0]]
    key_rank = {k.code: [(n, sc) for n, sc, _ in sem.rank(k.description, compat_layers, 3)] for k in keys}
    blocks = sorted({e.block_name for e in ents if e.block_name})
    has_circles = stats["has_circles"]
    tags = sorted({t for e in ents for t in e.attributes})
    lengths = [e.length() for e in ents if e.kind == "line"]
    avg_len = sum(lengths) / len(lengths) if lengths else None
    nearby_joined = " ".join(nearby).lower()
    n_layer = norm_name(layer_name)

    scored = []
    twins = schema.geometry_twins()
    for target in schema.layers:
        ok, how = compatible(kind, stats, target.geometry_type)
        if not ok:
            continue
        score, ev, strong = 0.15, [f"geometry {kind} compatible with {target.geometry_type} ({how})"], False
        direct_twin = next((t for t in twins.get(target.name, []) if schema.get_layer(t).geometry_type == kind), None)
        if direct_twin and how != "direct":
            # same asset exists in the drawn geometry: the drawing's geometry decides between the twins
            score -= 0.3
            ev.append(f"{direct_twin} is the same asset as a {kind} — geometry decides")
        # --- CAD key (legend / keynotes) linked to this layer: the engineer's primary source
        kbest, kwhy, kref, kcontrib = 0.0, [], None, 0.0
        for k in keys:
            sk, why = sem.score(k.description, target.name)
            if sk < 0.3:
                continue
            link = min(1.0, k.layers.get(layer_name, 0.0))
            ranking = key_rank.get(k.code, [])
            top = ranking[0] if ranking else None
            rivals = [n for n, _ in ranking[1:] if sem.identity_match(k.description, n)]
            decisive = top is not None and top[0] == target.name and sem.identity_match(k.description, target.name) \
                and (not rivals or top[1] - ranking[1][1] >= 0.12)
            contrib = link * (0.45 + 0.4 * min(1.0, sk / 0.5)) if decisive else 0.55 * sk
            if contrib > kcontrib:
                kbest, kwhy, kref, kcontrib = sk, why, k, contrib
        if kref is not None:
            score += kcontrib; strong = True
            ev.append(f"CAD key {kref.code} '{kref.description}' ({kref.source.replace('_', ' ')}) means "
                      f"{target.name} ({', '.join(kwhy)})")
        # --- meaning of the layer / block names (synonyms, plurals, compound words)
        sl, why_l = sem.score(layer_name, target.name)
        sb, why_b = (max((sem.score(b, target.name) for b in blocks), key=lambda x: x[0]) if blocks else (0.0, []))
        if max(sl, sb) >= 0.3:
            sv, why = (sl, why_l) if sl >= sb else (sb, why_b)
            score += 0.4 * sv; strong = True
            ev.append(f"{'layer' if sl >= sb else 'block'} name means {target.name} ({', '.join(why)})")
        # --- data dictionary: names this asset carries in ADM / AAM / DRM / municipal data (SW_PIPE, SOLENOIDVLVASM ...)
        dn = _dictionary_name_hit(target.name, [layer_name] + blocks)
        exact_name = False
        if dn:
            score += 0.5; strong = True
            exact_name = norm_name(dn[1]) in {norm_name(x) for x in [layer_name] + blocks}
            ev.append(f"data dictionary: '{dn[1]}' is a known name of {target.name} ({dn[0]})")
        h = target.classification_hints
        if how != "direct":
            score -= 0.05
        if n_layer and (n_layer == norm_name(target.name) or (target.alias and n_layer == norm_name(target.alias))):
            score += 0.55; strong = True
            ev.append(f"CAD layer name equals feature class name '{target.name}'")
        else:
            p = _search(h.layer_name_patterns, layer_name)
            if p:
                score += 0.45; strong = True
                ev.append(f"layer name matches schema pattern /{p}/")
        if blocks:
            for b in blocks:
                p = _search(h.block_name_patterns, b)
                if p:
                    score += 0.25; strong = True
                    ev.append(f"block '{b}' matches /{p}/")
                    break
        if tags:
            fnames = {f.name.upper() for f in target.fields}
            aliases = {a.upper() for al in target.attribute_aliases.values() for a in al}
            hit = [t for t in tags if t in fnames or t in aliases]
            if hit:
                score += min(0.2, 0.05 * len(hit)); strong = True
                ev.append(f"block attributes {hit[:4]} match target fields")
        if nearby_joined and h.text_keywords:
            kw = next((k for k in h.text_keywords if len(k) >= 2 and re.search(rf"\b{re.escape(k.lower())}", nearby_joined)), None)
            if kw:
                score += 0.12; strong = True
                ev.append(f"nearby labels contain '{kw}'")
            rule_hits, id_hit = 0, None
            for r in schema.rules_for(target.name):
                if r.field.upper() in SYSTEM_FIELDS:
                    continue
                try:
                    m_ = re.search(r.pattern, nearby_joined, re.IGNORECASE)
                except re.error:
                    m_ = None
                if m_:
                    rule_hits += 1
                    if target.id_field and r.field.upper() == target.id_field.upper():
                        id_hit = m_.group(0)
            if rule_hits:
                score += min(0.21, 0.07 * rule_hits); strong = True
                ev.append(f"{rule_hits} label rule(s) of {target.name} match nearby text")
            if id_hit:
                score += 0.2
                ev.append(f"labels carry {target.name} asset IDs ('{id_hit.strip().upper()}')")
        if h.value_ranges and nearby:
            from ..labels.engine import compile_rules, parse_label
            crs = [c for c in compile_rules(schema, {target.name}) if c.rule.field.upper() in {k.upper() for k in h.value_ranges}]
            inside = outside = 0
            for t in nearby:
                for cr, v, _ in parse_label(t, crs):
                    lo, hi = next(r for f_, r in h.value_ranges.items() if f_.upper() == cr.rule.field.upper())
                    if isinstance(v, (int, float)):
                        inside += lo <= v <= hi
                        outside += not (lo <= v <= hi)
            if inside + outside:
                frac = inside / (inside + outside)
                if frac >= 0.6:
                    score += 0.2 * frac; strong = True
                    ev.append(f"labelled values fit {target.name} ({inside}/{inside + outside} within typical range)")
                elif frac <= 0.3:
                    score -= 0.25
                    ev.append(f"labelled values do not fit {target.name} ({outside}/{inside + outside} outside typical range)")
        # --- how it is drawn (engineer's reading of the drawing, relative to the other styles)
        ctx = ctx or {}
        if kind == "line" and ctx.get("weight") and h.line_weight:
            if ctx["weight"] == h.line_weight:
                score += 0.2
                ev.append(f"drawn as the {ctx['weight']} line of its layer ({ctx.get('label', style)}) — "
                          f"{target.name} is drawn {h.line_weight}")
            else:
                score -= 0.15
                ev.append(f"drawn as the {ctx['weight']} line of its layer, but {target.name} is drawn {h.line_weight}")
        if kind == "line" and ctx.get("linetype") and h.linetype_patterns:
            p = _search(h.linetype_patterns, ctx["linetype"])
            if p:
                score += 0.08
                ev.append(f"linetype {ctx['linetype']} typical for {target.name}")
        if kind == "point" and h.network_position and ctx.get("on_network") is not None:
            f = ctx["on_network"]
            pos = "on_network" if f >= 0.6 else ("off_network" if f <= 0.2 else None)
            if pos == h.network_position:
                score += 0.15
                pct = round((f if pos == "on_network" else 1 - f) * 100)
                ev.append(f"{pct}% of the symbols sit {'on the pipe/cable lines' if pos == 'on_network' else 'away from the lines'}"
                          f" — as {target.name} does")
            elif pos:
                score -= 0.15
                ev.append(f"{round(f * 100)}% of the symbols sit on the lines — {target.name} is expected "
                          f"{h.network_position.replace('_', ' ')}")
        ref = ctx.get("reference") or {}
        if ref:
            if target.name in ref:
                share, m_, n_ = ref[target.name]
                bonus = 0.6 if share >= 0.8 else (0.35 if share >= 0.5 else (0.1 if share >= 0.2 else 0.0))
                if bonus:
                    score += bonus; strong = strong or share >= 0.5
                    ev.append(f"spatial join: {m_}/{n_} CAD objects lie on {target.name} features of the reference GIS "
                              f"({round(share * 100)}%)")
            elif max(v[0] for v in ref.values()) >= 0.8:
                other = max(ref.items(), key=lambda kv: kv[1][0])[0]
                score -= 0.2
                ev.append(f"spatial join places these objects on {other}, not {target.name}")
        if avg_len is not None and h.typical_length_range:
            lo, hi = h.typical_length_range
            if lo <= avg_len <= hi:
                score += 0.05
                ev.append(f"average length {avg_len:.1f} within [{lo}, {hi}]")
        if learned and learned == target.name:
            score += 0.5; strong = True
            ev.append("previously approved mapping rule")
        # --- all-templates mode: the layer's asset type routes it to the right template geodatabase
        # (applied to the capped evidence score, so a strong name match in the wrong template cannot outrank it)
        if target.template_id and ctx.get("asset_cat") is not None:
            score = min(score, 1.0)
            from .asset_category import CODE_OF
            tcode = CODE_OF.get(target.template_id)
            cat, cconf, src = ctx["asset_cat"]
            if cat and tcode == cat:
                score += 0.15 * cconf + (0.05 if src == "layer" else 0.0)
                ev.append(f"{src} is a {cat} asset — {target.name} is in the {cat} template")
            elif cat and tcode != cat:
                score -= (0.3 if src == "layer" else 0.1) * max(cconf, 0.5)
                ev.append(f"{src} is a {cat} asset, {target.name} belongs to the {tcode} template")
        if not strong:
            continue
        lw = {w for w in re.split(r"[^A-Z]+", layer_name.upper()) if len(w) >= 3}
        spec = sum(1 for w in lw if w in target.name.upper().replace("_", ""))
        scored.append({"target": target.name, "score": round(min(score, 1.0), 3), "evidence": ev,
                       "convert": CONVERT_FOR.get(how), "exact_name": exact_name, "_spec": spec})
    # an exact data-dictionary name settles a tie between classes that both look right; then the class whose name
    # carries more of the layer's words (PALM TREE -> PALMETREE rather than TREE)
    scored.sort(key=lambda s: (-s["score"], not s.get("exact_name"), -s["_spec"]))
    for s_ in scored:
        s_.pop("_spec", None)
    return scored


_DASHED = ("DASH", "HIDDEN", "DOT", "CENTER", "PHANTOM", "DIVIDE", "BORDER", "ACAD_ISO")


def drawing_context(doc: CadDocument, groups: Dict[str, List[CadEntity]]) -> Dict[str, Dict[str, Any]]:
    """Per group: relative line weight among the line styles of its layer, dominant linetype, and for
    point groups the share of symbols sitting on drawn lines (valves / manholes) vs. away (controllers)."""
    from collections import Counter as _C
    from ..cad.styles import split_group
    roles = doc.layer_roles or {}
    ctx: Dict[str, Dict[str, Any]] = {}
    by_layer: Dict[str, List[str]] = defaultdict(list)
    for key, ents in groups.items():
        layer, kind, style = split_group(key)
        if kind == "line":
            lt = _C((e.linetype or "Continuous").upper() for e in ents).most_common(1)[0][0]
            lw = _C(e.lineweight or 0 for e in ents).most_common(1)[0][0]
            ctx[key] = {"linetype": lt, "lineweight": lw, "label": style or lt.title()}
            by_layer[layer].append(key)
    for layer, keys in by_layer.items():
        if len(keys) < 2:
            continue
        def rank(k):
            c = ctx[k]
            return (c["lineweight"] or 0, 0 if any(d in c["linetype"] for d in _DASHED) else 1)
        ranks = {k: rank(k) for k in keys}
        top = max(ranks.values())
        if len(set(ranks.values())) < 2:
            continue
        for k in keys:
            ctx[k]["weight"] = "heavy" if ranks[k] == top else "light"
    # point symbols on / off the drawn network
    try:
        from shapely.geometry import LineString, Point
        from shapely.strtree import STRtree
    except Exception:   # pragma: no cover
        return ctx
    lines = []
    for key, ents in groups.items():
        layer, kind, _ = split_group(key)
        if kind != "line" or roles.get(layer, {}).get("role") in ("basemap", "sheet"):
            continue
        lines += [LineString([c[:2] for c in e.coords]) for e in ents if len(e.coords) >= 2]
    if not lines:
        return ctx
    tree = STRtree(lines)
    for key, ents in groups.items():
        layer, kind, _ = split_group(key)
        if kind != "point":
            continue
        pts = [e.rep_point() for e in ents if e.rep_point()]
        if not pts:
            continue
        on = 0
        for p in pts:
            g = Point(p[0], p[1])
            idx = tree.query(g.buffer(0.5))
            on += any(lines[i].distance(g) <= 0.5 for i in idx)
        ctx.setdefault(key, {})["on_network"] = on / len(pts)
    return ctx


def detect_subtype(target: LayerDef, layer_name: str, ents: List[CadEntity]) -> Optional[int]:
    if not target.subtypes:
        return None
    blocks = {e.block_name for e in ents if e.block_name}
    for st in target.subtypes:
        if _search(st.cad_patterns, layer_name) or any(_search(st.cad_patterns, b) for b in blocks):
            return st.code
    return target.default_subtype


def key_fingerprint(description: str) -> str:
    return "key|" + norm_name(description)


def auto_map(doc: CadDocument, schema: SchemaDefinition, rules_dir: Optional[str] = None,
             radius: Optional[float] = None, keys: Optional[List[Any]] = None,
             reference: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    from collections import Counter as _C
    from ..cad.keys import keys_by_layer
    from .semantics import SchemaSemantics, Thesaurus
    sem = SchemaSemantics.get(schema)
    th = Thesaurus.get()
    keys = keys or []
    layer_keys = keys_by_layer(keys)
    word_df = _C(w for k in keys for w in th.raw_words(k.description))
    radius = radius or schema.label_search_radius
    store = LearnedRuleStore.load(rules_dir, schema.schema_id) if rules_dir else None
    text_index: GridIndex = GridIndex(max(radius, 1e-6))
    for t in doc.texts:
        if t.text and t.center:
            text_index.insert((t.center[0], t.center[1], t.center[0], t.center[1]), t)

    from ..cad.styles import split_group, style_of
    groups: Dict[str, List[CadEntity]] = defaultdict(list)
    for e in doc.entities:
        if e.kind in FEATURE_KINDS:
            groups[e.group or group_key(e.layer, e.kind)].append(e)

    contexts = drawing_context(doc, groups)
    if reference:
        from ..reference.spatial_join import group_evidence
        for key in groups:
            ge = group_evidence(reference, key)
            if ge:
                contexts.setdefault(key, {})["reference"] = ge
    if any(l.template_id for l in schema.layers):     # several templates: route each layer to its asset type
        from .asset_category import categorize, drawing_category
        lcat = {}
        for key, ents in groups.items():
            ln = split_group(key)[0]
            blocks = sorted({e.block_name for e in ents if e.block_name})
            c, cf, _ = categorize(ln, *blocks)
            lcat[key] = (c, cf)
        dcat = drawing_category(doc.source_name or "", [c for c, _ in lcat.values()])
        for key, (c, cf) in lcat.items():
            contexts.setdefault(key, {})["asset_cat"] = (c, cf, "layer") if c else ((dcat, 0.6, "drawing") if dcat else (None, 0, ""))
    out: Dict[str, Dict[str, Any]] = {}
    for key, ents in sorted(groups.items()):
        layer_name, kind, style = split_group(key)
        dom_style = _C(style_of(e)[0] for e in ents).most_common(1)[0][0]
        lkeys = []
        for k in layer_keys.get(layer_name, []):
            ss, lg = k.evidence.get("symbol_style"), k.evidence.get("leader_group")
            if ss and style and f"{layer_name}|{dom_style}" not in ss:
                continue          # the legend swatch shows another drawing style of this layer
            if lg and style and not ss and key not in lg:
                continue          # the keynote arrows point at another style group of this layer
            lkeys.append(k)
        role = (doc.layer_roles or {}).get(layer_name, {})
        if role.get("role") in ("basemap", "sheet", "annotation") and not lkeys:
            out[key] = {"target": None, "role": "ignore", "status": "EXCLUDED", "confidence": "HIGH", "score": 0.0,
                        "evidence": [f"{ {'basemap': 'Base map', 'sheet': 'Drawing sheet', 'annotation': 'Annotation graphics'}[role['role']]} layer — "
                                     f"{role.get('reason', '')}; excluded automatically (include it to override)"],
                        "alternatives": [], "subtype": None, "keys": [], "attributes": {}, "layer_role": role.get("role")}
            continue
        fp_kind = kind + (f"@{style}" if style else "")
        rule = store.find(fingerprint(layer_name, fp_kind)) if store else None
        if rule is None and store:
            for k in lkeys:
                rule = store.find(key_fingerprint(k.description))
                if rule:
                    break
        learned = rule.target_layer if rule else None
        nearby = _nearby_texts(ents, text_index, radius)
        scored = score_group(schema, layer_name, kind, ents, nearby, learned, lkeys, style, contexts.get(key))
        kref = [{"code": k.code, "description": k.description} for k in lkeys]
        if not scored:
            ev = ["no feature class of this schema is supported by the evidence"]
            if lkeys:
                ev = [f"CAD key {k.code} '{k.description}' has no matching feature class in {schema.display_name}"
                      for k in lkeys]
            out[key] = {"target": None, "role": "feature", "status": "UNRESOLVED", "confidence": "LOW",
                        "score": 0.0, "evidence": ev, "alternatives": [], "subtype": None, "keys": kref,
                        "attributes": {}, "suggested_role": "ignore",
                        "suggestion_reason": (ev[0] if lkeys else
                                              f"no {schema.display_name} feature class matches layer '{layer_name}'")}
            continue
        best = scored[0]
        if learned:
            approved = next((x for x in scored if x["target"] == learned), None)
            if approved is not None:   # an explicitly approved rule for this layer is decisive
                best = approved
                best["score"] = max(best["score"], 0.9)
                scored = [best] + [x for x in scored if x is not best]
        conf, status = _confidence(best["score"])
        # ambiguity: runner-up too close -> never AUTO
        twin_set = set(schema.geometry_twins().get(best["target"], []))
        if len(scored) > 1 and scored[1]["score"] >= best["score"] - 0.05 and status == "AUTO" and best["target"] != learned \
                and not (best.get("exact_name") and not scored[1].get("exact_name")) and scored[1]["target"] not in twin_set:
            status, conf = "SUGGESTED", "MEDIUM"
            best["evidence"].append(f"ambiguous with '{scored[1]['target']}' (score {scored[1]['score']})")
        target = schema.get_layer(best["target"])
        attrs = sem.attributes_from_text(best["target"], [k.description for k in lkeys] + [layer_name], word_df)
        out[key] = {"target": best["target"], "role": "feature", "status": status, "confidence": conf,
                    "score": best["score"], "evidence": best["evidence"],
                    "alternatives": [{"target": s["target"], "score": s["score"]} for s in scored[1:5]],
                    "subtype": detect_subtype(target, layer_name, ents) if target else None,
                    "convert": best.get("convert"), "keys": kref, "attributes": attrs}
    assign_twins(schema, out)
    return out


DERIVE = {("polygon", "point"): "centroid", ("polygon", "line"): "centerline", ("line", "point"): "centroid"}


def assign_twins(schema: SchemaDefinition, layers: Dict[str, Dict[str, Any]]):
    """Municipal requirement: an asset defined in two geometries (BRIDGE + BRIDGE_POINT) is delivered in both.
    When the drawing has it only in one geometry, the twin is derived from the same CAD objects."""
    from ..cad.styles import split_group
    twins = schema.geometry_twins()
    drawn = {}
    for k, v in layers.items():
        if v.get("target") and v.get("role") != "ignore":
            drawn.setdefault(v["target"], k)
    for k, v in layers.items():
        tgt = v.get("target")
        if not tgt or v.get("role") == "ignore" or tgt not in twins:
            continue
        kind = split_group(k)[1]
        src_geom = schema.get_layer(tgt).geometry_type
        out = []
        for t in twins[tgt]:
            tg = schema.get_layer(t).geometry_type
            mode = DERIVE.get((src_geom, tg))
            if not mode:
                continue
            other = drawn.get(t)
            out.append({"target": t, "convert": mode, "enabled": other is None,
                        "reason": (f"drawn separately on '{split_group(other)[0]}'" if other else
                                   f"{t} derived from the {src_geom} of {tgt} ({'centre point' if mode == 'centroid' else 'centreline'})")})
        if out:
            v["twins"] = out


# ------------------------------------------------------------- field mapping
GEOMETRY_SOURCES = {"XCOORD": "x", "X": "x", "EASTING": "x", "YCOORD": "y", "Y": "y", "NORTHING": "y",
                    "ZCOORD": "z", "Z": "z", "LENGTH": "length", "SHAPE_LEN": "length", "AREA": "area",
                    "AREA_M2": "area", "WADI_AREA": "area",
                    }
# elevation fields of the official templates that describe the ground surface -> filled from the DEM elevation
# step (INVERT / BOTTOM / BASE / TOP levels are below-ground or structural and never come from a DEM)
ELEVATION_SOURCES = {"ZCOORD": "z", "Z": "z", "ZCOORD_START": "start_z", "ZCOORD_END": "end_z",
                     "GROUND_LEVEL": "dem_ground", "AVG_GLEVEL": "dem_ground", "COVER_LEVEL": "dem_ground"}


def default_field_mapping(schema: SchemaDefinition, target_name: str,
                          tags_seen: List[str]) -> Dict[str, Dict[str, Any]]:
    """Proposed source for each target field (editable in the UI)."""
    layer = schema.get_layer(target_name)
    if layer is None:
        return {}
    rule_fields = {r.field.upper() for r in schema.rules_for(target_name)} | \
        {r.field.upper() for r in schema.rules_for(target_name, "layer")}
    net = schema.network
    net_fields = set()
    if net.enabled and target_name in net.edges:
        net_fields = {x.upper() for x in (net.upstream_node_field, net.downstream_node_field, net.direction_field) if x}
    computed = {k.upper(): v for k, v in layer.computed_fields.items()}
    tags = {t.upper() for t in tags_seen}
    out: Dict[str, Dict[str, Any]] = {}
    for f in layer.fields:
        u = f.name.upper()
        if u in SYSTEM_FIELDS:
            out[f.name] = {"source": "system"}
            continue
        if layer.subtype_field and u == layer.subtype_field.upper():
            out[f.name] = {"source": "subtype"}
            continue
        if u in net_fields:
            out[f.name] = {"source": "network"}
            continue
        tag = None
        if u in tags:
            tag = u
        else:
            for a in layer.attribute_aliases.get(f.name, []) + layer.attribute_aliases.get(u, []):
                if a.upper() in tags:
                    tag = a.upper()
                    break
        geo = GEOMETRY_SOURCES.get(u)
        elev = ELEVATION_SOURCES.get(u) if layer.has_z or u in ("GROUND_LEVEL", "AVG_GLEVEL", "COVER_LEVEL") else None
        if tag:     # a level written in the drawing (block attribute / label) always wins over the DEM
            out[f.name] = {"source": "block_attr", "value": tag,
                           "fallback": "label" if u in rule_fields else (f"elevation:{elev}" if elev else None)}
        elif u in rule_fields:
            out[f.name] = {"source": "label", "fallback": f"elevation:{elev}" if elev else (f"geometry:{geo}" if geo else None),
                           "derived": computed.get(u) or ("network" if u in {x.upper() for x in (net.upstream_level_field, net.downstream_level_field) if x} and net_fields else None)}
        elif elev:
            out[f.name] = {"source": "elevation", "value": elev}
        elif geo and (geo not in ("area",) or layer.geometry_type == "polygon") and \
                (geo != "length" or layer.geometry_type == "line"):
            out[f.name] = {"source": "geometry", "value": geo}
        elif u in computed:
            out[f.name] = {"source": "computed", "value": computed[u]}
        elif f.default is not None:
            out[f.name] = {"source": "constant", "value": f.default}
        elif f.field_type == "DATE":
            out[f.name] = {"source": "today"}     # filled with the date of the conversion
        else:
            out[f.name] = {"source": "none"}
    return out
