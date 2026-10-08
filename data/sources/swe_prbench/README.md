---
license: cc-by-4.0
task_categories:
- text-classification
language:
- en
tags:
- code-review
- benchmark
- llm-evaluation
- pull-requests
- software-engineering
pretty_name: SWE-PRBench
size_categories:
- n<1K
configs:
- config_name: prs
  data_files: dataset/prs.jsonl
- config_name: eval_split
  data_files: dataset/evals/eval_100.json
---

# SWE-PRBench

**Benchmarking AI Code Review Quality Against Human Pull Request Feedback**

- Blog: [Read the blog](https://foundryhq.ai/blog/swe-prbench-benchmarking-ai-code-review-quality)  
- GitHub Repository: [View the code](https://github.com/FoundryHQ-AI/swe-prbench)
- arXiv Paper: [View the paper](https://arxiv.org/abs/2603.26130)

---

## Overview

SWE-PRBench is a benchmark of **350 pull requests with human-annotated 
ground truth** for evaluating whether LLMs can identify the same issues 
that real human reviewers flag in production code.

Existing benchmarks like SWE-Bench measure whether models can *produce* 
correct code. SWE-PRBench measures whether a model can *evaluate* proposed 
code changes as an expert reviewer would — a fundamentally different 
judgment task with no pass/fail test suite and no single correct answer.

**Key result:** 8 frontier models detect only 15–31% of human-flagged 
issues on the diff-only configuration. All 8 models degrade monotonically 
as context expands, establishing that attention representation — not content 
selection — is the binding constraint for AI code review.

---

## Why SWE-PRBench?

Existing code review datasets and tools fall short in three ways: they use 
synthetic or generated ground truth, they do not structure evaluation around 
issue detection capability, and none provide controlled context configurations 
for ablation. SWE-PRBench addresses all three gaps.

| Property | CodeReviewer | DeepCRCEval | RovoDev | SWE-PRBench (Ours) |
|----------|-------------|-------------|---------|-------------------|
| Primary contribution | Model + dataset | Eval metrics | Production tool | Dataset + protocol |
| Ground truth source | Synthetic pairs | Generated | CRR metric only | **Human reviewers** |
| Source links retained | No | No | N/A | **Yes** |
| Difficulty taxonomy | None | None | None | **3 types** |
| Context configurations | None | None | None | **3 frozen** |
| Issue detection eval | No | No | Partial | **Yes** |
| Judge validated | No | No | No | **κ=0.75** |
| Public dataset | Partial | No | No | **Yes** |

Ground truth in SWE-PRBench consists of review comments written by human 
engineers during the actual review process on real merged pull requests, 
collected after the fact via GitHub's review API. No comments are generated, 
synthesised, or modified during dataset construction.

---

## Leaderboard (Paper Baseline)

![SWE-PRBench Leaderboard](https://huggingface.co/datasets/foundry-ai/swe-prbench/resolve/main/leaderboard.png)

| Rank | Model | Overall (s̄) | DR_A | FPR |
|------|-------|-------------|------|-----|
| 1 | Claude Haiku 4.5 | 0.153 | 0.306 | 0.346 |
| 2 | Claude Sonnet 4.6 | 0.152 | 0.297 | 0.227 |
| 3 | DeepSeek V3 | 0.150 | 0.312 | 0.315 |
| 4 | Mistral Large 3 | 0.147 | 0.305 | 0.353 |
| 5 | GPT-4o | 0.113 | 0.220 | 0.193 |
| 6 | GPT-4o-mini | 0.108 | 0.210 | 0.353 |
| 7 | Mistral Small | 0.106 | 0.257 | 0.251 |
| 8 | Llama 3.3 70B | 0.079 | 0.223 | 0.417 |

Evaluated on `evals/eval_100.json`. Judge: GPT-5.2. Pipeline: v0.4.1.

---

## Dataset at a Glance

| Property | Value |
|----------|-------|
| Total PRs | 350 |
| Repositories | 65 across 100 RQS-qualified repos |
| Languages | Python (69%), JS (11%), Go (10%), TypeScript (6%), Java (4%) |
| Difficulty types | Type1\_Direct / Type2\_Contextual / Type3\_Latent |
| Context configs | config\_A (2k) / config\_B (2.2k) / config\_C (2.5k) tokens |
| Ground truth | Real human reviewer comments, not generated or synthesised |
| Judge validation | κ=0.75 (substantial agreement) |
| Pipeline version | v0.4.1 |

---

## Dataset Preparation

Dataset construction follows a four-stage pipeline designed to ensure 
ground truth quality, repository diversity, and contamination resistance.

**Stage 1 — Repository selection via RQS.**
Repositories are scored using a Repository Quality Score (RQS) across five 
dimensions: review culture (share of substantive human comments), PR recency, 
test quality, PR volume, and contamination risk (inverse star count). Only 
repositories scoring ≥60/100 are included, ensuring that ground truth comes 
from codebases with genuine human review activity.

**Stage 2 — PR collection and filtering.**
For each qualifying repository, merged pull requests are collected via 
GitHub's GraphQL and REST APIs over a six-month window. PRs pass through 
a ten-stage hard filter covering: merged-only status, minimum two substantive 
human comments, non-documentation changes, no automated dependency updates 
(Dependabot/Renovate), and explicit AI comment detection. PRs where more 
than 30% of review comments originate from known AI bots or match 
AI-generated structural patterns are excluded, preserving the integrity of 
human ground truth.

**Stage 3 — PR quality scoring via RVS.**
Each PR is scored using a PR Review Value Score (RVS) combining review 
depth, code complexity, discussion signal, test change signal, and bug-fix 
signal. Only PRs with RVS ≥ 0.35 enter the final dataset, ensuring every 
benchmark task carries meaningful ground-truth signal.

**Stage 4 — Difficulty classification.**
Each PR is classified into one of three difficulty types based on where 
the evidence for a reviewable issue resides: directly in the diff 
(Type1\_Direct), in surrounding unchanged code (Type2\_Contextual), or in 
dependent files (Type3\_Latent). Classification is derived automatically 
from the `is_in_diff` field of human reviewer comments cross-referenced 
against diff hunk line ranges.

The result is 350 PRs from 65 repositories across 6 languages, with a 
construction funnel of ~3,000 raw PRs → 700 after hard filtering → 350 
after RVS quality cut.

---

## Difficulty Taxonomy

Each PR is classified by where the evidence for a reviewable issue resides:

- **Type1\_Direct (66%)** — Issue is directly visible in the changed lines. A reviewer needs only the diff to identify it.
- **Type2\_Contextual (21%)** — Issue requires understanding changed code relative to surrounding unchanged code in the same file.
- **Type3\_Latent (12%)** — Issue resides in files that import or depend on the changed files. Requires cross-file reasoning.

---

## Context Configurations

Three frozen configurations enable systematic ablation of context provision:

| Config | Layers | Real-world analogue | Token budget |
|--------|--------|---------------------|-------------|
| config\_A | Task focus, summary, diff, metadata | GitHub PR email notification | 2,000 |
| config\_B | + Execution context, behaviour mapping | GitHub PR web view | 2,200 |
| config\_C | + Test signatures | Reviewer with full IDE access | 2,500 |

Configs differ in **layer composition**, not token volume. The A>B>C 
degradation implicates attention representation, not context length. 
Pre-built contexts for all 350 PRs are released as frozen artefacts at 
pipeline version v0.4.1.

---

## Dataset Structure
```
dataset/
├── prs.jsonl              # 350 PR records (metadata + diffs)
├── annotations/           # 350 human annotation files (ground truth)
│   ├── dask__12221_human.json
│   └── ...
├── contexts/
│   ├── config_A/          # 350 pre-built config_A contexts
│   ├── config_B/          # 350 pre-built config_B contexts
│   └── config_C/          # 350 pre-built config_C contexts
└── evals/
    └── eval_100.json      # 100-PR stratified sample used in paper
```

---

## File Formats

**`prs.jsonl`** — one line per PR:
```json
{
  "task_id": "dask__12221",
  "repo": "dask/dask",
  "language": "Python",
  "difficulty": "Type1_Direct",
  "rvs_score": 0.52,
  "diff_patch": "diff --git ...",
  "base_commit": "0a075534...",
  "head_commit": "59dab320...",
  "num_substantive_comments": 3
}
```

**`annotations/dask__12221_human.json`** — ground truth:
```json
{
  "task_id": "dask__12221",
  "comments": [
    {
      "comment_id": "c_1",
      "body": "Out of scope: this should belong to os.process_cpu_count...",
      "file": "dask/system.py",
      "line": 82,
      "is_in_diff": true,
      "is_initiating_comment": true
    }
  ]
}
```

**`contexts/config_A/dask__12221.json`**:
```json
{
  "task_id": "dask__12221",
  "config_name": "config_A",
  "pipeline_version": "v0.4.1",
  "total_tokens": 847,
  "was_truncated": false,
  "rendered": "## Layer 0 - Task + Focus\n..."
}
```

**`evals/eval_100.json`** — paper evaluation split:
```json
{
  "description": "100-PR stratified sample used in paper baseline.",
  "n": 100,
  "stratification": {
    "Type1_Direct": 40,
    "Type2_Contextual": 40,
    "Type3_Latent": 20
  },
  "task_ids": ["dask__12221", "prowler__9865", "..."]
}
```

---

## Ground Truth

Ground truth consists of review comments written by human engineers 
during the actual review process on real merged pull requests. Comments 
are collected from GitHub's review API after the fact. **No comments are 
generated, synthesised, or modified during dataset construction.**

Ground truth inclusion criteria (from `RUBRIC.md`):
- Human-authored
- Initiating comment, not a reply
- ≥10 words
- References specific code behaviour
- Not pure praise

---

## Leaderboard Submission

All 350 PRs are publicly released following the honor-system standard 
established by SWE-Bench and SWE-Bench Pro. Evaluation harness and 
submission instructions coming shortly. To register interest in 
submitting, open a discussion on this dataset page.

---

## Citation
```bibtex
@article{kumar2026sweprbench,
  title={SWE-PRBench: Benchmarking AI Code Review Quality
         Against Pull Request Feedback},
  author={Kumar, Deepak},
  journal={arXiv preprint},
  primaryClass={cs.SE},
  url={https://arxiv.org/abs/2603.26130}
  year={2026}
}
```

---

## License

Dataset: CC BY 4.0