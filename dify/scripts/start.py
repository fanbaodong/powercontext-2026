"""Run from any directory: python scripts/start.py [--stop]. Never deletes data."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--stop", action="store_true")
args = p.parse_args()
cmd = ["docker", "compose", "-p", "grudge-shop", "-f", "docker-compose.yaml", "-f", "compose.shop.yaml"]
if args.stop:
    subprocess.run(cmd + ["stop"], cwd=ROOT / ".runtime/docker", check=True)
else:
    subprocess.run([sys.executable, str(ROOT / "scripts/prepare_deploy.py")], check=True)
    subprocess.run(cmd + ["config", "--quiet"], cwd=ROOT / ".runtime/docker", check=True)
    subprocess.run(cmd + ["up", "-d", "--build"], cwd=ROOT / ".runtime/docker", check=True)
    subprocess.run(cmd + ["exec", "-T", "ollama", "ollama", "pull", "qwen3:0.6b"], cwd=ROOT / ".runtime/docker", check=True)
    print("Open http://localhost:8088/install, create your local Dify account, then import grudge-shop.yml.")
    print("Set DSL environment variables using .runtime/import-settings.local.json. Keep this file private.")
