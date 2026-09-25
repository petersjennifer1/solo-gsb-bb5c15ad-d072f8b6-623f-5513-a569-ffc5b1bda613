"""权衡预览 API 端到端测试: FastAPI TestClient + 临时 SQLite。

重点验证: 独立 API 可用、结果字段完整、录入校验 422、以及**只读**——
不修改当前管网、不写入历史方案。
"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import sys  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

_client_cm = TestClient(main.app)
client = _client_cm.__enter__()  # 进入生命周期: lifespan 建表

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1


# 双链路权衡网: 每条漏点通路可选「便宜但断水」或「贵但不断水」的阀门
NET_T = {
    "nodes": [
        {"id": "S", "role": "inlet"},
        {"id": "A", "role": "normal"},
        {"id": "B", "role": "normal"},
        {"id": "L1", "role": "leak"},
        {"id": "L2", "role": "leak"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "p2", "endpoint_a": "A", "endpoint_b": "L1"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "B"},
        {"id": "p4", "endpoint_a": "B", "endpoint_b": "L2"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 2},
        {"id": "V2", "segment_id": "p2", "cost": 5},
        {"id": "V3", "segment_id": "p3", "cost": 2},
        {"id": "V4", "segment_id": "p4", "cost": 5},
    ],
}
W = {"A": 3, "B": 3}
FRONT = [[4, 6, ["V1", "V3"]], [7, 3, ["V1", "V4"]], [10, 0, ["V2", "V4"]]]


def front_of(res):
    return [[p["total_cost"], p["impact"], p["closed_valves"]]
            for p in res["plans"]]


# ---- 基本求解: 显式提交当前编辑管网 + 权重 ----
r = client.post("/api/plan-tradeoff", json={"network": NET_T, "weights": W})
check(r.status_code == 200, f"tradeoff status {r.status_code} {r.text}")
res = r.json()
check(res["feasible"] and res["solver"] == "tradeoff", "tradeoff feasible")
check(front_of(res) == FRONT, f"tradeoff front {front_of(res)}")
check(res["plan_count"] == 3 and len(res["plans"]) == 3, "plan count")
check(res["subset_count"] == 16 and res["feasible_subset_count"] == 9,
      "subset counts")
check(res["weights"] == {"A": 3, "B": 3}, "weights echo")
check(res["normal_nodes"] == ["A", "B"], "normal nodes")
check(res["baseline_fed_normal"] == ["A", "B"], "baseline fed")
check(res["baseline_disconnected_normal"] == [], "baseline disconnected")
check("不写入" in res["message"], "readonly note in message")
p0 = res["plans"][0]
check(p0["closed_count"] == 2 and p0["newly_disconnected"] == ["A", "B"],
      "p0 newly")
check(p0["newly_disconnected_details"] == [
    {"id": "A", "label": "A", "weight": 3},
    {"id": "B", "label": "B", "weight": 3},
], "p0 details")
check(res["plans"][2]["newly_disconnected"] == [], "p2 newly empty")

# ---- 只读: 管网与历史方案均未改变 ----
net_before = client.get("/api/network").json()
plans_before = client.get("/api/plans?limit=100").json()["plans"]
r = client.post("/api/plan-tradeoff", json={"network": NET_T, "weights": W})
check(r.status_code == 200, "tradeoff again")
check(client.get("/api/network").json() == net_before, "network untouched")
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "no plan saved by tradeoff")

# ---- network 省略时使用已保存管网 ----
r = client.put("/api/network", json=NET_T)
check(r.status_code == 200, "save net")
r = client.post("/api/plan-tradeoff", json={"weights": W})
check(r.status_code == 200 and front_of(r.json()) == FRONT,
      "tradeoff with saved network")
r = client.post("/api/plan-tradeoff", json={})
check(r.status_code == 422, "empty weights -> 422 (missing normal weights)")
# 显式管网与已保存管网不同: 用请求体里的管网, 且不改已保存管网
NET_ONE = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
              {"id": "X", "role": "normal"}],
    "segments": [{"id": "q1", "endpoint_a": "S", "endpoint_b": "L"}],
    "valves": [{"id": "V1", "segment_id": "q1", "cost": 4}],
}
r = client.post("/api/plan-tradeoff",
                json={"network": NET_ONE, "weights": {"X": 7}})
res = r.json()
check(front_of(res) == [[4, 0, ["V1"]]], f"explicit net front {front_of(res)}")
check(res["baseline_disconnected_normal"] == ["X"], "explicit net baseline")
check(client.get("/api/network").json()["segments"][0]["id"] == "p1",
      "saved network still NET_T")

# ---- 无解: 200 + feasible:false + reason_code, 同样不入库 ----
BAD = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
              {"id": "A", "role": "normal"}],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}],
    "valves": [],
}
r = client.post("/api/plan-tradeoff",
                json={"network": BAD, "weights": {"A": 1}})
check(r.status_code == 200, f"infeasible status {r.status_code}")
res = r.json()
check(res["feasible"] is False and res["solver"] == "tradeoff", "infeasible")
check(res["reason_code"] == "UNCUTTABLE_LEAK_PATH", "reason code")
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "infeasible tradeoff not saved")
# 无进水点
r = client.post("/api/plan-tradeoff", json={
    "network": {"nodes": [{"id": "L", "role": "leak"},
                          {"id": "A", "role": "normal"}],
                "segments": [], "valves": []},
    "weights": {"A": 1}})
res = r.json()
check(res["feasible"] is False and res["reason_code"] == "NO_INLET",
      "no inlet")


# ---- 录入校验: 422 + detail.errors ----
def expect_422(body, keys):
    r = client.post("/api/plan-tradeoff", json=body)
    check(r.status_code == 422,
          f"422 for {keys}: got {r.status_code} {r.text}")
    errs = r.json()["detail"]["errors"]
    joined = " | ".join(errs)
    for key in keys:
        check(key in joined, f"error contains {key!r}; got: {errs}")


# 缺少普通节点权重 / 多余编号(非普通节点或不存在)
expect_422({"network": NET_T, "weights": {"A": 3}},
           ["缺少普通节点的用水权重", "B"])
expect_422({"network": NET_T, "weights": {"A": 3, "B": 3, "S": 1}},
           ["非普通节点", "S"])
expect_422({"network": NET_T, "weights": {"A": 3, "B": 3, "ZZ": 1}},
           ["非普通节点", "ZZ"])
# 权重非正整数: 0 / 负 / 小数 / 字符串 / 布尔 / null
for bad in [0, -2, 1.5, "3", True, None]:
    expect_422({"network": NET_T, "weights": {"A": bad, "B": 3}},
               ["用水权重必须是正整数"])
# 管网本身非法(端点不存在)也返回 422
expect_422({"network": {"nodes": [], "segments": [
    {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}], "valves": []},
    "weights": {}}, ["端点"])

# ---- 示例管网上的权衡预览 ----
r = client.post("/api/demo")
check(r.status_code == 200, "demo restored")
r = client.post("/api/plan-tradeoff", json={"weights": {"A": 2, "B": 5}})
res = r.json()
check(res["feasible"] and front_of(res) == [[7, 0, ["V2", "V4"]]],
      f"demo tradeoff {front_of(res)}")
check(res["feasible_subset_count"] == 1, "demo feasible count")
# 预览不写入历史方案
check(all(p.get("solver") != "tradeoff"
          for p in client.get("/api/plans?limit=100").json()["plans"]),
      "tradeoff never in history")

print(f"tradeoff API tests: {ok} checks passed")
