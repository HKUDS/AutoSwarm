"""Read canonical tool results and legacy double-part session rows.

Normalization is read-only and local to one message. Execution receipts and
permission state live in separate Store records and are not affected.
"""

from __future__ import annotations

import json
from typing import Any


def normalized_session_parts(parts: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    entries = [(part.part_type, dict(part.payload or {})) for part in parts]
    canonical_indexes = {index for index, (kind, _) in enumerate(entries) if kind == "tool_result"}
    consumed: set[int] = set()
    for index, (kind, payload) in enumerate(entries):
        if kind != "tool_output":
            continue
        output = payload.get("output", payload.get("text", payload.get("result", "")))
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except (ValueError, TypeError):
                pass
        for target, (target_kind, canonical) in enumerate(entries):
            if target not in canonical_indexes or target in consumed:
                continue
            old_id = str(payload.get("tool_call_id", "") or "")
            new_id = str(canonical.get("tool_call_id", "") or "")
            if old_id and new_id and old_id != new_id:
                continue
            old_name = payload.get("tool_name")
            if old_name and canonical.get("tool_name") and old_name != canonical["tool_name"]:
                continue
            body = output.get("result", output) if isinstance(output, dict) else output
            canonical_output = canonical.get("result", "")
            if not (old_id and old_id == new_id) and canonical_output != output and canonical_output != body:
                continue
            # The old tool_output carried the envelope that tool_result lost.
            # Retain success/error/approval fields without duplicating its body.
            if canonical.get("result_format") != "envelope_v1":
                entries[target] = (target_kind, {**canonical, "result": output, "result_format": "envelope_v1"})
            consumed.update((index, target))
            break
        else:
            entries[index] = ("tool_result", {**payload, "result": output, "result_format": "envelope_v1"})
    return [entry for index, entry in enumerate(entries) if not (index in consumed and entry[0] == "tool_output")]
