# Target Wiki entry template

Use one Wiki entry per atomic capability. Keep the machine record as structured data or front matter and render the same fields in human-readable sections.

## Title

Use the normalized capability name:

```text
控制_关节至目标位置
```

## Machine record

```yaml
schema_version: "1.0"
capability_id: CAP-TG-JOINT-POSITION-CMD
semantic_key: joint.position.control
name: 控制_关节至目标位置
effect:
  action: 设置
  object: 指定关节的位置控制目标
  observable_result: 控制系统接收到指定关节的位置、速度和电流限制参数
scope:
  vendor: 北京人形机器人创新中心
  model_id: tiangong-wujing-pro
  source_model_names:
    - 天工行者·无疆
    - 天工2.0 Pro版
  hardware_revision: unknown
  software_version: unknown
  firmware_version: unknown
  body_parts: [头部, 腰部, 双臂, 双腿]
  environment: 机器人本体控制系统
  selector: MotorName
  resolution_status: ambiguous
trigger: 向对应 ROS 2 控制话题发布合法消息
inputs: []
outputs: []
preconditions: []
hold_conditions: []
postconditions: []
constraints:
  time: []
  space: []
  information: []
  energy: []
quality_metrics: []
failure_modes: []
interfaces: []
dependencies: []
incompatible_resources: []
evidence: []
confidence:
  extraction_score: 0.65
  basis: 接口有直接文档证据，但型号范围和物理性能尚未验证
unknowns: []
lifecycle:
  status: draft
  supersedes: []
  replaced_by: []
  deprecation_reason: null
```

## Human-readable sections

Render sections in this order:

1. Capability summary
2. Observable effect
3. Model and version scope
4. Trigger and interface
5. Inputs and outputs
6. Preconditions, hold conditions, and postconditions
7. TSEI constraints
8. Quality metrics and acceptance methods
9. Failure and recovery facts
10. Dependencies and incompatible resources
11. Evidence ledger
12. Unknowns
13. Lifecycle and change history

## Wiki operational metadata

Store synchronization metadata outside the capability contract:

```yaml
wiki_sync:
  target_section_id: robot-model-capabilities
  entry_revision: "17"
  source_snapshot_id: SRCSET-2026-07-27-001
  changeset_id: CHG-TG-20260727-001
  content_hash: sha256:...
  last_sync_result: verified
```

Do not mix Wiki revision metadata into the semantic capability identity.
