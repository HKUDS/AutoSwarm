"""Persistent company employee registry helpers.

Employees are runtime/company assets, while organization configs describe the
role graph and policies.  This module keeps employee records outside org yaml
and normalizes template-backed employees to a canonical template id.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from opc.core.config import EmployeeConfig, validate_organization_id

EMPLOYEE_REGISTRY_SCHEMA_VERSION = 1
EMPLOYEE_REGISTRY_KIND = "company_employee"

_COUNT_KEYS = {
    "successes",
    "partial_successes",
    "failures",
    "reflection_count",
}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return slug or "employee"


def employee_registry_dir(opc_home: Path, organization_id: Any) -> Path:
    org_id = validate_organization_id(organization_id)
    return Path(opc_home) / "company_state" / org_id / "employees"


def employee_registry_path(opc_home: Path, organization_id: Any, employee_id: str) -> Path:
    filename = f"{_slugify(employee_id)}.yaml"
    return employee_registry_dir(opc_home, organization_id) / filename


def is_placeholder_employee(employee: EmployeeConfig | dict[str, Any]) -> bool:
    metadata = dict(employee.metadata if isinstance(employee, EmployeeConfig) else employee.get("metadata") or {})
    return bool(metadata.get("is_default_employee") or metadata.get("is_fallback_employee"))


def _employee_from_payload(raw: Any) -> EmployeeConfig | None:
    if isinstance(raw, EmployeeConfig):
        return raw.model_copy(deep=True)
    if not isinstance(raw, dict):
        return None
    payload = raw.get("employee") if isinstance(raw.get("employee"), dict) else raw
    try:
        return EmployeeConfig.model_validate(payload)
    except Exception:
        return None


def _append_unique(items: list[Any], additions: list[Any]) -> list[Any]:
    result = list(items or [])
    for item in list(additions or []):
        if item not in result:
            result.append(item)
    return result


def _merge_metadata(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in dict(incoming or {}).items():
        if key == "legacy_employee_ids":
            merged[key] = _append_unique(list(merged.get(key, []) or []), list(value or []))
        elif key in {"home_role_ids", "staffed_role_ids"}:
            merged[key] = _append_unique(list(merged.get(key, []) or []), list(value or []))
        elif key not in merged or _is_empty_value(merged.get(key)):
            merged[key] = value
        elif isinstance(merged.get(key), dict) and isinstance(value, dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
    return merged


def _canonicalize_employee(employee: EmployeeConfig) -> tuple[EmployeeConfig, dict[str, str]]:
    if is_placeholder_employee(employee):
        return employee.model_copy(deep=True), {}

    old_id = str(employee.employee_id or "").strip()
    template_id = str(employee.template_id or "").strip()
    canonical_id = template_id or old_id
    metadata = dict(employee.metadata or {})
    aliases: dict[str, str] = {}

    legacy_ids = [str(item).strip() for item in list(metadata.get("legacy_employee_ids", []) or []) if str(item).strip()]
    if old_id and old_id != canonical_id and old_id not in legacy_ids:
        legacy_ids.append(old_id)
    if legacy_ids:
        metadata["legacy_employee_ids"] = legacy_ids
        for legacy_id in legacy_ids:
            aliases[legacy_id] = canonical_id

    role_id = str(employee.role_id or "").strip()
    if role_id:
        metadata.setdefault("home_role_id", role_id)
        metadata["home_role_ids"] = _append_unique(list(metadata.get("home_role_ids", []) or []), [role_id])
        metadata["staffed_role_ids"] = _append_unique(list(metadata.get("staffed_role_ids", []) or []), [role_id])
    if template_id:
        metadata.setdefault("canonical_employee_id", canonical_id)

    return employee.model_copy(update={"employee_id": canonical_id, "metadata": metadata}), aliases


def _merge_employee(base: EmployeeConfig, incoming: EmployeeConfig) -> EmployeeConfig:
    merged = base.model_dump()
    other = incoming.model_dump()
    for field in ("domains", "tags", "prompt_refs", "skill_refs"):
        merged[field] = _append_unique(list(merged.get(field, []) or []), list(other.get(field, []) or []))
    for field in ("name", "template_id", "description", "category", "preferred_external_agent", "seniority", "status"):
        if not merged.get(field) and other.get(field):
            merged[field] = other[field]
        elif field == "description" and len(str(other.get(field, ""))) > len(str(merged.get(field, ""))):
            merged[field] = other[field]
    if not merged.get("role_id") and other.get("role_id"):
        merged["role_id"] = other["role_id"]
    merged["metadata"] = _merge_metadata(dict(merged.get("metadata", {}) or {}), dict(other.get("metadata", {}) or {}))
    return EmployeeConfig.model_validate(merged)


def normalize_employee_records(employees: list[Any]) -> tuple[list[EmployeeConfig], dict[str, str]]:
    real_by_id: dict[str, EmployeeConfig] = {}
    placeholders: list[EmployeeConfig] = []
    aliases: dict[str, str] = {}
    for raw in list(employees or []):
        employee = _employee_from_payload(raw)
        if employee is None:
            continue
        if is_placeholder_employee(employee):
            placeholders.append(employee)
            continue
        canonical, employee_aliases = _canonicalize_employee(employee)
        aliases.update(employee_aliases)
        existing = real_by_id.get(canonical.employee_id)
        real_by_id[canonical.employee_id] = _merge_employee(existing, canonical) if existing else canonical
    real = sorted(real_by_id.values(), key=lambda item: (item.category, item.name.lower(), item.employee_id))
    return [*real, *placeholders], aliases


def load_employee_registry(opc_home: Path, organization_id: Any) -> list[EmployeeConfig]:
    directory = employee_registry_dir(opc_home, organization_id)
    if not directory.is_dir():
        return []
    employees: list[EmployeeConfig] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        employee = _employee_from_payload(data)
        if employee is not None:
            employees.append(employee)
    return employees


def load_company_employees(
    opc_home: Path,
    organization_id: Any,
    legacy_employees: list[Any],
) -> list[EmployeeConfig]:
    registry_employees = load_employee_registry(opc_home, organization_id)
    employees, aliases = normalize_employee_records([*registry_employees, *list(legacy_employees or [])])
    migrate_evolution_employee_ids(opc_home, aliases)
    return employees


def write_employee_registry(
    opc_home: Path,
    organization_id: Any,
    employees: list[Any],
) -> tuple[list[EmployeeConfig], dict[str, str]]:
    normalized, aliases = normalize_employee_records(employees)
    real_employees = [employee for employee in normalized if not is_placeholder_employee(employee)]
    directory = employee_registry_dir(opc_home, organization_id)
    directory.mkdir(parents=True, exist_ok=True)
    expected_paths: set[Path] = set()
    for employee in real_employees:
        path = employee_registry_path(opc_home, organization_id, employee.employee_id)
        expected_paths.add(path)
        payload = {
            "schema_version": EMPLOYEE_REGISTRY_SCHEMA_VERSION,
            "kind": EMPLOYEE_REGISTRY_KIND,
            "organization_id": validate_organization_id(organization_id),
            "employee": employee.model_dump(),
        }
        _atomic_write_text(
            path,
            yaml.dump(payload, default_flow_style=False, sort_keys=False, allow_unicode=True),
        )
    for path in directory.glob("*.yaml"):
        if path not in expected_paths:
            try:
                path.unlink()
            except OSError:
                pass
    migrate_evolution_employee_ids(opc_home, aliases)
    return normalized, aliases


def migrate_evolution_employee_ids(opc_home: Path, aliases: dict[str, str]) -> None:
    canonical_aliases = {
        str(old).strip(): str(new).strip()
        for old, new in dict(aliases or {}).items()
        if str(old).strip() and str(new).strip() and str(old).strip() != str(new).strip()
    }
    if not canonical_aliases:
        return
    paths: list[Path] = [Path(opc_home) / "evolution" / "employees.json"]
    projects_dir = Path(opc_home) / "projects"
    if projects_dir.is_dir():
        paths.extend(sorted(projects_dir.glob("*/employee_evolution.json")))
    for path in paths:
        if not path.exists():
            continue
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(profile, dict):
            continue
        employees = profile.get("employees")
        if not isinstance(employees, dict):
            continue
        changed = False
        for legacy_id, canonical_id in canonical_aliases.items():
            if legacy_id not in employees:
                continue
            legacy_record = employees.pop(legacy_id)
            current = employees.get(canonical_id)
            employees[canonical_id] = _merge_evolution_records(current, legacy_record)
            changed = True
        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _merge_evolution_records(base: Any, incoming: Any) -> Any:
    if isinstance(base, dict) and isinstance(incoming, dict):
        merged = dict(base)
        for key, value in incoming.items():
            if key in _COUNT_KEYS and isinstance(value, (int, float)):
                merged[key] = int(merged.get(key, 0) or 0) + int(value)
            elif isinstance(value, list):
                merged[key] = _append_unique(list(merged.get(key, []) or []), value)
            elif isinstance(value, dict):
                merged[key] = _merge_evolution_records(merged.get(key, {}), value)
            elif key not in merged or _is_empty_value(merged.get(key)):
                merged[key] = value
        return merged
    if isinstance(base, list) and isinstance(incoming, list):
        return _append_unique(base, incoming)
    return base if not _is_empty_value(base) else incoming


def _is_empty_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
