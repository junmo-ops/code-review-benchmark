#!/usr/bin/env python3
"""
Metrics calculator for model evaluation

This module is responsible for:
1. Reading evaluation results from files
2. Calculating evaluation metrics (Precision, Recall, F1)
3. Generating summary reports

Can be run independently:
    python metrics_calculator.py --model <model_name> --language <lang>
"""

import json
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Optional

script_dir = Path(__file__).resolve().parent
OUTPUT_DIR = script_dir / "LLM-as-a-judge" / "LLM_responses" / "1"
PROMPTS_JSONL_DIR = script_dir.parent / "Prompt_construction" / "prompts"

SUPPORTED_LANGUAGES = ["py", "java", "js", "ts", "csharp"]


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(script_dir / "evaluate_with_llm.log"),
        logging.StreamHandler()
    ]
)


def count_prompt_tasks(model_name: str, language: str) -> int:
    """
    Count total tasks from prompts JSONL file for a specific language
    Only counts tasks where ground_truth.defects is not empty
    
    Args:
        model_name: Model name (not used, kept for compatibility)
        language: Language type
    
    Returns:
        Total number of valid tasks (with non-empty ground_truth.defects) in the prompts JSONL file
    """
    prompts_jsonl_file = PROMPTS_JSONL_DIR / f"{language}_prompts.jsonl"
    
    if not prompts_jsonl_file.exists():
        logging.warning(f"Prompts JSONL file not found: {prompts_jsonl_file}")
        return 0
    
    count = 0
    empty_gt_count = 0
    try:
        with open(prompts_jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    task_id = data.get("task_id", "")
                    if not task_id:
                        continue
                    
                    ground_truth = data.get("ground_truth", {})
                    defects = ground_truth.get("defects", []) if isinstance(ground_truth, dict) else []
                    
                    if isinstance(defects, list) and len(defects) > 0:
                        count += 1
                    else:
                        empty_gt_count += 1
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logging.warning(f"Error reading prompts JSONL file {prompts_jsonl_file}: {e}")
        return 0
    
    if empty_gt_count > 0:
        logging.debug(f"Excluded {empty_gt_count} tasks with empty ground_truth.defects for {language}")
    
    return count


def load_evaluation_results(results_file: Path) -> List[Dict]:
    """
    Load evaluation results from file
    
    Args:
        results_file: Path to evaluation results JSONL file
    
    Returns:
        List of evaluation results
    """
    if not results_file.exists():
        logging.error(f"Evaluation results file not found: {results_file}")
        return []
    
    results = []
    
    with open(results_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            try:
                result = json.loads(line)
                results.append(result)
            except json.JSONDecodeError as e:
                logging.warning(f"Failed to parse line in results file: {e}")
                continue
    
    logging.info(f"Loaded {len(results)} evaluation results from {results_file}")
    return results


def calculate_metrics(total_tp: int, total_fp: int, total_fn: int) -> Dict[str, float]:
    """
    Calculate evaluation metrics
    
    Args:
        total_tp: Total true positives
        total_fp: Total false positives
        total_fn: Total false negatives
    
    Returns:
        Dictionary containing precision, recall, and f1
    """
    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1
    }


def get_all_models() -> List[str]:
    """
    Get all available model names from OUTPUT_DIR
    
    Returns:
        List of model names
    """
    if not OUTPUT_DIR.exists():
        logging.warning(f"Output directory not found: {OUTPUT_DIR}")
        return []
    
    models = []
    for item in OUTPUT_DIR.iterdir():
        if item.is_dir() and not item.name.startswith('.'):
            models.append(item.name)
    
    return sorted(models)


def get_available_languages_for_model(model_name: str) -> List[str]:
    """
    Get available languages for a specific model
    
    Args:
        model_name: Model name
    
    Returns:
        List of available languages for this model
    """
    model_dir = OUTPUT_DIR / model_name
    if not model_dir.exists():
        return []
    
    languages = []
    for lang in SUPPORTED_LANGUAGES:
        results_file = model_dir / lang / f"{lang}_evaluation_results.jsonl"
        if results_file.exists():
            languages.append(lang)
    
    return sorted(languages)


def calculate_metrics_from_results(
    model_name: str,
    language: str,
    results_file: Optional[Path] = None
):
    """
    Calculate metrics from evaluation results file
    
    Each task already contains precision, recall, and f1.
    We calculate the average (macro average) for each language.
    
    Args:
        model_name: Model name
        language: Language type
        results_file: Path to evaluation results file (if None, auto-detect)
    
    Returns:
        Summary dictionary with metrics, or None if no results found
    """
    if results_file is None:
        results_file = OUTPUT_DIR / model_name / language / f"{language}_evaluation_results.jsonl"
    
    task_results = load_evaluation_results(results_file)
    
    if not task_results:
        logging.warning(f"No evaluation results found for {model_name}/{language}, skipping...")
        return None
    
    seen_task_ids = set()
    deduplicated_results = []
    duplicate_count = 0
    
    for task in task_results:
        task_id = task.get("task_id", "")
        if task_id and task_id not in seen_task_ids:
            seen_task_ids.add(task_id)
            deduplicated_results.append(task)
        elif task_id:
            duplicate_count += 1
    
    if duplicate_count > 0:
        logging.warning(f"Found {duplicate_count} duplicate task evaluations for {model_name}/{language}. "
                      f"Using {len(deduplicated_results)} unique tasks.")
    
    task_results = deduplicated_results
    
    total_tasks_in_prompts = count_prompt_tasks(model_name, language)
    
    if total_tasks_in_prompts == 0:
        logging.warning(f"No tasks found in prompts directory for {model_name}/{language}, using evaluated tasks count as denominator")
        total_tasks_in_prompts = len(task_results)
    
    precisions = []
    recalls = []
    f1_scores = []
    
    for task in task_results:
        if "precision" in task:
            precisions.append(task["precision"])
        if "recall" in task:
            recalls.append(task["recall"])
        if "f1" in task:
            f1_scores.append(task["f1"])
    
    avg_precision = sum(precisions) / total_tasks_in_prompts if total_tasks_in_prompts > 0 else 0.0
    avg_recall = sum(recalls) / total_tasks_in_prompts if total_tasks_in_prompts > 0 else 0.0
    avg_f1 = sum(f1_scores) / total_tasks_in_prompts if total_tasks_in_prompts > 0 else 0.0
    
    if avg_precision > 1.0:
        logging.warning(f"Precision > 1.0 ({avg_precision:.4f}) for {model_name}/{language}. "
                       f"This may indicate duplicate evaluations. Using evaluated tasks as denominator.")
        avg_precision = sum(precisions) / len(precisions) if len(precisions) > 0 else 0.0
        avg_recall = sum(recalls) / len(recalls) if len(recalls) > 0 else 0.0
        avg_f1 = sum(f1_scores) / len(f1_scores) if len(f1_scores) > 0 else 0.0
    
    summary = {
        "model_name": model_name,
        "language": language,
        "total_tasks_in_prompts": total_tasks_in_prompts,
        "evaluated_tasks": len(task_results),
        "duplicate_evaluations": duplicate_count,
        "metrics": {
            "precision": avg_precision,
            "recall": avg_recall,
            "f1": avg_f1
        }
    }
    
    output_dir = OUTPUT_DIR / model_name / language
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_file = output_dir / f"{language}_summary.json"
    
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    logging.info(f"\n{'='*80}")
    logging.info(f"Metrics Summary for {model_name}/{language}")
    logging.info(f"{'='*80}")
    logging.info(f"Total tasks in prompts: {total_tasks_in_prompts}")
    logging.info(f"Evaluated tasks: {len(task_results)}")
    if duplicate_count > 0:
        logging.info(f"Duplicate evaluations removed: {duplicate_count}")
    logging.info(f"Precision: {avg_precision:.4f} (average over {total_tasks_in_prompts} tasks)")
    logging.info(f"Recall: {avg_recall:.4f} (average over {total_tasks_in_prompts} tasks)")
    logging.info(f"F1 Score: {avg_f1:.4f} (average over {total_tasks_in_prompts} tasks)")
    logging.info(f"{'='*80}\n")
    
    return summary


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Calculate metrics from evaluation results file")
    
    parser.add_argument("--model", type=str, help="Model name (e.g., deepseek-v3.2, claude-haiku-4-5-20251001). If not specified, process all models")
    parser.add_argument("--language", type=str, choices=SUPPORTED_LANGUAGES, help="Programming language. If not specified, process all languages")
    parser.add_argument("--results-file", type=str, help="Evaluation results file path (if not specified, automatically read from default location)")
    
    args = parser.parse_args()
    
    if args.model:
        models_to_process = [args.model]
    else:
        models_to_process = get_all_models()
        if not models_to_process:
            logging.error("No models found in output directory. Please specify --model or ensure evaluation results exist.")
            return
        logging.info(f"Found {len(models_to_process)} models: {models_to_process}")
    
    if args.language:
        languages_to_process = [args.language]
    else:
        languages_to_process = SUPPORTED_LANGUAGES
        logging.info(f"Processing all supported languages: {languages_to_process}")
    
    processed = 0
    skipped = 0
    
    logging.info(f"\n{'='*80}")
    logging.info(f"Starting metrics calculation")
    logging.info(f"Models to process: {len(models_to_process)}")
    logging.info(f"Languages to process: {len(languages_to_process)}")
    logging.info(f"{'='*80}\n")
    
    for model_name in models_to_process:
        if args.language:
            model_languages = [args.language]
        else:
            model_languages = get_available_languages_for_model(model_name)
            if not model_languages:
                logging.warning(f"No evaluation results found for model {model_name}, skipping...")
                skipped += len(languages_to_process)
                continue
        
        for language in model_languages:
            if language not in languages_to_process:
                continue
            
            processed += 1
            logging.info(f"\n{'='*80}")
            logging.info(f"Processing {model_name}/{language} ({processed}/{len(models_to_process) * len(languages_to_process)})")
            logging.info(f"{'='*80}")
            
            results_file = Path(args.results_file) if args.results_file else None
            result = calculate_metrics_from_results(
                model_name=model_name,
                language=language,
                results_file=results_file
            )
            
            if not result:
                skipped += 1
    
    total_attempted = processed + skipped
    logging.info(f"\n{'='*80}")
    logging.info(f"Metrics calculation completed")
    logging.info(f"{'='*80}")
    logging.info(f"Total attempted: {total_attempted}")
    logging.info(f"Successfully processed: {processed}")
    logging.info(f"Skipped (no results): {skipped}")
    logging.info(f"{'='*80}\n")


if __name__ == "__main__":
    main()

