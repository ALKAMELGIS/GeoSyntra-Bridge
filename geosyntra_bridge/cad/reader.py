"""
CAD reader: DXF (ASCII + binary, R12..R2018+) through ezdxf, DWG through the
ODA File Converter (converted to a temporary DXF — the original is never
opened for writing).

Extracted: LINE, LWPOLYLINE, POLYLINE (2D/3D), ARC, CIRCLE, ELLIPSE, SPLINE,
POINT, INSERT (+ATTRIB values, rotation), HATCH (boundary + islands), SOLID,
3DFACE, TEXT, MTEXT, ATTRIB-less block text, MULTILEADER / LEADER labels.
Anything else is counted per type in `skipped_types` (never silently lost).
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from typing import Callable, Dict, List, Optional

from .model import CadDocument, CadEntity, CadLayerInfo, POINT, LINE, POLYGON, TEXT

ProgressFn = Optional[Callable[[float, str], None]]

INSUNITS = {0: "unitless", 1: "inches", 2: "feet", 3: "miles", 4: "millimeters", 5: "centimeters",
            6: "meters", 7: "kilometers", 14: "decimeters"}
UNIT_TO_METERS = {1: 0.0254, 2: 0.3048, 3: 1609.344, 4: 0.001, 5: 0.01, 6: 1.0, 7: 1000.0, 14: 0.1}


class CadReadError(Exception):
    """Raised for unsupported, corrupted or unreadable input."""


# ----------------------------------------------------------------- sniffing
def sniff_cad_file(path: str) -> str:
    """Return 'dxf' | 'dxf-binary' | 'dwg' by content (not by extension)."""
    with open(path, "rb") as f:
        head = f.read(4096)
    if not head:
        raise CadReadError("file is empty")
    if head.startswith(b"AutoCAD Binary DXF"):
        return "dxf-binary"
    if head[:4] in (b"AC10", b"AC1.") or head[:2] == b"AC" and head[2:6].isdigit():
        return "dwg"
    if len(head) > 4 and head[1:4] == b"\x09\xfe\x02" and head[0] in (0x08, 0xC8):
        return "dgn"                                     # MicroStation V7
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise CadReadError("this looks like a MicroStation V8 DGN (or another Office-type file): save it as DWG / DXF "
                           "in MicroStation (File > Save As) — DGN V7 is read directly")
    txt = head.decode("latin-1", "replace").upper()
    if "SECTION" in txt and ("HEADER" in txt or "ENTITIES" in txt or "TABLES" in txt or "CLASSES" in txt):
        return "dxf"
    if txt.lstrip().startswith("0") and "SECTION" in txt:
        return "dxf"
    raise CadReadError("file content is not a DXF, DWG or DGN drawing")


# ----------------------------------------------------------------- DWG
BUNDLED_ODA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools", "oda")


def _bundled_oda() -> List[str]:
    """ODA File Converter shipped inside the system: tools/oda (any sub-folder) — the extracted Windows package
    (ODAFileConverter.exe + its DLLs) or the Linux AppImage / extracted AppImage."""
    out: List[str] = []
    if not os.path.isdir(BUNDLED_ODA):
        return out
    for root, _dirs, files in os.walk(BUNDLED_ODA):
        for f in files:
            fl = f.lower()
            if (os.name == "nt" and fl == "odafileconverter.exe") or \
               (os.name != "nt" and (f == "ODAFileConverter" or fl.endswith(".appimage"))):
                out.append(os.path.join(root, f))
    return sorted(out, key=len)


def unpack_bundled_oda() -> Optional[str]:
    """Windows: an ODA .msi dropped into tools/oda is unpacked in place (administrative install — no admin rights,
    nothing installed on the machine), so the converter lives inside the system folder."""
    if os.name != "nt" or not os.path.isdir(BUNDLED_ODA) or any(p.lower().endswith(".exe") for p in _bundled_oda()):
        return None
    msis = sorted(f for f in os.listdir(BUNDLED_ODA) if f.lower().endswith(".msi"))
    if not msis:
        return None
    target = os.path.join(BUNDLED_ODA, "app")
    try:
        subprocess.run(["msiexec", "/a", os.path.join(BUNDLED_ODA, msis[-1]), "/qn", f"TARGETDIR={target}"],
                       timeout=600, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001
        return None
    found = _bundled_oda()
    return found[0] if found else None


def find_oda_converter() -> Optional[str]:
    env = os.environ.get("GSB_ODA_CONVERTER")
    if env and os.path.isfile(env):
        return env
    cands = _bundled_oda() + [shutil.which("ODAFileConverter"), shutil.which("ODAFileConverter.exe")]
    for root in (r"C:\Program Files\ODA", r"C:\Program Files (x86)\ODA"):
        if os.path.isdir(root):
            for d in sorted(os.listdir(root), reverse=True):
                cands.append(os.path.join(root, d, "ODAFileConverter.exe"))
    cands += ["/usr/bin/ODAFileConverter", "/opt/ODAFileConverter/ODAFileConverter",
              "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"]
    for c in cands:
        if c and os.path.isfile(c):
            if os.name != "nt" and not os.access(c, os.X_OK):
                try: os.chmod(c, 0o755)
                except OSError: continue
            return c
    return None


BUNDLED_LIBREDWG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                "tools", "libredwg")


def find_libredwg() -> Optional[str]:
    """GSB_DWG2DXF, then the copy shipped in tools/libredwg (Windows build of LibreDWG 0.13.3), then PATH."""
    env = os.environ.get("GSB_DWG2DXF")
    if env and os.path.isfile(env):
        return env
    for name in ("dwg2dxf.exe", "dwg2dxf") if os.name == "nt" else ("dwg2dxf",):
        p = os.path.join(BUNDLED_LIBREDWG, name)
        if os.path.isfile(p) and (os.name == "nt" or os.access(p, os.X_OK)):
            return p
    return shutil.which("dwg2dxf") or shutil.which("dwg2dxf.exe")


def find_autocad_console() -> Optional[str]:
    """AutoCAD's own headless engine (accoreconsole.exe) — installed with AutoCAD, Civil 3D, Map 3D and the other
    AutoCAD-based products. GSB_ACCORECONSOLE overrides; the newest installed version wins."""
    env = os.environ.get("GSB_ACCORECONSOLE")
    if env and os.path.isfile(env):
        return env
    if os.name != "nt":
        return None
    found: List[str] = []
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"):
        base = os.path.join(root, "Autodesk")
        if not os.path.isdir(base):
            continue
        for d in os.listdir(base):
            exe = os.path.join(base, d, "accoreconsole.exe")
            if os.path.isfile(exe):
                found.append(exe)
    return sorted(set(found), reverse=True)[0] if found else None


def dwg_converters() -> List[str]:
    """Every DWG engine available here, most reliable first."""
    out = []
    if find_oda_converter(): out.append(f"ODA File Converter ({find_oda_converter()})")
    if find_autocad_console(): out.append(f"AutoCAD Core Console ({find_autocad_console()})")
    if find_libredwg(): out.append(f"LibreDWG dwg2dxf ({find_libredwg()})")
    return out


def dwg_converter() -> Optional[str]:
    c = dwg_converters()
    return c[0] if c else None


def _convert_autocad(exe: str, dwg_path: str, out_dir: str, timeout: int) -> str:
    """Open a copy of the drawing in AutoCAD's core console and DXFOUT it (2018 DXF, full precision)."""
    work = tempfile.mkdtemp(prefix="gsb_dwg_acad_", dir=out_dir)
    src = os.path.join(work, "drawing.dwg")
    shutil.copy2(dwg_path, src)                                    # never open the original
    out = os.path.join(work, "drawing.dxf")
    scr = os.path.join(work, "export.scr")
    with open(scr, "w", encoding="utf-8") as f:
        f.write("FILEDIA\n0\n_.DXFOUT\n\"%s\"\n_V\n_2018\n16\n" % out)
    try:
        proc = subprocess.run([exe, "/i", src, "/s", scr, "/l", "en-US"], capture_output=True, timeout=timeout,
                              cwd=work, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise CadReadError(f"AutoCAD Core Console timed out after {timeout}s") from e
    if not os.path.isfile(out) or os.path.getsize(out) < 100:
        msg = (proc.stdout or b"").decode("utf-16-le" if (proc.stdout or b"")[1:2] == b"\x00" else "utf-8", "replace")
        raise CadReadError(f"AutoCAD Core Console could not export the drawing. {' '.join(msg.split())[-300:]}")
    return out


def _convert_libredwg(exe: str, dwg_path: str, out_dir: str, timeout: int) -> str:
    out = os.path.join(tempfile.mkdtemp(prefix="gsb_dwg_out_", dir=out_dir), "drawing.dxf")
    try:
        proc = subprocess.run([exe, "-y", "-o", out, dwg_path], capture_output=True, timeout=timeout,
                              cwd=os.path.dirname(exe) or None,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise CadReadError(f"dwg2dxf timed out after {timeout}s") from e
    if not os.path.isfile(out) or os.path.getsize(out) < 100:
        msg = " ".join((proc.stderr or b"").decode("utf-8", "replace").split())
        raise CadReadError(f"DWG conversion failed. {msg[-160:]}")
    return out


DWG_HELP = ("Install the free ODA File Converter (opendesign.com → Guest Files → ODA File Converter, Windows 64-bit) "
            "and restart GeoSyntra Bridge — it is found automatically. If AutoCAD or Civil 3D is installed it is used "
            "too. Or open the drawing in AutoCAD, run AUDIT, then save it as DXF and upload that.")


def convert_dwg_to_dxf(dwg_path: str, out_dir: str, timeout: int = 600) -> str:
    """Try every installed engine, most reliable first; the first good DXF wins. The original is never written."""
    engines = []
    if find_oda_converter(): engines.append(("ODA File Converter", _convert_oda, find_oda_converter()))
    if find_autocad_console(): engines.append(("AutoCAD Core Console", _convert_autocad, find_autocad_console()))
    if find_libredwg(): engines.append(("LibreDWG", _convert_libredwg, find_libredwg()))
    if not engines:
        raise CadReadError("DWG input needs a converter. " + DWG_HELP)
    errors = []
    for name, fn, exe in engines:
        try:
            return fn(exe, dwg_path, out_dir, timeout)
        except CadReadError as e:
            errors.append(f"{name}: {str(e)[:160]}")
    tried = ", ".join(n for n, _, _ in engines)
    hint = DWG_HELP if not find_oda_converter() else "Open the drawing in AutoCAD, run AUDIT and RECOVER, save it, and upload it again."
    raise CadReadError(f"This DWG could not be read by {tried}. {hint}")


def _convert_oda(exe: str, dwg_path: str, out_dir: str, timeout: int) -> str:
    in_dir = tempfile.mkdtemp(prefix="gsb_dwg_in_", dir=out_dir)
    conv_dir = tempfile.mkdtemp(prefix="gsb_dwg_out_", dir=out_dir)
    base = os.path.basename(dwg_path)
    if not base.lower().endswith(".dwg"):
        base += ".dwg"
    shutil.copy2(dwg_path, os.path.join(in_dir, base))  # ODA works on folders; work on a copy
    cmd = [exe, in_dir, conv_dir, "ACAD2018", "DXF", "0", "1", "*.DWG"]   # audit on: damaged files are repaired
    env = dict(os.environ)
    if os.name != "nt":
        env.setdefault("QT_QPA_PLATFORM", "offscreen")            # servers have no display
        if exe.lower().endswith(".appimage"):
            env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")      # works without FUSE (containers)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, cwd=os.path.dirname(exe) or None,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise CadReadError(f"ODA File Converter timed out after {timeout}s") from e
    out = os.path.join(conv_dir, os.path.splitext(base)[0] + ".dxf")
    if not os.path.isfile(out):
        msg = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")[:500]
        raise CadReadError(f"DWG conversion failed (file corrupted or unsupported version). {msg}")
    shutil.rmtree(in_dir, ignore_errors=True)
    return out


# ----------------------------------------------------------------- DXF
def _xyz(v) -> tuple:
    return (float(v[0]), float(v[1]), float(v[2]) if len(v) > 2 else 0.0)


def _flatten(entity, distance: float) -> List[tuple]:
    from ezdxf import path as ezpath
    p = ezpath.make_path(entity)
    return [_xyz(v) for v in p.flattening(distance=distance, segments=8)]


def _attribs(insert) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for a in insert.attribs:
            tag = (a.dxf.tag or "").strip()
            if tag:
                out[tag.upper()] = (a.dxf.text or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return out


def _clean_text(s: str) -> str:
    return " ".join((s or "").replace("\\P", " ").split())


def read_cad(path: str, work_dir: Optional[str] = None, progress: ProgressFn = None,
             flatten_distance: float = 0.01, explode_blocks: bool = False) -> CadDocument:
    """Read a DXF/DWG file into a CadDocument (read-only access to `path`)."""
    import ezdxf
    from ezdxf import recover

    if not os.path.isfile(path):
        raise CadReadError("input file not found")
    kind = sniff_cad_file(path)
    if kind == "dgn":
        if progress: progress(0.1, "Reading MicroStation DGN")
        cad = _read_dgn(path)
        cad.prj_epsg = sidecar_prj_epsg(path)
        return _finish(cad, progress)
    work_dir = work_dir or tempfile.gettempdir()
    dxf_path = path
    converted = False
    if kind == "dwg":
        if progress: progress(0.05, f"Converting DWG to DXF ({(dwg_converter() or 'no converter').split(' (')[0]})")
        dxf_path = convert_dwg_to_dxf(path, work_dir)
        converted = True

    if progress: progress(0.12, "Parsing drawing")
    warnings: List[str] = []
    try:
        doc = ezdxf.readfile(dxf_path)
    except ezdxf.DXFStructureError:
        try:
            doc, auditor = recover.readfile(dxf_path)
        except Exception as e:  # noqa: BLE001
            raise CadReadError(f"drawing is corrupted and could not be recovered: {e}") from e
        warnings.append(f"DXF structure errors were recovered ({len(auditor.fixes)} fixes, "
                        f"{len(auditor.errors)} unrecoverable errors)")
    except (IOError, UnicodeDecodeError) as e:
        raise CadReadError(f"cannot read drawing: {e}") from e
    except Exception as e:  # noqa: BLE001
        raise CadReadError(f"unsupported or corrupted drawing: {e}") from e

    insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    cad = CadDocument(source_name=os.path.basename(path), dxf_version=doc.dxfversion,
                      insunits=insunits, units_name=INSUNITS.get(insunits, f"code {insunits}"),
                      read_warnings=warnings, converted_from_dwg=converted)
    for layer in doc.layers:
        try:
            lw = layer.dxf.get("lineweight", -3)
            cad.layers[layer.dxf.name] = CadLayerInfo(
                name=layer.dxf.name, color=abs(layer.dxf.color) if layer.dxf.hasattr("color") else None,
                linetype=layer.dxf.linetype if layer.dxf.hasattr("linetype") else "",
                is_off=layer.is_off(), is_frozen=layer.is_frozen(),
                lineweight=lw if lw is not None and lw >= 0 else None,
                is_xref="|" in layer.dxf.name or bool(layer.dxf.get("flags", 0) & 16))
        except Exception:  # noqa: BLE001
            continue

    msp = doc.modelspace()
    try:
        gd = msp.get_geodata()
        if gd is not None:
            cad.geodata_epsg = geodata_epsg(gd)
    except Exception:  # noqa: BLE001
        pass
    cad.prj_epsg = sidecar_prj_epsg(path)

    all_ents = list(msp)
    n = max(1, len(all_ents))
    budget = [CONTAINER_BUDGET]
    for i, e in enumerate(all_ents):
        if progress and i % 2000 == 0:
            progress(0.12 + 0.75 * i / n, f"Reading entities {i:,}/{n:,}")
        if e.dxftype() == "INSERT" and (_is_container(e) or (explode_blocks and _explodable(e))):
            got = _explode_container(e, flatten_distance, budget)
            if got:
                cad.entities.extend(got)
                cad.recovered_types["INSERT (drawing block)"] = cad.recovered_types.get("INSERT (drawing block)", 0) + 1
                continue
        try:
            for ce in _convert_entity(e, flatten_distance):
                cad.entities.append(ce)
        except Exception as ex:  # noqa: BLE001 — isolate bad entity, continue
            t = e.dxftype()
            cad.skipped_types[t + " (unreadable)"] = cad.skipped_types.get(t + " (unreadable)", 0) + 1
            if len(cad.read_warnings) < 50:
                cad.read_warnings.append(f"entity {getattr(e.dxf, 'handle', '?')} ({t}) could not be read: {ex}")
            continue
        else:
            t = e.dxftype()
            if t not in _HANDLED:
                got = _explode_unhandled(e, flatten_distance) if t not in _ANNOTATION_ONLY else []
                if got:
                    cad.entities.extend(got)
                    cad.recovered_types[t] = cad.recovered_types.get(t, 0) + 1
                else:
                    cad.skipped_types[t] = cad.skipped_types.get(t, 0) + 1
    # paper space (layouts): legends / keys / title blocks — read for information, never converted
    try:
        for layout in doc.layouts:
            if layout.name.lower() == "model":
                continue
            for e in layout:
                if e.dxftype() in ("VIEWPORT",):
                    continue
                try:
                    for ce in _convert_entity(e, flatten_distance):
                        ce.space = layout.name
                        cad.paper.append(ce)
                except Exception:  # noqa: BLE001
                    continue
    except Exception as ex:  # noqa: BLE001
        cad.read_warnings.append(f"layouts could not be read: {ex}")
    fixed = doc.__dict__.get("_gsb_base_fixed")
    if fixed:
        cad.read_warnings.append("block base point restored for " + ", ".join(sorted(fixed)[:5]) +
                                 " (content already at world coordinates; read as stored it would land twice as far)")
    return _finish(cad, progress)


def geodata_epsg(gd) -> Optional[int]:
    """EPSG of an AutoCAD GEODATA object: the EPSG code, else the coordinate-system XML (Map 3D aliases / WKT)."""
    try:
        epsg, _ = gd.get_crs()
        if epsg:
            return int(epsg)
    except Exception:  # noqa: BLE001
        pass
    xml = ""
    try:
        xml = gd.coordinate_system_definition or ""
    except Exception:  # noqa: BLE001
        pass
    from ..geometry.crs import epsg_from_text
    return epsg_from_text(xml)


def sidecar_prj_epsg(path: str) -> Optional[int]:
    """A .prj file next to the drawing (same name first, else the only .prj in the folder)."""
    from ..geometry.crs import epsg_from_text
    d, stem = os.path.dirname(path) or ".", os.path.splitext(os.path.basename(path))[0]
    try:
        prjs = [f for f in os.listdir(d) if f.lower().endswith(".prj")]
    except OSError:
        return None
    pick = [f for f in prjs if os.path.splitext(f)[0].lower() == stem.lower()] or (prjs if len(prjs) == 1 else [])
    for f in pick:
        try:
            with open(os.path.join(d, f), encoding="utf-8", errors="ignore") as fh:
                e = epsg_from_text(fh.read(20000))
            if e:
                return e
        except OSError:
            continue
    return None


def _finish(cad: CadDocument, progress: ProgressFn = None) -> CadDocument:
    """Shared by every format: annotation blocks, artefacts, clean-up, styles, keys, layer roles."""
    n_ann = apply_annotation_blocks(cad)
    if n_ann:
        cad.read_warnings.append(f"{n_ann} attribute blocks recognised as annotation tags (treated as labels, not features)")
    _resolve_bylayer(cad)
    from .artifacts import classify_artifacts
    classify_artifacts(cad)
    from .cleaning import clean_drawing
    clean_drawing(cad)
    from .styles import assign_groups
    assign_groups(cad)
    from .keys import read_keys
    read_keys(cad)
    from .roles import classify_layer_roles
    classify_layer_roles(cad)
    if cad.recovered_types:
        cad.read_warnings.append("read from their drawn geometry (exploded): " + ", ".join(
            f"{v:,} {k}" for k, v in sorted(cad.recovered_types.items(), key=lambda x: -x[1])))
    for reason, k in cad.artifacts.items():
        cad.read_warnings.append(f"{k:,} {reason} ignored (drawing artefacts, not assets)")
    for ce in cad.entities:
        if ce.layer not in cad.layers:
            cad.layers[ce.layer] = CadLayerInfo(name=ce.layer)
    if progress: progress(0.9, f"Read {len(cad.entities):,} entities")
    return cad


_HANDLED = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "POINT", "INSERT",
            "HATCH", "SOLID", "3DFACE", "TEXT", "MTEXT", "MULTILEADER", "MLEADER", "TRACE"}


def _read_dgn(path: str) -> CadDocument:
    """MicroStation V7 DGN through GDAL: elements by level (the DGN 'layer'), lines, shapes, cells and texts."""
    import pyogrio.raw
    import shapely
    meta, fids, geoms, cols = pyogrio.raw.read(path, return_fids=True)
    names = list(meta["fields"])
    col = {n: cols[i] for i, n in enumerate(names)}
    cad = CadDocument(source_name=os.path.basename(path), dxf_version="DGN V7", insunits=6, units_name="meters",
                      read_warnings=["MicroStation DGN V7 read through GDAL — levels become CAD layers"])
    def num(name, i):
        a = col.get(name)
        if a is None:
            return None
        v = a[i]
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if v != v else int(v)

    for i in range(len(fids)):
        g = shapely.from_wkb(geoms[i]) if geoms[i] is not None else None
        if g is None or g.is_empty:
            continue
        layer = f"Level {num('Level', i) or 0}"
        tv = col["Text"][i] if "Text" in col else None
        text = str(tv) if tv not in (None, "") and str(tv) != "nan" else ""
        color = num("ColorIndex", i)
        en = num("EntityNum", i)
        handle = f"D{en if en is not None else int(fids[i])}"
        parts = list(getattr(g, "geoms", [g]))
        for k, part in enumerate(parts):
            h = handle if len(parts) == 1 else f"{handle}:{k}"
            t = part.geom_type
            if t == "Point":
                c = (part.x, part.y, part.z if part.has_z else 0.0)
                if text:
                    cad.entities.append(CadEntity(handle=h, layer=layer, dxftype="DGN TEXT", kind=TEXT, coords=[c], center=c,
                                               text=text, color=color))
                else:
                    cad.entities.append(CadEntity(handle=h, layer=layer, dxftype="DGN POINT", kind=POINT, coords=[c],
                                                  center=c, color=color))
            elif t == "LineString" and part.length == 0:          # a DGN point is a zero-length line
                x, y, *zz = part.coords[0]
                c = (x, y, zz[0] if zz else 0.0)
                cad.entities.append(CadEntity(handle=h, layer=layer, dxftype="DGN POINT", kind=POINT, coords=[c],
                                              center=c, color=color))
            elif t == "LineString":
                cs = [(x, y, z) for x, y, *zz in part.coords for z in [zz[0] if zz else 0.0]]
                closed = len(cs) > 3 and cs[0][:2] == cs[-1][:2]
                cad.entities.append(CadEntity(handle=h, layer=layer, dxftype="DGN LINE", kind=LINE, coords=cs,
                                              closed=closed, color=color))
            elif t == "Polygon":
                ring = [(x, y, z) for x, y, *zz in part.exterior.coords for z in [zz[0] if zz else 0.0]]
                cad.entities.append(CadEntity(handle=h, layer=layer, dxftype="DGN SHAPE", kind=POLYGON, coords=ring,
                                              closed=True, color=color,
                                              holes=[[(x, y, 0.0) for x, y, *_ in r.coords] for r in part.interiors]))
    for ce in cad.entities:
        if ce.layer not in cad.layers:
            cad.layers[ce.layer] = CadLayerInfo(name=ce.layer)
    return cad


# never assets: dimensions, viewports, images, frames
_ANNOTATION_ONLY = {"DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION", "LEADER", "TOLERANCE", "VIEWPORT", "IMAGE",
                    "WIPEOUT", "OLE2FRAME", "OLEFRAME", "ATTDEF", "ATTRIB", "SEQEND", "VERTEX", "RAY", "XLINE", "UNDERLAY",
                    "PDFUNDERLAY", "DWFUNDERLAY", "DGNUNDERLAY", "LIGHT", "SUN", "TABLE", "ACAD_TABLE"}


CONTAINER_MIN = 400           # a block this big is drawing content (a plan, a bound xref), not a symbol
CONTAINER_BUDGET = 400_000    # entities read out of drawing blocks per file (performance guard)


def _block_size(e) -> int:
    try:
        doc = e.doc
        cache = doc.__dict__.setdefault("_gsb_block_sizes", {})
        name = e.dxf.name
        if name not in cache:
            blk = doc.blocks.get(name)
            cache[name] = len(blk) if blk is not None else 0
        return cache[name]
    except Exception:  # noqa: BLE001
        return 0


def _is_container(e) -> bool:
    """INSERTs that hold part of the drawing itself (bound xrefs, copied drawings, grouped plans) — exploded into
    their real geometry. Symbols (valves, lamps, manholes, tagged blocks) stay one point each."""
    name = str(getattr(e.dxf, "name", "") or "")
    if name.startswith("*"):                       # anonymous dims / hatches / dynamic-block copies
        return False
    try:
        if e.attribs:
            return False
    except Exception:  # noqa: BLE001
        pass
    bound = "|" in name or "$0$" in name
    return bound or _block_size(e) >= CONTAINER_MIN


def _explodable(e) -> bool:
    """Explode on request (the user's Explode): every block without data tags — tagged blocks keep their values."""
    if str(getattr(e.dxf, "name", "") or "").startswith("*"):
        return False
    try:
        return not e.attribs and _block_size(e) > 0
    except Exception:  # noqa: BLE001
        return False


def _lost_base_point(e) -> Optional[tuple]:
    """A block whose content already sits at world coordinates, inserted at a point inside that content, with a base
    point of 0,0 — the base point was lost (it happens when a DWG is converted) and the real one is the insertion
    point; read as stored, the whole drawing lands twice as far from the origin. Returns the shift to undo it."""
    try:
        ins = e.dxf.insert
        if abs(ins[0]) + abs(ins[1]) < 10_000 or e.dxf.get("rotation", 0) or \
                any(abs(e.dxf.get(k, 1) - 1) > 1e-9 for k in ("xscale", "yscale")):
            return None
        blk = e.doc.blocks.get(e.dxf.name)
        from ezdxf.math import Vec2
        if blk is None or Vec2(blk.base_point).magnitude > 1e-6:
            return None
        cache = e.doc.__dict__.setdefault("_gsb_block_box", {})
        if e.dxf.name not in cache:
            xs, ys = [], []
            for k, s in enumerate(blk):
                if k > 3000:
                    break
                for a in ("start", "insert", "center", "location"):
                    if s.dxf.hasattr(a):
                        v = s.dxf.get(a); xs.append(v[0]); ys.append(v[1]); break
                else:
                    if s.dxftype() == "LWPOLYLINE":
                        for v in list(s.get_points("xy"))[:4]:
                            xs.append(v[0]); ys.append(v[1])
            if len(xs) < 20:
                cache[e.dxf.name] = None
            else:
                xs.sort(); ys.sort(); n = len(xs)
                cache[e.dxf.name] = (xs[n // 50], ys[n // 50], xs[-1 - n // 50], ys[-1 - n // 50])
        box = cache[e.dxf.name]
        if not box:
            return None
        pad = max(max(box[2] - box[0], box[3] - box[1]) * 5, 5000.0)   # same neighbourhood, far from the origin
        if box[0] - pad <= ins[0] <= box[2] + pad and box[1] - pad <= ins[1] <= box[3] + pad:
            return (-ins[0], -ins[1], -ins[2])
    except Exception:  # noqa: BLE001
        return None
    return None


def _explode_container(e, fd: float, budget: List[int], depth: int = 0) -> List[CadEntity]:
    try:
        subs = list(e.virtual_entities())
    except Exception:  # noqa: BLE001
        return []
    shift = _lost_base_point(e)
    if shift:
        moved = []
        for sub in subs:
            try:
                sub.translate(*shift); moved.append(sub)
            except Exception:  # noqa: BLE001
                continue
        subs = moved
        try:
            e.doc.__dict__.setdefault("_gsb_base_fixed", set()).add(str(e.dxf.name))
        except Exception:  # noqa: BLE001
            pass
    out: List[CadEntity] = []
    layer = str(getattr(e.dxf, "layer", "0") or "0")
    parent = str(getattr(e.dxf, "handle", "") or "")
    for i, sub in enumerate(subs):
        if budget[0] <= 0:
            break
        try:
            t = sub.dxftype()
            if t == "INSERT" and depth < 4 and _is_container(sub):
                got = _explode_container(sub, fd, budget, depth + 1)
            elif t in _HANDLED:
                got = _convert_entity(sub, fd)
            elif t not in _ANNOTATION_ONLY:
                got = _explode_unhandled(sub, fd)
            else:
                got = []
            for ce in got:
                ce.handle = f"{parent}:{i}" + (f":{ce.handle}" if depth or ":" in ce.handle else "")
                if not ce.layer or ce.layer == "0":
                    ce.layer = layer
                out.append(ce)
            budget[0] -= max(1, len(got))
        except Exception:  # noqa: BLE001
            continue
    return out


def _explode_unhandled(e, fd: float) -> List[CadEntity]:
    """MLINE, Civil 3D / AEC custom objects (pipes, structures, feature lines, alignments …) and proxy entities:
    read through the geometry they draw (their proxy graphics) — the same lines the user sees in AutoCAD."""
    try:
        subs = list(e.virtual_entities())
    except Exception:  # noqa: BLE001 — no drawable geometry (e.g. ACIS solid without proxy graphics)
        return []
    out: List[CadEntity] = []
    layer = str(getattr(e.dxf, "layer", "0") or "0")
    parent = str(getattr(e.dxf, "handle", "") or "")
    for i, sub in enumerate(subs[:5000]):
        try:
            for ce in _convert_entity(sub, fd):
                ce.handle = f"{parent}:{i}" if parent else ce.handle
                if not ce.layer or ce.layer == "0":
                    ce.layer = layer
                ce.dxftype = e.dxftype()
                out.append(ce)
        except Exception:  # noqa: BLE001
            continue
    return out


def _base(e, kind: str, **kw) -> CadEntity:
    dxf = e.dxf
    color = dxf.color if dxf.hasattr("color") else None
    lw = dxf.lineweight if dxf.hasattr("lineweight") else None
    return CadEntity(handle=str(dxf.handle or ""), layer=str(dxf.layer or "0"), dxftype=e.dxftype(), kind=kind,
                     color=color if color not in (256, None) else None,
                     linetype=dxf.linetype if dxf.hasattr("linetype") else "",
                     lineweight=lw if lw is not None and lw >= 0 else None, **kw)


def _convert_entity(e, fd: float) -> List[CadEntity]:
    t = e.dxftype()
    if t == "LINE":
        s, en = _xyz(e.dxf.start), _xyz(e.dxf.end)
        return [_base(e, LINE, coords=[s, en], elevation=s[2] if s[2] == en[2] else None)]
    if t in ("LWPOLYLINE", "POLYLINE"):
        if t == "POLYLINE" and (e.is_poly_face_mesh or e.is_polygon_mesh):
            return [_base(e, POLYGON, coords=[_xyz(v.dxf.location) for v in e.vertices][:4], closed=True,
                          issues=["polyface/mesh reduced to first face outline"])]
        closed = bool(e.closed if t == "LWPOLYLINE" else e.is_closed)
        pts = _flatten(e, fd)
        elev = None
        if t == "LWPOLYLINE":
            elev = float(e.dxf.elevation) if e.dxf.hasattr("elevation") else None
        elif pts and all(abs(p[2] - pts[0][2]) < 1e-9 for p in pts):
            elev = pts[0][2]
        if closed and len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            return [_base(e, POLYGON, coords=pts, closed=True, elevation=elev)]
        return [_base(e, LINE, coords=pts, elevation=elev)]
    if t in ("ARC", "SPLINE", "ELLIPSE"):
        pts = _flatten(e, fd)
        closed = False
        if t == "ELLIPSE":
            closed = abs((e.dxf.end_param - e.dxf.start_param) - 2 * math.pi) < 1e-6
        if t == "SPLINE":
            closed = bool(e.closed)
        if closed and len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            return [_base(e, POLYGON, coords=pts, closed=True)]
        return [_base(e, LINE, coords=pts)]
    if t == "CIRCLE":
        c = _xyz(e.ocs().to_wcs(e.dxf.center))
        r = float(e.dxf.radius)
        ring = _flatten(e, fd)
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        return [_base(e, POLYGON, coords=ring, closed=True, center=c, radius=r, elevation=c[2])]
    if t == "POINT":
        p = _xyz(e.dxf.location)
        return [_base(e, POINT, coords=[p], center=p, elevation=p[2])]
    if t == "INSERT":
        p = _xyz(e.ocs().to_wcs(e.dxf.insert))
        return [_base(e, POINT, coords=[p], center=p, block_name=e.dxf.name, attributes=_attribs(e),
                      rotation=float(e.dxf.rotation or 0.0), elevation=p[2])]
    if t == "HATCH":
        return _hatch(e, fd)
    if t in ("SOLID", "TRACE", "3DFACE"):
        vs = [_xyz(e.dxf.get(f"vtx{i}")) for i in range(4) if e.dxf.hasattr(f"vtx{i}")]
        if t in ("SOLID", "TRACE") and len(vs) == 4:
            vs = [vs[0], vs[1], vs[3], vs[2]]  # SOLID vertex order is Z-shaped
        uniq = []
        for v in vs:
            if v not in uniq:
                uniq.append(v)
        if len(uniq) >= 3:
            return [_base(e, POLYGON, coords=uniq + [uniq[0]], closed=True)]
        return []
    if t == "TEXT":
        align = e.dxf.align_point if e.dxf.hasattr("align_point") and e.dxf.get("halign", 0) else e.dxf.insert
        p = _xyz(e.ocs().to_wcs(align))
        return [_base(e, TEXT, coords=[p], center=p, text=_clean_text(e.plain_text()),
                      text_height=float(e.dxf.height or 0), rotation=float(e.dxf.rotation or 0.0))]
    if t == "MTEXT":
        p = _xyz(e.dxf.insert)
        try:
            rot = float(e.get_rotation())
        except Exception:  # noqa: BLE001
            rot = float(e.dxf.get("rotation", 0.0) or 0.0)
        return [_base(e, TEXT, coords=[p], center=p, text=_clean_text(e.plain_text(split=False)),
                      text_height=float(e.dxf.get("char_height", 0) or 0), rotation=rot)]
    if t in ("MULTILEADER", "MLEADER"):
        ctx = e.context
        if ctx.mtext is None:
            return []
        p = _xyz(ctx.mtext.insert)
        from ezdxf.tools.text import plain_mtext
        txt = _clean_text(plain_mtext(ctx.mtext.default_content))
        head = None
        try:
            head = _xyz(ctx.leaders[0].lines[0].vertices[0])
        except Exception:  # noqa: BLE001
            pass
        return [_base(e, TEXT, coords=[p], center=p, text=txt, text_height=float(ctx.char_height or 0),
                      leader_point=head)]
    return []


def _hatch(e, fd: float) -> List[CadEntity]:
    from ezdxf import path as ezpath
    rings = []
    for p in ezpath.from_hatch(e):
        pts = [_xyz(v) for v in p.flattening(distance=fd, segments=8)]
        if len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            rings.append(pts)
    if not rings:
        return []

    def area(r):
        return abs(sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1))) / 2

    rings.sort(key=area, reverse=True)
    return [_base(e, POLYGON, coords=rings[0], holes=rings[1:], closed=True, pattern=str(e.dxf.pattern_name or ""),
                  elevation=float(e.dxf.elevation[2]) if e.dxf.hasattr("elevation") else None)]


def _resolve_bylayer(cad: CadDocument):
    """Colour / linetype / lineweight as drawn (BYLAYER resolved from the layer table)."""
    for e in cad.entities + cad.paper:
        info = cad.layers.get(e.layer)
        if not info:
            continue
        if e.color is None:
            e.color = info.color
        if not e.linetype or e.linetype.upper() == "BYLAYER":
            e.linetype = info.linetype or "Continuous"
        if e.lineweight is None:
            e.lineweight = info.lineweight


DEFAULT_ANNOTATION_BLOCKS = [r"\bdata\b", r"\btag\b", r"label", r"annot", r"callout", r"legend", r"\binfo\b",
                             r"schedule", r"\bnote\b"]


def apply_annotation_blocks(cad: CadDocument, patterns: Optional[List[str]] = None) -> int:
    """INSERTs of annotation blocks (data tags, callouts) carry information *about* nearby features,
    they are not assets themselves: convert them into label entities (kind=text) keeping their
    attributes, so they never become features and their values remain available."""
    import re as _re
    env = os.environ.get("GSB_ANNOTATION_BLOCKS")
    pats = (patterns or []) + DEFAULT_ANNOTATION_BLOCKS + ([x for x in env.split(",") if x] if env else [])
    rx = [_re.compile(p, _re.I) for p in pats]
    n = 0
    for e in cad.entities:
        if e.dxftype == "INSERT" and e.attributes and any(r.search(e.block_name) for r in rx):
            vals = [f"{k}:{v}" for k, v in e.attributes.items() if v and v.strip() not in ("-", "")]
            e.kind = TEXT
            e.dxftype = "INSERT-TAG"
            e.text = " ".join(vals)
            n += 1
    return n


def unit_scale_to_meters(insunits: int) -> float:
    return UNIT_TO_METERS.get(insunits, 1.0)
