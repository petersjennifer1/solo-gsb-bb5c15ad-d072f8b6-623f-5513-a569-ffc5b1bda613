"""首次启动空库时写入的示例管网。

拓扑:
  S(进水点) --s1-- A --s2-- H1(必保)
                   |
                   s3(阀门V1, 代价3)
                   |
                   B --s5(阀门V2, 代价1)-- L(漏点)
                   |
                   s4(阀门V3, 代价4)
                   |
                   H2(必保)

  旁路: A --s6(阀门V4, 代价6)-- L

隔离漏点 L 的候选:
  关 V2(代价1) 即可切断 B-L, 但还需切断旁路 A-L => 关 V4(代价6), 共 7;
  或同时关 V1(3)+V3(4) 切掉 B 两侧? 那样 B 成为孤岛但 L 仍可经 V2 …
  实际最优: V2 + V4 = 7 (H1、H2 仍由 S 供水)。
"""

DEMO_NETWORK = {
    "nodes": [
        {"id": "S", "role": "inlet", "label": "进水点S"},
        {"id": "A", "role": "normal", "label": "节点A"},
        {"id": "B", "role": "normal", "label": "节点B"},
        {"id": "H1", "role": "must_keep", "label": "必保点H1"},
        {"id": "H2", "role": "must_keep", "label": "必保点H2"},
        {"id": "L", "role": "leak", "label": "漏点L"},
    ],
    "segments": [
        {"id": "s1", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "s2", "endpoint_a": "A", "endpoint_b": "H1"},
        {"id": "s3", "endpoint_a": "A", "endpoint_b": "B"},
        {"id": "s4", "endpoint_a": "B", "endpoint_b": "H2"},
        {"id": "s5", "endpoint_a": "B", "endpoint_b": "L"},
        {"id": "s6", "endpoint_a": "A", "endpoint_b": "L"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "s3", "cost": 3},
        {"id": "V2", "segment_id": "s5", "cost": 1},
        {"id": "V3", "segment_id": "s4", "cost": 4},
        {"id": "V4", "segment_id": "s6", "cost": 6},
    ],
}
