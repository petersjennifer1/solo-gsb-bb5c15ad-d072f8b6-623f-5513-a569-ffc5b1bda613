"""选址预览 API 端到端测试: FastAPI TestClient + 临时 SQLite。

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
from demo import DEMO_NETWORK  # noqa: E402

_client_cm = TestClient(main.app)
client = _client_cm.__enter__()  # 进入生命周期: lifespan 建表

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1


# 需要一个候选才能修复的管网: 漏点唯一通路 p2 无阀门
NET = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "L", "role": "leak"}, {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p2", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 100}],
}
CANDS = [{"id": "NV1", "segment_id": "p2", "install_cost": 1, "close_cost": 1}]

# ---- 基本求解: 安装费优先 -> 不装新阀, 关原阀 V1 ----
r = client.post("/api/plan-siting",
                json={"network": NET, "candidates": CANDS})
check(r.status_code == 200, f"siting status {r.status_code} {r.text}")
res = r.json()
check(res["feasible"] and res["solver"] == "siting", "siting feasible")
check(res["closed_valves"] == ["V1"], f"install-first {res['closed_valves']}")
check(res["install_cost_total"] == 0 and res["close_cost_total"] == 100,
      "siting totals")
check(res["new_valves"] == [] and res["new_segment_ids"] == [], "no new valves")
check(res["candidate_count"] == 1, "candidate count")
check(res["node_status"] and res["segment_status"], "statuses present")
check("不写入" in res["message"], "readonly note in message")

# ---- 必须增设新阀的情形 ----
NET_NO_VALVE = {**NET, "valves": []}
r = client.post("/api/plan-siting",
                json={"network": NET_NO_VALVE, "candidates": CANDS})
res = r.json()
check(res["feasible"] and res["closed_valves"] == ["NV1"], "must add NV1")
check(res["install_cost_total"] == 1 and res["close_cost_total"] == 1,
      "new valve billed")
check(res["new_valves"] == [{"id": "NV1", "segment_id": "p2",
                             "install_cost": 1, "close_cost": 1}], "new valves")
det = res["closed_valve_details"][0]
check(det["is_new"] and det["install_cost"] == 1, "closed detail new")

# ---- 只读: 管网与历史方案均未改变 ----
net_before = client.get("/api/network").json()
plans_before = client.get("/api/plans?limit=100").json()["plans"]
r = client.post("/api/plan-siting",
                json={"network": NET_NO_VALVE, "candidates": CANDS})
check(r.status_code == 200, "siting again")
check(client.get("/api/network").json() == net_before, "network untouched")
plans_after = client.get("/api/plans?limit=100").json()["plans"]
check(plans_after == plans_before, "no plan saved by siting")

# ---- network 省略时使用已保存管网 ----
r = client.put("/api/network", json=NET_NO_VALVE)
check(r.status_code == 200, "save net")
r = client.post("/api/plan-siting", json={"candidates": CANDS})
check(r.status_code == 200 and r.json()["closed_valves"] == ["NV1"],
      "siting with saved network")
r = client.post("/api/plan-siting", json={})
check(r.status_code == 200, "siting empty body uses saved network")
res = r.json()
# 已保存管网无任何阀门, 无候选 -> 候选范围内不可修复
check(res["feasible"] is False and res["solver"] == "siting",
      "empty body infeasible")
check("候选范围内不可修复" in res["message"], "empty body unfixable note")

# ---- 无解: 候选范围内不可修复 ----
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
r = client.post("/api/plan-siting",
                json={"network": BAD,
                      "candidates": [{"id": "NV1", "segment_id": "p2",
                                      "install_cost": 5, "close_cost": 1}]})
check(r.status_code == 200, f"infeasible status {r.status_code}")
res = r.json()
check(res["feasible"] is False and res["solver"] == "siting", "infeasible")
check(res["reason_code"] == "UNCUTTABLE_LEAK_PATH", "reason code")
check("候选范围内不可修复" in res["message"], "unfixable note")
# 无解预览同样不入库
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "infeasible siting not saved")

# ---- 录入校验: 422 + detail.errors ----
def expect_422(body, keys):
    r = client.post("/api/plan-siting", json=body)
    check(r.status_code == 422, f"422 for {keys}: got {r.status_code} {r.text}")
    errs = r.json()["detail"]["errors"]
    joined = " | ".join(errs)
    for key in keys:
        check(key in joined, f"error contains {key!r}; got: {errs}")


# 候选超过 6 个
expect_422({"network": NET_NO_VALVE, "candidates": [
    {"id": f"NV{i}", "segment_id": "p2", "install_cost": 1, "close_cost": 1}
    for i in range(7)]}, ["超过上限 6"])
# 新阀编号与原阀重复
expect_422({"network": NET, "candidates": [
    {"id": "V1", "segment_id": "p2", "install_cost": 1, "close_cost": 1}]},
    ["阀门编号重复"])
# 候选选在已有阀门的管段
expect_422({"network": NET, "candidates": [
    {"id": "NV1", "segment_id": "p1", "install_cost": 1, "close_cost": 1}]},
    ["同时绑定"])
# 管段不存在
expect_422({"network": NET_NO_VALVE, "candidates": [
    {"id": "NV1", "segment_id": "pX", "install_cost": 1, "close_cost": 1}]},
    ["管段 pX 不存在"])
# 安装费非法
for bad in [0, -1, 1.5, "x", True, None]:
    expect_422({"network": NET_NO_VALVE, "candidates": [
        {"id": "NV1", "segment_id": "p2", "install_cost": bad,
         "close_cost": 1}]}, ["安装费必须是正整数"])
# 关阀代价非法
expect_422({"network": NET_NO_VALVE, "candidates": [
    {"id": "NV1", "segment_id": "p2", "install_cost": 1, "close_cost": 0}]},
    ["关闭代价必须是正整数"])
# 缺少新阀编号 / 未选管段
expect_422({"network": NET_NO_VALVE, "candidates": [
    {"segment_id": "p2", "install_cost": 1, "close_cost": 1}]}, ["缺少新阀编号"])
expect_422({"network": NET_NO_VALVE, "candidates": [
    {"id": "NV1", "install_cost": 1, "close_cost": 1}]}, ["必须选择安装管段"])
# 原阀 + 候选合计 > 14
MANY = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"}]
             + [{"id": f"n{i}", "role": "normal"} for i in range(13)],
    "segments": [{"id": f"e{i}", "endpoint_a": "S", "endpoint_b": f"n{i}"}
                 for i in range(13)]
             + [{"id": "e13", "endpoint_a": "S", "endpoint_b": "L"}],
    "valves": [{"id": f"V{i}", "segment_id": f"e{i}", "cost": 1}
               for i in range(13)],
}
expect_422({"network": MANY, "candidates": [
    {"id": "NV1", "segment_id": "e13", "install_cost": 1, "close_cost": 1},
    {"id": "NV2", "segment_id": "e13", "install_cost": 1, "close_cost": 1}]},
    ["超过上限 14"])
# 管网本身非法(端点不存在)也返回 422
expect_422({"network": {"nodes": [], "segments": [
    {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}], "valves": []},
    "candidates": []}, ["端点"])

# 恢复示例管网, 确认演示数据上的选址预览可用
r = client.post("/api/demo")
check(r.status_code == 200, "demo restored")
r = client.post("/api/plan-siting", json={"candidates": [
    {"id": "NV1", "segment_id": "s1", "install_cost": 50, "close_cost": 1},
    {"id": "NV2", "segment_id": "s2", "install_cost": 50, "close_cost": 1},
]})
res = r.json()
# 示例管网原有阀门即可修复(V2+V4, 代价 7), 安装费优先 -> 不装新阀
check(res["feasible"] and res["closed_valves"] == ["V2", "V4"],
      f"demo siting {res.get('closed_valves')}")
check(res["install_cost_total"] == 0 and res["close_cost_total"] == 7,
      "demo siting totals")

print(f"siting API tests: {ok} checks passed")
