"""
GeoSyntra Bridge — Intelligent CAD Understanding Engine.

Core principle: Understand the CAD first. Normalize the data.
Repair the geometry. Then build the correct Geodatabase.

This package does NOT rely on predefined/expected CAD layer names.
It classifies CAD content from multiple signals (geometry, entity
properties, block attributes, TEXT/MTEXT content, naming patterns,
spatial relationships, and previously-approved mapping rules) and
converts it into a selected target GDB schema.
"""

__version__ = "2.0.0"
