"""solve_siting 选址预览求解测试: 目标次序 / 字典序决胜 / 录入校验 / 无解说明。"""
import itertools
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from solver import (  # noqa: E402
    NetworkValidationError, solve_siting, validate_network, _Graph,
)

passed = 0


def check(cond, msg):
    global passed
    assert cond, "FAIL: " + msg
    passed += 1


def expect_errors(net, cands, keys):
    try:
        solve_siting(net, cands)
    except NetworkValidationError as exc:
        joined = " | ".join(exc.errors)
        for key in keys:
            check(key in joined, f"error contains {key!r}; got: {exc.errors}")
        return
    raise AssertionError("should have raised: " + str(keys))


# ---- 基础网络: 漏点唯一通路无阀门, 必须增设候选阀 ----
NET1 = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "L", "role": "leak"}, {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p2", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [],
}

# 1. 基本选址: 在 p2 增设 NV1 并关闭
r = solve_siting(NET1, [
    {"id": "NV1", "segment_id": "p2", "install_cost": 5, "close_cost": 2},
])
check(r["feasible"] and r["solver"] == "siting", "net1 feasible")
check(r["closed_valves"] == ["NV1"], f"net1 closed {r['closed_valves']}")
check(r["new_valves"] == [{"id": "NV1", "segment_id": "p2",
                           "install_cost": 5, "close_cost": 2}],
      f"net1 new_valves {r['new_valves']}")
check(r["new_segment_ids"] == ["p2"], "net1 new segs")
check(r["install_cost_total"] == 5, "net1 install")
check(r["close_cost_total"] == 2, "net1 close cost")
check(r["candidate_count"] == 1, "net1 candidate count")
det = r["closed_valve_details"][0]
check(det["is_new"] and det["install_cost"] == 5 and det["close_cost"] == 2,
      f"net1 details {det}")
leak_st = [n for n in r["node_status"] if n["id"] == "L"][0]
keep_st = [n for n in r["node_status"] if n["id"] == "H"][0]
check(leak_st["status"] == "isolated", "net1 leak isolated")
check(keep_st["supplied"] and keep_st["connected_inlets"] == ["S"], "net1 keep fed")
seg_p2 = [s for s in r["segment_status"] if s["id"] == "p2"][0]
check(seg_p2["status"] == "closed" and seg_p2["valve_id"] == "NV1", "net1 p2 closed")
check("不写入" in r["message"], "net1 message readonly note")

# 2. 安装费优先: 原阀代价 100 也优于 1 元安装费的新阀
NET2 = {
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
r = solve_siting(NET2, [
    {"id": "NV1", "segment_id": "p2", "install_cost": 1, "close_cost": 1},
])
check(r["closed_valves"] == ["V1"], f"net2 install-first {r['closed_valves']}")
check(r["install_cost_total"] == 0 and r["close_cost_total"] == 100,
      "net2 totals")
check(r["new_valves"] == [] and r["new_segment_ids"] == [], "net2 no new")
check(r["closed_valve_details"][0]["is_new"] is False, "net2 detail existing")

# 3. 关阀代价次序: 串联两个候选位, 安装费相同 -> 取关阀代价小者
NET3 = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "L", "role": "leak"}, {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "pa", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "pb", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "ph", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [],
}
r = solve_siting(NET3, [
    {"id": "NVa", "segment_id": "pa", "install_cost": 5, "close_cost": 2},
    {"id": "NVb", "segment_id": "pb", "install_cost": 5, "close_cost": 9},
])
check(r["closed_valves"] == ["NVa"], f"net3 close-cost order {r['closed_valves']}")
check(r["install_cost_total"] == 5 and r["close_cost_total"] == 2, "net3 totals")

# 4a. 新增管段编号列表字典序: 安装费/关阀代价均相同 -> 取管段编号小者(pa < pb)
r = solve_siting(NET3, [
    {"id": "NVa", "segment_id": "pa", "install_cost": 5, "close_cost": 3},
    {"id": "NVb", "segment_id": "pb", "install_cost": 5, "close_cost": 3},
])
check(r["closed_valves"] == ["NVa"], f"net4a seg lex {r['closed_valves']}")
check(r["new_segment_ids"] == ["pa"], "net4a new segs")

