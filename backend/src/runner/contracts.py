"""The runner passes only code ranges to reviewers; references stay suite-owned."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass(frozen=True)
class ReviewCase:
    instance_id: str
    repo: str
    base_commit: str
    head_commit: str

    @property
    def safe_id(self):
        return self.instance_id.replace("/", "__")

class SuiteAdapter(Protocol):
    id: str
    name: str
    version: str
    required_capabilities: frozenset[str]
    parameters: dict
    def load_cases(self, dataset: Path) -> list[ReviewCase]: ...
    def identity(self) -> dict: ...
    def provenance(self, dataset: Path) -> dict: ...
    def evaluate(self, cases, run: Path, config: dict): ...
    def report(self, run: Path, config: dict): ...
    def convert(self, source: Path, output: Path, limit=None, seed=42): ...
