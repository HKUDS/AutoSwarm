from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
QUANTUM_HARBOR_ORG_CONFIG = REPO_ROOT / ".opc" / "config" / "company_orgs" / "org_quantum_harbor_config.yaml"


def test_quantum_harbor_org_config_is_importable() -> None:
    from opc.core.config import OPCConfig
    from opc.core.org_config import apply_org_config_payload_to_config, validate_org_config_payload

    raw = yaml.safe_load(QUANTUM_HARBOR_ORG_CONFIG.read_text(encoding="utf-8"))
    payload = validate_org_config_payload(QUANTUM_HARBOR_ORG_CONFIG, raw)
    config = apply_org_config_payload_to_config(OPCConfig(), payload, source_path=QUANTUM_HARBOR_ORG_CONFIG)

    assert config.org.organization_id == "quantum_harbor"
    assert config.org.organization_name == "Quantum Harbor Research Studio"
    assert config.org.company_profile == "custom"
    assert config.org.final_decider_role_id == "founder_ceo"
    assert [role.id for role in config.org.roles] == [
        "founder_ceo",
        "product_chief",
        "quant_chief",
        "engineering_chief",
        "risk_chief",
        "market_researcher",
        "portfolio_analyst",
        "platform_engineer",
        "compliance_analyst",
    ]
    assert config.org.employees == []


def test_quantum_harbor_has_no_tracked_company_style_copy() -> None:
    canonical = yaml.safe_load(QUANTUM_HARBOR_ORG_CONFIG.read_text(encoding="utf-8"))
    legacy = REPO_ROOT / ".opc" / "config" / "company_orgs" / "company_quantum_harbor_config.yaml"

    assert canonical["organization_id"] == "quantum_harbor"
    assert not legacy.exists()
