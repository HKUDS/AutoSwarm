# Remote peer support (proof-of-concept)

This branch adds an **optional** remote-peer transport to AutoSwarm so one
agent running on machine A can exchange messages and results with an agent on
machine B (e.g. two laptops on the same LAN).

## Status

- [x] HTTP transport module (`transport.py`) with signed payloads
- [x] Remote peer activation (`remote.py`) driven by config/env
- [x] Hook into `messages.send()` to forward remote messages
- [ ] Remote delegation of entire calls (`run` to a remote host)
- [ ] Discovery / pairing
- [ ] TLS and real peer authentication
- [ ] Tests and documentation

## How it works

Local AutoSwarm keeps the filesystem as the source of truth. When a message is
sent to an agent whose `agent.json` carries `"remote_peer": "office-mac"`, the
local `messages.send()` also POSTs the message to the configured URL for that
peer. The receiving machine stores the message in the target agent's local
mailbox and wakes the native runner, exactly as if the message had been written
locally.

## Configuration

On both machines, set in AutoSwarm's `config.json`:

```json
{
  "remote_peers": {
    "office-mac": "http://192.168.1.42:8787",
    "home-pc": "http://192.168.1.10:8787"
  }
}
```

And export a shared secret (optional but recommended):

```bash
export AUTOSWARM_SHARED_SECRET="something-long-and-random"
```

Start the local listener:

```bash
python -m autoswarm remote --port 8787
```

(The `remote` CLI subcommand is not yet wired; this doc describes the target
UX.)

## Security warning

This POC uses plain HTTP and a shared HMAC secret. It is fine for a trusted
LAN experiment but **must not** be exposed to the internet. A production version
needs TLS, per-peer tokens, and replay protection.

## Design decisions

1. **Filesystem stays canonical**: remote transport is just another delivery
   mechanism. Every agent still reads its own local records.
2. **Manual peer config first**: no auto-discovery until the basic protocol
   works reliably.
3. **Minimal surface**: only messages, final results, and approval requests
   cross the wire. Native agent protocol details stay local.

## Next steps

- Add `autoswarm remote` CLI command to start/stop the HTTP listener.
- Allow `run --agent hermes --remote-peer office-mac` to spawn the child on
  another host and stream the result back.
- Add tests using two temporary `AUTOSWARM_HOME` directories on the same
  machine to simulate a LAN.
