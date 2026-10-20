"""Refresh agency talent prompts without saving or changing any organization."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from opc.core.config import OPCConfig, get_opc_home
from opc.layer2_organization.talent_market import TalentMarket


def sync(repo: Path, opc_home: Path) -> dict:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError(f"Talent source directory does not exist: {repo}")
    market = TalentMarket(opc_home, OPCConfig())
    before = {p.stem: p.read_bytes() for p in (opc_home / "prompts" / "talent").glob("*.md")}
    templates = market.import_from_repo(repo)
    try:
        revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except subprocess.CalledProcessError:
        revision = "local"
    records = {t.id: {"source_path": t.source_path,
                      "sha256": hashlib.sha256((opc_home / t.prompt_ref).read_bytes()).hexdigest()}
               for t in templates}
    manifest = {"source": repo.name, "revision": revision, "templates": records}
    (opc_home / "prompts" / "talent" / ".agency-source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return {"source_revision": revision, "imported": len(templates),
            "added": sum(t.id not in before for t in templates),
            "updated": sum(t.id in before and before[t.id] != (opc_home / t.prompt_ref).read_bytes() for t in templates)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--opc-home", type=Path, default=get_opc_home())
    args = parser.parse_args()
    print(json.dumps(sync(args.repo, args.opc_home), indent=2))
