# AutoSwarm Channels

AutoSwarm has a channel runtime in `opc/channels/` for routing external messages into the engine and sending replies back through configured providers. Feishu is the primary documented setup in the README; the other providers are optional adapters with different dependency, credential, and bridge requirements.

Provider metadata is defined in `opc/channels/provider_registry.py`.

## Providers

| Provider | Runtime | Extra | Required config |
|---|---|---|---|
| `telegram` | polling | `channels-telegram` | `token` |
| `whatsapp` | bridge | `channels-whatsapp` | `bridge_url` |
| `discord` | socket | `channels-discord` | `token` |
| `feishu` | socket | `channels-feishu` | `app_id`, `app_secret` |
| `mochat` | bridge | `channels-mochat` | `base_url`, `claw_token`, `agent_user_id` |
| `dingtalk` | socket | `channels-dingtalk` | `client_id`, `client_secret` |
| `email` | polling | `channels-email` | IMAP/SMTP fields, `consent_granted` |
| `slack` | socket | `channels-slack` | `bot_token`, `app_token` |
| `qq` | socket | `channels-qq` | `app_id`, `secret` |
| `matrix` | polling | `channels-matrix` | `homeserver`, `access_token`, `user_id` |

## Setup Flow

1. Run `autoswarm init`.
2. Install the extra for the provider you plan to enable.
3. Edit `.opc/config/channel_config.yaml`, or `$OPC_HOME/config/channel_config.yaml` if `OPC_HOME` is set.
4. Set `allow_from` explicitly. Empty lists deny all inbound senders.
5. Run `autoswarm channels login <provider>` for provider-specific setup guidance.
6. Run `autoswarm channels status` and confirm the provider is enabled, available, and configured.
7. Start channels in the foreground with `autoswarm channels start -p <project>`, or start the engine and channel runtime together with `autoswarm run -p <project>`.

## Commands

```bash
autoswarm channels status
autoswarm channels login feishu
autoswarm channels start -p demo
autoswarm channels stop
autoswarm run -p demo
```

## Messaging Semantics

- Channel events are normalized into `UserMessage`.
- Outbound replies are sent as `SystemMessage`.
- Sender allow-lists are enforced before inbound messages are accepted.
- Session ids are derived from provider chat/thread context when available.
- Bridge providers require a separate companion service; see [channel-bridges.md](channel-bridges.md).
