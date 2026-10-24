# Agent capabilities and data scope

Selection notes distilled from the main capability matrix and Table 3 of `AutoSwarm 能力地图` (2026-10-23), plus the user's experience preferences. They describe documented routes, not successful probes of every installed version. Read only sections relevant to the assignment. Existing verified configuration can provide additional routes; an unlisted route is not a prohibition.

Use the user's chosen agents/models first. Otherwise consider the actual tools, data scope and native project context. Coding and image preferences for Codex, coding for Claude Code, and Claude Code with Opus 5.5+ for programmatic video are experience-based preferences. The model condition does not select a native model ID or override configured defaults. Other agents can make programmatic video; that shared capability is not a recorded specialty preference for them.

## Agent details

### codex

- Coding and image generation/editing; GitHub integration. Workspace connectors/plugins can supply Gmail, Drive, calendar, Outlook/M365, Slack and Teams; availability depends on native authentication and the actual session. Meta Ads and WhatsApp Business MCP, CloudBase tools, model-video plugins such as Higgsfield and deployment plugins are separate configured routes.
- The reviewed notes describe some mail/IM connectors as read/draft tools, not general send permission. WhatsApp Business has its own sending tools. Desktop browser access, computer use, voice, mobile pairing, scheduled tasks and cloud/event execution do not automatically transfer to an AutoSwarm child. CLI cloud submission and connector/image availability in the actual transport require verification. Native memory must be enabled when relied on.

### claude-code

- Coding; programmatic video with Opus 5.5+ is the user's preferred combination: Remotion, HyperFrames, JavaScript/Canvas animation and code-controlled presentations. Official document skills cover Office work. These are distinct from model-generated video, which uses a plugin such as Runway; image/audio generation also needs the corresponding tool.
- Native connectors cover Gmail/Drive/calendar and Microsoft 365, including Outlook, SharePoint, OneDrive and Teams. Slack plugins, Meta Ads/WhatsApp Business MCP, CloudBase, Postiz social publishing and deployment plugins add routes. M365 writes need their own enabled permissions. `mcp serve` exposes tools, not a replacement AutoSwarm child conversation.
- Artifacts publishing, Chrome with the user's login, Remote Control, cloud routines and Channels have host/authentication conditions; do not infer headless availability from the App. Desktop computer use in the reviewed notes is unavailable in print mode. Session loops depend on the native process; native memory is retained by Claude Code.

### opencode

- A general coding/execution candidate when its native setup fits; the source does not establish a special content-generation advantage. Its official GitHub integration installs Actions workflows and supports comment triggers; Actions can also supply schedules/events. These are separate from the local child.
- V1 and V2 differ despite sharing an executable name. Experimental V2 voice and paired web/desktop remote access require their native entry points. Rules and session history are not evidence of a separate cross-session memory service.

### cursor

- Native image generation; documented Cloud Agents REST API. Official plugins cover X search/timelines/trends, Gmail read/draft/send, Google Docs/Sheets/Slides/Drive/calendar, Outlook/M365, Slack, Teams, Feishu/Lark, Meta Ads and WhatsApp Business. Buffer supplies social publishing; CloudBase and deployment plugins supply development/deployment routes; trading plugins target their particular services.
- Cloud automations accept GitHub, Slack, webhook and Linear events; cloud tasks can deploy and use cloud/worker desktop tools. They require those entry points, not merely a local ACP session. Plugin loading/OAuth in the actual child must be checked. The native workspace browser is isolated, not the user's logged-in browser. Mobile control is conditional; the reviewed memory capability belongs to cloud automations.

### copilot

- Built-in GitHub MCP supports GitHub tool tasks. Work IQ supplies M365 mail, meeting and document queries and conditional Teams access; it is a preview with tenant/user authorization requirements. Sending mail through Work IQ is unverified, not implied by read access.
- Native mobile remote control is a separate session entry. Desktop/browser control is preview functionality whose child-mode behavior needs verification. Interactive schedules, GitHub Actions/Agentic Workflows and experimental cloud sandboxes have distinct execution conditions. Native memory is preview functionality.

### muse

- Native image generation/editing when its model catalog exposes an image model; `muse voice transcribe` handles audio files. Blender tools can support relevant coding tasks without implying a special video-quality ranking. Muse Code retains native memory.
- Computer use, attachment to the user's browser and session loops need verification in the MSP host. Muse App's Instagram/Facebook/advertising, WhatsApp, phone calls and Stripe Link checkout are not automatically Muse Code capabilities.

### codebuddy

