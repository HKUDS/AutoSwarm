# Channel Bridge Providers

Some channel providers need a companion bridge service in addition to the AutoSwarm Python runtime. AutoSwarm connects to these bridges through the provider adapter; it does not start or manage the external bridge process for you.

## WhatsApp

- Install: `pip install -e .[channels-whatsapp]`
- Runtime mode: WebSocket bridge
- Required config:
  - `bridge_url`
  - `allow_from`
- Optional config:
  - `bridge_token`
- Expected flow:
  1. Start the companion WhatsApp bridge service.
  2. Complete QR/device pairing in the bridge environment.
  3. Set `channels.whatsapp.bridge_url` in `.opc/config/channel_config.yaml`.
  4. Run `autoswarm channels status`.
  5. Start AutoSwarm with `autoswarm channels start -p <project>` or `autoswarm run -p <project>`.

## Mochat

- Install: `pip install -e .[channels-mochat]`
- Runtime mode: bridge
- Required config:
  - `base_url`
  - `claw_token`
  - `agent_user_id`
- Optional config:
  - `socket_url`
  - `socket_path`
  - `sessions`
  - `panels`
- Notes:
  - Socket mode is attempted first when available.
  - HTTP watch/poll fallback is used if socket startup fails.
  - Panel targets are treated as bridge/group-style destinations.

## Convenience Install

```bash
pip install -e .[channels-bridges]
```
