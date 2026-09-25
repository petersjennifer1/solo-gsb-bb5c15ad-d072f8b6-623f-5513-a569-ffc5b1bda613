"""临时供水跨接预览 API 端到端测试: FastAPI TestClient + 临时 SQLite。

重点验证: 独立 API 可用、结果字段完整、录入校验 422、以及**只读**——
不修改当前管网、不写入历史方案、不改变原方案。
"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import sys  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
from demo import DEMO_NETWORK  # noqa: E402

_client_cm = TestClient(main.app)
client = _client_cm.__enter__()  # 进入生命周期: lifespan 建表

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1


# 备援方案管网: 冗余关阀 V1+V2 使普通节点 M,X 断水且与漏点分离
NET_FO = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "X", "role": "normal"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p2", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "p4", "endpoint_a": "M", "endpoint_b": "X"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 1},
        {"id": "V2", "segment_id": "p2", "cost": 2},
    ],
}
CANDS = [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "M", "cost": 6},
    {"id": "J2", "endpoint_a": "H", "endpoint_b": "X", "cost": 4},
    {"id": "J3", "endpoint_a": "S", "endpoint_b": "L", "cost": 1},
]

# 健康检查
r = client.get("/api/health")
check(r.status_code == 200, "health")

# ---- 备援可行方案上的跨接预览 ----
r = client.post("/api/plan-failover", json=NET_FO)
check(r.status_code == 200, f"failover plan {r.status_code}")
fo = r.json()
check(fo["feasible"] and fo["closed_valves"] == ["V1", "V2"], "failover saved")
pid = fo["id"]
before = client.get(f"/api/plans/{pid}").json()
plans_before = client.get("/api/plans?limit=100").json()["plans"]
net_before = client.get("/api/network").json()

r = client.post(f"/api/plans/{pid}/jumpers", json={"candidates": CANDS})
check(r.status_code == 200, f"jumpers status {r.status_code} {r.text}")
res = r.json()
check(res["ok"] and res["plan_id"] == pid, "jumpers meta")
check(res["solver"] == "failover", "jumpers solver field")
check(res["closed_valves"] == ["V1", "V2"], "jumpers closed valves")
check(res["selected_ids"] == ["J2"], f"selected {res['selected_ids']}")
check(res["selected_jumpers"] == [
    {"id": "J2", "endpoint_a": "H", "endpoint_b": "X", "cost": 4}],
    "selected detail")
check(res["lay_cost_total"] == 4, "lay cost")
check(res["restored_nodes"] == ["M", "X"] and res["restored_count"] == 2,
      "restored")
check(res["disconnected_normal_before"] == ["M", "X"], "before list")
check(res["still_disconnected_normal"] == [], "still list")
check(res["candidate_count"] == 3 and res["combination_count"] == 8, "comb")
check(res["feasible_combination_count"] == 4, "feasible comb")
st_m = [n for n in res["node_status"] if n["id"] == "M"][0]
st_l = [n for n in res["node_status"] if n["id"] == "L"][0]
check(st_m["status"] == "supplied" and st_l["status"] == "isolated",
      "node statuses")
seg_j2 = [s for s in res["segment_status"] if s["id"] == "J2"]
check(seg_j2 and seg_j2[0]["is_jumper"] and seg_j2[0]["status"] == "flowing",
      "jumper segment status")
check("不修改" in res["message"], "readonly note")

# ---- 只读: 原方案、历史列表、当前管网均未改变 ----
check(client.get(f"/api/plans/{pid}").json() == before, "plan unchanged")
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "no plan saved by jumpers")
check(client.get("/api/network").json() == net_before, "network untouched")

# ---- 常规方案(孤岛普通节点)上的跨接预览 ----
NET_ISLAND = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
        {"id": "X", "role": "normal"}, {"id": "Y", "role": "normal"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
        {"id": "p2", "endpoint_a": "S", "endpoint_b": "H"},
        {"id": "p3", "endpoint_a": "X", "endpoint_b": "Y"},
    ],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 1}],
}
r = client.post("/api/plan", json=NET_ISLAND)
base_plan = r.json()
check(base_plan["feasible"] and base_plan["solver"] == "base", "base saved")
bid = base_plan["id"]
r = client.post(f"/api/plans/{bid}/jumpers",
                json={"candidates": [
                    {"id": "J1", "endpoint_a": "S", "endpoint_b": "X",
                     "cost": 5}]})
res = r.json()
check(r.status_code == 200 and res["solver"] == "base", "base jumpers meta")
check(res["selected_ids"] == ["J1"] and res["restored_nodes"] == ["X", "Y"],
      "island restored")

# ---- 空方案: 示例管网没有断水的普通节点 ----
r = client.post("/api/plan", json=DEMO_NETWORK)
demo_plan = r.json()
did = demo_plan["id"]
r = client.post(f"/api/plans/{did}/jumpers",
                json={"candidates": [
                    {"id": "J1", "endpoint_a": "A", "endpoint_b": "B",
                     "cost": 3}]})
res = r.json()
check(r.status_code == 200 and res["selected_ids"] == [], "demo empty")
check(res["lay_cost_total"] == 0 and res["restored_count"] == 0, "demo zero")
check("空方案" in res["message"], "demo empty message")

# ---- 空请求体: 无候选 -> 空方案 ----
r = client.post(f"/api/plans/{bid}/jumpers", json={})
res = r.json()
check(r.status_code == 200 and res["selected_ids"] == []
      and res["combination_count"] == 1, "empty body")

# ---- 方案不存在 ----
r = client.post("/api/plans/9999/jumpers", json={"candidates": []})
check(r.status_code == 404, f"404 got {r.status_code}")

# ---- 不可行方案: 不能规划跨接 ----
BAD = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
        {"id": "p2", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [],
}
r = client.post("/api/plan", json=BAD)
bad_plan = r.json()
check(bad_plan["feasible"] is False, "infeasible plan saved")
bad_id = bad_plan["id"]
r = client.post(f"/api/plans/{bad_id}/jumpers", json={"candidates": []})
check(r.status_code == 422, f"infeasible jumpers {r.status_code}")
errs = r.json()["detail"]["errors"]
check(any("不可行" in e and str(bad_id) in e for e in errs),
      f"infeasible msg {errs}")

# ---- 录入校验: 422 + detail.errors ----
def expect_422(body, keys):
    r = client.post(f"/api/plans/{bid}/jumpers", json=body)
    check(r.status_code == 422, f"422 for {keys}: got {r.status_code} {r.text}")
    errs = r.json()["detail"]["errors"]
    joined = " | ".join(errs)
    for key in keys:
        check(key in joined, f"error contains {key!r}; got: {errs}")


# 候选超过 6 条
expect_422({"candidates": [
    {"id": f"J{i}", "endpoint_a": "S", "endpoint_b": "X", "cost": 1}
    for i in range(7)]}, ["超过上限 6"])
# 编号重复 / 与现有管段编号重复 / 缺少编号
expect_422({"candidates": [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "X", "cost": 1},
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "H", "cost": 1}]},
    ["跨接编号重复"])
expect_422({"candidates": [
    {"id": "p1", "endpoint_a": "S", "endpoint_b": "X", "cost": 1}]},
    ["与现有管段编号重复"])
expect_422({"candidates": [
    {"endpoint_a": "S", "endpoint_b": "X", "cost": 1}]}, ["缺少跨接编号"])
# 端点: 缺失 / 不存在 / 相同
expect_422({"candidates": [{"id": "J1", "endpoint_b": "X", "cost": 1}]},
           ["必须选择两个不同的现有节点"])
expect_422({"candidates": [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "ZZ", "cost": 1}]},
    ["端点 ZZ 不存在"])
expect_422({"candidates": [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "S", "cost": 1}]},
    ["两个端点不能相同"])
# 铺设费非法: 0 / 负 / 小数 / 字符串 / bool / null
for bad in [0, -1, 1.5, "5", True, None]:
    expect_422({"candidates": [
        {"id": "J1", "endpoint_a": "S", "endpoint_b": "X", "cost": bad}]},
        ["铺设费必须是正整数"])

# ---- 跨接预览未新增任何历史方案(只读) ----
plans = client.get("/api/plans?limit=100").json()["plans"]
ids = sorted(p["id"] for p in plans)
check(ids == sorted(set([pid, bid, did, bad_id])),
      f"plan ids {ids}")

print(f"jumpers API tests: {ok} checks passed")
