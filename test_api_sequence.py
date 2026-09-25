"""API 端到端测试: FastAPI TestClient + 临时 SQLite。"""
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


# 健康检查
r = client.get("/api/health")
check(r.status_code == 200, "health")

# 用示例管网常规求解并保存
r = client.post("/api/plan", json=DEMO_NETWORK)
check(r.status_code == 200, "demo plan")
plan = r.json()
pid = plan["id"]
check(plan["feasible"] and plan["closed_valves"] == ["V2", "V4"],
      f"demo valves {plan.get('closed_valves')}")
before = client.get(f"/api/plans/{pid}").json()

# ---- 正常顺序规划 ----
r = client.post(f"/api/plans/{pid}/sequence",
                json={"durations": {"V2": 10, "V4": 20},
                      "leak_weights": {"L": 5}})
check(r.status_code == 200, f"seq status {r.status_code} {r.text}")
seq = r.json()
check(seq["plan_id"] == pid and seq["solver"] == "base", "seq meta")
check(seq["sequence"] == ["V2", "V4"], f"seq order {seq['sequence']}")
check(seq["total_exposure"] == 150 and seq["total_duration"] == 30, "seq totals")
check(len(seq["steps"]) == 2, "two steps")
check(seq["steps"][0]["start"] == 0 and seq["steps"][0]["end"] == 10, "step1 span")
check(seq["steps"][1]["newly_isolated_leaks"] == ["L"], "step2 isolates L")
check(seq["steps"][1]["flowing_leaks_after"] == [], "no final flowing leak")
check(seq["initial_flowing_leaks"] == ["L"], "initial flowing")

# ---- 只读: 原方案与管网均未改变 ----
after = client.get(f"/api/plans/{pid}").json()
check(after == before, "plan unchanged after sequence computation")

# ---- 遗漏编号 ----
r = client.post(f"/api/plans/{pid}/sequence",
                json={"durations": {"V2": 10}, "leak_weights": {"L": 5}})
check(r.status_code == 422, f"missing {r.status_code}")
errs = r.json()["detail"]["errors"]
check(any("缺少" in e and "V4" in e for e in errs), f"missing err {errs}")

# ---- 多余编号(阀 + 漏点) ----
r = client.post(f"/api/plans/{pid}/sequence",
                json={"durations": {"V2": 10, "V4": 5, "V1": 1, "V9": 2},
                      "leak_weights": {"L": 1, "X": 3}})
check(r.status_code == 422, "extra")
errs = r.json()["detail"]["errors"]
joined = " | ".join(errs)
check("V1" in joined and "V9" in joined and "多余" in joined, f"extra valve {errs}")
check("X" in joined and "非漏点" in joined, f"extra leak {errs}")

# ---- 非法数值: 0/负/小数/字符串/bool/null ----
for bad in [0, -1, 1.5, "10", True, None]:
    r = client.post(f"/api/plans/{pid}/sequence",
                    json={"durations": {"V2": bad, "V4": 5},
                          "leak_weights": {"L": 1}})
    check(r.status_code == 422, f"bad duration {bad!r} -> {r.status_code}")
    errs = r.json()["detail"]["errors"]
    check(any("V2" in e and "正整数" in e for e in errs),
          f"bad duration msg {bad!r}: {errs}")
for bad in [0, -2, 2.0, "x", False]:
    r = client.post(f"/api/plans/{pid}/sequence",
                    json={"durations": {"V2": 5, "V4": 5},
                          "leak_weights": {"L": bad}})
    check(r.status_code == 422, f"bad weight {bad!r} -> {r.status_code}")
    errs = r.json()["detail"]["errors"]
    check(any("L" in e and "正整数" in e for e in errs),
          f"bad weight msg {bad!r}: {errs}")

# 空请求体: 全部遗漏
r = client.post(f"/api/plans/{pid}/sequence", json={})
check(r.status_code == 422, "empty body")
errs = r.json()["detail"]["errors"]
check(any("缺少" in e and "V2" in e for e in errs), f"empty errs {errs}")
check(any("缺少" in e and "L" in e for e in errs), f"empty leak errs {errs}")

