"""Storage for reusable org architectures and session runtime snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from opc.core.config import (
    COMPANY_ORG_KIND,
    COMPANY_ORG_SCHEMA_VERSION,
    DEFAULT_ORGANIZATION_ID,
    OPCConfig,
    _atomic_write_text,
    _company_org_payload_to_org_mapping,
    _read_yaml_file,
    build_company_org_payload_from_config,
    slugify_organization_name,
    validate_organization_id,
)

ORG_INDEX_FILENAME = "org_index.yaml"
ORG_CONFIGS_DIRNAME = "company_orgs"
RUNTIME_ORG_CONFIGS_DIRNAME = "runtime_orgs"
ORG_CONFIG_KIND = COMPANY_ORG_KIND
ORG_CONFIG_SCHEMA_VERSION = COMPANY_ORG_SCHEMA_VERSION
RESERVED_ORG_CONFIG_IDS = frozenset({DEFAULT_ORGANIZATION_ID})


class RunnableOrgConfigError(ValueError):
    """Raised when a saved custom org cannot be safely activated or run."""


def org_index_path(config_dir: Path) -> Path:
    return Path(config_dir) / ORG_INDEX_FILENAME


def org_configs_dir(config_dir: Path) -> Path:
    return Path(config_dir) / ORG_CONFIGS_DIRNAME


def is_reserved_org_config_id(value: Any) -> bool:
    try:
        org_id = validate_organization_id(value)
    except ValueError:
        return False
    return org_id in RESERVED_ORG_CONFIG_IDS


def validate_saved_org_id(value: Any) -> str:
    org_id = validate_organization_id(value)
    if org_id in RESERVED_ORG_CONFIG_IDS:
        raise ValueError(f"Reserved organization_id for built-in company profile: {org_id!r}")
    return org_id


def org_config_filename(organization_id: Any) -> str:
    return f"org_{validate_saved_org_id(organization_id)}_config.yaml"


def organization_id_from_org_config_filename(path: Path) -> str | None:
    name = Path(path).name
    prefix = "org_"
    suffix = "_config.yaml"
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    candidate = name[len(prefix):-len(suffix)]
    try:
        return validate_saved_org_id(candidate)
    except ValueError:
        return None


def org_config_path(config_dir: Path, organization_id: Any) -> Path:
    return org_configs_dir(config_dir) / org_config_filename(organization_id)


def org_config_relative_path(organization_id: Any) -> str:
    return f"{ORG_CONFIGS_DIRNAME}/{org_config_filename(organization_id)}"


def runtime_org_config_path(config_dir: Path, organization_id: Any) -> Path:
    return Path(config_dir) / RUNTIME_ORG_CONFIGS_DIRNAME / org_config_filename(organization_id)


def is_session_scoped_org_payload(payload: dict[str, Any]) -> bool:
    """Distinguish runtime snapshots from user-saved, reusable architectures."""
    metadata = dict(payload.get("metadata", {}) or {})
    persistence = metadata.get("organization_persistence")
    if persistence in {"saved", "session"}:
        return persistence == "session"
    # Historical adaptive runs predate the explicit persistence marker.
    # Names/ID prefixes alone do not identify temporary organizations.
    return bool(metadata.get("adaptive_team_plan") and metadata.get("adaptive_source_project_id"))


def read_org_index(config_dir: Path) -> str | None:
    path = org_index_path(config_dir)
    if not path.exists():
        return None
    data = _read_yaml_file(path)
    active_id = data.get("active_organization_id")
    if not active_id:
        return None
    org_id = validate_organization_id(active_id)
    return None if org_id in RESERVED_ORG_CONFIG_IDS else org_id


def write_org_index(config_dir: Path, organization_id: Any) -> None:
    org_id = validate_saved_org_id(organization_id)
    _atomic_write_text(
        org_index_path(config_dir),
        yaml.dump(
            {
                "schema_version": 1,
                "active_organization_id": org_id,
            },
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ),
    )


def list_org_config_paths(config_dir: Path) -> list[Path]:
    org_dir = org_configs_dir(config_dir)
    if not org_dir.is_dir():
        return []
    return sorted(path for path in org_dir.glob("org_*_config.yaml") if organization_id_from_org_config_filename(path))


def allocate_org_config_id(config_dir: Path, organization_name: Any, *, preferred_id: Any = "") -> str:
    base = str(preferred_id or "").strip()
    try:
        candidate = validate_saved_org_id(base) if base else ""
    except ValueError:
        candidate = ""
    if not candidate:
        candidate = slugify_organization_name(organization_name)
    existing = {
        org_id
        for path in list_org_config_paths(config_dir)
        for org_id in [organization_id_from_org_config_filename(path)]
        if org_id
    }
    existing.update(
        org_id
        for path in (Path(config_dir) / RUNTIME_ORG_CONFIGS_DIRNAME).glob("org_*_config.yaml")
        for org_id in [organization_id_from_org_config_filename(path)]
        if org_id
    )
    existing.update(RESERVED_ORG_CONFIG_IDS)
    if candidate not in existing:
        return candidate
    suffix = 2
    while True:
        tail = f"_{suffix}"
        stem = candidate[: max(1, 64 - len(tail))].rstrip("_-") or "org"
        next_id = f"{stem}{tail}"
        if next_id not in existing:
            return next_id
        suffix += 1


def validate_org_config_payload(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    schema_version = int(data.get("schema_version", 1) or 1)
    if schema_version > ORG_CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"{path.name} schema_version {schema_version} is not supported by this version of OpenOPC"
        )
    kind = str(data.get("kind", "") or "").strip()
    if schema_version >= ORG_CONFIG_SCHEMA_VERSION and kind and kind != ORG_CONFIG_KIND:
        raise ValueError(f"Unsupported org architecture kind in {path.name}: {kind}")
    data["schema_version"] = schema_version
    return data


def build_org_config_payload_from_config(
    config: OPCConfig,
    *,
    organization_id: str | None = None,
    organization_name: str | None = None,
) -> dict[str, Any]:
    payload = build_company_org_payload_from_config(
        config,
        organization_id=organization_id,
        organization_name=organization_name,
        force_profile="custom",
    )
    org_id = validate_saved_org_id(payload.get("organization_id"))
    payload["metadata"] = {
        **dict(payload.get("metadata", {}) or {}),
        "source": "org_mode",
        "organization_config_file": org_config_relative_path(org_id),
    }
    return payload


def apply_org_config_payload_to_config(
    base_config: OPCConfig,
    data: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> OPCConfig:
    merged = base_config.model_dump()
    org_mapping = _company_org_payload_to_org_mapping(data, source_path=source_path)
    org_mapping["organization_id"] = validate_saved_org_id(org_mapping.get("organization_id"))
    org_mapping["company_profile"] = "custom"
    if org_mapping.get("organization_id"):
        directory = (
            RUNTIME_ORG_CONFIGS_DIRNAME
            if source_path is not None and Path(source_path).parent.name == RUNTIME_ORG_CONFIGS_DIRNAME
            else ORG_CONFIGS_DIRNAME
        )
        org_mapping["organization_config_file"] = f"{directory}/{org_config_filename(org_mapping['organization_id'])}"
    merged["org"] = org_mapping
    config = OPCConfig.model_validate(merged)
    config.org.talent_templates = []
    if source_path is not None:
        try:
            from opc.core.employee_registry import load_company_employees

            config_dir = Path(source_path).parent.parent
            config.org.employees = load_company_employees(
                config_dir.parent,
                config.org.organization_id,
                list(config.org.employees),
            )
        except Exception:
            pass
    return config


def validate_runnable_org_config(config: OPCConfig, *, organization_id: Any = "") -> None:
    """Reject custom orgs that would silently fall back to corporate builtin roles.

    Corporate can still fall back to builtin roles for legacy configs.
    Saved custom orgs must carry explicit roles before activation or execution.
    """
    org = config.org
    profile = str(getattr(org, "company_profile", "") or "").strip().lower()
    if profile != "custom":
        return

    org_id = validate_saved_org_id(organization_id or getattr(org, "organization_id", ""))
    roles = [
        role
        for role in list(getattr(org, "roles", []) or [])
        if str(getattr(role, "id", "") or "").strip()
    ]
    if roles:
        return

    raise RunnableOrgConfigError(
        f"Custom organization `{org_id}` has no roles. "
        "Refusing to activate or run it with corporate fallback roles."
    )


def write_org_config_payload(config_dir: Path, organization_id: Any, payload: dict[str, Any]) -> Path:
    org_id = validate_saved_org_id(organization_id)
    path = org_config_path(config_dir, org_id)
    return _write_org_config_payload(config_dir, org_id, payload, path, persistence="saved")


def write_runtime_org_config_payload(config_dir: Path, organization_id: Any, payload: dict[str, Any]) -> Path:
    """Persist a recoverable session snapshot without registering a saved org."""
    org_id = validate_saved_org_id(organization_id)
    path = runtime_org_config_path(config_dir, org_id)
    payload = dict(payload)
    previous_metadata = (
        dict(_read_yaml_file(path).get("metadata", {}) or {}) if path.exists() else {}
    )
    payload["metadata"] = {**previous_metadata, **dict(payload.get("metadata", {}) or {})}
    return _write_org_config_payload(config_dir, org_id, payload, path, persistence="session")


def _write_org_config_payload(
    config_dir: Path, org_id: str, payload: dict[str, Any], path: Path, *, persistence: str,
) -> Path:
    payload = dict(payload)
    raw_employees = list(payload.get("employees", []) or [])
    if raw_employees:
        from opc.core.employee_registry import load_employee_registry, write_employee_registry

        opc_home = Path(config_dir).parent
        existing = load_employee_registry(opc_home, org_id)
        write_employee_registry(opc_home, org_id, [*existing, *raw_employees])
    payload["employees"] = []
    payload["talent_templates"] = []
    payload["organization_id"] = org_id
    payload.setdefault("schema_version", ORG_CONFIG_SCHEMA_VERSION)
    payload.setdefault("kind", ORG_CONFIG_KIND)
    payload["metadata"] = {
        **dict(payload.get("metadata", {}) or {}),
        "organization_config_file": f"{path.parent.name}/{path.name}",
        "organization_persistence": persistence,
    }
    _atomic_write_text(
        path,
        yaml.dump(payload, default_flow_style=False, sort_keys=False, allow_unicode=True),
    )
    return path


def load_org_config_payload(config_dir: Path, organization_id: Any | None = None) -> tuple[dict[str, Any], Path]:
    config_dir = Path(config_dir)
    org_id = validate_saved_org_id(organization_id) if organization_id else read_org_index(config_dir)
    if not org_id:
        raise FileNotFoundError("No active org architecture is selected.")
    path = org_config_path(config_dir, org_id)
    if not path.exists():
        path = runtime_org_config_path(config_dir, org_id)
    if not path.exists():
        raise FileNotFoundError(f"Org architecture config does not exist: {path}")
    return validate_org_config_payload(path, _read_yaml_file(path)), path
