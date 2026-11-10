// OpenCode 1.18.33: installed as <project>/.opencode/plugins/feedback-manager.js.
// No SDK runtime dependency; the host loads the single exported plugin function.
import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { appendFile, mkdir, stat, rename } from "node:fs/promises";
import { dirname, resolve, relative, isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);

export const FeedbackManager = async ({ directory, client }) => {
  const project = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const skill = join(project, ".agents/skills/feedback-manager");
  const rel = relative(project, resolve(directory));
  if (rel === ".." || rel.startsWith("../") || isAbsolute(rel)) {
    throw new Error("Feedback Manager: plugin directory is outside this project.");
  }
  return {
    "chat.message": async (input, output) => {
      let result;
      try {
        const { stdout } = await run("uv", ["run", "--project", skill, join(skill, "adapters/bootstrap.py"), "--project", project], {
          cwd: project, timeout: 20000, maxBuffer: 4 * 1024 * 1024,
        });
        result = JSON.parse(stdout);
        if (result.status !== "ok" || typeof result.data?.context !== "string") throw new Error("invalid result");
      } catch {
        const message = "Feedback Manager: retrieval failed (HOST_RETRIEVAL_FAILED). Check uv, Python and .feedback-manager/errors.jsonl.";
        try {
          await mkdir(join(project, ".feedback-manager"), { recursive: true });
          const log = join(project, ".feedback-manager/host-errors.jsonl");
          try {
            if ((await stat(log)).size >= 1024 * 1024) await rename(log, log + ".1");
          } catch (error) { if (error.code !== "ENOENT") throw error; }
          await appendFile(log, JSON.stringify({ timestamp: new Date().toISOString(), operation: "opencode_hook", code: "HOST_RETRIEVAL_FAILED", message }) + "\n");
        } catch { console.error("Feedback Manager: LOG_WRITE_FAILED"); }
        console.error(message);
        // Throwing aborts message preparation in the target version.
        throw new Error(message);
      }
      if (result.data.policy?.mode === "off") return;
      output.parts.push({
        id: "prt_" + randomUUID().replaceAll("-", ""),
        sessionID: input.sessionID, messageID: output.message.id,
        type: "text", text: result.data.context, synthetic: true,
      });
      try {
        await client.tui.showToast({ body: { title: "Feedback Manager", message: "Project preferences and tags loaded", variant: "info" } });
      } catch {
        // Headless clients may not display toasts; context also requests a brief disclosure.
        console.error("Feedback Manager: project preferences and tags loaded (toast unavailable).");
      }
    },
  };
};
