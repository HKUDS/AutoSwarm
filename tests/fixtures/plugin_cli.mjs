import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
const args = process.argv.slice(2);
const callID = "call_" + "c".repeat(32);
const approvalCall = "call_" + "d".repeat(32);
const requestID = "approval_" + "e".repeat(32);
const marker = process.env.AUTOSWARM_PLUGIN_TEST_DIR && join(process.env.AUTOSWARM_PLUGIN_TEST_DIR, "approval-received");
if (args[0] === "run") console.log(JSON.stringify({ ok: true, data: { call_id: args.includes("--approval-sequence") ? approvalCall : callID } }));
else if (args[0] === "calls") console.log(JSON.stringify({ ok: true, data: [{ call_id: callID, parent: { session_id: "original" } }] }));
else if (args[0] === "wait") {
  const approval = args[1] === approvalCall && !existsSync(marker);
  console.log(JSON.stringify({ ok: true, data: [approval ? { kind: "approval_required", call_id: approvalCall, request_id: requestID, delegate_to_parent: false } : { call_id: args[1], outcome: "completed", content: "CHILD_RESULT" }] }));
}
else if (args[0] === "_receipt") {
  if (args[1] === approvalCall && args.includes("--request")) writeFileSync(marker, args[args.indexOf("--request") + 1]);
  console.log(JSON.stringify({ ok: true, data: { state: "delivered" } }));
}
else process.exit(1);
