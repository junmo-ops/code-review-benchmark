#!/usr/bin/env python3
"""
Prompt builder for defect matching evaluation

This module is responsible for constructing prompts for LLM-based defect matching evaluation.

This is a utility module primarily used by llm_evaluator.py.
It can be imported and used independently in other scripts.

Can be run independently to generate and save all prompts:
    python prompt_builder.py --model <model_name> --language <lang> [--limit <n>]
"""

import json
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Tuple


def build_matching_prompt(ground_truth_defects: List[Dict], model_defects: List[Dict], task_id: str) -> Tuple[str, str]:
    """
    Build prompt for defect matching evaluation
    
    Args:
        ground_truth_defects: Ground truth defect list
        model_defects: Model output defect list
        task_id: Task ID (not used in prompt, kept for compatibility)
    
    Returns:
        (system_prompt, user_prompt) tuple
    """
    system_prompt = """You are a code review evaluation expert. Your task is to determine whether defects in the model output match defects in the ground truth.

## Matching Criteria

The primary criterion for matching is **semantic similarity** - whether two defects describe the same or highly similar code issues.

### How to Judge Semantic Similarity:

1. **Core Problem Identification**
   - Compare the fundamental issue being described in both defects
   - Two defects match if they identify the same underlying problem, even if expressed differently
   - Example: "Null pointer dereference" and "Potential null reference exception" are semantically similar

2. **Description Analysis**
   - Focus on the essence of the problem, not exact wording
   - Consider synonyms and different phrasings of the same concept
   - Ignore minor differences in terminology or style

3. **Context Understanding**
   - Understand the code issue context from the description
   - Match defects that address the same code problem even if described from different angles

### Matching Rules:

- **True Positive (TP)**: A model defect matches a ground truth defect if they describe the same or highly similar code issue semantically
- **False Positive (FP)**: A model defect that cannot be matched to any ground truth defect (the model incorrectly identified a non-existent issue)
- **False Negative (FN)**: A ground truth defect that cannot be matched to any model defect (the model missed a real issue)

### Important Guidelines:

1. **One-to-One Matching**: Each model defect can match at most one ground truth defect, and vice versa
2. **Best Match Priority**: If a model defect could potentially match multiple ground truth defects, choose the best semantic match
3. **Strict Matching**: Only match defects that are semantically similar. Do not force matches for dissimilar defects
4. **Completeness**: Ensure you check all model defects against all ground truth defects

The output format must be valid JSON containing the matching results."""

    gt_defects_text = []
    for i, defect in enumerate(ground_truth_defects, 1):
        defect_id = defect.get('defect_id', 'N/A')
        title = defect.get('title', 'N/A')
        description = defect.get('description', 'N/A')
        gt_defects_text.append(f"""
<ground_truth_defect_{i}>
<id>{defect_id}</id>
<title>{title}</title>
<description>{description}</description>
</ground_truth_defect_{i}>""")
    
    model_defects_text = []
    for i, defect in enumerate(model_defects, 1):
        defect_id = defect.get('defect_id', 'N/A')
        title = defect.get('title', 'N/A')
        description = defect.get('description', 'N/A')
        model_defects_text.append(f"""
<model_defect_{i}>
<id>{defect_id}</id>
<title>{title}</title>
<description>{description}</description>
</model_defect_{i}>""")
    
    user_prompt = f"""Please determine whether the defects in the model output match the defects in the ground truth based on semantic similarity.

## Ground Truth Defects (Total: {len(ground_truth_defects)})

{''.join(gt_defects_text)}

## Model Output Defects (Total: {len(model_defects)})

{''.join(model_defects_text)}

## Instructions

1. For each model defect, determine if it semantically matches any ground truth defect
2. Compare the core problem described in the title and description
3. Match defects that describe the same underlying code issue, even if worded differently
4. Assign each model defect to at most one ground truth defect (the best match)
5. Identify all unmatched ground truth defects (false negatives)
6. Identify all unmatched model defects (false positives)

## Output Format

You must output valid JSON with the following structure:

{{
  "matches": [
    {{
      "model_defect_index": 1,
      "ground_truth_defect_index": 1,
      "match_confidence": "high",
      "match_reason": "Both defects describe the same null pointer dereference issue in the same code context"
    }}
  ],
  "unmatched_ground_truth": [2, 3],
  "summary": {{
    "total_ground_truth": {len(ground_truth_defects)},
    "total_model_outputs": {len(model_defects)},
    "matched_pairs": 0,
    "false_positives": 0,
    "false_negatives": 0
  }}
}}

### Field Descriptions:

- **matches**: Array of match results
  - `model_defect_index`: Index of the model defect (starting from 1)
  - `ground_truth_defect_index`: Index of the matched ground truth defect (starting from 1), or `null` if no match
  - `match_confidence`: One of "high", "medium", "low", "none"
  - `match_reason`: Brief explanation of why they match (or why they don't match if confidence is "none")

- **unmatched_ground_truth**: Array of ground truth defect indices (starting from 1) that were not matched by any model defect

- **summary**: Overall statistics
  - `matched_pairs`: Number of successfully matched pairs (TP)
  - `false_positives`: Number of model defects that could not be matched (FP)
  - `false_negatives`: Number of ground truth defects that were not matched (FN)

**CRITICAL**: Output ONLY valid JSON. Do not include markdown code blocks, explanations outside the JSON, or any other text."""

    return system_prompt, user_prompt


