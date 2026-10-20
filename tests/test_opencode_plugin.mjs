import assert from "node:assert/strict";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const source = (await readFile(new URL("../src/autoswarm/assets/opencode.js", import.meta.url), "utf8"))
  .replace('import { tool } from "@opencode-ai/plugin";', 'const tool = Object.assign(x => x, { schema: { array: x => x, string: () => ({}) } });')
  .replace("__AUTOSWARM_COMMAND__", JSON.stringify([process.execPath, fileURLToPath(new URL("./fixtures/plugin_cli.mjs", import.meta.url))]));
const { AutoSwarm } = await import("data:text/javascript;base64," + Buffer.from(source + "\n//# sourceURL=autoswarm-plugin-under-test.mjs").toString("base64"));
const awaitCondition = async condition => {
  const deadline = Date.now() + 10000; // Test harness only.
  while (!condition()) {
    if (Date.now() > deadline) throw new Error("Test did not observe native return");
    await new Promise(resolve => setTimeout(resolve, 10));
  }
};

function client(state) {
  return {
    session: {
      status: async () => { state.checked++; return { data: { original: { type: state.busy ? "busy" : "idle" } } }; },
      prompt: async request => {
        state.prompts.push(request);
        return state.fail ? { error: "native return failed" } : { data: { info: { id: "native-assistant", role: "assistant" } } };
      },
    },
    app: { log: async ({ body }) => { state.logs.push(body); } },
  };
}

test("idle original session continues without another user prompt", async () => {
  const state = { prompts: [], logs: [], checked: 0, busy: false };
  const plugin = await AutoSwarm({ client: client(state) });
  await plugin.tool.autoswarm.execute({ arguments: ["run", "--background"] }, { sessionID: "original" });
  await awaitCondition(() => state.prompts.length === 1);
  assert.equal(state.prompts[0].path.id, "original");
  assert.match(state.prompts[0].body.parts[0].text, /CHILD_RESULT/);
});

test("busy session defers return until its native idle event", async () => {
  const state = { prompts: [], logs: [], checked: 0, busy: true };
  const plugin = await AutoSwarm({ client: client(state) });
  await plugin.tool.autoswarm.execute({ arguments: ["run", "--background"] }, { sessionID: "original" });
  await awaitCondition(() => state.checked > 0);
  assert.equal(state.prompts.length, 0);
  state.busy = false;
  await plugin.event({ event: { type: "session.idle", properties: { sessionID: "original" } } });
  assert.equal(state.prompts.length, 1);
  assert.equal(state.prompts[0].path.id, "original");
});

test("failed native return retains pending output for a later idle event", async () => {
  const state = { prompts: [], logs: [], checked: 0, busy: false, fail: true };
  const plugin = await AutoSwarm({ client: client(state) });
  await plugin.tool.autoswarm.execute({ arguments: ["run", "--background"] }, { sessionID: "original" });
  await awaitCondition(() => state.logs.length > 0);
  state.fail = false;
  await plugin.event({ event: { type: "session.idle", properties: { sessionID: "original" } } });
  assert.equal(state.prompts.length, 2);
  assert.match(state.prompts[1].body.parts[0].text, /CHILD_RESULT/);
});

test("reloading the native host integration restores a bound call watcher", async () => {
  const state = { prompts: [], logs: [], checked: 0, busy: false };
  await AutoSwarm({ client: client(state), directory: "/original-workspace" });
  await awaitCondition(() => state.prompts.length === 1);
  assert.equal(state.prompts[0].path.id, "original");
  assert.match(state.prompts[0].body.parts[0].text, /CHILD_RESULT/);
});

test("approval notification is followed by the final result in the same parent session", async () => {
  const directory = await mkdtemp(join(tmpdir(), "autoswarm-plugin-approval-"));
  process.env.AUTOSWARM_PLUGIN_TEST_DIR = directory;
  try {
    const state = { prompts: [], logs: [], checked: 0, busy: true };
    const plugin = await AutoSwarm({ client: client(state) });
    await plugin.tool.autoswarm.execute({ arguments: ["run", "--background", "--approval-sequence"] }, { sessionID: "original" });
    await awaitCondition(() => state.checked > 0);
    assert.equal(state.prompts.length, 0);
    state.busy = false;
    await plugin.event({ event: { type: "session.idle", properties: { sessionID: "original" } } });
    await awaitCondition(() => state.prompts.length === 2);
    assert.match(state.prompts[0].body.parts[0].text, /"delegate_to_parent":false/);
    assert.match(state.prompts[1].body.parts[0].text, /CHILD_RESULT/);
    assert.ok(state.prompts.every(request => request.path.id === "original"));
    assert.equal(state.logs.length, 0);
    assert.match(await readFile(join(directory, "approval-received"), "utf8"), /^approval_/);
  } finally {
    delete process.env.AUTOSWARM_PLUGIN_TEST_DIR;
    await rm(directory, { recursive: true, force: true });
  }
});
