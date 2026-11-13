"""
Thin ctypes binding to the GDAL C API.

Why ctypes instead of `osgeo` (GDAL's own Python bindings)?
`osgeo` has no pip wheels on Windows/Linux and normally needs conda,
OSGeo4W or ArcGIS Pro's environment. `pyogrio` *does* ship pip wheels that
bundle a full libgdal (>= 3.6, with the OpenFileGDB *write* driver). pyogrio's
Python API cannot create feature datasets, field domains or relationship
classes, but the bundled libgdal can — so we call it directly.

Resolution order for the library:
  1. env GSB_GDAL_LIBRARY (explicit path to libgdal / gdal*.dll)
  2. the libgdal bundled inside the installed `pyogrio` wheel
  3. system libgdal on the loader path
"""
from __future__ import annotations

import ctypes
import ctypes.util
import glob
import os
import sys
import threading
from typing import Dict, List, Optional, Sequence, Tuple

P = ctypes.c_void_p
S = ctypes.c_char_p
I = ctypes.c_int
D = ctypes.c_double
B = ctypes.c_bool
PS = ctypes.POINTER(ctypes.c_char_p)

# OGRFieldType
OFTInteger, OFTReal, OFTString, OFTDate, OFTDateTime, OFTInteger64 = 0, 2, 4, 9, 11, 12
# OGRFieldSubType
OFSTNone, OFSTBoolean, OFSTInt16, OFSTFloat32, OFSTJSON, OFSTUUID = 0, 1, 2, 3, 4, 5
# OGRwkbGeometryType
wkbPoint, wkbLineString, wkbPolygon, wkbMultiPoint, wkbMultiLineString, wkbMultiPolygon = 1, 2, 3, 4, 5, 6
# OGRFieldDomainType / split / merge
OFDT_CODED, OFDT_RANGE = 0, 1
OFDSP_DEFAULT_VALUE, OFDSP_DUPLICATE, OFDSP_GEOMETRY_RATIO = 0, 1, 2
OFDMP_DEFAULT_VALUE, OFDMP_SUM, OFDMP_GEOMETRY_WEIGHTED = 0, 1, 2
# GDALRelationshipCardinality / Type
GRC_ONE_TO_ONE, GRC_ONE_TO_MANY, GRC_MANY_TO_ONE, GRC_MANY_TO_MANY = 0, 1, 2, 3
GRT_COMPOSITE, GRT_ASSOCIATION, GRT_AGGREGATION = 0, 1, 2

GDAL_OF_VECTOR = 0x04
GDAL_OF_UPDATE = 0x01
GDAL_OF_READONLY = 0x00


class OGRCodedValue(ctypes.Structure):
    _fields_ = [("pszCode", S), ("pszValue", S)]


class OGRField_Integer(ctypes.Structure):  # OGRField union, we only need the leading 8 bytes
    _fields_ = [("Integer", ctypes.c_int), ("pad", ctypes.c_int), ("pad2", ctypes.c_int64)]


class OGRFieldUnion(ctypes.Union):
    _fields_ = [("Integer", ctypes.c_int), ("Integer64", ctypes.c_int64), ("Real", ctypes.c_double),
                ("raw", ctypes.c_byte * 16)]


class GdalError(RuntimeError):
    pass


def _find_library() -> str:
    explicit = os.environ.get("GSB_GDAL_LIBRARY")
    if explicit and os.path.isfile(explicit):
        return explicit
    try:
        import pyogrio  # noqa: F401
        base = os.path.dirname(os.path.dirname(pyogrio.__file__))
        pats = ["pyogrio.libs/libgdal*", "pyogrio.libs/gdal*.dll", "pyogrio/.dylibs/libgdal*",
                "pyogrio.libs/*gdal*", "pyogrio/*gdal*.dll"]
        for pat in pats:
            hits = sorted(glob.glob(os.path.join(base, pat)))
            if hits:
                return hits[0]
    except ImportError:
        pass
    found = ctypes.util.find_library("gdal")
    if found:
        return found
    raise GdalError(
        "GDAL library not found. Install the Python package 'pyogrio' (it bundles GDAL), "
        "or set GSB_GDAL_LIBRARY to the path of libgdal/gdal.dll."
    )


