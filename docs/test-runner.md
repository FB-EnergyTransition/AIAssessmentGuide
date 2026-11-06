# GuideOS Test-Runner-Suite

Die Suite führt synthetische JSON-Szenarien über die versionierte REST-API aus.
Der Assessment-Core entscheidet weiterhin über Fragen, Validierung und Ergebnisse.
Der Runner prüft diese Ergebnisse und speichert Belege für Entwicklung und CI.

## Lokal starten

```bash
uv run python -m testsuite.runner
uv run python -m testsuite.runner --scenario verein_standard_sportverein --report html
uv run python -m testsuite.runner --tag deterministic --tag verein
```

Ohne URL startet jeder Lauf eine eigene Uvicorn-API auf einem freien Loopback-Port.
Ihr temporäres Arbeitsverzeichnis enthält den eigenen JSON-Assessment-Speicher.
Pilot-, Admin- und Datenbankvariablen werden aus der API-Umgebung entfernt. Lokale
Domain-Admin-Drafts werden nicht geladen; der Test verwendet die gebündelten Packs.
Die API und ihre Assessment-Dateien werden nach dem Lauf entfernt, auch bei
behandelten Fehlern, Strg+C und SIGTERM. Der automatische Start benötigt ein
POSIX-System, beispielsweise Linux oder macOS.

Die letzte stdout-Zeile ist ein JSON-Objekt mit `run_id`, `status`, Fallzahlen und
Reportpfad. Mit `--repeat N` läuft jedes ausgewählte Szenario N-mal als eigener
Fall; Summary und Report enthalten dann je Szenario die Bestehensquote
(`pass_rates`), um Modellverhalten zu messen statt einmalig zu bewerten. Ergebnisse landen standardmäßig unter `.guideos/testsuite/`:

- Eine JSONL-Datei je Lauf enthält append-only Updates für Läufe, Fälle,
  Requests, Responses, Assertions und eingebettete Report-Artefakte.
- Der aktuelle Zustand ergibt sich aus dem letzten Eintrag je Ergebnistyp und ID.
- Markdown ist das Standardformat; HTML zeigt denselben Inhalt mit escaped Text.
- LLM-Schritte zeigen im Report Eingabe und Modelltext: normalisierter Wert mit
  Confidence oder Rückfrage bei Freitext, die neueste Assistant-Antwort bei
  Fragehilfe und Konversation, Titel und Begründung bei LLM-Empfehlungen.
- Je Fall folgt ein Abschnitt `Dialog`, der das Gespräch chronologisch
  rekonstruiert: gestellte Frage mit Optionen, Nutzereingabe oder Wert,
  Rückmeldung von GuideOS, Validierungshinweise, Ergebnis mit
  Reason-Codes und Empfehlungen sowie Konversationen und LLM-Empfehlungen.
  Danach steht der Markdown-Assessment-Report der Session. Ohne
  `persist_raw: true` stehen in beiden Abschnitten nur `[REDACTED]`-Platzhalter.
- Ein fehlgeschlagener Schritt beendet seinen Fall; weitere Szenarien laufen weiter.
- Explizit erwartete fachliche Fehler lassen den Fall weiterlaufen.
- Laufstatus `error` hat Vorrang vor `failed`; deaktivierte LLM-Fälle sind `skipped`.

Exitcodes: `0` bestanden (gegebenenfalls mit sichtbaren Skips), `1` nicht erfüllte
Assertions, `2` Konfigurations-/Infrastrukturfehler, `130` Abbruch. Eine leere
Auswahl oder unbekannte Szenario-ID ergibt `2`.

## Szenarien schreiben

Dateien unter `testsuite/scenarios/` sind JSON; der Loader liest auch
Unterverzeichnisse. Eigene Verzeichnisse werden mit
`--scenario-dir PFAD` ausgewählt. Der Loader validiert alle JSON-Dateien vor der
Filterung; doppelte IDs (auch über Unterverzeichnisse hinweg) und nicht
unterstützte Schema-Versionen sind Fehler.
Mehrere `--scenario`-Angaben bilden eine Auswahl; mehrere `--tag`-Angaben müssen
alle zum Szenario passen. Konvention: Szenarien für behobene Fehler tragen das
Tag `regression` und nennen den Fehler in der `id` (etwa
`regression_unknown_year_revision`), damit `--tag regression` sie gesammelt
ausführt.