- Built-in Kling V3 model-video generation; Tencent Docs official MCP and CloudBase development tools. Image generation and Artifact public HTML/Markdown sharing have domestic/international edition differences. GitHub integration and audio transcription are not established working routes by the source; the reviewed audio tool configuration is disabled.
- Channels include bound WeChat, WeCom, WeChat customer service and supported Telegram/Discord plugins. Replies in connected conversations do not establish arbitrary new-contact messaging. A daemon can maintain schedules; messages and a Beta signed webhook can trigger work. Experimental macOS desktop use requires its native switch. Browser profile reuse remains unverified.
- ACP/SDK payment cards relay authorization; the payment-initiating plugin belongs to WorkBuddy. WorkBuddy's other connectors, cloud workers and full office integrations do not automatically transfer to CodeBuddy. Native memory/skills use CodeBuddy's own locations; installing another agent's skill does not install it here.

### hermes

- X search and xurl posting/replies/messages; GitHub skill; Google Workspace skill covering Gmail, calendar, Drive, contacts, Sheets and Docs. IMAP/SMTP and the himalaya skill support email. Images, model video (enable its toolset), TTS audio files and bundled docx/xlsx/powerpoint tools provide multimedia/document routes.
- A configured CDP URL can attach to the user's logged-in browser. The computer_use toolset needs a desktop session. Optional publish-site, payment/shopping and telephony skills add website publishing, checkout and AI outbound calls; telephony is the source's distinctive documented calling route, not a default installation promise.
- Gateway channels include WeChat, WeCom, QQ, Feishu, DingTalk, Slack, Teams, Google Chat, Discord, Telegram, WhatsApp, iMessage, Signal, LINE and Matrix, with different account/bot scopes. Gateway schedules, email/messages and signed webhooks support ongoing work. Native gateway configuration is not created by an ordinary child call.
- Cross-channel sending uses `hermes send`; a generic cross-platform send tool is deliberately not exposed directly to the model. Some CLI send destinations work without a gateway; receiving/ongoing channels require their own service. Teams meeting processing is narrower than full M365. The experimental Telegram Business plugin sees selected new private messages, not historical account data. Optional MCP serving exposes connected-channel/selected tools, not a generic agent-run endpoint. Hermes Cloud is managed through its own Portal/MCP route. Native memory stays native.

### nanobot

- Gateway-based messaging, IMAP/SMTP email, schedules and message/email/Linear triggers; a native message tool sends to connected conversations. Channel speech transcription needs its native service. Enable/configure image generation before relying on it. GitHub uses the bundled gh-based skill; native document tools are oriented to reading, though code can produce files.
- Google Workspace and M365 use configured MCP presets; model-video and deployment tools also use presets. WhatsApp conversation channels do not imply Meta Ads, Instagram or business-account management tools. The RingCentral Phone preset does not establish outbound calling. QQ mail through generic IMAP is mainly new-message processing, not a documented QQ-native historical mailbox connector.
- AutoSwarm uses the installed Python SDK bridge with native workspace, memory and configuration. A gateway/channel available elsewhere is not proof it is connected to this SDK session; verify the needed delivery path. Persistent schedules/channels require their service. Native Full Access/approval behavior is described in [setup](setup.md), not changed by this selection guide.

### dsh

- A general execution candidate in a suitable native project/profile. CloudBase tools, official QQ and DingTalk connectors add routes. The DingTalk bundle supplies only a subset of DWS skills and is disabled by default; see the platform data row below.
- Office skills belong to the SDK profile; browser attach and desktop use are experimental; schedules belong to Web/desktop; experimental GitHub webhook review belongs to `dsh web`. None is guaranteed by the ACP profile AutoSwarm starts. Memory is supplied through configured memory MCP. Delegating natively to Codex/Claude Code does not make their specialties intrinsic to DSH.

### grok

- A general execution candidate; X search, deep research, image generation/editing and model-video generation are specialty candidates with source/docs evidence, not verified child-mode guarantees. Do not infer them from the product name or use an interactive slash command as proof a stdio child can run it.
- Official plugins offer browser-use, Vercel/Netlify deployment and Quo SMS. Social publishing through the Helena plugin is unverified; a merchant-side Stripe plugin is not consumer checkout. Session loops require a live native session. Native memory must be enabled when relied on.

## Platform data: complete Table 3 scope

These rows concern authorized account/platform data beyond chat history. A bot's channel membership or a conversation with the agent does not grant account-wide data access. Plugins, presets and official CLI routes require their actual native authorization. All-agent CLI rows are shared routes, not reasons to prefer a particular brand; reuse a working tool and share retrieved inputs when useful.

