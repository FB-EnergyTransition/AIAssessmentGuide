"""Retrieval-Evaluation: findet die Wissenssuche einer Domäne die erwarteten
Passagen? Dieselben Fragen laufen in den Modi lexikalisch, hybrid und hybrid
mit Reranker gegen die konfigurierte Wissensbasis; ausgegeben werden
Trefferquote (ein erwarteter Treffer unter den ersten k), MRR und Dauer je
Modus sowie der Rang je Frage. Die Prüfsets liegen unter
testsuite/retrieval/<domain_id>.json; ihre erwarteten Treffer legt eine
Fachperson fest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from guideos.config import DEFAULT_OLLAMA_ENDPOINT, DEFAULT_PROVIDER, configure_logging
from guideos.domains.catalog import load_active_domain_pack
from guideos.domains.models import DomainPack
from guideos.knowledge.factory import knowledge_store_from_env
from guideos.knowledge.models import RetrievedPassage
from guideos.knowledge.service import KnowledgeService
from guideos.providers.base import LLMProvider
from guideos.providers.registry import get_provider

EVAL_SET_DIRECTORY = Path(__file__).resolve().parent / "retrieval"
MODES = ("lexical", "hybrid", "rerank")


class ExpectedHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    pages: list[int] = Field(default_factory=list)


class EvalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    query: str = Field(min_length=1)
    expected: list[ExpectedHit] = Field(min_length=1)


class EvalSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    domain_id: str = Field(min_length=1)
    description: str | None = None
    queries: list[EvalQuery] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [query.id for query in self.queries]
        if len(set(ids)) != len(ids):
            raise ValueError("Doppelte Frage-ID im Prüfset.")
        return self


def load_eval_set(path: Path) -> EvalSet:
    return EvalSet.model_validate_json(Path(path).read_text(encoding="utf-8"))


def bundled_eval_sets(directory: Path = EVAL_SET_DIRECTORY) -> list[EvalSet]:
    return [load_eval_set(path) for path in sorted(directory.glob("*.json"))]


def _page(locator: str | None) -> int | None:
    match = re.search(r"(\d+)", locator or "")
    return int(match.group(1)) if match else None


def hit_rank(passages: list[RetrievedPassage], expected: list[ExpectedHit] | list[dict]) -> int | None:
    """1-based rank of the first passage that is an expected hit (same title,
    and one of the pages when pages are given); None without a hit."""
    hits = [ExpectedHit.model_validate(item) if isinstance(item, dict) else item for item in expected]
    for rank, passage in enumerate(passages, 1):
        for hit in hits:
            if passage.title == hit.title and (not hit.pages or _page(passage.locator) in hit.pages):
                return rank
    return None


def evaluate_mode(service: KnowledgeService, pack: DomainPack, eval_set: EvalSet, endpoint: str, k: int) -> dict:
    queries, reported = [], set()
    for item in eval_set.queries:
        started = monotonic()
        passages, mode = service.search_with_mode(pack, item.query, endpoint, limit=k)
        seconds = monotonic() - started
        if passages:
            reported.add(mode)
        queries.append({"id": item.id, "rank": hit_rank(passages, item.expected), "seconds": round(seconds, 3),
                        "top": [(p.title, p.locator) for p in passages]})
    ranks = [query["rank"] for query in queries]
    return {"mode": "/".join(sorted(reported)), "queries": queries,
            "hit_rate": sum(rank is not None for rank in ranks) / len(ranks),
            "mrr": sum(1 / rank for rank in ranks if rank is not None) / len(ranks),
            "mean_seconds": round(sum(query["seconds"] for query in queries) / len(queries), 3)}


def evaluate(services: dict[str, KnowledgeService], pack: DomainPack, eval_set: EvalSet, endpoint: str,
             k: int = 5) -> dict:
    return {"domain_id": eval_set.domain_id, "k": k, "queries": len(eval_set.queries),
            "modes": {name: evaluate_mode(service, pack, eval_set, endpoint, k) for name, service in services.items()}}


def render_markdown(report: dict) -> str:
    k = report["k"]
    lines = [f"# Retrieval-Evaluation {report['domain_id']} ({report['queries']} Fragen, k={k})", "",
             f"| Modus | tatsächlich | Treffer@{k} | MRR | Ø Sekunden |", "| --- | --- | --- | --- | --- |"]
    for name, result in report["modes"].items():
        lines.append(f"| {name} | {result['mode']} | {result['hit_rate']:.0%} | {result['mrr']:.2f} | "
                     f"{result['mean_seconds']:.2f} |")
    names = list(report["modes"])
    lines += ["", "| Frage | " + " | ".join(names) + " |", "| --- |" + " --- |" * len(names)]
    per_query = {name: {q["id"]: q["rank"] for q in result["queries"]} for name, result in report["modes"].items()}
    for query in report["modes"][names[0]]["queries"]:
        cells = [str(per_query[name][query["id"]]) if per_query[name][query["id"]] is not None else "–"
                 for name in names]
        lines.append(f"| {query['id']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def services_from_env(provider: LLMProvider, modes: list[str], rerank_model: str | None,
                      embedding_model: str | None) -> dict[str, KnowledgeService]:
    store = knowledge_store_from_env()
    services = {}
    for mode in modes:
        if mode == "lexical":
            services[mode] = KnowledgeService(store, provider=provider)
        elif mode == "hybrid":
            services[mode] = KnowledgeService(store, provider=provider, embedding_model=embedding_model)
        else:
            services[mode] = KnowledgeService(store, provider=provider, embedding_model=embedding_model,
                                              rerank_model=rerank_model)
    return services


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval-Evaluation der Wissenssuche je Domäne.")
    parser.add_argument("--domain", required=True, help="domain_id, z. B. verein-demo")
    parser.add_argument("--set", type=Path, help="Prüfset (Standard: testsuite/retrieval/<domain>.json)")
    parser.add_argument("--modes", default="lexical,hybrid,rerank", help="Kommagetrennt aus lexical, hybrid, rerank")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--embedding-model", default=os.environ.get("GUIDEOS_EMBEDDING_MODEL"))
    parser.add_argument("--rerank-model", default=os.environ.get("GUIDEOS_RERANK_MODEL"))
    parser.add_argument("--ollama-endpoint", default=os.environ.get("GUIDEOS_OLLAMA_ENDPOINT", DEFAULT_OLLAMA_ENDPOINT))
    parser.add_argument("--json", type=Path, help="Ergebnis zusätzlich als JSON speichern")
    args = parser.parse_args(argv)
    configure_logging()
    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    if any(mode not in MODES for mode in modes):
        parser.error(f"--modes erlaubt nur {', '.join(MODES)}.")
    if "hybrid" in modes or "rerank" in modes:
        if not args.embedding_model:
            parser.error("hybrid und rerank brauchen --embedding-model oder GUIDEOS_EMBEDDING_MODEL.")
    if "rerank" in modes and not args.rerank_model:
        parser.error("rerank braucht --rerank-model oder GUIDEOS_RERANK_MODEL.")
    eval_set = load_eval_set(args.set or EVAL_SET_DIRECTORY / f"{args.domain}.json")
    if eval_set.domain_id != args.domain:
        parser.error("Das Prüfset gehört zu einer anderen Domäne.")
    pack = load_active_domain_pack(args.domain)
    services = services_from_env(get_provider(DEFAULT_PROVIDER), modes, args.rerank_model, args.embedding_model)
    report = evaluate(services, pack, eval_set, args.ollama_endpoint, args.k)
    sys.stdout.write(render_markdown(report))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
