"""AutoSwarm: Automating Agent Swarms — Self-Built, Self-Run, Self-Grown.

The compatible ``opc`` Python package.
"""

from __future__ import annotations

from opc.core.windows_ssl import sanitize_windows_sslkeylogfile

# Remove SSLKEYLOGFILE as early as possible on Windows so importing network
# clients (aiohttp, litellm/httpx, etc.) does not trigger OpenSSL crashes.
sanitize_windows_sslkeylogfile()

__version__ = "1.0.0"