| Platform | Candidate routes | Resources and limits |
| --- | --- | --- |
| 微信 / WeChat | Tencent Docs MCP; codebuddy is explicitly named in the reviewed provider list | List/search and read/write documents, spreadsheets and smart tables; create slides, mind maps and flowcharts. Documents created inside the WeChat mini-app are an untested same-account case. This does not cover arbitrary mini-apps, official accounts, payment bills, favorites, Moments or contacts. CloudBase accesses a developer's own backend, not all mini-app data. Other already-configured MCP clients can be verified; "named client" is not technical exclusivity. |
| 企业微信 / WeCom | Any of the eleven agents through wecom-cli / wecom-unified | Contacts, email, documents, tables/smart tables/slides, calendars, meeting notes and full transcripts, to-dos and WeDrive. User QR authorization and native resource permissions apply. This route does not establish chat history, approvals or attendance. The email skill may send after preview without another native confirmation; obtain required authorization before invocation. |
| QQ | codebuddy for Tencent Docs; hermes himalaya and nanobot email through generic IMAP | QQ-login Tencent Docs; QQ mail with an authorization code, with nanobot mainly handling new mail. This does not establish QQ channels, friends, group files or Qzone access. WorkBuddy's QQ-mail connector is not automatically a CodeBuddy tool. |
| 飞书 / Feishu / Lark | Any agent through lark-cli; cursor's Feishu/Lark plugin is an additional route | Documents, knowledge bases, spreadsheets/multidimensional tables, slides, cloud storage, calendars, meeting notes/Miaoji, email, approvals, tasks, OKRs, attendance and contacts. Cursor's plugin covers documents/comments/files/user information. User identity and resource/admin scopes matter; Hermes's bot-comment document tool is not the same user-data route. |
| 钉钉 / DingTalk | Any agent through full dws skills; dsh connector has a partial bundle | AI tables, documents, DingDrive, knowledge bases, calendars, AI meeting notes with summaries/full transcripts, email, to-dos, approvals, attendance, logs, contacts and DING. Admin CLI enablement is required. DSH's default-disabled bundle covers AI tables, approvals, attendance, calendars, contacts, DING, logs, to-dos, workbench/group management; documents, drive, meeting notes, email and knowledge bases need the full skills. |
| Slack | claude-code/cursor official plugins; codex Slack plugin | User-accessible channels/members, files, Canvas, lists and user profiles; official MCP/user and workspace authorization. Actual child plugin loading must be verified. Hermes/nanobot bot-channel tools do not establish this personal account scope. |
| Microsoft Teams | claude-code/codex/cursor connectors; nanobot M365 preset; copilot Work IQ; hermes teams_pipeline | Team/channel files through SharePoint/OneDrive; Work IQ meeting/document queries; Codex Teams meeting transcripts have an enterprise condition. Hermes's opt-in pipeline fetches meeting transcripts or downloads recordings for transcription via Graph application permissions; maintain its expiring subscriptions. Meeting processing does not imply full SharePoint/OneDrive access. Match the exact file/meeting/recording/transcript tool. |
| Google Chat | claude-code and nanobot: untested routes | Space/member listing is distinct from Google Workspace documents/calendar. The preview official Chat MCP is not a confirmed Claude Code route; nanobot's community Workspace preset contains candidate Chat tools but needs verification. Hermes's conversation channel is not evidence of account-data tools. |
| Discord | No documented ready-made user-data route among the eleven | Server/channel/member data visible to an invited bot is bot scope, not the user's general account data. |
| Telegram | No documented ready-made contact/channel/group account-data route | Hermes's experimental Business plugin sees selected new chats, not a general contact/channel/group inventory or history. |
| WhatsApp | codex/claude-code/cursor through official Business Tools MCP/plugins | Business accounts/numbers, message templates and webhooks; cursor's plugin additionally has business contacts. These are business-management tools, not personal chat history; Hermes/nanobot conversation channels do not imply these tools. |
| Messenger / Instagram | No documented ready-made inbox route among the eleven | Facebook Page and professional-account inbox APIs are not supplied by the reviewed Meta Ads or WhatsApp MCP tools. |
| iMessage | No additional platform-data route in Table 3 | Attachments accompany message-history access; contacts belong to the system address book. Existing Mac message tools have native host/authorization conditions, not a general platform-data connector. |
| Signal | No documented ready-made contact/group data route | Conversation channels do not supply contact/group inventory tools. |
| LINE | No documented ready-made personal-account data route | Official-account follower/group tools are a different identity/scope. Hermes's LINE channel is not personal-account access. |
| Matrix | No documented ready-made room/member inventory tool in the reviewed agents | The protocol's ability to expose rooms/members does not establish an installed Hermes/nanobot tool. |

## Applying the notes

Confirm only the capability the assignment needs: executable discovery/version checks do not verify plugin loading, a model tool, a native profile, a user token or a gateway. Prefer existing working configuration. When the short table does not identify a suitable route, use these details and local evidence before concluding no agent fits.

Keep retrieval scope precise: read access does not authorize sending, publishing, checkout or external calls. Follow the existing approval/interaction rules; never change permissions or native defaults merely to make a route work. If a route is unavailable, retain work and diagnostics, let the parent inspect/fix or choose another authorized route, and continue independent work. Do not blindly repeat uncertain side effects.