```json
{
  "schema_version": 1,
  "id": "verein_start",
  "version": "1.0.0",
  "domain_id": "verein-demo",
  "domain_version": "1.0.0",
  "tags": ["deterministic", "verein"],
  "data_classification": "synthetic",
  "persist_raw": true,
  "steps": [
    {"id": "start", "action": "start_assessment"},
    {
      "id": "answer",
      "action": "submit_answer",
      "payload": {"question_id": "building_type", "value": "single_family"},
      "assertions": [
        {"path": "/accepted_answer/normalized_value", "op": "eq", "expected": "single_family"},
        {"path": "/current_question/question_id", "op": "eq", "expected": "construction_year"}
      ]
    }
  ],
  "assertions": [{"path": "/status", "op": "eq", "expected": "in_progress"}]
}
```

Jeder Fall beginnt genau einmal mit `start_assessment` und erhält eine neue Session.
Vorher wird die aktive Domain-Version über `/api/v1/domains` geprüft. Payloads
enthalten keine Session-ID, keinen Provider-Endpunkt und kein Modell. Jeder
Fall läuft als eigener Teilnehmer: Der Runner schickt bei jeder Anfrage den
Header `X-GuideOS-Participant` mit der Fall-ID (`cases[].participant_id`), so
dass Sessions, Profil und Konversationen der Fälle eines Laufs getrennt
bleiben, auch gegen eine Pilot-Instanz mit PostgreSQL.

| Aktion | Payload-Felder |
| --- | --- |
| `start_assessment` | optional `locale`; Domain kommt aus dem Szenario |
| `get_session`, `get_report` | keine |
| `get_profile` | keine; Profil des Falls für die Domäne des Szenarios (`/api/v1/profiles/{domain_id}`); vor `complete` antwortet die API mit `profile_not_found` |
| `complete` | optional `knowledge_recommendations` (`true`/`false`); ohne das Feld bleibt der Abschluss deterministisch (keine KI-Empfehlungen). Mit `true` erzeugt die API beim Abschluss die ersten KI-Empfehlungen mit ihrem Standardmodell, wie es die Streamlit-Oberfläche tut; der Schritt zählt dann als Modellschritt (nur mit `--include-llm`) und die Antwort trägt sie unter `/result/knowledge_recommendations` und `/result/knowledge_generation` |
| `submit_answer`, `revise_answer` | `question_id`, `value`; optional `raw_answer`, `confidence` |
| `submit_file_answer` | `question_id`, `fixture` (Dateiname unter `testsuite/fixtures/datasets/`, ohne Pfad), optional `format_hint`; beantwortet eine Frage vom Typ `file`: der Dateiinhalt geht als Text im Wert `{filename, content, format_hint}` eines `submit_answer` an die API, GuideOS importiert ihn über das Werkzeug des Packs und speichert nur die Datensatzreferenz (`/accepted_answer/normalized_value/dataset_id`); Fehler des Werkzeugs stehen als `validation_errors` mit Code `tool_error` oder `tool_server_unavailable` |
| `submit_text_answer`, `question_help` | `question_id`, `text` |
| `accept_proposed_answer`, `discard_proposed_answer` | `message_id` |
| `llm_recommendations` | keine; erzeugt die KI-Empfehlungen zur abgeschlossenen Session neu und speichert sie als `result.knowledge_recommendations` |
| `update_profile_fact` | `field_key`, `value` (Optionswert oder Anzeigetext), optional `raw_answer`; ändert eine Angabe des Profils (`/api/v1/profiles/{domain_id}/facts`), Antwort mit `changed_field`, `previous_value`, `open_questions`, `result_changed` und dem Profil; Fehler `validation_failed` (mit `validation_code`), `invalid_question`, `profile_not_found` |
| `recommendation_feedback` | `recommendation_id`, `stance` (`accepted`/`rejected`); speichert die Rückmeldung am Profil (`/api/v1/profiles/{domain_id}/recommendations/{id}/feedback`), Fehler `invalid_recommendation` |
| `profile_recommendations` | keine; erzeugt die KI-Empfehlungen zum Profil des Falls neu (`/api/v1/profiles/{domain_id}/knowledge-recommendations`) und speichert sie am Profil; danach zeigt `get_profile` sie unter `/profile/result/knowledge_recommendations` |
| `create_conversation` | optional `title`; Domain kommt aus dem Fall, die Konversation hängt am Profil des Falls und ist daher erst nach `complete` möglich (`profile_not_found` davor) |
| `get_conversation`, `delete_conversation` | optional `conversation_id` |
| `send_conversation_message` | `text`; optional `conversation_id`; die Antwort trägt je Nachricht `tool_results` (Werkzeug, Argumente, Zusammenfassung, Ergebnis), prüfbar per JSON-Pointer |
| `list_conversations` | keine |
| `upload_dataset` | `fixture` (Dateiname unter `testsuite/fixtures/datasets/`, ohne Pfad), optional `format_hint`; sendet den Dateiinhalt als Text an `/api/v1/profiles/{domain_id}/dataset`, erst nach `complete` möglich (`profile_not_found` davor), nur bei Pack-Versionen mit Block `tools` (`invalid_state` sonst); Antwort mit `/profile/dataset` |
| `get_dataset`, `delete_dataset` | keine; `/api/v1/profiles/{domain_id}/dataset` (Datensatz und letzte Werkzeugergebnisse; Löschung auch auf dem Server, `invalid_state` ohne Datensatz) |

