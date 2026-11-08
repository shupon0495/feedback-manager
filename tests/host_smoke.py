"""Opt-in real host smoke tests. No external model or credentials are needed.

Run: uv run python tests/host_smoke.py opencode|codex
Host authentication/model-driven behavior remains a separate acceptance check.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from feedback_manager.core import Store


def setup(project):
    subprocess.run([sys.executable, str(ROOT / "scripts/install.py"), str(project)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    for tag, marker in [(None, "FM_GENERAL_SENTINEL"), ("docs", "FM_TAGGED_SENTINEL")]:
        result = Store(project).execute({"op": "create", "guideline": marker, "original": "synthetic test fixture", "tags": {"existing": [], "create": [tag] if tag else [], "confirmed": True}})
        assert result["status"] == "ok", result


def opencode(project, scratch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "XDG_CONFIG_HOME": str(scratch / "config"), "XDG_DATA_HOME": str(scratch / "data"), "XDG_CACHE_HOME": str(scratch / "cache"), "XDG_STATE_HOME": str(scratch / "state"), "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true", "UV_PYTHON": sys.executable}
    base = f"http://127.0.0.1:{port}"

    def request(path, data=None):
        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    with (scratch / "opencode.log").open("w+") as log:
        process = subprocess.Popen([os.environ.get("OPENCODE_BIN", "opencode"), "serve", "--hostname", "127.0.0.1", "--port", str(port)], cwd=project, env=env, stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    request("/global/health")
                    break
                except (OSError, ValueError):
                    if process.poll() is not None:
                        raise RuntimeError("OpenCode exited before becoming ready")
                    time.sleep(0.1)
            else:
                raise RuntimeError("OpenCode startup timeout")
            session = request("/session", {})
            for turn in range(2):
                response = request(f"/session/{session['id']}/message", {"noReply": True, "parts": [{"type": "text", "text": f"Write docs, turn {turn}"}]})
                text = json.dumps(response)
                assert "FM_GENERAL_SENTINEL" in text, response
                assert "FM_TAGGED_SENTINEL" not in text, response
                assert "docs" in text
            # Execute the documented second-stage command; noReply does not run a model.
            cli = project / ".agents/skills/feedback-manager/scripts/feedback.py"
            selected = subprocess.run([sys.executable, str(cli), "--project", str(project)], input='{"op":"search","tags":["docs"]}', text=True, capture_output=True, check=True)
            assert "FM_TAGGED_SENTINEL" in selected.stdout
            db = project / ".feedback-manager/feedback.sqlite3"
            db.unlink()
            db.mkdir()
            try:
                request(f"/session/{session['id']}/message", {"noReply": True, "parts": [{"type": "text", "text": "error fixture"}]})
            except urllib.error.HTTPError as error:
                assert error.code >= 400
            else:
                raise AssertionError("Retrieval failure was not surfaced by the host")
            print(json.dumps({"host": "opencode", "version": subprocess.check_output([os.environ.get("OPENCODE_BIN", "opencode"), "--version"], text=True).strip(), "two_user_message_hooks": "passed", "persisted_context": "passed", "second_stage_cli": "passed", "retrieval_failure": "passed", "model_selected_tags": "NOT_TESTED", "visual_toast": "NOT_TESTED"}))
        except Exception:
            log.flush()
            log.seek(0)
            print(log.read()[-6000:], file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def codex(project, scratch):
    # Read host metadata only. Never bypass hook trust or modify the user's trust store.
    log = (scratch / "codex.log").open("w+")
    template = json.loads((ROOT / "adapters/hooks.json").read_text())["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    inline = 'hooks.UserPromptSubmit=[{hooks=[{type="command",command=' + json.dumps(template["command"]) + ',statusMessage=' + json.dumps(template["statusMessage"]) + ',timeout=30}]}]'
    process = subprocess.Popen(["codex", "app-server", "--stdio", "-c", inline], cwd=project, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
    responses = queue.Queue()
    threading.Thread(target=lambda: [responses.put(line) for line in process.stdout], daemon=True).start()
    try:
        def rpc(request_id, method, params):
            process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                item = json.loads(responses.get(timeout=max(0.1, deadline - time.monotonic())))
                if item.get("id") == request_id:
                    if "error" in item:
                        raise RuntimeError(item["error"])
                    return item["result"]
            raise RuntimeError("RPC timeout")
        rpc(1, "initialize", {"clientInfo": {"name": "feedback-manager-test", "version": "0.1"}, "capabilities": {"experimentalApi": True}})
        result = rpc(2, "hooks/list", {"cwds": [str(project)]})
        serialized = json.dumps(result)
        assert "userPromptSubmit" in serialized, result
        assert "Loading Feedback Manager preferences" in serialized, result
        entries = [hook for entry in result["data"] for hook in entry["hooks"] if hook.get("statusMessage") == template["statusMessage"]]
        assert entries and all(hook["trustStatus"] == "untrusted" for hook in entries), entries
        print(json.dumps({"host": "codex", "version": subprocess.check_output(["codex", "--version"], text=True).strip(), "inline_hook_schema": "passed", "hook_trust_status": [hook["trustStatus"] for hook in entries], "project_hook_discovery": "NOT_TESTED: project untrusted", "trusted_execution": "NOT_TESTED: requires explicit hook trust", "model_selected_tags": "NOT_TESTED"}))
    except Exception:
        log.flush()
        log.seek(0)
        print(log.read()[-6000:], file=sys.stderr)
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=["opencode", "codex"])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="fm real host ") as tmp:
        scratch = Path(tmp)
        project = scratch / "project"
        project.mkdir()
        setup(project)
        globals()[args.host](project, scratch)