# 4b. 关阀编号列表字典序: 新增管段相同(同一候选), 原阀 V1/V2 等效 -> 取 V1
NET4 = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "L", "role": "leak"}, {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
        {"id": "e1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "e2", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "ph", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "e1", "cost": 7},
        {"id": "V2", "segment_id": "e2", "cost": 7},
    ],
}
r = solve_siting(NET4, [
    {"id": "NV1", "segment_id": "p1", "install_cost": 5, "close_cost": 3},
])
check(r["closed_valves"] == ["NV1", "V1"],
      f"net4b closed lex {r['closed_valves']}")
check(r["install_cost_total"] == 5 and r["close_cost_total"] == 10,
      "net4b totals")

# 5. 未关闭的候选不计安装费: NV2 无用, 只装 NV1
r = solve_siting(NET1, [
    {"id": "NV1", "segment_id": "p2", "install_cost": 5, "close_cost": 2},
    {"id": "NV2", "segment_id": "p1", "install_cost": 100, "close_cost": 1},
])
check(r["closed_valves"] == ["NV1"], f"net5 closed {r['closed_valves']}")
check(r["install_cost_total"] == 5, "net5 only closed candidate billed")
check([v["id"] for v in r["new_valves"]] == ["NV1"], "net5 new valves")
# 未增设的候选阀不出现在方案执行后的管段上
seg_p1 = [s for s in r["segment_status"] if s["id"] == "p1"][0]
check(seg_p1["valve_id"] is None, "net5 unchosen candidate not on segment")

# 6. 空候选列表: 退化为常规求解(示例管网 V2+V4, 代价 7)
from demo import DEMO_NETWORK  # noqa: E402

r = solve_siting(DEMO_NETWORK, [])
check(r["feasible"] and r["closed_valves"] == ["V2", "V4"],
      f"net6 empty candidates {r['closed_valves']}")
check(r["install_cost_total"] == 0 and r["close_cost_total"] == 7, "net6 totals")
check(r["new_valves"] == [] and r["candidate_count"] == 0, "net6 no new")

# 7. 候选范围内不可修复: 漏点经无阀通路直连进水点
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
r = solve_siting(BAD, [
    {"id": "NV1", "segment_id": "p2", "install_cost": 5, "close_cost": 1},
])
check(r["feasible"] is False and r["solver"] == "siting", "net7 infeasible")
check(r["reason_code"] == "UNCUTTABLE_LEAK_PATH", f"net7 code {r['reason_code']}")
check("候选范围内不可修复" in r["message"], "net7 unfixable note")
check(r["reason_detail"]["witness_path"], "net7 witness path")

# 8. 必保点冲突: 漏点位于进水点与必保点之间, 隔离漏点必然饿死必保点
CONF = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
        {"id": "p2", "endpoint_a": "L", "endpoint_b": "H"},
    ],
    "valves": [],
}
r = solve_siting(CONF, [
    {"id": "NV1", "segment_id": "p1", "install_cost": 5, "close_cost": 1},
])
check(r["feasible"] is False, "net8 infeasible")
check(r["reason_code"] == "GUARANTEED_CONFLICT", f"net8 code {r['reason_code']}")
check("候选范围内不可修复" in r["message"], "net8 unfixable note")
check(r["reason_detail"]["starved_in_every_isolation"] == ["H"], "net8 starved")

# 9. 无进水点
r = solve_siting({"nodes": [{"id": "L", "role": "leak"}],
                  "segments": [], "valves": []}, [])
check(r["feasible"] is False and r["reason_code"] == "NO_INLET", "net9 no inlet")
check("候选范围内不可修复" in r["message"], "net9 unfixable note")

# ---- 10. 录入校验 ----
CAND_OK = [{"id": "NV1", "segment_id": "p2", "install_cost": 1, "close_cost": 1}]
# 候选数量 > 6
too_many = [{"id": f"NV{i}", "segment_id": "p2", "install_cost": 1,
             "close_cost": 1} for i in range(7)]
expect_errors(NET1, too_many, ["超过上限 6", "候选位置数量"])
# 新阀编号与原有阀门重复 / 候选之间重复
expect_errors(NET2, [{"id": "V1", "segment_id": "p2", "install_cost": 1,
                      "close_cost": 1}], ["阀门编号重复", "V1"])
