# 驾驶员注意力监测的分层体验与版本复核

本应用用于评估 V15 驾驶员注意力监测在**帽子、太阳镜等佩戴条件与不同照明**下的可用性证据，
支持持续导入脱敏的监督式驾驶会话、按条件分层分析、跟踪人工复核与针对性研究安排。

> 所有车辆、驾驶员、地区、道路、调查参与者及事件标识均为**本地合成的虚构值**。
> 新闻报道仅描述厂商发布信息，不提供独立测试数据，本应用不接入任何外部数据源。

## 适用边界与口径护栏（最重要）

1. **提示次数 ≠ 驾驶员注意力缺失次数**：提示率只描述系统提示的发生频率。
2. **系统未读取到眼部信号 ≠ 驾驶员没有注视道路**：`occluded`/`unknown` 只代表摄像头不可观测。
3. **反馈是自愿样本**：完成调查的人群与全部会话不同，反馈覆盖不足时结论只适用于回复者，禁止外推。
4. **同一驾驶员可贡献多段会话**：指标同时给会话口径与驾驶员口径，避免把重复测量当独立样本。
5. **条件定义更新后历史结论不删除**：旧结论标记为 `superseded`，映射到新条件的结论标记为
   `carried_over`（待复核确认），只有重新分析产出的快照才是 `current`。

## 安装与测试

无需第三方依赖，Python 3.11–3.14 标准库即可运行。

```sh
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
python -I tools/check_materials.py     # 基线合成资料完整性检查
```

## 快速上手（以基线 data/ 为例）

```sh
# 1. 初始化本地状态库（不修改任何输入 CSV）
python -I tools/run_app.py init .work/state

# 2. 幂等导入；重复执行只跳过不重复计数
python -I tools/run_app.py import .work/state data

# 3. 分层分析，输出中文 Markdown 报告与结构化 JSON
python -I tools/run_app.py analyze .work/state --report .work/report.md --json .work/result.json

# 4. 查看证据快照适用范围与研究名额状态
python -I tools/run_app.py report .work/state --out .work/status.md

# 5. 条件定义更新（v1: 12 条件 → v2: 合并 dusk/night 为 low_light，8 条件）
python -I tools/run_app.py taxonomy activate .work/state v2 --actor 体验负责人 --note "合并低光条件"
python -I tools/run_app.py analyze .work/state --report .work/report_v2.md

# 6. 决策、研究计划与名额运营
python -I tools/run_app.py decision add .work/state --actor 体验负责人 \
    --condition C1-both-night --snapshot ES-012 --action request_research --note "夜间困难条件"
python -I tools/run_app.py plan create .work/state --created-by 研究协调员 \
    --conditions C1-both-night --region R-N --note "补充研究"
python -I tools/run_app.py plan accept .work/state RP-001 --slots RS-03,RS-06 --accepted-by 体验负责人
python -I tools/run_app.py slot execute .work/state RS-03 --consent-scope research-only --actor 研究协调员
python -I tools/run_app.py slot withdraw .work/state RS-06 --actor 驾驶员      # 撤回：释放未执行容量
python -I tools/run_app.py slot device-fail .work/state RS-09 --actor 研究协调员 # 设备失效：仅释放未执行
python -I tools/run_app.py material remove .work/state MAT-001 --actor 研究协调员

# 7. 追溯：为什么某条件仍未知、计划由谁接受、新结果改变了哪些决策
python -I tools/run_app.py trace .work/state C2-hat_plus_sunglasses-low_light

# 8. 校验审计哈希链
python -I tools/run_app.py verify .work/state
```

持续导入（轮询目录，文件内容变化后自动幂等导入）：

```sh
python -I tools/run_app.py watch .work/state data --interval 2 --cycles 10
```

生成一份新的固定种子合成资料（用于隔离演示，不覆盖基线 `data/`）：

```sh
python -I tools/run_app.py generate-synth .work/synth --seed 20261124
```

## 代码结构

```
src/driver_attention_monitoring_review/
  models.py      受控词表与领域模型（缺失/撤回/待复核是有效业务事实）
  importer.py    CSV 契约校验、交叉引用检查、内容哈希
  taxonomy.py    条件分类法 v1/v2 与跨版本映射
  metrics.py     分层指标、口径护栏、证据分级规则
  analysis.py    证据快照、变化检测、受影响决策标记
  store.py       JSON 状态库 + 哈希链审计日志 + 研究名额生命周期
  watch.py       目录轮询持续导入
  report.py      中文报告
  synthdata.py   固定种子合成数据生成器
  cli.py         命令行入口
tests/           43 个 unittest（指标口径、规则、生命周期、版本重标、端到端）
docs/            资料契约、指标口径说明、运营流程与审计说明
```

## 证据分级

| 分级 | 含义 | 默认建议动作 |
| --- | --- | --- |
| `sufficient` | 样本量、反馈覆盖、复核覆盖与一致性均达标 | 继续观察 |
| `needs_review` | 有基础样本，但不可观测比例/标注清晰度/复核一致性存在疑问 | 继续观察并安排复核 |
| `needs_research` | 样本量或反馈覆盖存在根本缺口 | 申请针对性研究 |
| `unknown` | 该条件下没有任何会话资料 | 申请针对性研究 |

阈值集中在 `metrics.EvidenceRules`，可按团队标准调整，分析命令也暴露了
`--min-sessions`、`--min-drivers` 两个常用参数。
