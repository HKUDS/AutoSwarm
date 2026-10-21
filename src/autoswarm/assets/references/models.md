# Select models and reasoning effort

`run` and `resume` accept optional `--model <native-id>` and `--effort <native-value>`. Omission preserves the agent's native defaults; resume inherits previously explicit choices, and supplied values replace those choices. Internal message turns and idle-message continuation retain them. Each child has its own selection; the main agent's model does not determine it.

Use the installed CLI's current model catalog/configuration. Reuse native credentials and provider profiles. AutoSwarm keeps no model catalog or common list of effort levels and does not edit global native model settings.

| Child | Model | Effort |
|---|---|---|
| Codex | Native model ID on thread/turn | Native `turn/start.effort` |
| Claude Code | Native `--model` | Native `--effort` |
| OpenCode | `provider/model` | That model's advertised variant |
| Cursor | Native ACP model ID, including bracket parameters | Negotiate native parameter controls and set the model's advertised reasoning parameter; retain other parameters |
| Copilot | Native `--model`; ACP selection when advertised | Native `--reasoning-effort`; ACP effort control when advertised |
| CodeBuddy | Native ACP model ID | Advertised ACP `thought_level`/reasoning-effort control |
| Muse | Native model ID via MSP session selection | Native MSP `session/setReasoningEffort` |
| Hermes | Advertised ACP model ID via `session/set_model` | Only if its installed ACP version advertises a control; otherwise retain native defaults and report an explicit override as unsupported |
| Nanobot | Native SDK's explicit-model override | Native preset/default `reasoning_effort`, changed only in this process |
| DSH | Advertised `provider/model` route or exact native ACP option value | Advertised ACP `reasoning_effort` values |
| Grok Build (`grok`) | Native `--model` | Native `--reasoning-effort` |

For example, with an accessible Codex model:

```bash
autoswarm run --agent codex --model <native-model-id> --effort <native-effort> --input task.txt --background
autoswarm resume <call-id> --effort <another-native-effort> --input followup.txt
```

Native policies, model capabilities and account access still apply. `requested_selection` in inspect/results records requested values; acceptance is not evidence that a remote backend honored them without native policy adjustments. Do not ask a model to self-report its settings as verification. Cursor's persistent ACP preferences are isolated per invocation; its sessions retain native storage.

On rejection, inspect the native error and live catalog/configuration, then correct a routine identifier mismatch within the user's request. Do not downgrade or choose another model to conceal the failure. Cursor Auto or a model with no advertised reasoning parameter cannot provide a verified effort override; select a user-authorized model that exposes one or ask for the needed decision. If OpenCode cannot disclose an unambiguous default for variant validation, resolve its native model and pass `--model` explicitly. An empty Copilot BYOK model catalog alone is not an authentication failure; use its configured native CLI options.
