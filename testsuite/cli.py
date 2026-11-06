from __future__ import annotations

import argparse
import json
import math
import os
import signal
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from testsuite.client import GuideOSClient, validate_target
from testsuite.lifecycle import git_metadata, managed_api
from testsuite.loader import SCENARIO_DIRECTORY, content_hash, load_scenarios
from testsuite.redaction import SECRET_KEY, redact
from testsuite.reports import build_report, pass_rates
from testsuite.repository import JsonlRepository, TERMINAL
from testsuite.runner import execute_run, now, rerun_scenarios, run_exit_code


def parser():
    result = argparse.ArgumentParser(description="Isolierte GuideOS REST-Szenarien ausführen und auswerten.")
    result.add_argument("--scenario", action="append", default=[])
    result.add_argument("--scenario-dir", type=Path, default=SCENARIO_DIRECTORY)
    result.add_argument("--tag", action="append", default=[])
    result.add_argument("--include-llm", action="store_true")
    result.add_argument("--base-url")
    result.add_argument("--target-manifest", type=Path)
    result.add_argument("--allow-host", action="append", default=[])
    result.add_argument("--allow-production", action="store_true")
    result.add_argument("--ollama-endpoint", default=os.environ.get("GUIDEOS_OLLAMA_ENDPOINT"))
    result.add_argument("--ollama-model", default=os.environ.get("GUIDEOS_OLLAMA_MODEL"))
    result.add_argument("--knowledge-dir", type=Path,
                        help="Dokumente unter <dir>/<domain_id>/ für die Wissensbasis der temporären API")
    result.add_argument("--connect-timeout", type=float, default=5)
    result.add_argument("--read-timeout", type=float, default=30)
    result.add_argument("--llm-timeout", type=float, default=150)
    result.add_argument("--results-dir", type=Path, default=Path(".guideos/testsuite"))
    result.add_argument("--database-url", default=os.environ.get("GUIDEOS_TESTSUITE_DATABASE_URL"))
    result.add_argument("--run-id")
    result.add_argument("--report", choices=["markdown", "html"], default="markdown")
    result.add_argument("--rerun")
    result.add_argument("--repeat", type=int, default=1)
    result.add_argument("--cleanup", action="store_true")
    result.add_argument("--older-than-days", type=int)
    result.add_argument("--confirm", action="store_true")
    result.add_argument("--include-rated", action="store_true")
    return result


def export_report(repository, run_id, format, directory):
    data = repository.read(run_id)
    content = build_report(data, format)
    artifact = {"id": uuid4().hex, "run_id": run_id, "case_id": None,
                "artifact_type": format, "content": content, "content_hash": content_hash(content), "created_at": now()}
    repository.write("artifacts", artifact)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.{'md' if format == 'markdown' else 'html'}"
    if path.is_symlink():
        raise ValueError("Symbolische Reportdateien sind nicht erlaubt.")
    path.write_text(content, encoding="utf-8")
    return path


def cleanup(args, repository):
    if bool(args.run_id) == (args.older_than_days is not None):
        raise ValueError("Bereinigung benötigt entweder --run-id oder --older-than-days.")
    if args.older_than_days is not None and args.older_than_days < 0:
        raise ValueError("Altersgrenze darf nicht negativ sein.")
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days or 0)
    candidates = []
    for run in repository.runs():
        if run["status"] not in TERMINAL:
            continue
        if args.run_id:
            selected = run["id"] == args.run_id
        else:
            selected = bool(run.get("finished_at")) and datetime.fromisoformat(run["finished_at"]) < cutoff
        if selected:
            candidates.append(run["id"])
    # Expert ratings are not reproducible; rated runs survive cleanup unless explicitly included.
    kept_rated = [] if args.include_rated else [run_id for run_id in candidates if repository.read(run_id)["ratings"]]
    candidates = [run_id for run_id in candidates if run_id not in kept_rated]
    if args.confirm:
        for run_id in candidates:
            repository.delete(run_id)
            for suffix in ("md", "html"):
                path = args.results_dir / f"{run_id}.{suffix}"
                if path.is_file() and not path.is_symlink():
                    path.unlink()
    return {"action": "cleanup", "confirmed": args.confirm, "run_ids": candidates, "kept_rated": kept_rated}


