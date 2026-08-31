---
topic_types:
  - all_robots
  - robot_scope
  - functional_scope
entity_types:
  - robot
  - application
  - tool
  - workflow
  - sdk
  - api
  - hardware
  - solution
  - operations
  - after_sales
  - unknown
topics:
  - keys: [all]
    labels: [全部机器人, All Robots]
    type: all_robots
    entities: []
    search_aliases: []
  - keys: [tian_gong]
    labels: [天工行者无界&无疆]
    type: robot_scope
    entities: [天工行者无界, 天工行者无疆]
    search_aliases: [天工行者, 天工, TienKung, TianGong, tianxing, Walker TienKung, Plus, Pro, 无界, 无疆, tienkung-3, tienkung-pro, tienkung-plus, walker-tienkung, walker_tienkung]
  - keys: [tian_gong_dex, tienkung_dex, walker_tienkung_dex]
    labels: [天工行者DEX]
    type: robot_scope
    entities: [天工行者DEX]
    search_aliases: [DEX, TienKung 3.0, TianGong 3.0, 天工3.0, 天工行者3.0, tiangong-walker-dex, tienkung-dex, tiangong-dex, 灵巧手机器人]
  - keys: [walker_c1]
    labels: [Walker_C1_EDU共创者, Walker C1 EDU 共创者]
    type: robot_scope
    entities: [Walker C1 EDU 共创者]
    search_aliases: [Walker C1, Walker_C1, Walker-C1, C1 EDU, c1_edu, 共创者, Astron, walker-c1-edu, C1]
  - keys: [walker_s2]
    labels: [Walker_S2_EDU探索者, Walker S2 EDU 探索者]
    type: robot_scope
    entities: [Walker S2 EDU 探索者]
    search_aliases: [Walker S2, Walker_S2, Walker-S2, S2 EDU, s2_edu, 探索者, Walker S2 Industrial, walker-s2-industrial, s2-api-tiny, ROSA 2.0, S2]
  - keys: [operations, operation]
    labels: [运营]
    type: functional_scope
    entities: [运营]
    search_aliases: [operations, growth, KA, 商业模式, 渠道, 生态, 收益模式, 产教融合]
  - keys: [solutions, solution]
    labels: [方案]
    type: functional_scope
    entities: [方案]
    search_aliases: [solutions, 9-solutions, 建设方案, 产业学院, 产教融合, 实训基地, 申报]
  - keys: [after_sales, aftersales]
    labels: [售后]
    type: functional_scope
    entities: [售后]
    search_aliases: [after-sales, aftersale, 9-aftersale, FAQ, 常见问题, 排查, 故障, troubleshooting, 维修, 保修, warranty, 急停, emergency-stop]
canonical_aliases:
  - canonical: 天工行者雷达头版
    entity_type: robot
    patterns:
      - '(?:天工|天工行者|TianGong|TienKung)[\s_-]*2\.0[\s_-]*(?:雷达头版|Radar[\s_-]*Edition)'
  - canonical: 天工行者基础版
    entity_type: robot
    patterns:
      - '(?:天工|天工行者|TianGong|TienKung)[\s_-]*2\.0[\s_-]*Lite(?:版)?'
  - canonical: 天工行者无界
    entity_type: robot
    patterns:
      - '(?:天工|天工行者|TianGong|TienKung)[\s_-]*2\.0[\s_-]*Plus(?:版)?'
  - canonical: 天工行者无疆
    entity_type: robot
    patterns:
      - '(?:天工|天工行者|TianGong|TienKung)[\s_-]*2\.0[\s_-]*Pro(?:版)?'
  - canonical: 天工行者DEX
    entity_type: robot
    patterns:
      - '(?:天工|天工行者|TianGong|TienKung)[\s_-]*3\.0(?:[\s_-]*(?:DEX))?'
      - '天工行者dex'
  - canonical: Thinkerstudio遥操数采平台
    entity_type: application
    patterns: [慧思开物平台]
  - canonical: Thinkerstudio
    entity_type: application
    patterns: [慧思开物]
  - canonical: Thinkercosmos平台
    entity_type: application
    patterns: [慧思宇宙平台]
  - canonical: Thinkercosmos
    entity_type: application
    patterns: [慧思宇宙]
ambiguous_alias_groups:
  - label: 未明确的天工行者旧版型号
    entity_type: robot
    patterns:
      - '(?:天工|天工行者|TianGong|TienKung)[\s_-]*2\.0'
    candidates: [天工行者基础版, 天工行者无界, 天工行者无疆, 天工行者雷达头版]
    output_allowed: false
---

# Topic and entity interpretation

Treat the selected topic's type separately from the identity being asked about.
A robot scope constrains explicit robot-model questions only. It must not suppress
questions whose main object is an application, tool, workflow, SDK, API, hardware
item, solution, operations topic, or after-sales topic.

Normalize known aliases to their canonical customer-facing identities internally.
Aliases are valid for retrieval expansion. A bare 2.0 reference is ambiguous across
multiple documented products; preserve that ambiguity until evidence identifies one
specific product. Never choose a candidate merely from the active topic or history.

The current request dominates conversation history. Use history only to resolve a
clear reference in the current message. Do not inject an earlier robot into a new
generic, tool, application, solution, SDK, or API question.
