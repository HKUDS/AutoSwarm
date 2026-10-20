"""Security validation for AutoSwarm Market packages."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .package_format import SandboxReport

if TYPE_CHECKING:
    from .package_format import OPCPackage

# Tools that could execute arbitrary code or access the filesystem
DANGEROUS_TOOLS = frozenset({
    "shell_exec", "bash", "terminal", "subprocess",
    "file_write", "file_delete", "file_move",
    "eval", "exec", "os_command",
})

# Patterns that suggest prompt injection attempts
SUSPICIOUS_PROMPT_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?above", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*:", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"base64\s*decode", re.IGNORECASE),
]


class SandboxChecker:
    """Validates an AutoSwarm package for security concerns before installation."""

    def validate(self, package: OPCPackage) -> SandboxReport:
        report = SandboxReport()
        self._check_tools(package, report)
        self._check_prompts(package, report)
        self._check_manifest(package, report)
        report.passed = len(report.errors) == 0
        return report

    def _check_tools(self, package: OPCPackage, report: SandboxReport) -> None:
        for role in package.roles:
            tools = role.get("tools") or []
            role_id = role.get("id", "unknown")
            for tool in tools:
                if tool.lower() in DANGEROUS_TOOLS:
                    report.errors.append(
                        f"Role '{role_id}' uses dangerous tool: {tool}"
                    )

    def _check_prompts(self, package: OPCPackage, report: SandboxReport) -> None:
        for filename, content in package.prompt_contents.items():
            for pattern in SUSPICIOUS_PROMPT_PATTERNS:
                match = pattern.search(content)
                if match:
                    report.warnings.append(
                        f"Prompt '{filename}' contains suspicious pattern: '{match.group()}'"
                    )

    def _check_manifest(self, package: OPCPackage, report: SandboxReport) -> None:
        m = package.manifest
        if not m.id:
            report.errors.append("Package manifest missing 'id'")
        if not m.name:
            report.errors.append("Package manifest missing 'name'")
        if m.id and not re.match(r"^[a-z0-9][a-z0-9_-]*$", m.id):
            # The id is used as a directory name under prompts/market and is passed to
            # ``shutil.rmtree`` on uninstall. A malformed value enables path traversal
            # (arbitrary file write / directory deletion), so this must be a hard error,
            # not a warning.
            report.errors.append(
                f"Package id '{m.id}' must be lowercase alphanumeric with hyphens/underscores"
            )