class _Gdal:
    """Lazily-initialised singleton holding typed C function pointers."""

    _lock = threading.Lock()

    def __init__(self):
        path = _find_library()
        if sys.platform == "win32":
            # bundled DLL dependencies sit next to gdal.dll
            try:
                os.add_dll_directory(os.path.dirname(path))
            except (AttributeError, OSError):
                pass
        self.path = path
        self.lib = ctypes.CDLL(path)
        self._bind()
        self.lib.GDALAllRegister()
        # Errors go to CPLGetLastErrorMsg instead of stderr
        self.CPLSetConfigOption(b"CPL_LOG", b"OFF" if sys.platform != "win32" else b"NUL")
        self.CPLSetConfigOption(b"OGR_ORGANIZE_POLYGONS", b"SKIP")

    def _f(self, name, res, *args, optional=False):
        try:
            fn = getattr(self.lib, name)
        except AttributeError:
            if optional:
                setattr(self, name, None)
                return
            raise
        fn.restype = res
        fn.argtypes = list(args)
        setattr(self, name, fn)

    def _bind(self):
        f = self._f
        f("GDALAllRegister", None)
        f("GDALVersionInfo", S, S)
        f("CPLSetConfigOption", None, S, S)
        f("CPLGetLastErrorMsg", S)
        f("CPLErrorReset", None)
        f("GDALGetDriverByName", P, S)
        f("GDALCreate", P, P, S, I, I, I, I, P)
        f("GDALOpenEx", P, S, ctypes.c_uint, P, P, P)
        f("GDALClose", I, P)
        f("GDALDatasetGetLayerCount", I, P)
        f("GDALDatasetGetLayer", P, P, I)
        f("GDALDatasetGetLayerByName", P, P, S)
        f("GDALDatasetCreateLayer", P, P, S, P, I, PS)
        f("GDALDatasetAddFieldDomain", B, P, P, PS)
        f("GDALDatasetGetFieldDomainNames", PS, P, PS)
        f("GDALDatasetGetFieldDomain", P, P, S)
        f("GDALDatasetStartTransaction", I, P, I)
        f("GDALDatasetCommitTransaction", I, P)
        f("CSLDestroy", None, PS)
        # relationships (GDAL >= 3.6)
        f("GDALRelationshipCreate", P, S, S, S, I, optional=True)
        f("GDALDestroyRelationship", None, P, optional=True)
        f("GDALRelationshipSetLeftTableFields", None, P, PS, optional=True)
        f("GDALRelationshipSetRightTableFields", None, P, PS, optional=True)
        f("GDALRelationshipSetType", None, P, I, optional=True)
        f("GDALRelationshipSetForwardPathLabel", None, P, S, optional=True)
        f("GDALRelationshipSetBackwardPathLabel", None, P, S, optional=True)
        f("GDALRelationshipSetRelatedTableType", None, P, S, optional=True)
        f("GDALDatasetAddRelationship", B, P, P, PS, optional=True)
        f("GDALDatasetGetRelationshipNames", PS, P, PS, optional=True)
        # SRS
        f("OSRNewSpatialReference", P, S)
        f("OSRImportFromEPSG", I, P, I)
        f("OSRSetAxisMappingStrategy", None, P, I)
        f("OSRRelease", None, P)
        f("OSRGetAuthorityCode", S, P, S)
        f("OSRGetName", S, P)
        f("OSRAutoIdentifyEPSG", I, P)
        # domains
        f("OGR_CodedFldDomain_Create", P, S, S, I, I, ctypes.POINTER(OGRCodedValue))
        f("OGR_RangeFldDomain_Create", P, S, S, I, I, ctypes.POINTER(OGRFieldUnion), B,
          ctypes.POINTER(OGRFieldUnion), B)
        f("OGR_FldDomain_Destroy", None, P)
        f("OGR_FldDomain_SetSplitPolicy", None, P, I)
        f("OGR_FldDomain_SetMergePolicy", None, P, I)
        f("OGR_FldDomain_GetName", S, P)
        f("OGR_FldDomain_GetDomainType", I, P)
        f("OGR_FldDomain_GetDescription", S, P)
        f("OGR_FldDomain_GetFieldType", I, P)
        f("OGR_CodedFldDomain_GetEnumeration", ctypes.POINTER(OGRCodedValue), P)
        # field defn
        f("OGR_Fld_Create", P, S, I)
        f("OGR_Fld_Destroy", None, P)
        f("OGR_Fld_SetWidth", None, P, I)
        f("OGR_Fld_SetPrecision", None, P, I)
        f("OGR_Fld_SetSubType", None, P, I)
        f("OGR_Fld_SetNullable", None, P, I)
        f("OGR_Fld_SetDefault", None, P, S)
        f("OGR_Fld_SetDomainName", None, P, S)
        f("OGR_Fld_SetAlternativeName", None, P, S)
        f("OGR_Fld_GetNameRef", S, P)
        f("OGR_Fld_GetType", I, P)
        f("OGR_Fld_GetSubType", I, P)
        f("OGR_Fld_GetWidth", I, P)
        f("OGR_Fld_IsNullable", I, P)
        f("OGR_Fld_GetDomainName", S, P)
        f("OGR_Fld_GetAlternativeNameRef", S, P)
        f("OGR_Fld_GetDefault", S, P)
        # layer
        f("OGR_L_CreateField", I, P, P, I)
        f("OGR_L_GetLayerDefn", P, P)
        f("OGR_L_GetName", S, P)
        f("OGR_L_GetGeomType", I, P)
        f("OGR_L_GetFeatureCount", ctypes.c_int64, P, I)
        f("OGR_L_CreateFeature", I, P, P)
        f("OGR_L_GetSpatialRef", P, P)
        f("OGR_L_ResetReading", None, P)
        f("OGR_L_GetNextFeature", P, P)
        f("OGR_L_GetMetadataItem", S, P, S, S, optional=True)
        f("GDALGetMetadataItem", S, P, S, S)
        f("OGR_FD_GetFieldCount", I, P)
        f("OGR_FD_GetFieldDefn", P, P, I)
        f("OGR_FD_GetFieldIndex", I, P, S)
        # feature
        f("OGR_F_Create", P, P)
        f("OGR_F_Destroy", None, P)
        f("OGR_F_SetFieldString", None, P, I, S)
        f("OGR_F_SetFieldDouble", None, P, I, D)
        f("OGR_F_SetFieldInteger", None, P, I, I)
        f("OGR_F_SetFieldInteger64", None, P, I, ctypes.c_int64)
        f("OGR_F_SetFieldDateTime", None, P, I, I, I, I, I, I, I, I)
        f("OGR_F_SetFieldNull", None, P, I)
        f("OGR_F_SetGeometryDirectly", I, P, P)
        f("OGR_F_GetFID", ctypes.c_int64, P)
        # single-row attribute update (map editor)
        f("OGR_L_GetFeature", P, P, ctypes.c_int64, optional=True)
        f("OGR_L_SetFeature", I, P, P, optional=True)
        f("OGR_F_GetFieldAsString", S, P, I, optional=True)
        f("OGR_F_IsFieldSetAndNotNull", I, P, I, optional=True)
        f("OGR_F_GetGeometryRef", P, P, optional=True)
        f("OGR_G_ExportToIsoWkt", I, P, ctypes.POINTER(ctypes.c_char_p), optional=True)
        f("VSIFree", None, P, optional=True)
        f("OGR_G_CreateFromWkb", I, ctypes.c_char_p, P, ctypes.POINTER(P), I)
        f("OGR_G_DestroyGeometry", None, P)
        # raster (DEM elevation)
        f("GDALGetRasterXSize", I, P)
        f("GDALGetRasterYSize", I, P)
        f("GDALGetRasterCount", I, P)
        f("GDALGetGeoTransform", I, P, ctypes.POINTER(ctypes.c_double))
        f("GDALSetGeoTransform", I, P, ctypes.POINTER(ctypes.c_double))
        f("GDALGetProjectionRef", S, P)
        f("GDALSetProjection", I, P, S)
        f("GDALGetRasterBand", P, P, I)
        f("GDALGetRasterNoDataValue", D, P, ctypes.POINTER(I))
        f("GDALSetRasterNoDataValue", I, P, D)
        f("GDALGetRasterDataType", I, P)
        f("GDALRasterIO", I, P, I, I, I, I, I, ctypes.c_void_p, I, I, I, I, I)

    # -- helpers -----------------------------------------------------------
    def last_error(self) -> str:
        msg = self.CPLGetLastErrorMsg()
        return msg.decode("utf-8", "replace") if msg else ""

    def version(self) -> str:
        return self.GDALVersionInfo(b"RELEASE_NAME").decode()

    def has_driver(self, name: str) -> bool:
        return bool(self.GDALGetDriverByName(name.encode()))

    def driver_can_create(self, name: str) -> bool:
        drv = self.GDALGetDriverByName(name.encode())
        if not drv:
            return False
        v = self.GDALGetMetadataItem(drv, b"DCAP_CREATE", None)
        return bool(v) and v.upper() == b"YES"

    @staticmethod
    def cstrlist(items: Sequence[str]):
        arr = (S * (len(items) + 1))()
        for i, it in enumerate(items):
            arr[i] = it.encode("utf-8")
        arr[len(items)] = None
        return arr

    def read_cstrlist(self, ptr) -> List[str]:
        out: List[str] = []
        if not ptr:
            return out
        i = 0
        while ptr[i]:
            out.append(ptr[i].decode("utf-8", "replace"))
            i += 1
        return out


_instance: Optional[_Gdal] = None
_instance_error: Optional[str] = None


def gdal() -> _Gdal:
    global _instance, _instance_error
    if _instance is None:
        with _Gdal._lock:
            if _instance is None:
                try:
                    _instance = _Gdal()
                except Exception as e:  # noqa: BLE001
                    _instance_error = str(e)
                    raise GdalError(str(e)) from e
    return _instance


def gdal_status() -> Dict[str, object]:
    """Capability summary used by /api/system/capabilities and verification."""
    try:
        g = gdal()
    except GdalError as e:
        return {"available": False, "error": str(e)}
    return {
        "available": True,
        "version": g.version(),
        "library": g.path,
        "filegdb_write": g.driver_can_create("OpenFileGDB"),
        "relationships": g.GDALDatasetAddRelationship is not None,
    }