def _main(args):
    if args.rerun and (args.run_id or args.cleanup or args.scenario or args.tag):
        raise ValueError("--rerun kann nicht mit Lauf-ID, Bereinigung oder Filtern kombiniert werden.")
    if not args.cleanup and (args.confirm or args.include_rated or args.older_than_days is not None):
        raise ValueError("Bereinigungsoptionen benötigen --cleanup.")
    if any(not math.isfinite(v) or v <= 0 for v in (args.connect_timeout, args.read_timeout, args.llm_timeout)):
        raise ValueError("Timeouts müssen endlich und positiv sein.")
    if args.repeat < 1:
        raise ValueError("--repeat muss mindestens 1 sein.")
    secrets = [value for key, value in os.environ.items() if SECRET_KEY.search(key)]
    if args.database_url:
        secrets.append(args.database_url)
        from testsuite.postgres import PostgresRepository, migrate
        migrate(args.database_url)
        repository = PostgresRepository(args.database_url)
    else:
        repository = JsonlRepository(args.results_dir)
    if args.cleanup:
        return 0, cleanup(args, repository)
    if args.run_id:
        path = export_report(repository, args.run_id, args.report, args.results_dir)
        return 0, {"run_id": args.run_id, "report": str(path)}
    scenarios = rerun_scenarios(repository.read(args.rerun)) if args.rerun else load_scenarios(args.scenario_dir)
    unknown = set(args.scenario) - {s.id for s in scenarios}
    if unknown:
        raise ValueError(f"Unbekannte Szenario-IDs: {', '.join(sorted(unknown))}")
    scenarios = [s for s in scenarios if (not args.scenario or s.id in args.scenario)
                 and (not args.tag or set(args.tag) <= set(s.tags))]
    if not scenarios:
        raise ValueError("Keine Szenarien ausgewählt.")
    if args.base_url:
        manifest = json.loads(args.target_manifest.read_text()) if args.target_manifest else None
        validate_target(args.base_url, manifest, args.allow_host, args.allow_production)
        manifest = {**manifest, "source": "externally_declared"}
        context = nullcontext({"base_url": args.base_url, "manifest": manifest})
    else:
        if args.target_manifest or args.allow_production or args.allow_host:
            raise ValueError("Externe Zieloptionen benötigen --base-url.")
        if args.knowledge_dir and not args.knowledge_dir.is_dir():
            raise ValueError(f"Wissensverzeichnis {args.knowledge_dir} wurde nicht gefunden.")
        if args.include_llm and (not args.ollama_endpoint or not args.ollama_model):
            raise ValueError("Echte LLM-Läufe benötigen expliziten Ollama-Endpunkt und Modell.")
        context = managed_api(ollama_endpoint=args.ollama_endpoint if args.include_llm else None,
                              ollama_model=args.ollama_model, knowledge_dir=args.knowledge_dir)
    with context as target:
        with GuideOSClient(target["base_url"], connect_timeout=args.connect_timeout,
                           read_timeout=args.read_timeout, llm_timeout=args.llm_timeout) as client:
            run = execute_run(scenarios, client, repository, include_llm=args.include_llm,
                              metadata={"runner_git": git_metadata(), "target": target["manifest"],
                                        "include_llm": args.include_llm, "repeat": args.repeat,
                                        "timeouts": {"connect": args.connect_timeout, "read": args.read_timeout, "llm": args.llm_timeout}},
                              parent_run_id=args.rerun, secrets=secrets, repeat=args.repeat)
    report = export_report(repository, run["id"], args.report, args.results_dir)
    data = repository.read(run["id"])
    counts = {status: sum(case["status"] == status for case in data["cases"]) for status in sorted(TERMINAL)}
    summary = {"run_id": run["id"], "status": run["status"], "counts": counts, "report": str(report)}
    if args.repeat > 1:
        summary["pass_rates"] = pass_rates(data["cases"])
    return run_exit_code(run), summary


def main(argv=None):
    args = parser().parse_args(argv)
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        code, summary = _main(args)
    except KeyboardInterrupt:
        code, summary = 130, {"status": "cancelled"}
    except Exception as exc:
        secrets = [value for key, value in os.environ.items() if SECRET_KEY.search(key)]
        if args.database_url:
            secrets.append(args.database_url)
        detail = ("Szenario ungültig: " + ", ".join(".".join(map(str, e["loc"])) + ": " + e["type"]
                  for e in exc.errors(include_input=False, include_context=False))
                  if isinstance(exc, ValidationError) else str(exc))
        code, summary = 2, {"status": "error", "error_code": type(exc).__name__,
                            "message": redact(detail, persist_raw=True, secrets=secrets)}
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(summary, ensure_ascii=False))
    return code
