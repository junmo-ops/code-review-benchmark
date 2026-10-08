# 🚀 From Static to Dynamic: Benchmarking Real-world Code Review with MCR-bench

MCR-bench moves **from static to dynamic** code review evaluation by capturing **real-world code review scenarios** with multi-round interactions, iterative discussions, and evolving code changes. This repository provides both the construction pipeline for building the benchmark from real GitHub Pull Requests and the evaluation framework for assessing LLM performance.

## 📑 Table of Contents

- [🎯 Overview](#overview)
- [🔨 MCR-bench Construction](#mcr-bench-construction)
  - [1. 📂 Project Selection](#1-project-selection)
  - [2. 🕷️ PR Crawling](#2-pr-crawling)
  - [3. 🔍 PR Filtering](#3-pr-filtering)
  - [4. 🏷️ PR Annotation](#4-pr-annotation)
- [📊 Evaluation](#evaluation)
  - [1. 📝 Prompt Construction](#1-prompt-construction)
  - [2. ⚖️ LLM as a Judge](#2-llm-as-a-judge)
- [⚡ Quick Start](#quick-start)
- [📁 Directory Structure](#directory-structure)

## 🎯 Overview

MCR-bench provides a standardized dataset and evaluation framework for code review tasks. The benchmark includes:

- **5 Programming Languages** 🐍☕💻: C#, Java, JavaScript, Python, TypeScript
- **Multi-round Code Review Scenarios** 🔄: Real-world PRs with multiple review rounds
- **Comprehensive Annotations** 📝: Defect taxonomy, severity levels, and state tracking
- **LLM Evaluation Framework** 🤖: Automated evaluation using LLM-as-a-judge methodology

The following figure illustrates the structure of a MCR-bench instance, showing how PR timeline events, linked issues, and annotated defects are organized:

<div align="center">
  <img src="figures/instance_overview.png" alt="Instance Overview">
</div>

## 🔨 MCR-bench Construction

The construction pipeline consists of four main stages that transform raw GitHub data into an annotated benchmark dataset.

![Construction Pipeline](figures/construction_pipeline.png)

### 1. 📂 Project Selection

Selects high-quality open-source projects from GitHub based on criteria (stars > 100, PR count > 1500, issue resolution rate > 40%, etc.).

```bash
cd MCR-bench/construction_pipeline/Project_selection
python crawl_github_repos.py --language python
```

---

### 2. 🕷️ PR Crawling

Crawls Pull Request data including timeline events, code diffs, and linked issues from selected repositories.

```bash
cd MCR-bench/construction_pipeline/PR_crawling
python crawl_prs.py --language python --top-n 50 --max-workers 4
```

---

### 3. 🔍 PR Filtering

Filters PRs to ensure high-quality code review scenarios: code changes only, merged status, size ≤ 400 lines, and multi-round discussions with real reviewers.

```bash
cd MCR-bench/construction_pipeline/PR_filtering
python pr_filtering.py --language python --save
```

---

### 4. 🏷️ PR Annotation

Annotates PRs with defect information using a three-stage LLM pipeline: (1) issue extraction per round, (2) cross-round linking with state tracking (runs 3 times), (3) consistency verification to retain only agreed-upon issues.

```bash
cd MCR-bench/construction_pipeline/PR_annotation
python run_annotation.py \
    --input ../PR_filtering/{language}/filtered_prs.jsonl \
    --output output/{language} \
    --model openai \
    --api-key YOUR_API_KEY \
    --model-name gpt-4
```

---

## 📊 Evaluation

The evaluation framework provides tools for constructing prompts and evaluating LLM performance on code review tasks.

### 1. 📝 Prompt Construction

Builds LLM code review prompts for each PR round, including timeline events, code diffs, and linked issues.

```bash
cd evaluation/Prompt_construction
python construct_prompts.py --lang python --mcrbench-dir <path-to-mcrbench-data>
```

---

### 2. ⚖️ LLM as a Judge

Evaluates LLM code review outputs using LLM-as-a-judge methodology. The framework includes prompt building, defect matching evaluation, and metrics calculation (Precision, Recall, F1).

```bash
cd evaluation/Metric
python llm_evaluator.py \
    --model-dir ../generated_results/gpt-5.2 \
    --provider openai \
    --api-key YOUR_API_KEY \
    --model-name gpt-4

python metrics_calculator.py --model gpt-5.2 --language python
```

---

## ⚡ Quick Start

### 🔨 Building the Dataset

1. **📂 Select Projects**:
   ```bash
   cd MCR-bench/construction_pipeline/Project_selection
   python crawl_github_repos.py --language python
   ```

2. **🕷️ Crawl PRs**:
   ```bash
   cd ../PR_crawling
   python crawl_prs.py --language python --top-n 50
   ```

3. **🔍 Filter PRs**:
   ```bash
   cd ../PR_filtering
   python pr_filtering.py --language python --save
   ```

4. **🏷️ Annotate PRs**:
   ```bash
   cd ../PR_annotation
   python run_annotation.py \
       --input ../PR_filtering/python/filtered_prs.jsonl \
       --output output/python \
       --model openai \
       --api-key YOUR_KEY \
       --model-name gpt-4
   ```

### 📊 Evaluating Models

1. **📝 Construct Prompts**:
   ```bash
   cd evaluation/Prompt_construction
   python construct_prompts.py --lang python
   ```

2. **⚖️ Run Evaluation**:
   ```bash
   cd ../Metric
   python llm_evaluator.py \
       --model-dir ../generated_results/gpt-5.2 \
       --provider openai \
       --api-key YOUR_KEY \
       --model-name gpt-4
   ```

3. **📈 Calculate Metrics**:
   ```bash
   python metrics_calculator.py --model gpt-5.2 --language python
   ```

---

## 📁 Directory Structure

```
MCR-bench/
├── MCR-bench/
│   ├── construction_pipeline/     # 🔨 Dataset construction pipeline
│   │   ├── Project_selection/     # 📂 Step 1: Project selection
│   │   ├── PR_crawling/           # 🕷️ Step 2: PR crawling
│   │   ├── PR_filtering/          # 🔍 Step 3: PR filtering
│   │   └── PR_annotation/         # 🏷️ Step 4: PR annotation
│   └── dataset/                    # 📊 Final benchmark dataset
│       ├── csharp.jsonl
│       ├── java.jsonl
│       ├── js.jsonl
│       ├── py.jsonl
│       └── ts.jsonl
├── evaluation/
│   ├── Prompt_construction/        # 📝 Prompt construction
│   └── Metric/                     # ⚖️ LLM-as-a-judge evaluation
├── generated_results/              # 🤖 Model response files
└── figures/                        # 📊 Visualization figures
```