expect_errors(NET1, [
    {"id": "NV1", "segment_id": "p1", "install_cost": 1, "close_cost": 1},
    {"id": "NV1", "segment_id": "p2", "install_cost": 1, "close_cost": 1},
], ["阀门编号重复", "NV1"])
# 候选选在已有阀门的管段
expect_errors(NET2, [{"id": "NV1", "segment_id": "p1", "install_cost": 1,
                      "close_cost": 1}], ["同时绑定", "p1"])
# 两个候选共用一条无阀管段
expect_errors(NET1, [
    {"id": "NV1", "segment_id": "p2", "install_cost": 1, "close_cost": 1},
    {"id": "NV2", "segment_id": "p2", "install_cost": 1, "close_cost": 1},
], ["同时绑定", "p2"])
# 管段不存在 / 缺少编号 / 缺少管段
expect_errors(NET1, [{"id": "NV1", "segment_id": "pX", "install_cost": 1,
                      "close_cost": 1}], ["管段 pX 不存在"])
expect_errors(NET1, [{"id": "", "segment_id": "p2", "install_cost": 1,
                      "close_cost": 1}], ["缺少新阀编号"])
expect_errors(NET1, [{"id": "NV1", "segment_id": "", "install_cost": 1,
                      "close_cost": 1}], ["必须选择安装管段"])
# 安装费非法: 0 / 负 / 小数 / 字符串 / bool / null
for bad in [0, -3, 1.5, "x", True, None]:
    expect_errors(NET1, [{"id": "NV1", "segment_id": "p2",
                          "install_cost": bad, "close_cost": 1}],
                  ["安装费必须是正整数"])
# 关阀代价非法(复用阀门代价校验)
for bad in [0, -1, 2.5, "x", False]:
    expect_errors(NET1, [{"id": "NV1", "segment_id": "p2",
                          "install_cost": 1, "close_cost": bad}],
                  ["关闭代价必须是正整数"])
# candidates 不是数组
expect_errors(NET1, {"id": "NV1"}, ["candidates 必须是数组"])
# 原阀 + 候选合计 > 14
many_valves = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"}]
             + [{"id": f"n{i}", "role": "normal"} for i in range(13)],
    "segments": [{"id": f"e{i}", "endpoint_a": "S", "endpoint_b": f"n{i}"}
                 for i in range(13)]
             + [{"id": "e13", "endpoint_a": "S", "endpoint_b": "L"}],
    "valves": [{"id": f"V{i}", "segment_id": f"e{i}", "cost": 1}
               for i in range(13)],
}
expect_errors(many_valves, [
    {"id": "NV1", "segment_id": "e13", "install_cost": 1, "close_cost": 1},
    {"id": "NV2", "segment_id": "e13", "install_cost": 1, "close_cost": 1},
], ["超过上限 14"])
# 13 原阀 + 1 候选 = 14 -> 合法
r = solve_siting(many_valves, [
    {"id": "NV1", "segment_id": "e13", "install_cost": 2, "close_cost": 3},
])
check(r["feasible"] and r["closed_valves"] == ["NV1"],
      f"net10 14 valves ok {r.get('closed_valves')}")

# ---- 11. 随机对拍: 与独立暴力枚举比较目标次序 ----
def bruteforce_siting(net, cands):
    """独立实现: 显式组合枚举 + 集合 BFS, 返回最优 (install, close, new_segs, closed)。"""
    ext = {
        "nodes": net["nodes"],
        "segments": net["segments"],
        "valves": list(net["valves"])
                  + [{"id": c["id"], "segment_id": c["segment_id"],
                      "cost": c["close_cost"]} for c in cands],
    }
    nodes, segments, valves = validate_network(ext)
    idx = {nd["id"]: i for i, nd in enumerate(nodes)}
    adj = {nd["id"]: [] for nd in nodes}
    seg_valve = {}
    for v in valves:
        seg_valve[v["segment_id"]] = v["id"]
    for sg in segments:
        a, b = sg["endpoint_a"], sg["endpoint_b"]
        vid = seg_valve.get(sg["id"])
        adj[a].append((b, vid))
        adj[b].append((a, vid))
    inlets = [nd["id"] for nd in nodes if nd["role"] == "inlet"]
    leaks = {nd["id"] for nd in nodes if nd["role"] == "leak"}
    keeps = {nd["id"] for nd in nodes if nd["role"] == "must_keep"}
    vcost = {v["id"]: v["cost"] for v in valves}
    cinstall = {c["id"]: c["install_cost"] for c in cands}
    cseg = {c["id"]: c["segment_id"] for c in cands}
    all_ids = sorted(vcost)

    def fed_after(closed):
        seen = set(inlets)
        stack = list(inlets)
        while stack:
            u = stack.pop()
            for w, vid in adj[u]:
                if vid in closed or w in seen:
                    continue
                seen.add(w)
                stack.append(w)
        return seen

    best = None
    for k in range(len(all_ids) + 1):
        for combo in itertools.combinations(all_ids, k):
            closed = set(combo)
            fed = fed_after(closed)
            if fed & leaks or not keeps <= fed:
                continue
            key = (sum(cinstall.get(v, 0) for v in combo),
                   sum(vcost[v] for v in combo),
                   tuple(sorted(cseg[v] for v in combo if v in cinstall)),
                   tuple(combo))
            if best is None or key < best:
                best = key
    return best


