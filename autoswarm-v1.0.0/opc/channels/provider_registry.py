"""Static metadata for built-in AutoSwarm channel providers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChannelProviderSpec:
    name: str
    module_name: str
    class_name: str
    delivery_mode: str
    extra_name: str
    required_package: str | None = None
    bridge_required: bool = False
    required_config_fields: tuple[str, ...] = field(default_factory=tuple)
    login_summary: str = ""


PROVIDER_SPECS: dict[str, ChannelProviderSpec] = {
    "telegram": ChannelProviderSpec(
        name="telegram",
        module_name="opc.channels.telegram",
        class_name="TelegramChannel",
        delivery_mode="polling",
        extra_name="channels-telegram",
        required_package="telegram",
        required_config_fields=("token",),
        login_summary="Create a Telegram bot with BotFather, set `token`, then allow approved sender IDs in `allow_from`.",
    ),
    "whatsapp": ChannelProviderSpec(
        name="whatsapp",
        module_name="opc.channels.whatsapp",
        class_name="WhatsAppChannel",
        delivery_mode="bridge",
        extra_name="channels-whatsapp",
        required_package="websockets",
        bridge_required=True,
        required_config_fields=("bridge_url",),
        login_summary="Start the WhatsApp bridge, pair via QR code, then configure `bridge_url`, optional `bridge_token`, and `allow_from`.",
    ),
    "discord": ChannelProviderSpec(
        name="discord",
        module_name="opc.channels.discord",
        class_name="DiscordChannel",
        delivery_mode="socket",
        extra_name="channels-discord",
        required_package="discord",
        required_config_fields=("token",),
        login_summary="Create a Discord bot, set `token`, enable required gateway intents, and configure `group_policy` / `allow_from`.",
    ),
    "feishu": ChannelProviderSpec(
        name="feishu",
        module_name="opc.channels.feishu",
        class_name="FeishuChannel",
        delivery_mode="socket",
        extra_name="channels-feishu",
        required_package="lark_oapi",
        required_config_fields=("app_id", "app_secret"),
        login_summary="Create a Feishu app, set `app_id` / `app_secret`, and configure `verification_token` / `encrypt_key` if your tenant requires them.",
    ),
    "mochat": ChannelProviderSpec(
        name="mochat",
        module_name="opc.channels.mochat",
        class_name="MochatChannel",
        delivery_mode="bridge",
        extra_name="channels-mochat",
        required_package="socketio",
        bridge_required=True,
        required_config_fields=("base_url", "claw_token", "agent_user_id"),
        login_summary="Configure Mochat HTTP/Socket endpoints plus `claw_token` and `agent_user_id`; socket mode is preferred and HTTP watch fallback is automatic.",
    ),
    "dingtalk": ChannelProviderSpec(
        name="dingtalk",
        module_name="opc.channels.dingtalk",
        class_name="DingTalkChannel",
        delivery_mode="socket",
        extra_name="channels-dingtalk",
        required_package="dingtalk_stream",
        required_config_fields=("client_id", "client_secret"),
        login_summary="Create a DingTalk Stream Mode app, set `client_id` / `client_secret`, and approve sender IDs in `allow_from`.",
    ),
    "email": ChannelProviderSpec(
        name="email",
        module_name="opc.channels.email",
        class_name="EmailChannel",
        delivery_mode="polling",
        extra_name="channels-email",
        required_config_fields=("imap_host", "imap_username", "imap_password", "smtp_host", "smtp_username", "smtp_password"),
        login_summary="Configure IMAP/SMTP credentials, set `consent_granted: true`, then add approved sender addresses in `allow_from`.",
    ),
    "slack": ChannelProviderSpec(
        name="slack",
        module_name="opc.channels.slack",
        class_name="SlackChannel",
        delivery_mode="socket",
        extra_name="channels-slack",
        required_package="slack_sdk",
        required_config_fields=("bot_token", "app_token"),
        login_summary="Create a Slack app with Socket Mode, set `bot_token` / `app_token`, then configure DM and group policies.",
    ),
    "qq": ChannelProviderSpec(
        name="qq",
        module_name="opc.channels.qq",
        class_name="QQChannel",
        delivery_mode="socket",
        extra_name="channels-qq",
        required_package="botpy",
        required_config_fields=("app_id", "secret"),
        login_summary="Create a QQ bot application, set `app_id` / `secret`, and allow approved openids in `allow_from`.",
    ),
    "matrix": ChannelProviderSpec(
        name="matrix",
        module_name="opc.channels.matrix",
        class_name="MatrixChannel",
        delivery_mode="polling",
        extra_name="channels-matrix",
        required_package="nio",
        required_config_fields=("homeserver", "access_token", "user_id"),
        login_summary="Create a Matrix access token, set `homeserver`, `access_token`, `user_id`, and optionally `device_id` for sync persistence.",
    ),
}


def ordered_provider_specs() -> list[ChannelProviderSpec]:
    return [PROVIDER_SPECS[name] for name in (
        "telegram",
        "whatsapp",
        "discord",
        "feishu",
        "mochat",
        "dingtalk",
        "email",
        "slack",
        "qq",
        "matrix",
    )]
