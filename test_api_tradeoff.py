"""权衡预览 API 端到端测试: FastAPI TestClient + 临时 SQLite。

重点验证: 独立 API /api/plan-tradeoff 可用、Pareto 结果字段完整、
录入校验 422、无解返回、以及**只读**——不修改当前管网、不写入历史方案。
"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import sys  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

client = TestClient(main.app).__enter__()  # 进入生命周期: lifespan 建表

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1


NET = {
    "nodes": [
        {"id": "S", "role": "inlet"},
        {"id": "A", "role": "normal"},
        {"id": "B", "role": "normal"},
        {"id": "C", "role": "normal"},
        {"id": "L1", "role": "leak"},
        {"id": "L2", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "e0", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "e1", "endpoint_a": "A", "endpoint_b": "B"},
        {"id": "e2", "endpoint_a": "B", "endpoint_b": "L1"},
        {"id": "e3", "endpoint_a": "A", "endpoint_b": "C"},
        {"id": "e4", "endpoint_a": "C", "endpoint_b": "L2"},
        {"id": "e5", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "e1", "cost": 1},
        {"id": "V2", "segment_id": "e2", "cost": 10},
        {"id": "V3", "segment_id": "e3", "cost": 1},
        {"id": "V4", "segment_id": "e4", "cost": 10},
    ],
}
W = {"A": 100, "B": 5, "C": 7}

# ---- 基本求解: 三个非支配方案 ----
r = client.post("/api/plan-tradeoff", json={"network": NET, "weights": W})
check(r.status_code == 200, f"status {r.status_code} {r.text}")
res = r.json()
check(res["feasible"] and res["solver"] == "tradeoff", "feasible/tradeoff")
check(res["pareto_count"] == 3, f"pareto {res['pareto_count']}")
front = [(o["total_cost"], o["disconnected_impact"], o["closed_valves"])
         for o in res["options"]]
check(front == [(2, 12, ["V1", "V3"]), (11, 5, ["V1", "V4"]),
                (20, 0, ["V2", "V4"])], f"front {front}")
check(res["weights"] == W, "weights echoed")
o0 = res["options"][0]
check(o0["rank"] == 1 and o0["newly_disconnected_nodes"] == ["B", "C"],
      f"opt1 {o0['newly_disconnected_nodes']}")
check({(x["id"], x["weight"]) for x in o0["newly_disconnected_weights"]}
      == {("B", 5), ("C", 7)}, "opt1 weights detail")
check(len(o0["node_status"]) == 7 and len(o0["segment_status"]) == 6,
      "statuses present")
check("只读" in res["message"] and "不修改管网" in res["message"],
      "readonly note")

# ---- 只读: 管网与历史方案均未改变 ----
net_before = client.get("/api/network").json()
plans_before = client.get("/api/plans?limit=100").json()["plans"]
r = client.post("/api/plan-tradeoff", json={"network": NET, "weights": W})
check(r.status_code == 200, "again ok")
check(client.get("/api/network").json() == net_before, "network untouched")
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "no plan saved by tradeoff")

# ---- network 省略时使用已保存管网 ----
r = client.put("/api/network", json=NET)
check(r.status_code == 200, "save net")
r = client.post("/api/plan-tradeoff", json={"weights": W})
check(r.status_code == 200 and r.json()["pareto_count"] == 3,
      "saved network used")
r = client.post("/api/plan-tradeoff", json={})
check(r.status_code == 422, f"missing weights -> 422, got {r.status_code}")
check("缺少普通节点的用水权重" in r.text, "missing weights message")

# ---- 无解: 200 + feasible:false ----
BAD = {"nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"}],
       "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}],
       "valves": []}
r = client.post("/api/plan-tradeoff", json={"network": BAD, "weights": {}})
check(r.status_code == 200, f"infeasible status {r.status_code}")
res = r.json()
check(res["feasible"] is False and res["solver"] == "tradeoff"
      and res["reason_code"] == "UNCUTTABLE_LEAK_PATH", f"infeasible {res}")
# 无解预览同样不入库
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "infeasible tradeoff not saved")

# ---- 录入校验: 422 + detail.errors ----
def expect_422(body, keys):
    rr = client.post("/api/plan-tradeoff", json=body)
    check(rr.status_code == 422, f"422 for {keys}: got {rr.status_code} {rr.text}")
    errs = rr.json()["detail"]["errors"]
    joined = " | ".join(errs)
    for key in keys:
        check(key in joined, f"error contains {key!r}; got: {errs}")


expect_422({"network": NET, "weights": {}}, ["缺少普通节点的用水权重"])
expect_422({"network": NET, "weights": {"A": 100, "B": 5, "C": 7, "X": 1}},
           ["提供了非普通节点的用水权重(多余编号)"])
expect_422({"network": NET, "weights": {"A": 100, "B": 5, "C": 7, "L1": 1}},
           ["多余编号", "L1"])
for bad in [0, -1, 1.5, "3", True, None]:
    expect_422({"network": NET,
                "weights": {"A": bad, "B": 5, "C": 7}},
               ["用水权重必须是正整数"])
# weights 不是对象: Pydantic 请求体校验直接 422
rr = client.post("/api/plan-tradeoff", json={"network": NET, "weights": []})
check(rr.status_code == 422, f"weights list -> 422, got {rr.status_code}")
# 管网本身非法(端点不存在)
expect_422({"network": {"nodes": [], "segments": [
    {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}], "valves": []},
    "weights": {}}, ["端点"])

# ---- 示例管网: 唯一非支配方案 V2+V4(代价 7, 影响 0), 且不入库 ----
client.post("/api/demo")
r = client.post("/api/plan-tradeoff",
                json={"weights": {"A": 11, "B": 22}})
check(r.status_code == 200, f"demo status {r.status_code} {r.text}")
res = r.json()
check(res["feasible"] and res["pareto_count"] == 1, f"demo pareto {res.get('pareto_count')}")
o = res["options"][0]
check(o["closed_valves"] == ["V2", "V4"] and o["total_cost"] == 7
      and o["disconnected_impact"] == 0
      and o["newly_disconnected_nodes"] == [], f"demo opt {o}")
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "demo tradeoff not saved")

# 健康检查仍可用
check(client.get("/api/health").json() == {"ok": True}, "health")

print(f"tradeoff API tests: {ok} checks passed")
