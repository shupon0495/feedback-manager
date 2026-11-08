import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, copyFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
const run = promisify(execFile);
const source = resolve(import.meta.dirname, "..");

test("OpenCode chat.message calls Python, includes context and surfaces failures", async () => {
  const root = await mkdtemp(join(tmpdir(), "fm opencode "));
  try {
    await run("uv", ["run", "--project", source, join(source, "scripts/install.py"), root]);
    const plugin = join(root, ".opencode/plugins/feedback-manager.js");
    // .mjs ensures Node treats the same source as ESM; OpenCode uses Bun's loader.
    await copyFile(plugin, plugin + ".mjs");
    const { FeedbackManager } = await import(pathToFileURL(plugin + ".mjs"));
    let toasted = 0;
    const hooks = await FeedbackManager({ directory: root, client: { tui: { showToast: async () => { toasted++; } } } });
    const output = { message: { id: "msg_test" }, parts: [] };
    await hooks["chat.message"]({ sessionID: "ses_test" }, output);
    assert.equal(output.parts.length, 1);
    assert.equal(output.parts[0].synthetic, true);
    assert.equal(output.parts[0].messageID, "msg_test");
    assert.match(output.parts[0].text, /BEGIN FEEDBACK DATA/);
    assert.equal(toasted, 1);
    await rm(join(root, ".feedback-manager"), { recursive: true });
    await mkdir(join(root, ".feedback-manager/feedback.sqlite3"), { recursive: true });
    await assert.rejects(hooks["chat.message"]({ sessionID: "ses_test" }, output), /HOST_RETRIEVAL_FAILED/);
    assert.equal(output.parts.length, 1);
  } finally { await rm(root, { recursive: true, force: true }); }
});
