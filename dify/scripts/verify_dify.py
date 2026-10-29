"""Import and exercise the actual local Dify engine; stores sanitized evidence.

Use --setup only on a fresh local instance to create a random local admin.
Existing credentials must be in ignored .runtime/dify-admin.local.json.
"""
import argparse
import json
from pathlib import Path
import secrets
import uuid
import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--base", default="http://127.0.0.1:8088")
p.add_argument("--setup", action="store_true")
args = p.parse_args()
base = args.base.rstrip("/") + "/console/api"
if not args.base.startswith(("http://127.0.0.1:", "http://localhost:")):
    raise ValueError("This verification helper is restricted to the local demo")
credentials_file = ROOT / ".runtime/dify-admin.local.json"
client = httpx.Client(timeout=180, trust_env=False)
setup = client.get(base+"/setup")
setup.raise_for_status()
if setup.json()["step"] != "finished":
    if not args.setup:
        raise RuntimeError("Create a local account first or pass --setup for this new demo")
    if not credentials_file.exists():
        credentials_file.write_text(json.dumps({"email":"shop-admin@example.com", "password":"Gs9"+secrets.token_urlsafe(24)},indent=2),encoding="utf-8")
        credentials_file.chmod(0o600)
    credentials = json.loads(credentials_file.read_text())
    r = client.post(base+"/setup", json={**credentials, "name":"杂货铺本地管理员"})
    r.raise_for_status()
else:
    if not credentials_file.exists():
        raise RuntimeError("Existing Dify instance: supply your own local login file; no account changes were made")
credentials = json.loads(credentials_file.read_text())
login = client.post(base+"/login", json={**credentials, "remember_me":False})
login.raise_for_status()
client.headers["Authorization"] = "Bearer " + login.json()["data"]["access_token"]
document = yaml.safe_load((ROOT / "grudge-shop.yml").read_text(encoding="utf-8"))
settings = json.loads((ROOT / ".runtime/import-settings.local.json").read_text())
for var in document["workflow"]["environment_variables"]:
    var["value"] = settings[var["name"]]
payload = dict(mode="yaml-content", yaml_content=yaml.safe_dump(document,allow_unicode=True,sort_keys=False))
app_file = ROOT / ".runtime/dify-app.local.json"
if app_file.exists():
    payload["app_id"] = json.loads(app_file.read_text())["app_id"]
response = client.post(base+"/apps/imports", json=payload)
if response.status_code not in (200,202):
    # Do not print submitted DSL or environment secrets on validation errors.
    raise RuntimeError("Dify import failed: " + response.text[:800])
imported = response.json()
if imported.get("status") == "pending":
    r = client.post(base+"/apps/imports/"+imported["id"]+"/confirm")
    r.raise_for_status()
    imported = r.json()
app_id = imported["app_id"]
app_file.write_text(json.dumps({"app_id":app_id},indent=2),encoding="utf-8")
print("DSL imported into real Dify", app_id, flush=True)

player = "dify_"+uuid.uuid4().hex[:12]
runs = []
samples = [
    (player, "prank", "我把价签全换成了谜语！", 1, 43, -2, []),
    (player, "visit", "老板，你还记得我做过什么吗？", 2, 43, -2, []),
    (player, "apologize", "请你喝汽水赔礼。", 3, 38, 0, []),
    (player, "help", "我来整理货架。", 4, 43, 1, []),
    (player, "buy", "我要会打嗝的月亮！", 5, 25, 1, ["会打嗝的月亮"]),
    (player + "_new", "visit", "第一次来，请多关照。", 1, 40, 0, []),
]
for current_player, action, message, turn, coins, trust, inventory in samples:
    events = []
    with client.stream("POST",base+f"/apps/{app_id}/workflows/draft/run",
                       json={"inputs":{"player_id":current_player,"action":action,"message":message},"files":[]}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    finish = next((e for e in reversed(events) if e.get("event")=="workflow_finished"),None)
    # Save final output and node status only: raw HTTP node traces may contain tokens.
    record = {"action":action,"player_id":current_player,
              "nodes":[{"node_id":e.get("data",{}).get("node_id"),"status":e.get("data",{}).get("status"),
                        "error":e.get("data",{}).get("error")} for e in events if e.get("event")=="node_finished"],
              "finish": finish.get("data") if finish else None}
    runs.append(record)
    evidence = {"scope":"Actual Dify 1.9.1 import and workflow execution", "app_id":app_id,"runs":runs}
    (ROOT / "evidence/dify-verification.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
    if not finish or finish["data"]["status"] != "succeeded":
        raise RuntimeError("Workflow did not succeed; see sanitized evidence/dify-verification.json")
    answer=finish["data"]["outputs"]["answer"]
    if "模型暂不可用" in answer:
        raise RuntimeError("Workflow used fallback; load the model before claiming AI verification")
    model_event = next(e["data"] for e in events if e.get("event")=="node_finished" and e["data"].get("node_id")=="model")
    generated = json.loads(model_event["outputs"]["body"])
    assert generated.get("done") and generated.get("eval_count",0)>0, "No confirmed model generation"
    record["ollama_generation"] = {k:generated.get(k) for k in
        ("model","done","done_reason","prompt_eval_count","eval_count","total_duration")}
    if current_player==player and action=="visit" and "价签" not in answer:
        raise RuntimeError("Previous event missing from retrieved memory output")
    state_response = client.get("http://127.0.0.1:8099/players/"+current_player,
                               headers={"Authorization":"Bearer "+settings["SHOP_API_TOKEN"]})
    state_response.raise_for_status()
    state = state_response.json()
    expected = {"turn":turn,"coins":coins,"trust":trust,"inventory":inventory}
    assert all(state[k]==v for k,v in expected.items()), "Persisted seekdb state differs from rules"
    assert f"| {turn} | {coins} | {trust} |" in answer, "Displayed account differs from persisted state"
    if current_player!=player:
        assert "首次来店，账本里还没有旧事" in answer
    record["persisted_seekdb_state"] = state
    record["state_assertions"] = "passed"
    (ROOT / "evidence/dify-verification.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
    print("PASS", action, finish["data"].get("id"), flush=True)
r = client.post(base+f"/apps/{app_id}/workflows/publish",json={"marked_name":"validated","marked_comment":"seekdb memory verification"})
r.raise_for_status()
print("Published the validated local workflow. Admin credentials remain in ignored .runtime/.")