Konversationsschritte verwenden die zuletzt im Fall angelegte Konversation.
Ein anderes Ziel wird über eine Referenz gesetzt, etwa
`"references": {"conversation_id": "create_a#/conversation/conversation_id"}`.

Quellenangaben lassen sich wie alle Antwortfelder per JSON-Pointer prüfen,
etwa `/conversation/messages/1/sections/0/sources/0/kind` oder
`/recommendations/0/sources/0/kind` (`rule`, `user_context`, `document`, `web`,
`llm`). Die temporäre API startet standardmäßig mit leerer Wissensbasis
(`GUIDEOS_KNOWLEDGE_PATH` im Temp-Verzeichnis), und es gibt keine REST-Aktion
zum Befüllen. Mit `--knowledge-dir PFAD` indiziert der Runner vor dem Start
die Dateien unter `PFAD/<domain_id>/` (PDF, Markdown, Text) in diese
Wissensbasis, lexikalisch und ohne Embedding-Modell, und vermerkt Titel,
Dateiname und Hash je Domäne im Zielmanifest unter `configuration.knowledge`.
Dateien direkt in `PFAD` sind ein Fehler. Die Originale der Pilot-Wissensbasis
lädst du im Domain-Admin (Tab Wissen) herunter; ein Verzeichnis wie
`.guideos/testsuite-knowledge/verein-demo/` ist git-ignoriert. Ohne
`--knowledge-dir` können Szenarien nur `rule`, `user_context` und `llm`
erwarten; mit Dokumenten auch `document`. Webquellen decken die pytest-Tests ab.

Antwortreferenzen ersetzen einzelne Payload-Felder. Beispielsweise übernimmt
`"references": {"message_id": "help#/chat_messages/1/message_id"}` die ID aus der
Antwort des früheren Schritts `help`. Es gibt keine Code-Auswertung, Schleifen
oder automatischen Workflow-Entscheidungen im Szenarioformat.

Jeder Schritt erwartet standardmäßig HTTP 200 und `ok=true`. Ein negativer Test
setzt `expected_ok: false` und prüft beispielsweise mit `contains` auf `/errors`
den erwarteten Fehlercode. `expected_http` erlaubt einen anderen HTTP-Status;
`expected_ok: null` deaktiviert die Contract-`ok`-Prüfung, etwa für HTTP-404-Bodies.
Assertions direkt am Szenario beziehen sich auf die letzte ausgeführte Antwort.

| Operator | Bedeutung |
| --- | --- |
| `eq` | JSON-Gleichheit; boolesche Werte sind keine Zahlen |
| `exists` | Pfad vorhanden, auch bei `null`; `expected: false` prüft Abwesenheit |
| `contains` | Listenelement, Teilobjekt oder Teilzeichenfolge enthalten |
| `not_contains` | Gegenteil von `contains`; Typfehler oder fehlender Pfad gelten als nicht erfüllt |
| `between` | Zahl im inklusiven Bereich `expected: [min, max]` |
| `min_length` | Zeichenfolge oder Liste hat mindestens `expected` Elemente |

