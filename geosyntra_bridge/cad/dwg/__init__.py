"""GeoSyntra Bridge's own DWG reader (AutoCAD 2010 – 2018+ drawings, AC1024 / AC1027 / AC1032)."""
from .container import DwgError, DwgFile
from .to_dxf import dwg_to_dxf, read_dwg_doc

__all__ = ["DwgError", "DwgFile", "dwg_to_dxf", "read_dwg_doc"]