def load_ground_truth(language: str, pr_data_dir: Path) -> Dict[str, Dict]:
    """
    Load ground truth data for specified language
    
    Args:
        language: Language type
        pr_data_dir: Directory containing PR data
    
    Returns:
        Mapping of task_id -> ground_truth
    """
    pr_data_file = pr_data_dir / f"{language}.jsonl"
    
    if not pr_data_file.exists():
        logging.error(f"Ground truth file not found: {pr_data_file}")
        return {}
    
    ground_truth_map = {}
    
    with open(pr_data_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            try:
                data = json.loads(line)
                instance_id = data.get("instance_id", "")
                if not instance_id:
                    continue
                
                final_annotation = data.get("final_annotation", {})
                if not final_annotation:
                    continue
                
                rounds = final_annotation.get("rounds", {})
                for round_id, round_data in rounds.items():
                    round_defects = round_data.get("defects", [])
                    if round_defects:
                        task_id = f"{instance_id}_round{round_id}"
                        ground_truth_map[task_id] = {"defects": round_defects}
                    
            except json.JSONDecodeError as e:
                logging.warning(f"Failed to parse line in ground truth file: {e}")
                continue
    
    logging.info(f"Loaded {len(ground_truth_map)} ground truth entries for {language}")
    return ground_truth_map


def load_model_responses(model_name: str, language: str, llm_responses_dir: Path) -> Dict[str, Dict]:
    """
    Load model response data for specified model and language
    
    Args:
        model_name: Model name
        language: Language type
        llm_responses_dir: Directory containing LLM responses
    
    Returns:
        Mapping of task_id -> generated_results
    """
    responses_file = llm_responses_dir / model_name / language / f"{language}_responses.jsonl"
    
    if not responses_file.exists():
        logging.error(f"Model responses file not found: {responses_file}")
        return {}
    
    responses_map = {}
    
    with open(responses_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            try:
                data = json.loads(line)
                task_id = data.get("task_id", "")
                generated_results = data.get("generated_results", {})
                
                if task_id:
                    responses_map[task_id] = generated_results
                    
            except json.JSONDecodeError as e:
                logging.warning(f"Failed to parse line in responses file: {e}")
                continue
    
    logging.info(f"Loaded {len(responses_map)} model responses for {model_name}/{language}")
    return responses_map


def save_prompt(task_id: str, system_prompt: str, user_prompt: str, ground_truth_defects: List[Dict], output_dir: Path):
    """
    Save prompt to file
    
    Args:
        task_id: Task ID
        system_prompt: System prompt
        user_prompt: User prompt
        ground_truth_defects: Ground truth defects list
        output_dir: Output directory
    """
    prompt_data = {
        "task_id": task_id,
        "prompt": {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt
        },
        "ground_truth": ground_truth_defects
    }
    
    output_file = output_dir / f"{task_id}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(prompt_data, f, ensure_ascii=False, indent=2)
    
    return output_file


def main():
    """Main function for standalone execution"""
    parser = argparse.ArgumentParser(description="Generate and save prompts for all evaluation tasks")
    
    parser.add_argument("--model", type=str, required=True, help="Model name (e.g., deepseek-v3.2, gpt-5.2)")
    parser.add_argument("--language", type=str, required=True, choices=["py", "java", "js", "ts", "csharp"], help="Programming language")
    parser.add_argument("--limit", type=int, help="Limit number of tasks to generate (for testing)")
    parser.add_argument("--output-dir", type=str, help="Output directory (default: prompt directory in current directory)")
    
    args = parser.parse_args()
    
    script_dir = Path(__file__).resolve().parent
    pr_data_dir = script_dir.parent.parent / "MCR-bench" / "dataset"
    llm_responses_dir = script_dir.parent.parent / "generated_results"
    
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = script_dir / "prompt"
    
    output_dir = output_dir / args.model / args.language
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(script_dir / "prompt_builder.log"),
            logging.StreamHandler()
        ]
    )
    
    ground_truth_map = load_ground_truth(args.language, pr_data_dir)
    responses_map = load_model_responses(args.model, args.language, llm_responses_dir)
    
    if not ground_truth_map:
        logging.error(f"No ground truth data found for {args.language}")
        return
    
    if not responses_map:
        logging.error(f"No model responses found for {args.model}/{args.language}")
        return
    
    common_tasks = set(ground_truth_map.keys()) & set(responses_map.keys())
    
    if not common_tasks:
        logging.warning(f"No common tasks found between ground truth and model responses for {args.model}/{args.language}")
        return
    
    logging.info(f"Found {len(common_tasks)} common tasks for {args.model}/{args.language}")
    
    if args.limit:
        common_tasks = sorted(common_tasks)[:args.limit]
        logging.info(f"Limited to {len(common_tasks)} tasks for prompt generation")
    
    saved_count = 0
    for i, task_id in enumerate(sorted(common_tasks), 1):
        logging.info(f"[{args.model}/{args.language}] Generating prompt {i}/{len(common_tasks)}: {task_id}")
        
        ground_truth = ground_truth_map[task_id]
        generated_results = responses_map[task_id]
        
        ground_truth_defects = ground_truth.get("defects", []) if ground_truth else []
        model_defects = generated_results.get("defects", []) if generated_results else []
        
        if not ground_truth_defects and not model_defects:
            logging.debug(f"[{task_id}] Both ground truth and model defects are empty, skipping prompt generation (TN=1)")
            continue
        
        system_prompt, user_prompt = build_matching_prompt(
            ground_truth_defects, 
            model_defects, 
            task_id
        )
        
        output_file = save_prompt(task_id, system_prompt, user_prompt, ground_truth_defects, output_dir)
        saved_count += 1
        
        if (i + 1) % 10 == 0:
            logging.info(f"[{args.model}/{args.language}] Progress: {i+1}/{len(common_tasks)} prompts saved")
    
    summary = {
        "model_name": args.model,
        "language": args.language,
        "total_tasks": len(common_tasks),
        "saved_prompts": saved_count,
        "output_directory": str(output_dir)
    }
    
    summary_file = output_dir / "summary.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    logging.info(f"\n{'='*80}")
    logging.info(f"Prompt Generation Summary for {args.model}/{args.language}")
    logging.info(f"{'='*80}")
    logging.info(f"Total tasks: {len(common_tasks)}")
    logging.info(f"Saved prompts: {saved_count}")
    logging.info(f"Output directory: {output_dir}")
    logging.info(f"{'='*80}\n")


if __name__ == "__main__":
    main()
