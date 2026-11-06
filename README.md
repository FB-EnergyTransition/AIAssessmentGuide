# GuideOS

GuideOS ist ein domänenneutraler Kern für geführte Assessments mit
anschließender Beratung: Ein versioniertes **Domain-Pack** beschreibt Fragen,
Validierung, Regeln und Empfehlungen; GuideOS führt durch die Fragen, prüft
jede Antwort deterministisch, berechnet Regel-Empfehlungen, erzeugt einen
Bericht und bietet danach einen Beratungschat mit Quellenangaben an. Ein
lokales Sprachmodell (Ollama) interpretiert Freitext, formuliert Erklärungen
und ergänzt klar gekennzeichnete KI-Empfehlungen aus einer kuratierten
Wissensbasis. Es trifft keine Entscheidungen.

**Status:** Forschungsprototyp. Das mitgelieferte Pack `verein-demo` ist eine
technische Demonstration, keine fachliche Beratung. Der Domain-Admin und die
Wissensbasis sind nicht durch eine Anmeldung geschützt; der Pilot-Login ist
für synthetische Testdaten hinter einem Reverse-Proxy gedacht, nicht als
Produktions-Authentifizierung (siehe [SECURITY.md](SECURITY.md)).

## Das Prinzip: Das Modell entscheidet nichts

- **Deterministisch:** Welche Frage als Nächstes kommt, ob eine Antwort gültig
  ist, welche Regel greift und welche Empfehlung daraus folgt, entscheidet
  Code, der ohne Modell testbar ist. Das Pack ist die einzige Quelle dafür.
- **Das Modell schlägt vor:** Bei Freitext extrahiert es einen Wert, die
  Validierung entscheidet. Im Beratungschat formuliert es Antworten in
  Abschnitten, die nur Kennungen von Belegen nennen.
- **Herkunft vergibt GuideOS:** Jede Aussage trägt ihre Quelle: GuideOS-Regel,
  eigene Angabe, Dokument oder Webseite der Wissensbasis, Berechnung eines
  Werkzeugs oder **KI-Fachwissen**. Unbekannte Kennungen werden verworfen,
  Zahlen ohne Beleg als **Ungeprüft** markiert.
- **Reproduzierbar:** Sessions speichern Pack-Version und Inhalts-Hash;
  aktive Pack-Versionen sind unveränderlich, Änderungen werden neue Versionen.

Die Zielarchitektur steht in
[docs/entwicklung/target-architecture-overview.md](docs/entwicklung/target-architecture-overview.md)
(Englisch).

## Schnellstart

