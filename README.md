# standard-compliance-agent（贯标核验智能体）

判断**一份待审查文件**（施工组织设计/作业方案/验收报告…）是否**满足某标准的条款要求**，输出《贯标核查报告》。

- 与 `standard-review-agent` 区别：它审"**标准文件**是否按 GB/T 1.1 编写合规"；本 agent 审"**工程/产品文件**是否满足**某标准条款**"。
- 判据来源：① 已蒸馏目录 `standard-distill-agent/specs/<slug>/`（skills + EXECUTION，富判据）；② 标准全文（MinerU 现场解析抽条款）。

## 架构
```
POST /api/v1/compliance  (draft + [standard 或 spec_slug 或 auto])
  → 解析草案 → 选/加载标准判据 → 逐条判定(合规/违规/缺项/不完整) → 《贯标核查报告.md》+ compliance.json
```

## 技能注册表 + 路由（load all + select subset）
- `core/registry.py`：扫描 `standard-distill-agent/specs/*/skills/*/SKILL.md` → 建索引 `data/registry.json`（名字+描述+标准+条号+路径），**不把全量塞进内存/上下文**。
- `core/router.py`：为一份草案**多路召回**（标准号 + 条号 + 关键词二元组）取并集 → **LLM 精排** → 选相关 skill 子集（召回优先，宁多勿漏）。
- **三种判据来源**：`spec_slug=<标准>`（单标准）/ `standard=<PDF>`（现场抽条款）/ **`auto`**（只传草案，自动从全部标准挑 skill）。

> 路由可靠性：**条号命中/标准号路由** 准；**纯语义匹配** 中低。故采用"召回优先 + 多路并集 + 条款级兜底 + 可人工核对"。无标准号时精度下降（条号跨标准碰撞）。
外部依赖：MinerU(10.100.100.99:8000)、vLLM qwen3.8-27b。

## 配置分环境
`APP_ENV=dev`（隧道 127.0.0.1:31633）/ `prod`（直连，默认）。优先级：环境变量 > configs/app.<ENV>.json > configs/app.json。

## 快速开始
```bash
pip install -r requirements.txt
APP_ENV=dev uvicorn src.api.main:app --host 0.0.0.0 --port 5013
```

```bash
# 用已蒸馏判据
curl -X POST http://localhost:5013/api/v1/compliance \
  -F "draft=@待审查文件.md" -F "spec_slug=gb-16994.1-2021" -F "std_name=GB 16994.1-2021"
# 用标准 PDF
curl -X POST http://localhost:5013/api/v1/compliance \
  -F "draft=@待审查文件.md" -F "standard=@GB+16994.1-2021.pdf"
# → {job_id} → /status/{id} → /result/{id} → /download/{id}
```

## 验证（对 60 份贯标样例算命中率）
```bash
APP_ENV=dev PYTHONPATH=src python3 scripts/run_sample.py --sample GB16994.1-2021
```
样例与预期答案在 `10000-强标实施责任清单/07-贯标样例/`。