Antworten tragen seit Pack-Block `tools.derive` ein Feld `origin` (`user`
oder `measurement`, dazu `calculation_id`), und die Antwort zur aktuellen
Frage kann einen Vorschlag `proposal` (`field_key`, `normalized_value`,
`tool`, `calculation_id`, `confidence`, `note`) enthalten; beides lässt sich
wie jedes Feld per JSON-Zeiger prüfen, etwa nach einem `submit_file_answer`
mit einer Fixture, aus der die erste Analyse Antworten ableitet.


Pfade sind JSON Pointer: `/items/0/id`, `~1` für `/`, `~0` für `~`; leerer Pfad
adressiert das gesamte JSON-Dokument. Objektlisten erlauben Teilvergleiche,
beispielsweise `{"recommendation_id": "funding-diversification"}`. Fehlende Pfade
lassen Wertprüfungen fehlschlagen. Es gibt keine Wildcards oder JSONPath-Sprache.
Das Pydantic-Modell `testsuite.models.Scenario` ist der Schema-Vertrag; mit
`Scenario.model_json_schema()` lässt sich ein JSON Schema exportieren.

Die gebündelten deterministischen Szenarien (`verein_*`, Tag `verein`, Pack
`verein-demo`) prüfen Standardabschluss, ungültige Eingaben mit Korrektur,
unbekannte Werte und Revision, ungültige Zustandswechsel, den Lebenszyklus von
Beratungskonversationen sowie Profil, Faktenänderung und Feedback ohne
Modellaufruf. Die optionale Budgetfrage wird vor Abschluss ausdrücklich mit
`null` übersprungen. Sichtbarkeit, Validierung und Revision aller Fragetypen
prüft pytest mit Inline-Packs (`tests/test_file_questions.py`,
`tests/test_assessment_orchestrator.py`).


Für jede Domäne, deren aktives Pack den Block `tools` einschaltet, beschreibt
`testsuite/tool_servers/<domain_id>.json`, welcher Werkzeug-Server im Testlauf
dafür läuft: `module` (gestartet als `python -m <module> --host --port`, MCP
unter `/mcp`, Gesundheitsprüfung unter `/health`), `token_env` (die Variable,
in der der Server sein Token erwartet), feste `environment`-Variablen und
optional `in_process` (`paket.modul:funktion`, ein Starter für Tests im
selben Prozess). Der Runner startet je Modul einen Unterprozess neben der API
(zufälliges Token), registriert ihn im Werkzeugspeicher der isolierten API für
seine Domänen und gibt alle Werkzeuge frei; das Zielmanifest nennt die Server
unter `configuration.tools.servers`. Fehlt die Datei für eine Domäne mit
Werkzeugen, bricht der Start mit einer Fehlermeldung ab; ohne Domäne mit
Werkzeugen startet kein Server. Im Docker-Teststack registriert die API
stattdessen extern laufende Server: `GUIDEOS_TESTSUITE_MCP_URL` und
`GUIDEOS_TESTSUITE_MCP_TOKEN` gelten für alle Domänen mit Werkzeugen,
`GUIDEOS_TESTSUITE_MCP_URL__<DOMAIN_ID>` und `GUIDEOS_TESTSUITE_MCP_TOKEN__<DOMAIN_ID>`
(Großbuchstaben, `-` als `_`) für eine Domäne.


## Ollama optional einschalten

```bash
GUIDEOS_OLLAMA_ENDPOINT=http://localhost:11434 \
GUIDEOS_OLLAMA_MODEL=gpt-oss:20b \
uv run python -m testsuite.runner --include-llm --tag llm --report html
```

