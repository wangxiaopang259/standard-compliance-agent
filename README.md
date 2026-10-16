# standard-compliance-agent（贯标核验智能体）

判断**一份待审查文件**（施工组织设计/作业方案/验收报告…）是否**满足某标准的条款要求**，输出《贯标核查报告》。

- 与 `standard-review-agent` 区别：它审"**标准文件**是否按 GB/T 1.1 编写合规"；本 agent 审"**工程/产品文件**是否满足**某标准条款**"。
- 判据来源：① 已蒸馏目录 `standard-distill-agent/specs/<slug>/`（skills + EXECUTION，富判据）；② 标准全文（MinerU 现场解析抽条款）。

## 架构
```
POST /api/v1/compliance  (draft + [standard 或 spec_slug])
  → 解析草案 → 加载标准判据 → 逐条判定(合规/违规/缺项/不完整) → 《贯标核查报告.md》+ compliance.json
```
外部依赖：MinerU(10.100.100.99:8000)、vLLM qwen3.8-27b。

## 配置分环境
`APP_ENV=dev`（隧道 127.0.0.1:31633）/ `prod`（直连，默认）。优先级：环境变量 > configs/app.<ENV>.json > configs/app.json。

## 快速开始
```bash
pip install -r requirements.txt
APP_ENV=dev uvicorn src.api.main:app --host 0.0.0.0 --port 6002
```

```bash
# 用已蒸馏判据
curl -X POST http://localhost:6002/api/v1/compliance \
  -F "draft=@待审查文件.md" -F "spec_slug=gb-16994.1-2021" -F "std_name=GB 16994.1-2021"
# 用标准 PDF
curl -X POST http://localhost:6002/api/v1/compliance \
  -F "draft=@待审查文件.md" -F "standard=@GB+16994.1-2021.pdf"
# → {job_id} → /status/{id} → /result/{id} → /download/{id}
```

## 验证（对 60 份贯标样例算命中率）
```bash
APP_ENV=dev PYTHONPATH=src python3 scripts/run_sample.py --sample GB16994.1-2021
```
样例与预期答案在 `10000-强标实施责任清单/07-贯标样例/`。
