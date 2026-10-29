"""Generate the submitted Dify 1.9.1 workflow from reviewable Python nodes."""
from pathlib import Path
import yaml
import uuid

ROOT = Path(__file__).resolve().parents[1]
nodes = []


def node(id, type, title, **data):
    nodes.append(dict(id=id, type="custom", position={"x": len(nodes)*330, "y": 200},
                      positionAbsolute={"x": len(nodes)*330, "y": 200}, width=280, height=100,
                      sourcePosition="right", targetPosition="left",
                      data=dict(type=type, title=title, desc="", selected=False, **data)))


def code(id, title, source, variables, outputs):
    node(id, "code", title, code_language="python3", code=(ROOT / "workflow_code" / source).read_text(encoding="utf-8-sig"),
         variables=[{"variable": k, "value_selector": v} for k,v in variables.items()],
         outputs={k: {"type": v, "children": None} for k,v in outputs.items()})


def http(id, title, url, body, headers, optional=False):
    data = dict(method="post", url=url, authorization={"type": "no-auth"},
                headers=headers, params="", body={"type": "json", "data": [{"key": "", "type": "text", "value": body}]},
                timeout={"connect": 10, "read": 120, "write": 20}, ssl_verify=True,
                retry_config={"retry_enabled": not optional, "max_retries": 1, "retry_interval": 1000})
    if optional:
        data.update(error_strategy="default-value", default_value=[{"key": "body", "type": "string", "value": ""}])
    node(id, "http-request", title, **data)


node("start", "start", "走进记仇杂货铺", variables=[
    dict(variable="player_id", label="玩家代号（同一代号继续存档）", type="text-input", required=True, max_length=64, options=[]),
    dict(variable="action", label="动作：visit看看 / prank恶作剧 / help帮忙 / apologize道歉 / buy购买", type="select", required=True,
         max_length=48, options=["visit", "prank", "help", "apologize", "buy"]),
    dict(variable="message", label="想对老葛说的话（可留空）", type="paragraph", required=False, max_length=500, options=[]),
])
code("request", "整理玩家请求", "request.py",
     dict(player_id=["start","player_id"], action=["start","action"], message=["start","message"], request_id=["sys","workflow_run_id"]),
     {"body": "string"})
http("turn", "读取旧账并写入本轮 · seekdb", "{{#env.SHOP_BASE_URL#}}/turn", "{{#request.body#}}",
     "Content-Type:application/json\nAuthorization:Bearer {{#env.SHOP_API_TOKEN#}}")
code("prepare", "核对账本并准备角色上下文", "prepare.py",
     dict(body=["turn","body"], status_code=["turn","status_code"], model=["env","OLLAMA_MODEL"]),
     {"game":"string", "model_body":"string"})
http("model", "本地模型 · 老葛翻旧账", "{{#env.OLLAMA_BASE_URL#}}/api/generate", "{{#prepare.model_body#}}", "Content-Type:application/json", True)
code("render", "展示对白与真实账本", "render.py", dict(game=["prepare","game"], body=["model","body"]), {"answer":"string"})
node("end", "end", "本轮结果", outputs=[{"variable":"answer", "value_selector":["render","answer"]}])
edges = []
for a,b in zip(nodes, nodes[1:]):
    edges.append(dict(id=a["id"]+"-"+b["id"], type="custom", source=a["id"], target=b["id"],
                      sourceHandle="source", targetHandle="target",
                      data={"sourceType":a["data"]["type"], "targetType":b["data"]["type"], "isInIteration":False, "isInLoop":False}))
envs = [("SHOP_BASE_URL","http://shop:8099","string"), ("SHOP_API_TOKEN","","secret"),
        ("OLLAMA_BASE_URL","http://ollama:11434","string"), ("OLLAMA_MODEL","qwen3:0.6b","string")]
app = dict(version="0.4.0", kind="app", dependencies=[],
    app=dict(name="记仇杂货铺 · 老葛的奇怪账本", description="三件怪东西，一个记仇店主。seekdb 留住你的每次恶作剧，Dify 让店主跨次翻旧账。",
             mode="workflow", icon="🏪", icon_background="#FFF4D6", use_icon_as_answer_icon=False),
    workflow=dict(conversation_variables=[], environment_variables=[dict(id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "grudge-shop."+k)), name=k, value=v, value_type=t, description="") for k,v,t in envs],
        features=dict(file_upload={"enabled":False}, opening_statement="", retriever_resource={"enabled":False},
                      sensitive_word_avoidance={"enabled":False}, speech_to_text={"enabled":False}, suggested_questions=[],
                      suggested_questions_after_answer={"enabled":False}, text_to_speech={"enabled":False,"language":"","voice":""}),
        graph=dict(nodes=nodes, edges=edges, viewport={"x":0,"y":0,"zoom":0.65})))
(ROOT / "grudge-shop.yml").write_text(yaml.safe_dump(app, allow_unicode=True, sort_keys=False), encoding="utf-8")
print("Generated grudge-shop.yml")
