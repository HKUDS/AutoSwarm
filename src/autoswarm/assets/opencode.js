import { tool } from "@opencode-ai/plugin";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";

const runtime = __AUTOSWARM_COMMAND__;
const instanceKey = Symbol.for("autoswarm.native-host-instance");
const hostInstance = globalThis[instanceKey] ||= randomUUID();

export const AutoSwarm = async ({ client, directory }) => {
  const watched = new Set();
  const pending = new Map();
  const sending = new Set();
  const acknowledgements = new Map();
  const key = item => `${item.call_id}/${item.request_id || "result"}`;

  const invoke = (args, parent) => new Promise((resolve, reject) => {
    const child = spawn(runtime[0], [...runtime.slice(1), ...args], {
      env: { ...process.env, ...(parent ? { AUTOSWARM_PARENT: JSON.stringify(parent) } : {}) },
      stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
    });
    let stdout = "", stderr = "";
    child.stdout.on("data", chunk => { stdout += chunk.toString(); });
    child.stderr.on("data", chunk => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code, signal) => {
      if (code !== 0) return reject(new Error(stderr || stdout || `AutoSwarm process ended: ${signal || code}`));
      try { resolve(JSON.parse(stdout)); } catch (error) { reject(error); }
    });
  });

  const flush = async sessionID => {
    if (sending.has(sessionID) || !pending.get(sessionID)?.length) return;
    sending.add(sessionID);
    let delivered = false;
    try {
      const status = await client.session.status();
      if (status.error) throw new Error(JSON.stringify(status.error));
      if (status.data?.[sessionID]?.type === "busy" || status.data?.[sessionID]?.type === "retry") return;
      const results = [...pending.get(sessionID)];
      const response = await client.session.prompt({
        path: { id: sessionID },
        body: { parts: [{ type: "text", text: "AutoSwarm child events. Continue the user's existing task. approval_required is a paused native permission request: decide if delegate_to_parent is true, otherwise ask the user here and relay their explicit answer with --by user. Do not wait for a paused child before replying. Final outcomes are separate events.\n" + JSON.stringify(results) }] },
      });
      if (response.error) throw new Error(JSON.stringify(response.error));
      const info = response.data?.info;
      if (!info?.id || info.role !== "assistant" || info.error) throw new Error("Original OpenCode session did not return a successful assistant receipt");
      for (const result of results) {
        await invoke(["_receipt", result.call_id, "--session", sessionID, "--message", info.id,
          ...(result.request_id ? ["--request", result.request_id] : []),
        ]);
        acknowledgements.get(key(result))?.();
        acknowledgements.delete(key(result));
      }
      pending.set(sessionID, pending.get(sessionID).filter(item => !results.includes(item)));
      delivered = true;
    } catch (error) {
      // Results remain in the call directories; an idle event retries delivery.
      await client.app.log({ body: { service: "autoswarm", level: "error", message: String(error) } });
    } finally { sending.delete(sessionID); }
    if (delivered && pending.get(sessionID)?.length) await flush(sessionID);
  };

  const watch = (callID, sessionID) => {
    if (watched.has(callID)) return;
    watched.add(callID);
    void (async () => {
      while (true) {
        const value = await invoke(["wait", callID, "--peek", "--unseen"]);
        const accepted = value.data.map(item => new Promise(resolve => acknowledgements.set(key(item), resolve)));
        pending.set(sessionID, [...(pending.get(sessionID) || []), ...value.data]);
        await flush(sessionID);
        await Promise.all(accepted);
        if (value.data.some(item => item.kind !== "approval_required")) return;
      }
    })().catch(error => {
      watched.delete(callID);
      return client.app.log({ body: { service: "autoswarm", level: "error", message: String(error) } });
    });
  };

  const recover = sessionID => invoke(["calls", "--pending-return", "--host", "opencode", "--parent-workspace", directory,
    ...(sessionID ? ["--parent-session", sessionID] : ["--parent-instance", hostInstance]),
  ]).then(value => {
    for (const call of value.data) watch(call.call_id, call.parent.session_id);
  }).catch(error => client.app.log({ body: { service: "autoswarm", level: "error", message: String(error) } }));

  // Reload only this host's watchers. Another CLI in the same workspace must
  // not take over the original host's sessions. A new host may recover an
  // exact conversation when native chat.message confirms it was reopened.
  if (directory) void recover();

  return {
    tool: {
      autoswarm: tool({
        description: "Run AutoSwarm with argv entries. Collect parallel calls with wait; add --notify-parent to return events to this original session automatically.",
        args: { arguments: tool.schema.array(tool.schema.string()) },
        async execute(args, context) {
          const parent = { host: "opencode", session_id: context.sessionID, mode: "hook", workspace: directory, host_instance: hostInstance };
          const value = await invoke(args.arguments, parent);
          if (value.data?.call_id && value.data.return_mode === "native" && args.arguments.includes("--background")) watch(value.data.call_id, context.sessionID);
          return JSON.stringify(value);
        },
      }),
    },
    event: async ({ event }) => {
      if (event.type === "session.idle") await flush(event.properties.sessionID);
    },
    "chat.message": async ({ sessionID }) => {
      if (directory) await recover(sessionID);
    },
  };
};
