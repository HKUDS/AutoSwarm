"""Proof-of-concept integration hooks for remote peers.

This module replaces the local-only message routing with a hybrid that:

1. Still uses the filesystem as the local source of truth.
2. Sends outbound messages to a remote peer when the target is a remote peer.
3. Accepts inbound messages via an HTTP server started on demand.

It is intentionally minimal: no auto-discovery, no TLS, manual peer config.
"""
from __future__ import annotations

import os
from .records import Records
from .transport import RemoteTransport


def _enabled(records: Records) -> bool:
    cfg = records.config()
    return bool(cfg.get("remote_peers")) or bool(os.environ.get("AUTOSWARM_REMOTE_PORT")) or bool(os.environ.get("AUTOSWARM_REMOTE_PEERS"))


def _transport_for(records: Records) -> RemoteTransport | None:
    if not _enabled(records):
        return None
    port = int(os.environ.get("AUTOSWARM_REMOTE_PORT", "8787"))
    host = os.environ.get("AUTOSWARM_REMOTE_HOST", "0.0.0.0")
    # Explicit add_peer calls happen on the transport returned by _transport_for.
    # To make the singleton pick those up, cache by (home, port) and re-use it.
    _key = (str(records.home), port)
    if _key in _TRANSPORT_CACHE:
        return _TRANSPORT_CACHE[_key]
    transport = RemoteTransport(records, host=host, port=port)
    cfg = records.config()
    for name, url in cfg.get("remote_peers", {}).items():
        transport.add_peer(name, url)
    transport.start()
    _TRANSPORT_CACHE[_key] = transport
    return transport


# Global cache keyed by (home, port). In a real implementation this should be
# managed by the engine/runner lifecycle.
_TRANSPORT_CACHE: dict[tuple[str, int], RemoteTransport] = {}


def get_transport(records: Records) -> RemoteTransport | None:
    cfg = records.config()
    if not _enabled(records):
        return None
    port = int(os.environ.get("AUTOSWARM_REMOTE_PORT", "8787"))
    host = os.environ.get("AUTOSWARM_REMOTE_HOST", "0.0.0.0")
    _key = (str(records.home), port)
    if _key not in _TRANSPORT_CACHE:
        return _transport_for(records)
    return _TRANSPORT_CACHE[_key]
