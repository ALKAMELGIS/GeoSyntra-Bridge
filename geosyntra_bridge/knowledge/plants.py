"""
Plant names for Soft Landscaping features — SCIENTIFIC_NAME, COMMON_NAME and LOCAL_NAME (Arabic).

Planting drawings name the species in the CAD layer / block ("…-SS-PA-Washingtonia Robusta-Mexican Fan Palm").
The species is recognised there (or in the value already read from the drawing) and the three names are completed
from the list below: the scientific name in botanical form, the common English name and the usual Arabic name.
A value the drawing gives for COMMON_NAME / LOCAL_NAME is never replaced; SCIENTIFIC_NAME is only rewritten to the
botanical spelling of the same species (or to the template's own code for it when the field has a domain).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# scientific (botanical form), common name, Arabic local name, extra aliases found in drawings
SPECIES: List[Tuple[str, str, str, Tuple[str, ...]]] = [
    # palms
    ("Washingtonia robusta", "Mexican Fan Palm", "واشنطونيا", ("mexican fan palm",)),
    ("Washingtonia filifera", "California Fan Palm", "واشنطونيا فيليفيرا", ("california fan palm",)),
    ("Phoenix dactylifera", "Date Palm", "نخيل التمر", ("date palm",)),
    ("Phoenix roebelenii", "Pygmy Date Palm", "نخيل الروبيليني", ("pygmy date palm",)),
    ("Phoenix canariensis", "Canary Island Date Palm", "نخيل الكناري", ("canary island date palm",)),
    ("Copernicia prunifera", "Carnauba Wax Palm", "نخلة الشمع", ("copernicia wax palm", "wax palm")),
    ("Bismarckia nobilis", "Bismarck Palm", "نخلة بسمارك", ("bismarck palm",)),
    ("Cocos nucifera (Coconut Palm)", "Coconut Palm", "نخيل جوز الهند", ("cocos nucifera", "coconut palm")),
    ("Livistona chinensis", "Chinese Fan Palm", "ليفستونا", ("chinese fan palm",)),
    ("Hyophorbe lagenicaulis", "Bottle Palm", "نخلة الزجاجة", ("bottle palm",)),
    ("Wodyetia bifurcata", "Foxtail Palm", "نخلة ذيل الثعلب", ("foxtail palm",)),
    ("Roystonea regia", "Royal Palm", "النخلة الملكية", ("royal palm",)),
    ("Cycas revoluta", "Sago Palm", "سيكاس", ("sago palm",)),
    # trees
    ("Prosopis cineraria", "Ghaf", "غاف", ("ghaf",)),
    ("Ziziphus spina-christi", "Christ's Thorn Jujube", "سدر", ("ziziphus spina christi", "sidr")),
    ("Acacia tortilis", "Umbrella Thorn Acacia", "سمر", ("vachellia tortilis", "umbrella thorn")),
    ("Conocarpus erectus", "Buttonwood", "كونوكاربس", ("conocarpus", "buttonwood")),
    ("Delonix regia", "Royal Poinciana", "البونسيانا", ("royal poinciana", "flame tree")),
    ("Azadirachta indica", "Neem", "النيم", ("neem",)),
    ("Albizia lebbeck", "Lebbeck Tree", "اللبخ", ("lebbeck",)),
    ("Ficus microcarpa 'Nitida'", "Indian Laurel Fig", "فيكس نيتدا", ("ficus nitida", "ficus microcarpa")),
    ("Ficus benghalensis", "Banyan Tree", "فيكس بنغالي", ("banyan",)),
    ("Tabebuia rosea", "Pink Trumpet Tree", "تابيبويا", ("tabebuia", "pink trumpet tree")),
    ("Plumeria rubra", "Frangipani", "الفتنة", ("plumeria", "frangipani")),
    ("Parkinsonia aculeata", "Jerusalem Thorn", "باركنسونيا", ("parkinsonia",)),
    # shrubs, grasses, ground covers
    ("Pennisetum setaceum 'Rubrum'", "Red Fountain Grass", "حشيشة النافورة الحمراء",
     ("pennisetum rubrum", "pennisetum setaceum rubrum", "red fountain grass", "purple fountain grass")),
    ("Pennisetum setaceum", "Fountain Grass", "حشيشة النافورة", ("fountain grass",)),
    ("Agave attenuata 'Variegata'", "Variegated Foxtail Agave", "أغاف مبرقش", ("agave attenuata variegata",)),
    ("Agave attenuata", "Foxtail Agave", "أغاف", ("foxtail agave",)),
    ("Bougainvillea 'Pink Pixie'", "Pink Pixie Bougainvillea", "جهنمية بينك بيكسي", ("bougainvillea pink pixie",)),
    ("Bougainvillea glabra", "Bougainvillea", "جهنمية", ("bougainvillea",)),
    ("Carissa macrocarpa 'Green Carpet'", "Natal Plum", "كاريسا", ("carissa macrocarpa green carpet", "carissa macrocarpa", "natal plum")),
    ("Ehretia microphylla", "Fukien Tea", "شاي فوكيان", ("fukien tea", "carmona microphylla")),
    ("Nerium oleander", "Oleander", "الدفلة", ("oleander",)),
    ("Lantana camara", "Lantana", "لانتانا", ("lantana",)),
    ("Hibiscus rosa-sinensis", "Chinese Hibiscus", "هيبسكس", ("hibiscus rosa sinensis", "hibiscus")),
    ("Ixora coccinea", "Ixora", "إكسورا", ("ixora",)),
    ("Dodonaea viscosa", "Hopbush", "الشث", ("dodonaea", "hopbush")),
    ("Leucophyllum frutescens", "Texas Sage", "لوكوفيلم", ("leucophyllum", "texas sage")),
    ("Jasminum sambac", "Arabian Jasmine", "الفل", ("arabian jasmine",)),
    ("Clerodendrum inerme", "Wild Jasmine", "كليروديندرم", ("clerodendrum",)),
    ("Duranta erecta", "Golden Dewdrop", "دورانتا", ("duranta",)),
    ("Aloe vera", "Aloe", "الصبر", ("aloe",)),
    ("Sesuvium portulacastrum", "Sea Purslane", "سيسوفيوم", ("sesuvium", "sea purslane")),
    ("Cynodon dactylon", "Bermuda Grass", "النجيل", ("bermuda grass",)),
    ("Paspalum vaginatum", "Seashore Paspalum", "باسبالم", ("paspalum", "seashore paspalum")),
]

NAME_FIELDS = ("SCIENTIFIC_NAME", "COMMON_NAME", "LOCAL_NAME")
_EMPTY = (None, "", "N/A", "NA")


def _norm(s: str) -> str:
    return " " + re.sub(r"[^a-z]+", " ", (s or "").lower()).strip() + " "


_KEYS: List[Tuple[str, int]] = []
for _i, (_sci, _com, _ar, _al) in enumerate(SPECIES):
    for _k in (_sci, _com) + _al:
        _KEYS.append((_norm(_k), _i))
_KEYS.sort(key=lambda x: -len(x[0]))          # most specific first (cultivar before species, species before genus)


def find_species(*texts: str) -> Optional[Tuple[str, str, str]]:
    """The species named in any of the texts (layer name, block name, drawing value), most specific match first."""
    for t in texts:
        nt = _norm(t)
        if len(nt) < 4:
            continue
        for k, i in _KEYS:
            if k in nt:
                return SPECIES[i][:3]
    return None


# ---------------------------------------------------------------- names as written in the drawing (general rule)
# Fields that name the ELEMENT itself (not its city, road, district, contractor or category).
_NOT_ELEMENT = re.compile(r"^(CITY|ZONE|SECTOR|ROAD|DIST|NEW_DIST|OLD_DIST|CONTR|CONS|ANS_|NETWORK|MAINASSET|SUBASSET|"
                          r"SUB_ASSET|LOCATION)", re.I)
_ARABIC = re.compile(r"(_AR|_ARA|_ARABIC|ARA|ARABIC)$", re.I)


def is_name_field(name: str) -> bool:
    """Any field that holds a name (the general 'names are taken as written' rule applies to all of them)."""
    return "NAME" in name.upper()


def element_name_fields(layer) -> List:
    return [fd for fd in layer.fields if is_name_field(fd.name) and not _NOT_ELEMENT.match(fd.name)
            and fd.field_type == "TEXT"]


def _is_code(tok: str) -> bool:
    t = tok.strip()
    return (not t or " " not in t and (len(t) <= 4 or any(ch.isdigit() for ch in t))) and "(" not in t


def names_from_text(text: str) -> List[str]:
    """Descriptive parts of a CAD layer / block name, as written:
    'ISL-ALBT-2025-P02-SS-PA-Washingtonia Robusta-Mexican Fan Palm' -> ['Washingtonia Robusta', 'Mexican Fan Palm']
    'SS-PA-COPERNICIA (WAX Palm )' -> ['COPERNICIA', 'WAX Palm']"""
    if not text:
        return []
    text = re.sub(r"^[^|]*\|", "", text)                     # xref prefix 'file|layer'
    parts = [p.strip() for p in re.split(r"\s*[-_]\s*(?=\S)|\s+-\s+", text) if p and p.strip()]
    while parts and _is_code(parts[0]):                      # project / discipline / type codes in front
        parts.pop(0)
    out: List[str] = []
    for p in parts:
        if _is_code(p):
            continue
        m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", p)
        if m:
            out += [x.strip() for x in m.groups() if x.strip()]
        else:
            out.append(re.sub(r"\s+", " ", p))
    return out


def _put(f, fd, v, out):
    v = str(v).strip()
    if not v:
        return
    f.attrs[fd.name] = v[: fd.length] if fd.length else v
    out.append(fd.name)


def fill_names(f, layer, schema) -> List[str]:
    """Element names taken from the drawing as written (labels / attributes first, then the CAD layer or block name),
    completed from the plant list only where the drawing has nothing. Returns the fields written."""
    fields = element_name_fields(layer)
    if not fields:
        return []
    by = {fd.name.upper(): fd for fd in fields}
    empty = lambda fd: f.attrs.get(fd.name) in _EMPTY  # noqa: E731
    out: List[str] = []
    src = names_from_text(f.source.layer or "") or names_from_text(getattr(f.source, "block_name", "") or "")
    sci, com = by.get("SCIENTIFIC_NAME"), by.get("COMMON_NAME")
    if sci is not None or com is not None:                    # planting: scientific + common
        if sci is not None and empty(sci) and src:
            _put(f, sci, src[0], out)
        if com is not None and empty(com) and len(src) > 1:
            _put(f, com, " - ".join(src[1:]), out)
    else:                                                     # any other element name: the drawing's own name
        for fd in fields:
            if empty(fd) and not _ARABIC.search(fd.name) and src:
                _put(f, fd, " - ".join(src), out)
    sp = find_species(str(f.attrs.get(sci.name) or "") if sci is not None else "",
                      str(f.attrs.get(com.name) or "") if com is not None else "",
                      f.source.layer or "", getattr(f.source, "block_name", "") or "")
    if sp is not None:                                        # only what the drawing does not give
        for key, v in (("SCIENTIFIC_NAME", sp[0]), ("COMMON_NAME", sp[1]), ("LOCAL_NAME", sp[2])):
            fd = by.get(key)
            if fd is not None and empty(fd):
                _put(f, fd, v, out)
    loc = by.get("LOCAL_NAME")
    if loc is not None and empty(loc) and com is not None and not empty(com):
        pass                                                  # no Arabic name known: left for the engineer
    if out:
        f.remarks.append(f"names from the drawing: {', '.join(dict.fromkeys(out))}")
    return out
