"""
Reads real schema definitions directly out of an Esri File Geodatabase
(.gdb folder) WITHOUT GDAL/arcpy — neither is installable in this
offline sandbox.

How: FileGDB's internal catalog table ("GDB_Items", always discoverable
via the fixed system table a00000001.gdbtable = GDB_SystemCatalog) stores
each dataset's full definition as a plain, well-formed, uncompressed XML
document (Esri's standard DE* schema, the same XML used in XML Workspace
Documents) embedded directly in the row bytes of a00000004-ish .gdbtable
file. This reader does NOT attempt to decode FileGDB's general binary row
format (which is undocumented/proprietary and risky to reverse-engineer
byte-exact) — it only locates and extracts those self-contained XML
blocks by tag matching, then parses them with the stdlib XML parser.
This is deliberately conservative: it only trusts what is literally
well-formed, parseable XML; it does not guess at binary field encodings.

Extracts:
  - Feature classes / tables: name, catalog path (feature dataset),
    shape type, OID/shape field names, and full field list (name, Esri
    field type, nullable/required, length where present).
  - Coded-value domains: name, field type, code->description pairs.

Everything here is read-only and does not touch the GDB's user feature
data at all.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# --- low-level: find the physical table id for a system-catalog item name ---

def _read_system_catalog(gdb_path: str) -> Dict[str, int]:
    """Parses a00000001.gdbtable (GDB_SystemCatalog — always table id 1 by
    FileGDB convention) to map item name -> physical table id.

    Row shape (verified empirically against real ADM template GDBs):
    [4-byte little-endian row length][1-byte name length][ASCII name][4
    more bytes of a field we don't need]. Physical table ids are NOT
    stored in the row — they are simply the 1-based row order (verified:
    row 1 = GDB_SystemCatalog itself = a00000001, row 4 = GDB_Items =
    a00000004, etc., matching the .gdb directory's own file numbering).
    """
    import struct

    path = os.path.join(gdb_path, "a00000001.gdbtable")
    with open(path, "rb") as f:
        data = f.read()

    anchor = b"GDB_SystemCatalog"
    anchor_idx = data.find(anchor)
    if anchor_idx == -1:
        raise RuntimeError("a00000001.gdbtable does not look like GDB_SystemCatalog "
                            "(anchor string 'GDB_SystemCatalog' not found).")
    # The row's length-byte sits one byte before the anchor string, and the
    # row's 4-byte length prefix sits 4 bytes before that.
    strlen = data[anchor_idx - 1]
    assert strlen == len(anchor), f"unexpected catalog row framing (strlen={strlen})"
    start = anchor_idx - 1 - 4  # position of the first row's 4-byte length prefix

    names: Dict[str, int] = {}
    pos = start
    table_id = 0
    n = len(data)
    while pos + 4 <= n:
        blob_len = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        if blob_len <= 0 or pos + blob_len > n:
            break
        blob = data[pos:pos + blob_len]
        pos += blob_len
        table_id += 1
        ln = blob[0]
        if 1 + ln > len(blob):
            continue
        try:
            name = blob[1:1 + ln].decode("ascii")
        except UnicodeDecodeError:
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", name):
            names[name] = table_id
    return names


def _find_items_table_path(gdb_path: str) -> str:
    catalog = _read_system_catalog(gdb_path)
    if "GDB_Items" not in catalog:
        raise RuntimeError(
            "Could not locate 'GDB_Items' in GDB_SystemCatalog — this does not "
            "look like a standard Esri File Geodatabase, or its internal layout "
            "is unexpected."
        )
    table_id = catalog["GDB_Items"]
    return os.path.join(gdb_path, f"a{table_id:08x}.gdbtable")


# --- XML block extraction (conservative: only trusts well-formed XML) ---

_NS_STRIP_RE = re.compile(r"\sxmlns(:\w+)?='[^']*'")


def _extract_blocks(data: bytes, root_tag: str) -> List[str]:
    open_marker = f"<{root_tag} ".encode("utf-8")
    open_marker_bare = f"<{root_tag}>".encode("utf-8")
    close_marker = f"</{root_tag}>".encode("utf-8")
    blocks = []
    i = 0
    while True:
        i1 = data.find(open_marker, i)
        i2 = data.find(open_marker_bare, i)
        starts = [x for x in (i1, i2) if x != -1]
        if not starts:
            break
        start = min(starts)
        end = data.find(close_marker, start)
        if end == -1:
            break
        end += len(close_marker)
        raw = data[start:end]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        blocks.append(text)
        i = end
    return blocks


def _parse_xml(text: str) -> Optional[ET.Element]:
    # Namespace prefixes (xsi:, xs:, typens:) on *attributes* are fine for
    # ElementTree; strip xmlns declarations only to avoid needing a parser
    # that resolves every typens: prefix on attribute values we don't use.
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        try:
            return ET.fromstring(_NS_STRIP_RE.sub("", text))
        except ET.ParseError:
            return None


# --- data model for what we extract ---

@dataclass
class RealFieldDef:
    name: str
    esri_type: str              # e.g. esriFieldTypeString
    nullable: bool = True
    required: bool = False
    length: Optional[int] = None


@dataclass
class RealFeatureClassDef:
    name: str
    catalog_path: str
    dataset_type: str           # esriDTFeatureClass | esriDTTable
    shape_type: Optional[str]   # esriGeometryPoint/Polyline/Polygon or None
    shape_field: Optional[str]
    fields: List[RealFieldDef] = field(default_factory=list)


@dataclass
class RealDomainDef:
    name: str
    field_type: str
    coded_values: Dict[str, str] = field(default_factory=dict)  # code -> description


SHAPE_TYPE_TO_GEOM = {
    "esriGeometryPoint": "point",
    "esriGeometryMultipoint": "point",
    "esriGeometryPolyline": "line",
    "esriGeometryPolygon": "polygon",
}


def _field_from_xml(el: ET.Element) -> RealFieldDef:
    def t(tag, default=None):
        child = el.find(tag)
        return child.text if child is not None and child.text is not None else default

    length_txt = t("Length")
    return RealFieldDef(
        name=t("Name", ""),
        esri_type=t("FieldType", "esriFieldTypeString"),
        nullable=(t("IsNullable", "true") == "true"),
        required=(t("Required", "false") == "true"),
        length=int(length_txt) if length_txt and length_txt.isdigit() else None,
    )


def _feature_class_from_xml(el: ET.Element) -> RealFeatureClassDef:
    def t(tag, default=None):
        child = el.find(tag)
        return child.text if child is not None and child.text is not None else default

    fields_el = el.find("GPFieldInfoExs")
    fields = []
    if fields_el is not None:
        for fe in fields_el.findall("GPFieldInfoEx"):
            fields.append(_field_from_xml(fe))

    return RealFeatureClassDef(
        name=t("Name", ""),
        catalog_path=t("CatalogPath", ""),
        dataset_type=t("DatasetType", ""),
        shape_type=t("ShapeType"),
        shape_field=t("ShapeFieldName"),
        fields=fields,
    )


def _domain_from_xml(el: ET.Element) -> RealDomainDef:
    def t(tag, default=None):
        child = el.find(tag)
        return child.text if child is not None and child.text is not None else default

    coded: Dict[str, str] = {}
    cv_array = el.find("CodedValues")
    if cv_array is not None:
        for cv in cv_array.findall("CodedValue"):
            name_el = cv.find("Name")
            code_el = cv.find("Code")
            if code_el is not None and code_el.text is not None:
                coded[code_el.text] = name_el.text if name_el is not None else code_el.text

    return RealDomainDef(
        name=t("DomainName", ""),
        field_type=t("FieldType", "esriFieldTypeString"),
        coded_values=coded,
    )


@dataclass
class ExtractedGdbSchema:
    feature_classes: List[RealFeatureClassDef] = field(default_factory=list)
    domains: List[RealDomainDef] = field(default_factory=list)


def extract_schema_from_gdb(gdb_path: str) -> ExtractedGdbSchema:
    """gdb_path: path to the unzipped <name>.gdb directory."""
    items_path = _find_items_table_path(gdb_path)
    with open(items_path, "rb") as f:
        data = f.read()

    result = ExtractedGdbSchema()

    seen_fc_names = set()
    for tag in ("DEFeatureClassInfo", "DETableInfo"):
        for raw in _extract_blocks(data, tag):
            el = _parse_xml(raw)
            if el is None:
                continue
            fc = _feature_class_from_xml(el)
            if fc.name and fc.name not in seen_fc_names:
                seen_fc_names.add(fc.name)
                result.feature_classes.append(fc)

    seen_domain_names = set()
    for raw in _extract_blocks(data, "GPCodedValueDomain2"):
        el = _parse_xml(raw)
        if el is None:
            continue
        d = _domain_from_xml(el)
        if d.name and d.name not in seen_domain_names:
            seen_domain_names.add(d.name)
            result.domains.append(d)

    return result
