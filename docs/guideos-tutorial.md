# GuideOS Tutorial

Dieses Tutorial zeigt, wie du GuideOS lokal ausprobierst und einen internen
Pilot mit mehreren Testpersonen durchführst. Die mitgelieferten Demo-Packs
sind technische Demos und keine fachliche Beratung.

## 1. Voraussetzungen

Du brauchst Python 3.12+, [uv](https://docs.astral.sh/uv/) und für
Freitext-Antworten oder den Chat einen erreichbaren Ollama-Server.

```bash
uv sync
ollama serve
ollama pull gpt-oss:20b
```

Wenn Ollama bereits als Dienst läuft, ist `ollama serve` nicht erforderlich.
Die Anwendung kann auch einen Ollama-Server in deinem Netzwerk verwenden.

## 2. Lokale Anwendung starten

Starte im Repository:

```bash
uv run streamlit run app.py
```

Öffne anschließend die von Streamlit ausgegebene Adresse, normalerweise
`http://localhost:8501`.

In der Seitenleiste stehen vier Modi zur Verfügung:

- **Chat**: freies Gespräch mit dem ausgewählten Ollama-Modell.
- **Assessment**: geführter Check mit strukturierten Fragen.
- **Domain-Admin**: lokales Entwicklungswerkzeug; nur sichtbar, wenn es
  explizit aktiviert wurde (Abschnitt 7).
- **Beratungstest**: fachliche Bewertung von Testläufen; nur mit demselben
  Schalter sichtbar (Abschnitt 7).

Im lokalen Modus lassen sich Ollama-Endpunkt und Modell in der Seitenleiste
ändern. Verwende beispielsweise `http://localhost:11434`, wenn Ollama auf
demselben Rechner läuft.

## 3. Erstes Assessment durchführen

1. Wähle **Assessment**.
2. Wähle unter **Benutzer** einen bestehenden Benutzernamen oder trage unter
   **Neuer Benutzername** einen neuen ein und klicke auf **Benutzer anlegen**.
   Die Seitenleiste imitiert damit ein Mehrbenutzersystem: Assessments,
   Profil, Empfehlungen, Beratungschats und hochgeladene Datensätze gehören
   dem gewählten Benutzer. Ohne Auswahl arbeitest du als Standardbenutzer `local`.
3. Wähle eine Domain, etwa **Vereins-Check Demo**.
4. Klicke auf **Neues Assessment starten**.
5. Beantworte Auswahl- und Ja/Nein-Fragen über die angezeigten Optionsfelder
   oder Kontrollkästchen und klicke auf **Antwort speichern**. Text, Zahlen
   und Daten gibst du im Chat-Feld ein; Grenzen stehen unter der Frage.
   Eindeutige Eingaben prüft GuideOS direkt, unklare Formulierungen deutet
   Ollama. Gespeichert werden nur gültige Antworten; danach erscheint die
   nächste Frage, bisherige Angaben stehen aufklappbar darüber. Bei erlaubten
   Feldern kannst du **Unbekannt / überspringen** wählen oder "unbekannt"
   schreiben.
6. Stelle Rückfragen im Chat-Feld, wenn du Hilfe zur aktuellen Frage
   benötigst. Ohne klare Antwort wird nichts gespeichert.
7. Vor dem Abschluss fasst der Chat deine Angaben zusammen. Korrigiere bei
   Bedarf einzelne Antworten unter **Angaben korrigieren** und klicke auf
   **Assessment abschließen**.
8. Nach dem Abschluss öffnet sich automatisch der Beratungschat **Ergebnis**.
   Er meldet, dass das Onboarding abgeschlossen ist, und liefert die erste
   Empfehlung: die Regel-Empfehlungen mit Gründen und Bezug zu deinen
   Angaben, gefolgt von den KI-Empfehlungen, die beim Abschluss aus deinen
   Angaben und der Wissensbasis erzeugt und im Ergebnis gespeichert wurden.
   Konnten sie nicht erzeugt werden (etwa weil Ollama nicht erreichbar war),
   fragt der Chat, ob du sie möchtest: Antworte mit **Ja**, um sie
   nachzuholen, oder stelle direkt eine Frage. Ergibt sich aus den Regeln keine Empfehlung,
   kommt die erste Empfehlung sofort aus dem KI-Fachwissen. Unter jedem
   Abschnitt einer Beratungsantwort steht die Quelle: GuideOS-Regel, deine
   Angabe, ein Dokument oder eine Website der Wissensbasis oder
   KI-Fachwissen; zitierte Textstellen findest du unter **Quellenauszüge**.
   Weitere Chats legst du in der Seitenleiste unter
   **Beratungschats** mit **Neuer Chat** an; sie starten leer wie ein normaler
   Chat, kennen aber serverseitig dein Profil. Alle Chats hängen am Profil:
   Schließt du später ein weiteres Assessment derselben Domäne ab, laufen sie
   weiter, und der Chat **Ergebnis** bekommt einen neuen Block mit den
   aktualisierten Empfehlungen. Der Markdown-Bericht wird
   nur angezeigt, wenn die App mit `-- --show-report` gestartet wurde; dann
   findest du ihn unter **Bericht (Markdown)**.
9. Im Pilotmodus kannst du unter **Feedback zum Pilot** eine Bewertung von 1
   bis 5 und einen optionalen Kommentar abgeben. Der Bereich ist nach dem
   Abschluss bereits aufgeklappt.

Lokal werden laufende Assessments in `.guideos/assessments.json` gespeichert.
Sie erscheinen nach einem Neustart wieder in der Liste. Das gilt nicht für
den Pilotmodus: Dort ist die Liste an den aktuellen Browser gebunden.

Mit dem Abschluss entsteht dein **Profil** für die Domäne: Angaben und
Empfehlungen des jüngsten abgeschlossenen Assessments. Über dem Chat
**Ergebnis** zeigt der Bereich **Profil** diesen Stand; mit `--show-report`
enthält der Berichts-Bereich zusätzlich den Profilbericht. Schließt du in
derselben Domäne ein weiteres Assessment ab, ersetzt es das Profil; die
ältere Session bleibt in der Liste und zeigt beim Öffnen nur noch Angaben
und Bericht mit dem Hinweis, dass das Profil aus einem neueren Assessment
stammt. Die Beratungschats findest du bei der Session, aus der das Profil
stammt.

Hat sich etwas geändert, schreibst du es einfach in den Chat, etwa „Wir
haben inzwischen 120 Mitglieder“. GuideOS fragt nach („Soll ich deine
Angabe ändern? … Antworte mit Ja oder Nein“) und übernimmt die Angabe erst
nach einem Ja. Danach siehst du die neu berechneten Regel-Empfehlungen,
gegebenenfalls eine Nachfrage zu einer Angabe, die dadurch nötig wird, und
das Angebot, die KI-Empfehlungen zum neuen Stand neu zu erzeugen.

Im Bereich **Profil** kannst du zu jeder Empfehlung **Passt** oder
**Passt nicht** wählen, oder du schreibst es in den Chat („Eine
Vereinsberatung kommt für uns nicht in Frage“). Die Rückmeldung bleibt im
Profil, erscheint im Bericht und sorgt dafür, dass Abgelehntes bei der
nächsten Erzeugung der KI-Empfehlungen nicht wieder vorgeschlagen wird.


## 4. Empfohlene Testszenarien

Verwende ausschließlich erfundene Daten. Diese Szenarien decken die wichtigsten
Pfade ab:

| Szenario | Beispiel | Worauf achten? |
|---|---|---|
| Standardfall Verein | Sportverein, gegründet 1985, 120 Mitglieder, Einnahmen aus Mitgliedsbeiträgen, Ehrenamtliche knapp | Sind Fragen und Empfehlungen nachvollziehbar? |
| Gegenprobe Verein | Kulturverein, gegründet 2015, Einnahmen gemischt, Ehrenamtliche ausreichend, Ausgaben angegeben | Unterscheiden sich Reason Codes und Empfehlungen plausibel? |
| Unbekannte Werte | Gründungsjahr oder jährliche Ausgaben als unbekannt markieren | Bleibt der Ablauf verständlich? Nennt das Ergebnis die fehlende Angabe? |
| Ungültige Eingabe | Gründungsjahr oder Mitgliederzahl außerhalb des erlaubten Bereichs | Ist die Fehlermeldung hilfreich? |
| Freitext | "Sportverein seit 1985, etwa 120 Mitglieder" | Wird die Antwort korrekt übernommen oder nachgefragt? |
| Paralleltest | Zwei Browser oder Geräte | Im Pilot sieht jede Person nur eigene Sessions. |
| KI-Empfehlungen beim Abschluss | Standardfall abschließen, Spinner abwarten | Erscheinen nach den Regel-Empfehlungen KI-Empfehlungen mit Quellenauszügen? Passen die Quellen zur Aussage? Steht nirgends ein Feldschlüssel oder eine Kennung wie D3 im Text? |
| Profil | Bereich **Profil** öffnen | Stehen Angaben und Empfehlungen so da, wie du geantwortet hast? Zeigt der Profilbericht dasselbe? |
| Angabe im Chat ändern | „Wir haben inzwischen 120 Mitglieder“ schreiben | Fragt GuideOS nach und übernimmt erst nach „Ja“? Ändern sich die Regel-Empfehlungen sofort? Kommt das Angebot, die KI-Empfehlungen neu zu erzeugen? |
| Keine Änderung | „Ich überlege, ob wir den Mitgliedsbeitrag erhöhen sollen“ | Wird das als Frage beantwortet und nicht als Änderung behandelt? |
| Rückmeldung | **Passt nicht** bei einer Empfehlung oder „Eine Vereinsberatung kommt für uns nicht in Frage“ | Wird die Rückmeldung vermerkt? Fehlt die abgelehnte Empfehlung nach dem nächsten „Ja“ auf die Neuerzeugung? Bleiben Regel-Empfehlungen sichtbar, nur markiert? |
| Zweites Assessment | Dieselbe Domäne erneut abschließen | Bekommt der Chat **Ergebnis** einen neuen Block statt eines neuen Chats? Zeigt die ältere Session nur Angaben und Bericht? |
| Profil löschen | Im Bereich **Profil** die Bestätigung setzen und **Profil löschen** | Verschwinden Profil, Chats und die Assessments dieser Domäne? Startet danach ein neues Assessment ohne alten Verlauf? |

Bitte beantworte nach jedem Szenario drei Fragen: Welche Formulierung war
unklar? Welche Empfehlung war überraschend? Wo wärst du beinahe ausgestiegen?

## 5. Interner Mehrbenutzer-Pilot

Der Pilot ist für ein kleines internes Testteam bestimmt. Er verwendet
PostgreSQL, einen gemeinsamen Zugang und einen vorhandenen Reverse-Proxy mit
HTTPS. Er ist nicht für echte Personen-, Immobilien-, Finanz- oder
Beratungsdaten freigegeben.

### Pilot mit Docker Compose starten

```bash
cp .env.example .env
docker compose up --build -d
```

Stelle sicher, dass eine `compose.override.yaml` auf dem Pilot-Host Streamlit
nicht freigibt. Diese lokale Entwicklerdatei (Vorlage:
`compose.override.example.yaml`, git-ignoriert) wird von Docker Compose
automatisch geladen; ihr `app`-Block würde Streamlit auf allen Interfaces
freigeben und den Reverse-Proxy umgehen. Zulässig ist nur der Block
`postgres` auf `127.0.0.1:5434`, den eine zweite, lokale Instanz mit
Domain-Admin an derselben Datenbank braucht (README, „Lokale Test-Oberfläche
mit PostgreSQL“).


Trage vor dem Start in `.env` ein:

- ein starkes `GUIDEOS_POSTGRES_PASSWORD`,
- das gemeinsame `GUIDEOS_TEST_PASSWORD`,
- `GUIDEOS_OLLAMA_ENDPOINT` für den vom Pilot-Host erreichbaren Ollama-Server,
- optional `GUIDEOS_OLLAMA_MODEL`,
- optional `GUIDEOS_MODEL_SELECTION=1` (nur für Testphasen): Tester sehen im
  Web-Client unten in der Seitenleiste das Dropdown **KI-Modell** mit den
  Modellen des Ollama-Endpoints; die Wahl gilt für den Browser-Tab (auch nach
  dem Neuladen der Seite), wird beim Abmelden vergessen und steht als `model=…`
  in den Logzeilen `message_sent` und `knowledge_recommendations_persisted`.
  Die API nimmt nur Modelle an, die der Endpoint listet (sonst HTTP 400).
  Standard aus, damit Testkunden keinen Modellnamen sehen,
- optional `GUIDEOS_PROVIDER_TIMEOUT_SECONDS` (Zeitlimit je Modellanfrage, Standard
  120; mit `gpt-oss:120b` brauchen die KI-Empfehlungen beim Abschluss mit den
  Werkzeugergebnissen im Prompt mehrere Minuten, der Pilot verwendet 300),
- optional `GUIDEOS_EMBEDDING_MODEL` (in Ollama geladenes Embedding-Modell für
  die semantische Suche in der Wissensbasis; ohne den Wert wird lexikalisch
  gesucht),
- optional `GUIDEOS_DOMAINS` (kommagetrennte `domain_id`s, die das Assessment anbietet, etwa `verein-demo`; leer = alle aktiven Domänen; die übrigen Packs bleiben im Image, damit gespeicherte Sessions ladbar bleiben)
- optional `GUIDEOS_RETENTION_DAYS` (Aufbewahrungsfrist in Tagen; leer = unbegrenzt,
  Standard). Mit einer Frist entfernt der Container bei jedem Start Sessions,
  Profile und Beratungschats, deren letzte Änderung älter ist; ein Profil mit
  jüngerem Chat und die Quell-Session eines bleibenden Profils bleiben.
  Manuell: `python -m guideos.pilot.retention` (Probelauf) oder `--apply`.
- optional `GUIDEOS_LOG_LEVEL` (Standard `INFO`; `DEBUG` protokolliert zusätzlich
  jede abgeschlossene Ollama-Anfrage mit Dauer).

Der Stack stellt Streamlit nur auf `127.0.0.1:8501` bereit und die REST-API
(Dienst `api`, Pilot-Login mit Benutzername und Testpasswort, Session-Cookie)
nur auf `127.0.0.1:8005`. Der Reverse-Proxy der Organisation muss diese
lokalen Ports über interne HTTPS-Namen erreichbar machen; für `api` gehören
`/` und `/api` unter dieselbe Origin, und das Cookie wird nur über HTTPS
gesendet (`GUIDEOS_COOKIE_SECURE`, Standard `1`; optional
`GUIDEOS_SESSION_SECRET` als eigener Signaturschlüssel). Der Dienst `api`
wartet auf `app`, das die Migrationen anwendet. Der Pilot startet die App mit
`--show-report` (im `command` des `app`-Dienstes in `compose.yaml`), zeigt
also nach dem Abschluss zusätzlich den Markdown-Bericht.


### Verhalten für Testpersonen

1. Öffne die vom Team mitgeteilte HTTPS-Adresse.
2. Melde dich mit deinem Benutzernamen (im Team abgestimmt, z. B. `test`) und
   dem gemeinsamen Testpasswort an.
3. Führe ein oder mehrere Assessments mit synthetischen Daten durch.
4. Lies die Empfehlungen, stelle Fragen im Chat, ändere eine Angabe im Chat
   und gib zu Empfehlungen eine Rückmeldung (siehe Abschnitt 4).
5. Gib nach dem Abschluss Feedback ab.
6. Melde dich ab, wenn du fertig bist.

Der Benutzername ist der Teilnehmer-Schlüssel: Sessions, Profil und
Beratungschats gehören zu diesem Namen und sind nach Abmelden, Browserwechsel
oder auf einem anderen Gerät mit demselben Namen wieder da. Wer einen anderen
Namen eingibt, sieht andere Daten; das Passwort schützt nur den Zugang zum
Piloten, nicht einen Namen vor anderen Testpersonen. Vor dem 22.10.2026
erzeugte Sessions hängen an zufälligen Schlüsseln `participant_…` und sind
über den Login nicht mehr erreichbar (Operator: Session-Berichte).

### Pilot abschließen

Der Operator exportiert zuerst das Feedback als CSV und jede Session als
Markdown-Bericht (Angaben, Dialog, Ereignisse, Ergebnis, Beratungschats,
Feedback) und löscht danach alle Pilotdaten (Assessment-Sessions, Feedback und
Beratungschats):

```bash
docker compose exec app uv run --no-sync python -m guideos.pilot.export_feedback > feedback.csv
docker compose exec app uv run --no-sync python -m guideos.pilot.session_report --output-dir /tmp/berichte
docker compose exec app uv run --no-sync python -m guideos.pilot.purge_data --confirm   # löscht Sessions, Konversationen, Profile und Feedback
docker compose exec app uv run --no-sync python -m guideos.pilot.retention --days 30 --apply   # alternativ: nur Daten älter als 30 Tage
```

Beim Update auf den Stand mit Profilen (Oktober 2026) ist dieser Aufruf einmal
nötig: Ältere Konversationen waren an Sessions gebunden und werden nicht
migriert; die Migration `008` benennt nur die Spalte um.

## 6. Fehlerbehebung

| Problem | Prüfung |
|---|---|
| Keine LLM-Antwort | Ist Ollama erreichbar? Stimmen Endpunkt und Modell? Ist das Modell mit `ollama pull` geladen? |
| Chat-Antwort funktioniert nicht | Antworten im Chat benötigen den konfigurierten Ollama-Server. Ohne Ollama ist nur **Unbekannt / überspringen** möglich; prüfe Endpunkt und Modell. |
| Pilot startet nicht | Sind alle Werte in `.env` gesetzt und ist `docker compose ps` gesund? |
| Login nicht möglich | Benutzername darf nicht leer sein; prüfe `GUIDEOS_TEST_PASSWORD` in `.env`; es gibt absichtlich keine Passwort-Zurücksetzung. |
| Session nicht sichtbar | Im Pilot gehört sie möglicherweise zu einem anderen Browser oder einer beendeten Browser-Sitzung. |
| Datenbanktest ist übersprungen | Das ist ohne `GUIDEOS_TEST_POSTGRES_URL` normal. |
| Werkzeugaufruf endet mit `tool_output_invalid` | Der Server wurde aktualisiert und liefert neue Felder; im Tab **Werkzeuge** erneut **Werkzeuge abrufen** und die Freigaben speichern. |
| Beratung nennt nur KI-Fachwissen | Sind im Domain-Admin unter **Wissen** Dokumente für die Domäne hinterlegt, und findet **Suche testen** dort passende Abschnitte? Webseiten zählen nur, wenn die aktive Pack-Version Webquellen aktiviert. |

## 7. Lokale Entwicklerfunktionen

Den Domain-Admin aktivierst du nur lokal für Entwicklung und Demos:

```bash
GUIDEOS_ADMIN_ENABLED=1 uv run streamlit run app.py
```

Der vollständige Workflow steht im
[Domain-Admin Tutorial](domain-admin-tutorial.md).

Mit demselben Schalter erscheint der Modus **Beratungstest**. Dort bewertest du
die Beratungsantworten eines Testlaufs fachlich: Namen in der Seitenleiste
eintragen, Lauf wählen, je Antwort „Passt“, „Teilweise“ oder „Mangelhaft“
wählen, bei Bedarf eine Notiz ergänzen und **Speichern** drücken. Die
Bewertungen liegen danach beim Testlauf und nicht im Browser; über
**Alle Bewertungen als CSV herunterladen** bekommst du sie als Datei. Wie ein
Lauf zum Beratungstest wird, steht in
[test-runner.md](test-runner.md#beratungstest-und-expertenbewertung).

Die lokale REST-API ist ebenfalls ein Entwicklungswerkzeug:

```bash
uv run uvicorn guideos.adapters.fastapi:create_app --factory --reload
```

Die interaktive Dokumentation liegt unter `http://localhost:8000/docs`. Diese
API ist nicht für den internen Pilot freigeschaltet und noch keine öffentliche
Produkt-API.
