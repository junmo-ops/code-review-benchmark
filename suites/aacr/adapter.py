"""AACR data/scoring adapter; the runner is unaware of its reference schema."""
import hashlib,json,subprocess,sys
from pathlib import Path
from runner.contracts import ReviewCase
from runner.storage import ROOT
from vendor.aacr.evaluation.schema import load_instances

class AACRSuite:
    id = "aacr"
    name = "AACR-Bench"
    version = "1"
    parameters = {"eval_rounds": 3, "line_k": 1}
    required_capabilities = frozenset({"git_range", "findings"})

    def load_cases(self, dataset):
        # References are validated by the inherited schema and stay in the suite.
        return [ReviewCase(i.instance_id, i.repo, i.base_commit, i.head_commit)
                for i in load_instances(dataset)]

    def identity(self):
        paths = []
        for directory in (ROOT / "suites/aacr", ROOT / "vendor/aacr"):
            paths += [p for p in directory.rglob("*") if p.is_file()
                      and "__pycache__" not in p.parts
                      and (p.suffix == ".py" or p.name in ("UPSTREAM.json", "LICENSE"))]
        files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(paths)}
        return {"id": self.id, "name": self.name, "adapter_version": self.version,
                "required_capabilities": sorted(self.required_capabilities),
                "evaluator": "aacr-inherited-local-snapshot", "files": files}

    def provenance(self, dataset):
        manifest = json.loads((ROOT / "data/sources/aacr/provenance.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(Path(dataset).read_bytes()).hexdigest()
        known = next((d for d in manifest["datasets"] if d["sha256"] == digest), None)
        if known:
            source = known.get("source", {})
            raw_file = ROOT / source.get("raw_file", manifest["raw_file"])
            expected_raw_sha = source.get("raw_sha256", manifest["raw_sha256"])
            if hashlib.sha256(raw_file.read_bytes()).hexdigest() != expected_raw_sha:
                raise ValueError("AACR 原始数据与来源清单哈希不一致")
            return {"status": source.get("status", "retained_pilot_source"), "manifest": manifest,
                    "selected_dataset": known}
        return {"status": "unverified_custom_dataset", "dataset_sha256": digest,
                "note": "仅验证 AACR 输入格式，未自动认定此清单来自官方数据；需另行记录来源。"}

    def _execute(self, stage, run):
        # Upstream uses generic module names (config/judge/pipeline). Isolate them
        # so another suite's scorer cannot reuse imported globals or patched clients.
        subprocess.run([sys.executable, "-m", "suites.aacr", stage, "--run", str(run)],
                       cwd=ROOT, check=True)

    def evaluate(self, cases, run, config):
        self._execute("eval", run)

    def report(self, run, config):
        self._execute("report", run)

    def convert(self, source, output, limit=None, seed=42):
        args = [sys.executable, "-m", "suites.aacr.convert", "--input", str(source),
                "--output", str(output), "--seed", str(seed), "--validate"]
        if limit is not None:
            args += ["--limit", str(limit)]
        subprocess.run(args, cwd=ROOT, check=True)