Mit `--knowledge-dir .guideos/testsuite-knowledge` nutzen die LLM-Szenarien
dabei die dort abgelegten Dokumente als Wissensbasis (siehe oben).
Alternativ gibt es `--ollama-endpoint` und `--ollama-model`. Es erfolgt kein
Modell-Download. Die echten Modell-Szenarien prüfen Freitextinterpretation,
Fragehilfe, isolierte Beratungskonversationen auf derselben Session und
KI-Empfehlungen mit Provenienz, auf Anfrage wie direkt beim Abschluss
(`complete` mit `knowledge_recommendations: true`, Szenario
`verein_completion_knowledge_recommendations`). Sie werden ohne `--include-llm` anhand ihrer Aktionen
erkannt und übersprungen; ein Tag allein schaltet kein LLM ein. Die
`regression_*`-Szenarien (Tag `regression`) messen mit `--repeat N` die
Interpretation von Zahlwörtern, Jahreszahlen in Worten und umgangssprachlichen
Angaben zur Art des Vereins. Modellverhalten
kann variieren, deshalb werden strukturierte Eigenschaften und keine exakten
Erklärungstexte verglichen. Unsicherheit, Vorschlagsannahme/-verwerfen sowie
Provider-Timeouts und ungültige Ausgaben werden zusätzlich in pytest mit
kontrollierten Test-Doubles durch den REST-Adapter getestet.

Timeouts: Verbindung 5 s, Antwort 30 s, LLM-Aktion 150 s. Überschreibbar mit
`--connect-timeout`, `--read-timeout`, `--llm-timeout`. Nur GET-Verbindungsfehler
werden maximal zweimal wiederholt. POST und GET-Lese-Timeouts werden nicht
wiederholt. Jeder Request erhält `X-Request-ID`; Redirects und implizite
HTTP-Proxy-Umgebungsvariablen werden nicht verwendet.

## PostgreSQL und Docker

`GUIDEOS_TESTSUITE_DATABASE_URL` oder `--database-url` wählt PostgreSQL statt
JSONL. Ausschließlich eine eigene Testdatenbank verwenden. Zugangsdaten gehören
in die Umgebung, nicht in Szenarien. Der Runner migriert das Schema `testsuite`
mit eigener Migrationshistorie und transaktionaler Migrationssperre.

Die Tabellen `test_runs`, `test_cases`, `test_events`, `test_assertions`,
`test_artifacts` und `test_ratings` verwenden relationale IDs/Fremdschlüssel und
JSONB für die versionierten Belege. Artefaktinhalte liegen in der Datenbank; exportierte
Markdown-/HTML-Dateien sind zusätzliche Kopien.

Der separate Compose-Stack verwendet ein internes Netzwerk ohne veröffentlichte
Ports. Er verwendet keine Pilot-Volumes und liest keine Pilot-Zugangsdaten:

```bash
export GUIDEOS_TESTSUITE_GIT_COMMIT=$(git rev-parse HEAD)
export GUIDEOS_TESTSUITE_GIT_BRANCH=$(git branch --show-current)
export GUIDEOS_TESTSUITE_GIT_DIRTY=$(test -z "$(git status --porcelain)" && echo false || echo true)
docker compose -f compose.testsuite.yaml up --build --abort-on-container-exit --exit-code-from runner
docker compose -f compose.testsuite.yaml --profile verification run --build --rm checks
docker compose -f compose.testsuite.yaml down
```

Die Build-Argumente dokumentieren den getesteten Checkout; ohne Angaben steht dort
`unknown`. API-Assessments sind temporär. PostgreSQL-Ergebnisse bleiben im eigenen
Volume, Reports unter `.guideos/testsuite-docker/`. `down` erhält die Volumes;
`down -v` entfernt die gesamte Testdatenbank und ist keine reguläre Bereinigung.
Die eingebauten Zugangsdaten gelten ausschließlich für das isolierte Testnetz.
Der Docker-Standardlauf hat keinen Zugriff auf echtes Ollama. Für einen
vollständigen Docker-Lauf mit LLM-Szenarien braucht es ein eigenes, lokales
Compose-Override (git-ignoriert), das `GUIDEOS_OLLAMA_ENDPOINT` und
`GUIDEOS_OLLAMA_MODEL` für `api` und `runner` setzt, dem Runner
`--include-llm --report html` als `command` gibt und das interne Netz nach
außen öffnet:

```bash
docker compose -f compose.testsuite.yaml -f <override.yaml> down --remove-orphans
docker compose -f compose.testsuite.yaml -f <override.yaml> \
  up --build --force-recreate --abort-on-container-exit --exit-code-from runner
```

Ein Lauf gilt nur dann als vollständig, wenn `counts.skipped` in der Summary `0` ist und der
Report `"include_llm": true` enthält.