rng = random.Random(20260925)
trials = 0
for trial in range(150):
    n = rng.randint(3, 7)
    node_ids = [f"n{i}" for i in range(n)]
    roles = ["inlet", "leak"] + ["normal"] * (n - 2)
    rng.shuffle(roles)
    role_map = dict(zip(node_ids, roles))
    # 随机连通图
    order = node_ids[:]
    rng.shuffle(order)
    seg_list = []
    used = [order[0]]
    si = 0
    for u in order[1:]:
        v = rng.choice(used)
        seg_list.append({"id": f"e{si}", "endpoint_a": u, "endpoint_b": v})
        used.append(u)
        si += 1
    for _ in range(rng.randint(0, 3)):
        a, b = rng.sample(node_ids, 2)
        seg_list.append({"id": f"e{si}", "endpoint_a": a, "endpoint_b": b})
        si += 1
    # 随机必保点(保证不关阀时可达, 避免前置无解干扰对拍)
    for nid in node_ids:
        if role_map[nid] == "normal" and rng.random() < 0.4:
            role_map[nid] = "must_keep"
    # 原阀 0..4 个, 候选 1..3 个, 合计 <= 14
    segs = seg_list[:]
    rng.shuffle(segs)
    n_valve = rng.randint(0, min(4, len(segs)))
    valve_list = [{"id": f"V{i+1}", "segment_id": sg["id"],
                   "cost": rng.randint(1, 9)}
                  for i, sg in enumerate(segs[:n_valve])]
    free_segs = segs[n_valve:]
    if not free_segs:
        continue
    n_cand = rng.randint(1, min(3, len(free_segs)))
    cand_list = [{"id": f"NV{i+1}", "segment_id": sg["id"],
                  "install_cost": rng.randint(1, 9),
                  "close_cost": rng.randint(1, 9)}
                 for i, sg in enumerate(free_segs[:n_cand])]
    if not cand_list:
        continue
    net = {"nodes": [{"id": nid, "role": role_map[nid]} for nid in node_ids],
           "segments": seg_list, "valves": valve_list}
    res = solve_siting(net, cand_list)
    best = bruteforce_siting(net, cand_list)
    if best is None:
        check(res["feasible"] is False, f"trial {trial} both infeasible")
        check("候选范围内不可修复" in res["message"], f"trial {trial} note")
        continue
    check(res["feasible"] is True, f"trial {trial} feasible")
    got = (res["install_cost_total"], res["close_cost_total"],
           tuple(res["new_segment_ids"]), tuple(res["closed_valves"]))
    check(got == best, f"trial {trial}: {got} != brute {best}")
    # 结果自洽: 关闭的候选 = 新增位置; 安装费合计一致
    new_ids = {v["id"] for v in res["new_valves"]}
    check(new_ids == {c["id"] for c in cand_list} & set(res["closed_valves"]),
          f"trial {trial} new==closed candidates")
    check(sum(v["install_cost"] for v in res["new_valves"])
          == res["install_cost_total"], f"trial {trial} install sum")
    check(sum(d["close_cost"] for d in res["closed_valve_details"])
          == res["close_cost_total"], f"trial {trial} close sum")
    trials += 1
check(trials >= 30, f"enough random trials ({trials})")

print(f"siting solver tests: {passed} checks passed")
