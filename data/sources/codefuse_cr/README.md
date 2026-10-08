---
license: apache-2.0
task_categories:
- text-generation
- question-answering
language:
- en
tags:
- code
- code-review
- software-engineering
- benchmark
- python
size_categories:
- n<1K
dataset_info:
  features:
  - name: instance_id
    dtype: string
  - name: repo
    dtype: string
  - name: language
    dtype: string
  - name: pull_number
    dtype: int64
  - name: title
    dtype: string
  - name: body
    dtype: string
  - name: created_at
    dtype: string
  - name: problem_statement
    dtype: string
  - name: hints_text
    dtype: string
  - name: resolved_issues
    list:
    - name: body
      dtype: string
    - name: number
      dtype: int64
    - name: title
      dtype: string
  - name: base_commit
    dtype: string
  - name: commit_to_review
    struct:
    - name: head_commit
      dtype: string
    - name: head_commit_message
      dtype: string
    - name: patch_to_review
      dtype: string
  - name: reference_review_comments
    list:
    - name: diff_hunk
      dtype: string
    - name: line
      dtype: int64
    - name: original_line
      dtype: int64
    - name: original_start_line
      dtype: int64
    - name: path
      dtype: string
    - name: start_line
      dtype: int64
    - name: text
      dtype: string
  - name: merged_commit
    dtype: string
  - name: merged_patch
    dtype: string
  - name: metadata
    struct:
    - name: difficulty
      dtype: string
    - name: estimated_review_effort
      dtype: int64
    - name: problem_domain
      dtype: string
  splits:
  - name: dev
    num_bytes: 341885132
    num_examples: 7086
  - name: test
    num_bytes: 35656314
    num_examples: 671
  download_size: 137206004
  dataset_size: 377541446
configs:
- config_name: default
  data_files:
  - split: dev
    path: data/dev-*
  - split: test
    path: data/test-*
---

# SWE-CARE: A Comprehensiveness-aware Benchmark for Code Review Evaluation

<p align="center">
  <a href="https://arxiv.org/pdf/2509.14856">
    <img src="https://img.shields.io/badge/Tech Report-arXiv-red"></a>
  <a href="https://huggingface.co/datasets/inclusionAI/SWE-CARE">
    <img src="https://img.shields.io/badge/Dataset-HuggingFace-orange"></a>
  <a href="https://github.com/inclusionAI/SWE-CARE">
    <img src="https://img.shields.io/badge/Code-GitHub-blue"></a>
  <a href="https://github.com/inclusionAI/SWE-CARE/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-Apache-blue"></a>
</p>

## Dataset Description

SWE-CARE (Software Engineering - Comprehensive Analysis and Review Evaluation) is a comprehensiveness-aware benchmark for evaluating Large Language Models (LLMs) on repository-level code review tasks. The dataset features real-world code review scenarios from popular open-source Python and Java repositories, with comprehensive metadata and reference review comments.

### Dataset Summary