## Bestehende Test-API

```bash
uv run python -m testsuite.runner --base-url http://localhost:8000 --target-manifest /tmp/test-target.json
```

Ein externes Zielmanifest muss mindestens Folgendes deklarieren:

```json
{
  "environment": "test",
  "base_url": "http://localhost:8000",
  "git": {"commit": "COMMIT_DES_SERVERS", "branch": "test", "dirty": false},
  "domain_packs": [{"domain_id": "verein-demo", "version": "1.0.0", "content_hash": "HASH_DES_PACKS"}],
  "provider": {"name": "ollama", "endpoint": "http://localhost:11434", "model": "gpt-oss:20b"}
}
```

Nicht lokale Hosts brauchen zusätzlich `--allow-host HOST`. Ein als `production`
deklariertes Ziel erfordert außerdem `--allow-production`. Die Einstufung und
Server-Metadaten sind Betreiberangaben, keine automatische Produktions-Erkennung.
Externe Angaben werden als `externally_declared` gespeichert; der lokale Git-Stand
wird getrennt als Runner-Version erfasst. Der Runner kann externe Code-/Pack-Hashes
nicht über den bestehenden REST-Vertrag verifizieren.

Die externe API muss bereits isoliert betrieben werden. Der Runner erzeugt neue
Sessions, startet oder löscht diesen Server aber nicht. Weil die REST-API keinen
Lösch-Endpunkt anbietet, verwaltet der Betreiber dessen Assessment-Speicher.
Ollama-Konfiguration einer externen API wird ausschließlich dort festgelegt.

## Reports, Wiederholung und manuelle Bereinigung

```bash
uv run python -m testsuite.runner --run-id RUN_ID --report html
uv run python -m testsuite.runner --rerun RUN_ID
uv run python -m testsuite.runner --cleanup --run-id RUN_ID
uv run python -m testsuite.runner --cleanup --run-id RUN_ID --confirm
uv run python -m testsuite.runner --cleanup --older-than-days 30 --confirm
```

Dieselben Repository-Optionen wie beim ursprünglichen Lauf verwenden. Eine
Wiederholung nimmt die gespeicherten Szenario-Snapshots, erzeugt neue Lauf- und
Session-IDs und verweist auf den Ursprungslauf. Die API wird mit dem aktuellen
Checkout gestartet; ein historischer GuideOS-Commit wird nicht automatisch
hergestellt. Echte Modelltests benötigen auch bei Wiederholung `--include-llm`.