Voraussetzungen: Python 3.12, [uv](https://docs.astral.sh/uv/) und ein
erreichbarer Ollama-Endpoint (Standard `http://localhost:11434`, Modell
`gpt-oss:20b`; `ollama pull gpt-oss:20b`).

```bash
uv run streamlit run app.py                          # Chat und Assessment
GUIDEOS_ADMIN_ENABLED=1 uv run streamlit run app.py  # zusätzlich Domain-Admin und Beratungstest
uv run streamlit run app.py -- --show-report         # zeigt den Markdown-Bericht nach dem Abschluss
```

Der Modus wird in der Seitenleiste gewählt, ebenso Ollama-Endpoint und Modell
(die Modelle des Endpoints werden abgefragt; ist er nicht erreichbar, bleibt
ein Textfeld). Alle Daten liegen lokal als JSON unter `.guideos/`
(git-ignoriert). Eine Schritt-für-Schritt-Anleitung steht in
[docs/guideos-tutorial.md](docs/guideos-tutorial.md).

## Assessment und Demo-Pack

Der Assessment-Modus zeigt jeweils die aktuelle Frage wie in einem Formular.
Auswahl- und Ja/Nein-Fragen werden über Optionsfelder, Kontrollkästchen oder
Auswahllisten beantwortet; Text, Zahlen und Daten kommen über das Chat-Feld.
Eindeutige Eingaben prüft die Validierung direkt, bei unklaren Angaben
extrahiert das Modell einen Wert, den die Validierung entscheidet; Rückfragen
zur aktuellen Frage laufen über denselben Chat. Vor dem Abschluss lassen sich
alle Angaben prüfen und ändern, das Ergebnis enthält nachvollziehbare Reason
Codes, Empfehlungen und einen Markdown-Bericht. Einen Score gibt es nicht.

Gebündelt ist das Pack **`verein-demo`** („Vereins-Check Demo“): sechs Fragen
zu einem Verein (Art, Gründungsjahr, Mitgliederzahl, Einnahmequelle, Lage
beim Ehrenamt, Jahresausgaben), fünf Regeln mit Reason Codes, darunter eine
Verhältnisregel (Ausgaben je Mitglied), und vier Empfehlungen. Es zeigt die
Mechanik und ist fachlich nicht abgesichert. Alle Fragetypen (Einfach- und
Mehrfachauswahl, ganze Zahlen, Zahlen, Text, Ja/Nein, Datum, Datei-Upload),
Sichtbarkeitsbedingungen, Themenkatalog mit Ausschluss- und
Priorisierungsregeln sowie der Werkzeug-Layer werden in den Tests mit eigenen
Packs geprüft; wie man solche Packs baut, beschreibt
[docs/domain-admin-tutorial.md](docs/domain-admin-tutorial.md).

Die Seitenleiste imitiert ein Mehrbenutzersystem: Unter **Benutzer** wählst
du einen Namen oder legst einen neuen an; Assessments, Profil, Empfehlungen
und Beratungschats gehören dem gewählten Benutzer. Das ist ein Demo-Hilfsmittel,
keine Anmeldung.

### Nach dem Abschluss: Profil und Beratung

Mit dem Abschluss entsteht das **Profil** des Benutzers für die Domäne (Angaben,
Ergebnis, Pack-Version und Hash; ein weiteres Assessment ersetzt Angaben und
Ergebnis, die Profil-ID bleibt). Beim Abschluss erzeugt GuideOS mit dem
gewählten Modell die ersten **KI-Empfehlungen** aus den Angaben und der
Wissensbasis; schlägt das fehl, wird trotzdem abgeschlossen und der Fehlercode
vermerkt. Der Chat **Ergebnis** zeigt Regel- und KI-Empfehlungen mit Quellen,
weitere Chats lassen sich anlegen; jeder kennt den aktuellen Profilstand, nie
den Verlauf anderer Chats.

Einzelne Angaben lassen sich im Chat ändern („Wir haben inzwischen 140
Mitglieder“): Das Modell schlägt Feld und Wert vor, GuideOS validiert, fragt
nach und übernimmt erst nach einem Ja; Regeln werden sofort neu berechnet,
sichtbar gewordene Pflichtfragen einzeln nachgestellt. Zu jeder Empfehlung
kann der Benutzer Stellung nehmen („Passt“ / „Passt nicht“, auch im Chat);
Abgelehntes wird nicht erneut vorgeschlagen, Befürwortetes bleibt zuerst.

Ein Pack kann einen Themenkatalog (`topics`) führen: Regeln mit `kind:
exclude` verwerfen KI-Empfehlungen zu einem Thema, `kind: prioritize` ordnet
sie nach vorne. Jede Regel hat einen Lebenszyklus (`status` draft → review →
approved); eine Version lässt sich erst aktivieren, wenn alle Regeln
freigegeben sind. Empfehlungen können Kennzahlen aus Werkzeugergebnissen
nennen (`figures`, `message_with_figures`), die GuideOS deterministisch
einsetzt.

## Domain-Packs und Domain-Admin

Ein Pack ist eine JSON-Datei `domain_packs/<domain_id>/<version>/pack.json`,
validiert mit Pydantic (Feldtypen, Validierungsschlüssel, Querverweise,
Sichtbarkeit nur auf frühere Fragen). Der Loader berechnet einen SHA-256-Hash;
Sessions verweisen auf Version und Hash, und eine Session mit abweichendem
Pack wird nicht geladen. Der lokale **Domain-Admin** (`GUIDEOS_ADMIN_ENABLED=1`)
bearbeitet Fragen, Regeln, Themen, Empfehlungen, Metadaten, Wissensbasis und
Werkzeuge im Formular, speichert Drafts unter `.guideos/domain_packs/`,
aktiviert Versionen nach erfolgreicher Validierung und exportiert Fragen als
[SOyA](https://www.ownyourdata.eu/soya/)-Struktur. Er ist ein Entwicklungs-
und Demo-Werkzeug ohne Zugriffsschutz.

## Wissensbasis und Quellenangaben

Jede Domäne kann eine Wissensbasis aus Dokumenten (PDF, Markdown, Text) und
einmalig abgerufenen Webseiten erhalten (Tab **Wissen**). Vor jeder
Beratungsantwort und vor den KI-Empfehlungen sucht GuideOS deterministisch die
passendsten Abschnitte (lexikalisch mit BM25; hybrid mit einem Embedding-Modell
über `GUIDEOS_EMBEDDING_MODEL`, optional ein Reranker über
`GUIDEOS_RERANK_MODEL`, alles im Prozess ohne Vektordatenbank) und gibt sie
dem Modell mit Kennungen mit. Das Modell zitiert Kennungen, GuideOS löst sie
zu Quellen auf und zeigt die zitierten Auszüge; bei PDFs führt ein Link auf die
zitierte Seite. Zahlen, die weder in den zitierten Abschnitten noch in den
Angaben stehen, erscheinen als **Ungeprüft**. Es gibt keine Live-Websuche;
Webseiten werden beim Hinzufügen abgerufen und auf Wunsch neu geholt
(geänderter Inhalt wird eine neue Quellversion). Domänenspezifische Vorgaben
für das Modell stehen nur im Pack-Feld `advisory_guidance`; die Vorgaben im
Kern sind domänenneutral.

## Werkzeuge (MCP)

Im Tab **Werkzeuge** registriert der Domain-Admin je Domäne MCP-Server (Name,
URL, Token), ruft ihre Werkzeuge ab und gibt sie einzeln frei. Eine
Pack-Version schaltet den Werkzeug-Layer mit dem Block `tools` ein: GuideOS
importiert dann die Datei einer Frage vom Typ `file` über das `dataset_tool`
des Servers, bewahrt nur die Datensatzreferenz, führt definierte
Analyseschritte nach dem Import und beim Abschluss aus, leitet Antworten aus
den Ergebnissen ab oder schlägt sie vor, wertet Kennzahlen-Regeln aus und
lässt das Modell in der Beratung freigegebene Werkzeuge in einer begrenzten
Schleife mit Leitplanken aufrufen (Allowlist, Argument- und Ausgabeprüfung
gegen die Schemas, Budget je Nachricht; den `owner` setzt GuideOS). Antworten
zitieren Berechnungen als Quelle. Dieses Repository enthält keinen
MCP-Server und kein Pack mit Werkzeugen; der Layer ist mit Test-Doubles
getestet und in [docs/domain-admin-tutorial.md](docs/domain-admin-tutorial.md)
beschrieben.

## REST-API

```bash
uv run uvicorn guideos.adapters.fastapi:create_app --factory --reload
```

Der FastAPI-Adapter spricht denselben Kern wie die Streamlit-App und nutzt
dieselbe Ablage. Der versionierte Rand `/api/v1` bietet Gesundheit, Domänen,
Assessment-Sessions (`POST /api/v1/assessment-sessions`, `.../inputs` mit den
Eingaben `submit_answer`, `revise_answer`, `complete` usw., `.../chat` für die
Fragehilfe, `.../report`, `.../llm-recommendations`, `DELETE`), Profile
(`/api/v1/profiles`, Fakten ändern, Feedback, KI-Empfehlungen neu erzeugen,
Datensatz und Zeitreihen bei Packs mit Werkzeugen) und Beratungschats
(`/api/v1/conversations`, `/api/v1/conversations/result`). Der Header
`X-GuideOS-Participant` begrenzt die Daten auf einen Teilnehmer, ist aber
keine Authentifizierung. Mit `GUIDEOS_PILOT_MODE=1` verlangt die API den
Pilot-Login (`POST /api/v1/login`, signiertes Cookie). Liegt ein gebauter
Web-Client unter `frontend/dist` oder `GUIDEOS_FRONTEND_DIST`, liefert die API
ihn unter `/` aus. Den Vertrag beschreibt `guideos/assessment/contract.py`;
der Test-Runner spricht ausschließlich diesen Vertrag.

## Mehrbenutzer-Pilot mit Docker Compose

`compose.yaml` startet PostgreSQL, die Streamlit-App im Pilotmodus
(`127.0.0.1:8501`) und die REST-API (`127.0.0.1:8005`). Die Anmeldung verlangt
einen Benutzernamen und das gemeinsame Testpasswort; der Benutzername trennt
die Daten. Mit `GUIDEOS_DATABASE_URL` liegen Sessions, Profile, Chats,
Wissensbasis und Domain-Katalog in PostgreSQL (Migrationen:
`python -m guideos.assessment.migrations`, der Container wendet sie beim Start
an). `GUIDEOS_DOMAINS` beschränkt die angebotenen Domänen,
`GUIDEOS_RETENTION_DAYS` löscht ältere Daten beim Start.

```bash
cp .env.example .env    # Passwörter und Ollama-Endpoint eintragen
docker compose up --build -d
```

Der Pilot ist für synthetische Testdaten hinter einem HTTPS-Reverse-Proxy
gedacht. Betrieb, Feedback-Export, Session-Berichte und Datenlöschung
beschreibt [docs/guideos-tutorial.md](docs/guideos-tutorial.md).

## Tests, Lint und Test-Runner

```bash
uv run ruff check .
uv run pytest
uv run python -m testsuite.runner --report html
```

`tests/` ist die pytest-Suite (Kern, Adapter, Test-Doubles, kein echtes
Modell); PostgreSQL-Tests laufen nur mit `GUIDEOS_TEST_POSTGRES_URL` und
`GUIDEOS_TESTSUITE_DATABASE_URL`. `testsuite/` ist ein REST-Test-Runner: JSON-
Szenarien unter `testsuite/scenarios/` werden gegen eine temporäre API
gespielt, Belege als JSONL oder in PostgreSQL gespeichert; Szenarien mit
Modellbedarf laufen nur mit `--include-llm` gegen Ollama, `--repeat N` misst
Bestehensraten, und mit `GUIDEOS_ADMIN_ENABLED=1` bewerten Fachleute die
Antworten im Modus **Beratungstest**. `compose.testsuite.yaml` führt den
deterministischen Lauf in einem isolierten Docker-Stack aus. Details:
[docs/test-runner.md](docs/test-runner.md).

## Dokumentation

| Dokument | Art | Inhalt |
|---|---|---|
| [docs/guideos-tutorial.md](docs/guideos-tutorial.md) | Referenz | Lokale Nutzung, Assessment, Pilot mit Docker Compose, Fehlerbehebung. |
| [docs/domain-admin-tutorial.md](docs/domain-admin-tutorial.md) | Referenz | Packs anlegen und aktivieren, Fragen, Regeln, Empfehlungen, SOyA, Wissensbasis, MCP-Server und Block `tools`. |
| [docs/test-runner.md](docs/test-runner.md) | Referenz | Szenarien, Runner-CLI, PostgreSQL und Docker, Beratungstest, Retrieval-Evaluation, CI. |
| [docs/entwicklung/target-architecture-overview.md](docs/entwicklung/target-architecture-overview.md) | Architektur (Englisch) | Zielarchitektur, Prinzipien, Phasen und Entscheidungen; vor Strukturänderungen lesen. |

## Mitwirken, Sicherheit, Lizenz

Dieses Repository ist ein Snapshot: Die Entwicklung findet in einem nicht
öffentlichen Repository statt, aus dem der Kern in Abständen exportiert wird.
Hinweise und Pull Requests sind willkommen und werden dort übernommen; siehe
[CONTRIBUTING.md](CONTRIBUTING.md). Sicherheitshinweise und der Meldeweg
stehen in [SECURITY.md](SECURITY.md). GuideOS steht unter der MIT-Lizenz
([LICENSE](LICENSE)), Rechteinhaberin ist die Forschung Burgenland GmbH; die verwendeten Bibliotheken stehen unter permissiven
Lizenzen (Apache-2.0, MIT, BSD-3, MPL-2.0, psycopg unter LGPL-3.0 als
unverändertes Paket).
