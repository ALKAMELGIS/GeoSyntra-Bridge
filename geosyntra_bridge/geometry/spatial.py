"""Tiny uniform-grid spatial index (no external dependency, fast enough for 1e6 items)."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, Generic, Iterable, List, Tuple, TypeVar

T = TypeVar("T")


class GridIndex(Generic[T]):
    def __init__(self, cell: float):
        self.cell = max(float(cell), 1e-9)
        self._g: Dict[Tuple[int, int], List[Tuple[Tuple[float, float, float, float], T]]] = defaultdict(list)

    def _rng(self, a: float, b: float) -> range:
        return range(int(math.floor(a / self.cell)), int(math.floor(b / self.cell)) + 1)

    def insert(self, bbox: Tuple[float, float, float, float], item: T):
        minx, miny, maxx, maxy = bbox
        xs, ys = self._rng(minx, maxx), self._rng(miny, maxy)
        if len(xs) * len(ys) > 4096:  # huge object: register in its corner cells only
            xs, ys = [xs[0], xs[-1]], [ys[0], ys[-1]]
        for i in xs:
            for j in ys:
                self._g[(i, j)].append((bbox, item))

    def query(self, x: float, y: float, r: float) -> Iterable[T]:
        seen = set()
        for i in self._rng(x - r, x + r):
            for j in self._rng(y - r, y + r):
                for bbox, item in self._g.get((i, j), ()):
                    k = id(item)
                    if k in seen:
                        continue
                    if bbox[0] - r <= x <= bbox[2] + r and bbox[1] - r <= y <= bbox[3] + r:
                        seen.add(k)
                        yield item
