"""
Persisted, approved mapping rules — "Adaptive Mapping Intelligence" /
"Learning from Corrections" from the spec.

Rules are stored per schema, keyed by a *content fingerprint* rather than
the raw CAD layer name — so the engine still benefits from a prior
approval even when a future drawing names the layer differently (the
fingerprint captures geometry type + the dominant keyword/evidence class
that drove the original approval, not the literal string).

Rules are never auto-applied blindly: they add strong positive evidence
to the scoring in engine.py, but a rule is only promoted to AUTO
confidence, never silently substituted for classification. Publishing
(overwriting) an existing approved rule requires explicit authorization
(approve_mapping(..., authorized=True)) — matching "Do not automatically
change published mapping rules without authorization."
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ApprovedRule:
    fingerprint: str          # e.g. "line|keyword:water" or exact source layer name
    target_layer: str
    match_type: str           # "exact_layer_name" | "fingerprint"
    approved_by: str = "user"
    note: str = ""


@dataclass
class LearnedRuleStore:
    schema_id: str
    path: str
    rules: List[ApprovedRule] = field(default_factory=list)

    @classmethod
    def load(cls, mapping_rules_dir: str, schema_id: str) -> "LearnedRuleStore":
        path = os.path.join(mapping_rules_dir, f"{schema_id}.rules.json")
        rules: List[ApprovedRule] = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rules = [ApprovedRule(**r) for r in data.get("rules", [])]
        return cls(schema_id=schema_id, path=path, rules=rules)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"schema_id": self.schema_id,
                       "rules": [r.__dict__ for r in self.rules]}, f, indent=2)

    def find(self, fingerprint: str) -> Optional[ApprovedRule]:
        for r in self.rules:
            if r.fingerprint == fingerprint:
                return r
        return None

    def approve_mapping(self, fingerprint: str, target_layer: str,
                         match_type: str = "exact_layer_name",
                         approved_by: str = "user", note: str = "",
                         authorized: bool = False):
        existing = self.find(fingerprint)
        if existing is not None:
            if not authorized:
                raise PermissionError(
                    f"A published mapping rule already exists for '{fingerprint}' "
                    f"-> '{existing.target_layer}'. Overwriting requires authorized=True."
                )
            existing.target_layer = target_layer
            existing.approved_by = approved_by
            existing.note = note
        else:
            self.rules.append(ApprovedRule(
                fingerprint=fingerprint, target_layer=target_layer,
                match_type=match_type, approved_by=approved_by, note=note,
            ))
        self.save()