Es gibt keine automatische Aufbewahrungsfrist. Bereinigung zeigt standardmäßig
nur Kandidaten und löscht mit `--confirm` abgeschlossene ausgewählte Läufe samt
Belegen, eingebetteten Artefakten und Reports im angegebenen Ergebnisverzeichnis.
Aktive Läufe werden nicht gelöscht. Läufe mit Expertenbewertungen (siehe
[Beratungstest](#beratungstest-und-expertenbewertung)) bleiben ebenfalls
erhalten und stehen in der Ausgabe unter `kept_rated`; erst `--include-rated`
löscht sie samt Bewertungen. SIGKILL oder ein Host-Ausfall kann einen
unvollständigen Lauf hinterlassen; solche Läufe werden nicht als bestanden
interpretiert und nicht automatisch als abgeschlossen eingestuft.

Rohtexte werden nur bei `persist_raw: true` gespeichert. Die mitgelieferten
Szenarien erlauben dies für ihre synthetischen Daten. Zugangsdaten, Tokens,
sensitive Schlüssel, URL-Zugangsdaten und konfigurierte bekannte Secrets werden
vor Speicherung entfernt. Ohne Rohtextfreigabe werden auch Rohtext-Assertions
redigiert. Verändert die Redaction einen Szenario-Snapshot, lehnt `--rerun` dessen
Wiederholung anhand des Originalhashes ab, statt andere Eingaben zu senden.

## Beratungstest und Expertenbewertung

Ob eine Beratungsantwort fachlich stimmt, lässt sich nicht mit Assertions
prüfen. Dafür gibt es den Beratungstest: Szenarien stellen der Beratung Fragen
oder fordern die KI-Empfehlungen an, und Fachleute bewerten die Antworten in der
Streamlit-App. Der Runner entscheidet weiterhin nur über die Assertions; eine
Bewertung ändert kein Urteil eines Laufs.

Ein Szenario nimmt am Beratungstest teil, wenn es den Block `review` trägt:

```json
"review": {
  "persona": "oel_eigenheim",
  "persona_label": "Pensionistenpaar im Einfamilienhaus (Eigentum, Ölheizung)",
  "kind": "advisory",
  "check": "Bezug auf 25 Jahre alte Ölheizung; keine erfundene Amortisationsrechnung"
}
```

| Feld | Bedeutung |
| --- | --- |
| `persona` | Kurzname; Fälle mit demselben Wert werden gemeinsam angezeigt. |
| `persona_label` | Überschrift der Persona in der Ansicht. |
| `kind` | `advisory` (Fachfrage), `edge` (Grenzfall) oder `llm_recommendations` (zusätzliche KI-Empfehlungen). |
| `check` | Prüfkriterium, das den Bewertenden unter „Worauf achten“ gezeigt wird. |

Bewertet wird die Antwort des letzten Schritts `send_conversation_message`, bei
`kind: llm_recommendations` die des letzten Schritts `llm_recommendations` oder `profile_recommendations`; der
passende Schritt muss im Szenario vorkommen. `review` verlangt
`persist_raw: true`, weil sonst die Antworttexte nicht gespeichert werden. Der
Runner übernimmt den Block in den Fall (`cases[].review`) und zählt die Fälle im
Lauf (`runs[].review_cases`). Alles, was die Ansicht zeigt (Frage, Antwort in
Abschnitten, zugeordnete Quellen mit Auszügen, ungeprüfte Zahlen, Angaben und
Ergebnis der Persona), stammt aus den gespeicherten Belegen des Laufs; Pack und
Wissensbasis müssen dafür nicht mehr vorhanden sein. Angaben und Ergebnis der
Persona zeigen den letzten Profilstand des Falls (letzte Antwort mit `profile`,
etwa `get_profile` nach einer Änderung im Chat, Werte mit Herkunft `chat_update`
sind als „im Chat geändert“ markiert, Regel-Empfehlungen mit Rückmeldung als
„vom Nutzer befürwortet/abgelehnt“); ohne Profilabfrage die Angaben beim
Abschluss. Bei Beratungs- und Grenzfällen steht über der bewerteten Antwort
der zugeklappte „Verlauf davor“ mit allen früheren Nachrichten der
Konversation (Änderungen, Rückfragen, Rückmeldungen); bewertet wird weiterhin
nur die letzte Antwort.

Die Ansicht ist der Modus **Beratungstest** der Streamlit-App und erscheint mit
`GUIDEOS_ADMIN_ENABLED=1`. Sie liest denselben Ergebnisspeicher wie der Runner:
PostgreSQL, wenn `GUIDEOS_TESTSUITE_DATABASE_URL` gesetzt ist, sonst die
JSONL-Dateien unter `.guideos/testsuite/` (anderes Verzeichnis über
`GUIDEOS_TESTSUITE_RESULTS_DIR`). In der Seitenleiste wählt man den Lauf und
trägt den eigenen Namen ein. Je Antwort gibt es „Passt“, „Teilweise“ oder
„Mangelhaft“ und eine Notiz; gespeichert wird mit **Speichern** je Karte.
Filter zeigen offene Fälle, einzelne Arten oder alles, was jemand als
„Teilweise“ oder „Mangelhaft“ bewertet hat.

Bewertungen gehen nicht unabsichtlich verloren:

- Jede gespeicherte Bewertung ist ein eigener Datensatz der Art `ratings` beim
  Lauf (Tabelle `testsuite.test_ratings` oder eine Zeile in der JSONL-Datei)
  mit Fall, Name, Urteil (`gut`, `teils`, `schlecht` oder leer), Notiz und
  Zeitpunkt. Eine geänderte Bewertung ersetzt die alte nicht, sondern kommt
  dazu; gültig ist die jüngste je Fall und Person.
- Mehrere Personen bewerten denselben Lauf unabhängig voneinander und sehen die
  Bewertungen der anderen.
- **Alle Bewertungen als CSV herunterladen** exportiert den gesamten Verlauf;
  die Spalte `aktuell` markiert den gültigen Stand.
- `--cleanup` lässt bewertete Läufe stehen (siehe oben).

Die Ansicht hat wie der Domain-Admin keine Anmeldung: Der Name ist eine
Selbstangabe und dient der Zuordnung, nicht der Zugriffskontrolle. Die
JSONL-Dateien liegen im git-ignorierten `.guideos/`; für mehrere Bewertende und
eine gesicherte Ablage ist PostgreSQL vorgesehen.

## Retrieval-Evaluation

`python -m testsuite.retrieval_eval` misst, ob die Wissenssuche einer Domäne
die erwarteten Passagen findet, und vergleicht dabei die Modi `lexical`,
`hybrid` (braucht `GUIDEOS_EMBEDDING_MODEL` und eingebettete Dokumente) und
`rerank` (zusätzlich `GUIDEOS_RERANK_MODEL` oder `--rerank-model`). Die Suche
läuft gegen die konfigurierte Wissensbasis (`GUIDEOS_DATABASE_URL` für die
Pilot-Datenbank, sonst `GUIDEOS_KNOWLEDGE_PATH`) mit dem aktiven Pack der
Domäne; es wird nichts geschrieben.

```bash
GUIDEOS_DATABASE_URL=postgresql://guideos:...@127.0.0.1:5434/guideos \
GUIDEOS_EMBEDDING_MODEL=bge-m3 \
uv run python -m testsuite.retrieval_eval --domain <domain_id> --rerank-model gpt-oss:20b --k 5 --json out.json   # Prüfset testsuite/retrieval/<domain_id>.json
```

Die Prüfsets liegen unter `testsuite/retrieval/<domain_id>.json`
(`schema_version`, `domain_id`, optional `description`, `queries` mit `id`,
`query` und `expected`: Dokumenttitel wie in der Wissensbasis plus optional
`pages`). Ein Treffer liegt vor, wenn unter den ersten k Passagen eine mit
diesem Titel und, falls angegeben, einer dieser Seiten ist. Die Ausgabe ist
eine Markdown-Tabelle je Modus (tatsächlich gemeldeter Modus, Treffer@k, MRR,
mittlere Dauer) und je Frage der Rang des ersten erwarteten Treffers („–“ ohne
Treffer); `--json` speichert zusätzlich die vollständigen Ränge und die
gefundenen Passagen. Die gebündelten Sets stammen aus den Seiteninhalten
der klimaaktiv-PDFs und sind seit 2026-10-12 fachlich freigegeben; neue
erwartete Treffer legt eine Fachperson fest, Assertions werden nicht
angepasst, um einen Lauf gut aussehen zu lassen.

## CI und Aufruf durch andere Prozesse

`.github/workflows/testsuite.yaml` führt pytest sowie deterministische REST-Läufe
mit PostgreSQL aus. Reports und Datenbank-Belege werden auch bei Fehlern als
GitHub-Artefakte exportiert. Diese CI-Kopien unterliegen der GitHub-Aufbewahrung
(hier 90 Tage; siehe [GitHub-Artefaktaufbewahrung](https://github.com/actions/upload-artifact#retention-period)); die temporäre CI-Datenbank wird mit dem Job entfernt. Lokale und
Docker-Ergebnisse bleiben bis zur manuellen Löschung erhalten.

Einen CI-Workflow mit echtem Ollama gibt es nicht (der frühere manuelle Workflow
für einen Self-hosted Runner wurde am 05.11.2026 entfernt, weil kein solcher
Runner existiert); LLM-Szenarien laufen lokal mit `--include-llm`.

Ein Agent oder ein anderer Prozess kann denselben CLI-Einstieg verwenden:

```python
import json
import subprocess

result = subprocess.run(
    ["uv", "run", "python", "-m", "testsuite.runner", "--tag", "deterministic"],
    text=True, capture_output=True,
)
summary = json.loads(result.stdout.splitlines()[-1])
# result.returncode entscheidet über Erfolg; summary enthält run_id und Report.
```

Der aufrufende Prozess darf Szenarien auswählen, Reports lesen und Wiederholungen
starten. Es gibt keine CLI-Funktion zum Umschreiben fehlgeschlagener Assertions.
Neue generierte Szenarien werden validiert und regulär im Repository geprüft.