# ---- 方案不存在 ----
r = client.post("/api/plans/9999/sequence",
                json={"durations": {}, "leak_weights": {}})
check(r.status_code == 404, f"404 got {r.status_code}")

# ---- 不可行方案: 无阀通路的漏点 ----
BAD = {
    "nodes": [
        {"id": "S", "role": "inlet"},
        {"id": "L", "role": "leak"},
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
bid = bad_plan["id"]
r = client.post(f"/api/plans/{bid}/sequence",
                json={"durations": {}, "leak_weights": {"L": 1}})
check(r.status_code == 422, f"infeasible seq {r.status_code}")
errs = r.json()["detail"]["errors"]
check(any("不可行" in e and str(bid) in e for e in errs), f"infeasible msg {errs}")

# 不可行方案在列表/详情中仍可回看(GET 不受影响)
r = client.get(f"/api/plans/{bid}")
check(r.status_code == 200 and r.json()["feasible"] is False, "GET infeasible")

# ---- 备援可行方案也能做顺序规划 ----
FO = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "L", "role": "leak"}, {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "a", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "b", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "h", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "a", "cost": 1},
        {"id": "V2", "segment_id": "b", "cost": 2},
    ],
}
r = client.post("/api/plan-failover", json=FO)
fo = r.json()
check(fo["feasible"] and fo["solver"] == "failover"
      and fo["closed_valves"] == ["V1", "V2"], "failover plan")
fid = fo["id"]
r = client.post(f"/api/plans/{fid}/sequence",
                json={"durations": {"V1": 4, "V2": 6},
                      "leak_weights": {"L": 3}})
check(r.status_code == 200, f"failover seq {r.status_code} {r.text}")
fseq = r.json()
check(fseq["solver"] == "failover", "solver field")
# 两阀串联: 先 V1 只切断 S-M, L 仍不通? 关 a 后 M-L 无进水 => L 隔离!
# W({})=3, W({V1})=0; V1 先: 4*3+6*0=12; V2 先: 6*3+4*0=18 => V1 先
check(fseq["sequence"] == ["V1", "V2"], f"fo order {fseq['sequence']}")
check(fseq["total_exposure"] == 12, f"fo exposure {fseq['total_exposure']}")
check(fseq["steps"][0]["newly_isolated_leaks"] == ["L"], "fo isolates at step1")

# ---- 无漏点的可行空方案 ----
EMPTY = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "H", "role": "must_keep"}],
    "segments": [{"id": "p", "endpoint_a": "S", "endpoint_b": "H"}],
    "valves": [],
}
r = client.post("/api/plan", json=EMPTY)
e = r.json()
check(e["feasible"] and e["closed_valves"] == [], "empty feasible")
eid = e["id"]
r = client.post(f"/api/plans/{eid}/sequence", json={})
check(r.status_code == 200, f"empty seq {r.status_code} {r.text}")
ej = r.json()
check(ej["steps"] == [] and ej["total_exposure"] == 0, "empty result")
# 空方案多给编号 -> 多余错误
r = client.post(f"/api/plans/{eid}/sequence",
                json={"durations": {"V1": 1}})
check(r.status_code == 422 and "多余" in r.text, "empty extra valve")
r = client.post(f"/api/plans/{eid}/sequence",
                json={"leak_weights": {"L": 1}})
check(r.status_code == 422 and "多余" in r.text, "empty extra leak")

# ---- 原方案数量未因顺序计算而增加(只读, 未新入库) ----
plans = client.get("/api/plans?limit=100").json()["plans"]
ids = [p["id"] for p in plans]
check(ids.count(pid) == 1 and len(ids) == len(set(ids)), "no extra plans saved")
check(sorted(ids) == sorted(set([pid, bid, fid, eid])),
      f"plan ids {sorted(ids)}")

print(f"API tests: {ok} checks passed")
