"""k-nearest neighbours within a radius — numpy only (no scipy dependency), bucketed on a grid of cell = radius."""
from __future__ import annotations

from collections import defaultdict
from typing import Tuple

import numpy as np


def knn(C: np.ndarray, P: np.ndarray, k: int, radius: float) -> Tuple[np.ndarray, np.ndarray]:
    """-> (dist, idx) of shape (len(P), k); missing neighbours have dist = inf and idx = len(C)."""
    n = len(P)
    D = np.full((n, k), np.inf)
    I = np.full((n, k), len(C), dtype=np.int64)
    if not len(C) or not n:
        return D, I
    cell = max(float(radius), 1e-9)
    cc = np.floor(C[:, :2] / cell).astype(np.int64)
    grid = defaultdict(list)
    for i, (a, b) in enumerate(map(tuple, cc)):
        grid[(a, b)].append(i)
    grid = {key: np.array(v, dtype=np.int64) for key, v in grid.items()}
    pc = np.floor(P[:, :2] / cell).astype(np.int64)
    groups = defaultdict(list)
    for i, (a, b) in enumerate(map(tuple, pc)):
        groups[(a, b)].append(i)
    for (a, b), rows in groups.items():
        cand = [grid[(a + da, b + db)] for da in (-1, 0, 1) for db in (-1, 0, 1) if (a + da, b + db) in grid]
        if not cand:
            continue
        cand = np.concatenate(cand)
        rows = np.array(rows, dtype=np.int64)
        d = np.hypot(P[rows, 0][:, None] - C[cand, 0][None, :], P[rows, 1][:, None] - C[cand, 1][None, :])
        kk = min(k, len(cand))
        part = np.argpartition(d, kk - 1, axis=1)[:, :kk]
        dd = np.take_along_axis(d, part, axis=1)
        order = np.argsort(dd, axis=1)
        dd = np.take_along_axis(dd, order, axis=1)
        ii = cand[np.take_along_axis(part, order, axis=1)]
        far = dd > radius
        dd[far] = np.inf
        ii[far] = len(C)
        D[rows, :kk] = dd
        I[rows, :kk] = ii
    return D, I
