import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
const args = process.argv.slice(2);
const callID = "call_" + "c".repeat(32);
const approvalCall = "call_" + "d".repeat(32);
const interactionCall = "call_" + "f".repeat(32);
const issueCall = "call_" + "b".repeat(32);
const requestID = "approval_" + "e".repeat(32);
const marker = process.env.AUTOSWARM_PLUGIN_TEST_DIR && join(process.env.AUTOSWARM_PLUGIN_TEST_DIR, "approval-received");
if (args[0] === "run") console.log(JSON.stringify({ ok: true, data: { call_id: args.includes("--approval-sequence") ? approvalCall : args.includes("--interaction-sequence") ? interactionCall : args.includes("--issue-sequence") ? issueCall : callID, return_mode: args.includes("--notify-parent") ? "native" : "wait" } }));
else if (args[0] === "calls") console.log(JSON.stringify({ ok: true, data: [{ call_id: callID, parent: { session_id: "original" } }] }));
else if (args[0] === "wait") {
  const approval = args[1] === approvalCall && !existsSync(marker);
  const questionMarker = process.env.AUTOSWARM_PLUGIN_TEST_DIR && join(process.env.AUTOSWARM_PLUGIN_TEST_DIR, "question-received");
  const issueMarker = process.env.AUTOSWARM_PLUGIN_TEST_DIR && join(process.env.AUTOSWARM_PLUGIN_TEST_DIR, "issue-received");
  const question = args[1] === interactionCall && !existsSync(questionMarker);
  const issue = args[1] === issueCall && !existsSync(issueMarker);
  console.log(JSON.stringify({ ok: true, data: [question ? { kind: "interaction_required", call_id: interactionCall, request_id: requestID, user_required: false, response_format: "cursor-question" } : issue ? { kind: "communication_issue", call_id: issueCall, diagnostic: { at: "diagnostic-stamp", code: "unsupported_native_interaction" } } : approval ? { kind: "approval_required", call_id: approvalCall, request_id: requestID, delegate_to_parent: false } : { call_id: args[1], outcome: "completed", content: "CHILD_RESULT" }] }));
}
else if (args[0] === "_receipt") {
  if (args[1] === approvalCall && args.includes("--request")) writeFileSync(marker, args[args.indexOf("--request") + 1]);
  if (args[1] === interactionCall && args.includes("--request")) writeFileSync(join(process.env.AUTOSWARM_PLUGIN_TEST_DIR, "question-received"), "ok");
  if (args[1] === issueCall && args.includes("--issue")) writeFileSync(join(process.env.AUTOSWARM_PLUGIN_TEST_DIR, "issue-received"), args[args.indexOf("--issue") + 1]);
  console.log(JSON.stringify({ ok: true, data: { state: "delivered" } }));
}
else process.exit(1);
