"""Saved, reusable mapping configurations (per schema) + learned rule recording."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from ..classification.learned_rules import LearnedRuleStore
from .automap import fingerprint, key_fingerprint


def _safe(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_\- ]", "", name).strip().replace(" ", "_")
    if not s:
        raise ValueError("profile name must contain letters or digits")
    return s[:64]


class ProfileStore:
    def __init__(self, root: str):
        self.root = root

    def _dir(self, schema_id: str) -> str:
        d = os.path.join(self.root, "profiles", _safe(schema_id))
        os.makedirs(d, exist_ok=True)
        return d

    def list(self, schema_id: str) -> List[Dict[str, Any]]:
        d = self._dir(schema_id)
        out = []
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        p = json.load(fh)
                    out.append({"name": p.get("name", f[:-5]), "saved_at": p.get("saved_at"),
                                "saved_by": p.get("saved_by"), "layer_count": len(p.get("layers", {}))})
                except (OSError, ValueError):
                    continue
        return out

    def load(self, schema_id: str, name: str) -> Dict[str, Any]:
        path = os.path.join(self._dir(schema_id), _safe(name) + ".json")
        if not os.path.isfile(path):
            raise KeyError(f"profile '{name}' not found")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, schema_id: str, name: str, mapping: Dict[str, Any], user: str,
             update_rules: bool = True, authorized: bool = False) -> Dict[str, Any]:
        layers = {k: {"target": v.get("target"), "role": v.get("role", "feature"), "subtype": v.get("subtype"),
                      "convert": v.get("convert"), "keys": [x.get("description") for x in v.get("keys") or []]}
                  for k, v in mapping.get("layers", {}).items()}
        profile = {"name": name, "schema_id": schema_id, "saved_at": datetime.now(timezone.utc).isoformat(),
                   "saved_by": user, "layers": layers, "fields": mapping.get("fields", {}),
                   "label_layers": mapping.get("label_layers"), "options": mapping.get("options", {})}
        path = os.path.join(self._dir(schema_id), _safe(name) + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        conflicts: List[str] = []
        if update_rules:
            store = LearnedRuleStore.load(self.root, schema_id)
            for key, v in layers.items():
                if not v["target"] or v["role"] != "feature":
                    continue
                from ..cad.styles import split_group
                layer, kind, style = split_group(key)
                kind = kind + (f"@{style}" if style else "")
                fps = [(fingerprint(layer, kind), f"{layer} ({kind})")] + \
                      [(key_fingerprint(d), f"key '{d}'") for d in v.get("keys") or [] if d]
                for fp, label in fps:
                    try:
                        existing = store.find(fp)
                        if existing and existing.target_layer == v["target"]:
                            continue
                        store.approve_mapping(fp, v["target"], match_type="fingerprint", approved_by=user,
                                              note=f"profile {name}", authorized=authorized)
                    except PermissionError:
                        conflicts.append(f"{label} -> {v['target']}")
        return {"profile": profile["name"], "rule_conflicts": conflicts}

    @staticmethod
    def apply(profile: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Any]:
        """Overlay a saved profile on an auto-generated mapping (exact group-key matches)."""
        for key, v in profile.get("layers", {}).items():
            if key in mapping["layers"]:
                m = mapping["layers"][key]
                m.update({k: v[k] for k in ("target", "role", "subtype", "convert") if v.get(k) is not None or k == "target"})
                m["status"] = "PROFILE"
                m["confidence"] = "HIGH" if v.get("target") else m.get("confidence")
        for fc, fields in profile.get("fields", {}).items():
            mapping.setdefault("fields", {}).setdefault(fc, {}).update(fields)
        if profile.get("label_layers") is not None:
            mapping["label_layers"] = profile["label_layers"]
        mapping.setdefault("options", {}).update(profile.get("options", {}))
        return mapping
