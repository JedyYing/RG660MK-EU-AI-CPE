"""Time one headless round: stamps when each event lands, so startup vs inference
vs tool execution can be separated. Usage: python trace_round.py [task-kind]"""
import json
import os
import subprocess
import sys
import time

kind = sys.argv[1] if len(sys.argv) > 1 else "ls"
TASKS = {
    "disk": r"请调用 pwsh 工具执行：Get-Date; Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free。然后用一句中文总结 C: 盘的已用与剩余空间（用 GB 表示）。必须先调用工具再回答。",
    "ls": r"请调用 pwsh 工具执行：Get-ChildItem C:\AI -Directory | Select-Object -ExpandProperty Name。然后用一句中文说出这些目录名称。必须先调用工具再回答。",
}
task = TASKS[kind]
out = rf"C:/AI/logs/trace-{kind}.jsonl"
env = dict(os.environ, DSH_HOME=r"C:/AI/dsh-home", LOCAL_LLM_KEY="sk-local")
NODE = r"C:/Users/jedy.ying/AppData/Local/hermes/tools/node-26.12.1-win32-x64/node.exe"
BIN = r"C:/Users/jedy.ying/dsh-runtime/npm-global/node_modules/@deepseek-ai/dsh/lib/bin.js"

t0 = time.time()
with open(out, "w", encoding="utf-8") as fh:
    p = subprocess.Popen([NODE, BIN, "--profile", "thinkpad-local", "--json", task],
                         cwd=r"C:/AI/dsh", env=env, stdin=subprocess.DEVNULL,
                         stdout=fh, stderr=subprocess.STDOUT)
    seen = 0
    while p.poll() is None:
        try:
            with open(out, "r", encoding="utf-8", errors="replace") as f:
                lines = [l for l in f if l.strip().startswith("{")]
        except OSError:
            lines = []
        while seen < len(lines):
            try:
                e = json.loads(lines[seen])
                label = e.get("type")
                if label == "status":
                    label = f"status/{e.get('phase')}"
                elif label == "tool_call":
                    label = f"tool_call/{e.get('tool')}"
                print(f"{time.time()-t0:6.2f}s  {label}", flush=True)
            except Exception:
                pass
            seen += 1
        time.sleep(0.1)
    print(f"{time.time()-t0:6.2f}s  process exited rc={p.returncode}", flush=True)