- **Repository**: [inclusionAI/SWE-CARE](https://github.com/inclusionAI/SWE-CARE)
- **Paper**: [CodeFuse-CR-Bench: A Comprehensiveness-aware Benchmark for End-to-End Code Review Evaluation](https://arxiv.org/abs/2509.14856)
- **Languages**: Python
- **License**: Apache 2.0
- **Splits**:
  - `test`: 671 instances (primary evaluation set)
  - `dev`: 7,086 instances (development/training set)

## Dataset Structure

### Data Instances

Each instance in the dataset represents a code review task with the following structure:

```json
{
    "instance_id": "voxel51__fiftyone-2353@02e9ba1",
    "repo": "voxel51/fiftyone",
    "language": "Python",
    "pull_number": 2353,
    "title": "Fix issue with dataset loading",
    "body": "This PR fixes...",
    "created_at": "2023-01-15T10:30:00Z",
    "problem_statement": "Issue #2350: Dataset fails to load...",
    "hints_text": "Comments from the issue discussion...",
    "resolved_issues": [
        {
            "number": 2350,
            "title": "Dataset loading error",
            "body": "When loading datasets..."
        }
    ],
    "base_commit": "abc123...",
    "commit_to_review": {
        "head_commit": "def456...",
        "head_commit_message": "Fix dataset loading logic",
        "patch_to_review": "diff --git a/file.py..."
    },
    "reference_review_comments": [
        {
            "text": "Consider adding error handling here",
            "path": "src/dataset.py",
            "diff_hunk": "@@ -10,5 +10,7 @@...",
            "line": 15,
            "start_line": 14,
            "original_line": 15,
            "original_start_line": 14
        }
    ],
    "merged_commit": "ghi789...",
    "merged_patch": "diff --git a/file.py...",
    "metadata": {
        "problem_domain": "Bug Fixes",
        "difficulty": "medium",
        "estimated_review_effort": 3
    }
}
```

### Data Fields

#### Core Fields

- `instance_id` (string): Unique identifier in format `repo_owner__repo_name-PR_number@commit_sha_short`
- `repo` (string): GitHub repository in format `owner/name`
- `language` (string): Primary programming language (`Python` or `Java`)
- `pull_number` (int): GitHub pull request number
- `title` (string): Pull request title
- `body` (string): Pull request description
- `created_at` (string): ISO 8601 timestamp of PR creation

#### Problem Context

- `problem_statement` (string): Combined title and body of resolved issue(s)
- `hints_text` (string): Relevant comments from issues prior to the PR
- `resolved_issues` (list): Array of resolved issues with:
  - `number` (int): Issue number
  - `title` (string): Issue title
  - `body` (string): Issue description

#### Code Changes

- `base_commit` (string): Base commit SHA before changes
- `commit_to_review` (dict): The commit being reviewed:
  - `head_commit` (string): Commit SHA to review
  - `head_commit_message` (string): Commit message
  - `patch_to_review` (string): Git diff of changes to review
- `merged_commit` (string): Final merged commit SHA
- `merged_patch` (string): Final merged changes (ground truth)

#### Reference Reviews

- `reference_review_comments` (list): Human code review comments with:
  - `text` (string): Review comment text
  - `path` (string): File path being reviewed
  - `diff_hunk` (string): Relevant code diff context
  - `line` (int): Line number in new version
  - `start_line` (int): Start line for multi-line comments
  - `original_line` (int): Line number in original version
  - `original_start_line` (int): Original start line

#### Metadata

- `metadata` (dict): LLM-classified attributes:
  - `problem_domain` (string): Category like "Bug Fix", "Feature", "Refactoring", etc.
  - `difficulty` (string): "Easy", "Medium", or "Hard"
  - `estimated_review_effort` (int): Scale of 1-5 for review complexity

### Data Splits

| Split | Instances | Description |
|-------|-----------|-------------|
| test  | 671       | Primary evaluation set for benchmarking |
| dev   | 7,086     | Development set for training/fine-tuning |

## Usage

### Loading the Dataset

```python
from datasets import load_dataset

# Load the test split (default for evaluation)
dataset = load_dataset("inclusionAI/SWE-CARE", split="test")

# Load the dev split
dev_dataset = load_dataset("inclusionAI/SWE-CARE", split="dev")

# Load both splits
full_dataset = load_dataset("inclusionAI/SWE-CARE")
```

### Using with SWE-CARE Evaluation Framework

```python
from swe_care.utils.load import load_code_review_dataset

# Load from Hugging Face (default)
instances = load_code_review_dataset()

# Access instance data
for instance in instances:
    print(f"Instance: {instance.instance_id}")
    print(f"Repository: {instance.repo}")
    print(f"Problem: {instance.problem_statement}")
    print(f"Patch to review: {instance.commit_to_review.patch_to_review}")
    print(f"Reference comments: {len(instance.reference_review_comments)}")
```

### Running Evaluation

See the [GitHub repository](https://github.com/inclusionAI/SWE-CARE) for detailed documentation and examples.

### Evaluation Metrics and Baselines Results

See the [paper](https://arxiv.org/abs/2509.14856) for comprehensive evaluation metrics and baseline results on various LLMs.

## Additional Information

### Citation

If you use this dataset in your research, please cite:

```bibtex
@misc{guo2025codefusecrbenchcomprehensivenessawarebenchmarkendtoend,
      title={CodeFuse-CR-Bench: A Comprehensiveness-aware Benchmark for End-to-End Code Review Evaluation in Python Projects}, 
      author={Hanyang Guo and Xunjin Zheng and Zihan Liao and Hang Yu and Peng DI and Ziyin Zhang and Hong-Ning Dai},
      year={2025},
      eprint={2509.14856},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2509.14856}, 
}
```

### Contributions

We welcome contributions! Please see our [GitHub repository](https://github.com/inclusionAI/SWE-CARE) for:

- Data collection improvements
- New evaluation metrics
- Baseline model results
- Bug reports and feature requests

### License

This dataset is released under the Apache 2.0 License. See [LICENSE](https://github.com/inclusionAI/SWE-CARE/blob/main/LICENSE) for details.

### Changelog

- **v0.2.0** (2025-10): Expanded dataset to 671 test instances
- **v0.1.0** (2025-09): Initial release with 601 test instances and 7,086 dev instances
