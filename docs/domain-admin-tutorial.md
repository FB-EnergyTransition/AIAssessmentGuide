# Domain-Admin Tutorial

Der Domain-Admin ist ein lokales Entwicklungs- und Demo-Werkzeug. Damit legst
du Domain Packs an, bearbeitest ihre Fragen, validierst und aktivierst
Versionen und pflegst die Wissensbasis einer Domäne. Er ist nicht Teil des
internen Mehrbenutzer-Piloten und ersetzt keinen produktiven Freigabeprozess.

## Voraussetzungen und Start

Starte GuideOS im Repository mit aktiviertem Admin-Modus:

```bash
GUIDEOS_ADMIN_ENABLED=1 uv run streamlit run app.py
```

Öffne die von Streamlit ausgegebene Adresse und wähle in der Seitenleiste
**Domain-Admin**. Im Pilotmodus bleibt dieser Modus bewusst verborgen.

Ohne weitere Konfiguration liegen Drafts, Aktivierungen und Wissensbasis unter
`.guideos/`. Ist `GUIDEOS_DATABASE_URL` gesetzt, liegen sie in PostgreSQL und
gelten für alle Prozesse mit derselben Datenbank, also auch für den Pilot (siehe
„Lokale Test-Oberfläche mit PostgreSQL“ im README).

## Grundbegriffe

Ein **Domain Pack** beschreibt einen fachlichen Assessment-Typ. Es enthält
Metadaten, Fragen mit ihrer Validierung, Regeln mit Reason Codes, Empfehlungen und
optional die Einstellung für Webquellen und Beratungsvorgaben für das Modell. Die mitgelieferten Beispiele sind:

- `verein-demo` für einen einfachen Vereins-Check (Auswahl- und Zahlenfragen,
  Regeln mit Empfehlungen),

Ein Pack besitzt eine Domain-ID, etwa `verein-demo`, sowie eine Version, etwa
`1.0.0`. Aktive Versionen sind unveränderlich: Bestehende Sessions behalten
immer ihre gestartete Domain-Version und ihren Content-Hash. Wird eine Version
entfernt, lassen sich ihre Sessions nicht mehr öffnen.

Ein **Draft** ist eine Version, die im Admin entstanden ist (Quelle `local`).
**Gebündelt** (`bundled`) sind die Packs, die im Repository unter
`domain_packs/` liegen. Zur Domäne gehört außerdem eine **Wissensbasis**, die
von den Versionen unabhängig ist.

## Die Admin-Oberfläche verwenden

Oben steht der aufklappbare Bereich **Neues Domain-Pack erstellen**, darunter
das Auswahlfeld **Domain-Version**. Wähle dort ein Pack. Die Kennzeichnung
zeigt:

- **Quelle**: `bundled` für mit dem Repository gelieferte Packs oder `local`
  für Drafts,
- **aktiv** oder **draft**,
- **gültig** oder ungültig.

Die Tabs haben diese Aufgaben:

| Tab | Zweck |
|---|---|
| Metadaten | Domain-ID, Version, Name, Beschreibung und Locale vorbereiten; Webquellen der Wissensbasis für die Beratung ein- oder ausschalten; Beratungsvorgaben für das Modell festlegen; KI-Empfehlungen beim Abschluss ein- oder ausschalten und begrenzen; Empfehlungen im Chat „Ergebnis“ kompakt oder ausführlich anzeigen und die Einleitungs- und Angebotstexte des Chats festlegen. |
| Fragen | Fragen im Formular anlegen, bearbeiten, verschieben, löschen und als Draft speichern. |
| Regeln | Regeln mit Art, Bedingung oder Kennzahl, Reason Code, verknüpften Empfehlungen oder Themen, Status und Fachquelle im Formular bearbeiten; Themenkatalog pflegen. |
| Empfehlungen | Den Empfehlungskatalog bearbeiten: Empfehlungs-ID, Priorität, Titel, Text, optionale Fachquelle (`source_reference`) und optionale Kennzahlen aus Werkzeugergebnissen (`figures`, `message_with_figures`) je Empfehlung anlegen, ändern, löschen und als Draft speichern. |
| JSON-Draft | Das komplette Pack als JSON bearbeiten und als Draft speichern. |
| Aktivieren | Draft prüfen und eine vorhandene gültige Version aktivieren. |
| SOyA | Die Fragen als SOyA-Struktur (JSON) exportieren oder eine SOyA-Datei als Draft importieren. |
| Wissen | Dokumente und Webseiten der Wissensbasis hinzufügen, entfernen und die Suche testen. |
| Werkzeuge | MCP-Server der Domäne registrieren, ihre Werkzeuge abrufen und einzeln für das Modell freigeben. |

## Ein neues Domain-Pack anlegen

Für eine neue Domäne ohne Vorlage gibt es oben im Domain-Admin den Bereich
**Neues Domain-Pack erstellen**:

1. Klappe den Bereich auf und vergib eine **Domain-ID** aus Kleinbuchstaben,
   Ziffern und Bindestrichen, zum Beispiel `sanierungs-check`. Die ID darf
   noch nicht existieren und lässt sich später nicht umbenennen, weil
   Sessions und Wissensbasis daran hängen.
2. Trage einen **Namen** und optional eine Beschreibung ein.
3. Klicke auf **Neues Domain-Pack erstellen**.

GuideOS legt ein leeres Pack als Draft in Version `0.1.0` an und wählt es
unter **Domain-Version** aus. Bei einer ungültigen oder bereits vergebenen
Domain-ID oder einem leeren Namen erscheint eine Fehlermeldung, und es wird
nichts gespeichert.

So wird aus dem leeren Pack ein Assessment:

1. Lege im Tab **Fragen** mit **Neue Frage** die Fragen an (siehe
   [Fragen im Editor bearbeiten](#fragen-im-editor-bearbeiten)).
2. Lege im Tab **Empfehlungen** die Empfehlungen an (siehe
   [Empfehlungen bearbeiten](#empfehlungen-bearbeiten)) und im Tab
   **Regeln** die Regeln, die sie auslösen (siehe
   [Themen und Regelarten](#themen-und-regelarten)). Ohne Regeln gibt es
   keine Regel-Empfehlung, und die erste Empfehlung im Beratungschat kommt vom Modell.
3. Validiere und aktiviere die Version im Tab **Aktivieren**. Erst danach
   erscheint das Pack im Assessment-Modus.
4. Hinterlege bei Bedarf Unterlagen im Tab **Wissen**.

## Eine neue Version aus einer Vorlage erstellen

1. Wähle eine vorhandene Domain-Version als Vorlage.
2. Öffne **Metadaten**. Das Feld **Version** schlägt die nächste Patch-Version
   vor, zum Beispiel `1.4.1` für die Vorlage `1.4.0`.
3. Klicke auf **Als Draft speichern**. Dadurch entsteht ein Draft unter
   `.guideos/domain_packs/` (mit `GUIDEOS_DATABASE_URL` in der Tabelle
   `domain_pack_drafts`); gebündelte Packs im Repository werden nie direkt
   verändert.
4. Wähle die neue Version unter **Domain-Version** aus.
5. Ändere Fragen im Tab **Fragen**, Regeln im Tab **Regeln** und
   Empfehlungen im Tab **Empfehlungen**; den Pack-Block `tools` nur im Tab
   **JSON-Draft**.
6. Prüfe den Draft im Tab **Aktivieren** mit **Draft validieren**.

Eine andere **Domain-ID** im Tab **Metadaten** erzeugt eine Kopie der Vorlage
als neue Domäne, samt Fragen, Regeln und Empfehlungen.

Erst wenn die Validierung erfolgreich ist, darf eine Version aktiviert werden.
Nach der Aktivierung wird die Auswahl im lokalen Katalog
`.guideos/domain_catalog.json` gespeichert, mit `GUIDEOS_DATABASE_URL` in der
Tabelle `domain_active_versions`; dann sehen alle Prozesse mit derselben
Datenbank die Aktivierung sofort.

## Fragen im Editor bearbeiten

Der Tab **Fragen** zeigt oben die Fragen in Pack-Reihenfolge und darunter den
Editor für die unter **Frage bearbeiten** gewählte Frage:

1. **Neue Frage** hängt eine Textfrage `frage_<n>` an; **Nach oben** und
   **Nach unten** ändern die Reihenfolge, **Frage löschen** entfernt die Frage,
   **Änderungen verwerfen** stellt den gespeicherten Stand wieder her.
2. Wähle den **Feldtyp** (außerhalb des Formulars, damit die passenden Felder
   erscheinen). Ein Typwechsel verwirft Optionen und Grenzen des alten Typs.
3. Fülle das Formular aus und klicke auf **Frage übernehmen**. Auswahlfragen
   erhalten ihre Optionen in der Tabelle (Wert und Bezeichnung; eine leere
   Bezeichnung übernimmt den Wert), Sichtbarkeit verweist nur auf frühere
   Fragen, Listenwerte werden kommagetrennt eingegeben.
4. Ein rotes Banner nennt, warum der aktuelle Stand ungültig ist, etwa wenn
   eine Regel auf eine gelöschte Frage verweist. Speichern ist dann gesperrt.
5. **Fragen als Draft speichern** schreibt nach der Domain-ID und Version aus
   dem Tab **Metadaten**. Aktive und gebündelte Versionen werden nie
   überschrieben; vergib dort zuerst eine neue Version.

Feldtypen, Darstellung und Validierung:

| Feldtyp | `field_type` | Darstellung (`widget`) | Validierung | Normalisierter Wert |
|---|---|---|---|---|
| Einfachauswahl | `select` | `radio` (Standard), `dropdown` | Optionen | Optionswert |
| Mehrfachauswahl | `multiselect` | `checkboxes` (Standard), `dropdown` | Optionen, `min_count`, `max_count` | Liste von Optionswerten in Pack-Reihenfolge |
| Ganzzahl | `integer` | | `minimum`, `maximum` | ganze Zahl |
| Dezimalzahl | `number` | | `minimum`, `maximum` | Zahl |
| Text | `text` | `input` (Standard), `textarea` | `min_length`, `max_length`, `pattern` | Text ohne Randleerzeichen |
| Ja/Nein | `boolean` | | | `true` oder `false` |
| Datum | `date` | | `min_date`, `max_date` (JJJJ-MM-TT) | Datum als JJJJ-MM-TT |
| Datei-Upload | `file` | | Block `upload`: `tool` (leer: `dataset_tool` des Packs), `arguments` (feste Argumente als JSON-Objekt, nie `owner`, `filename`, `content`, `format_hint`, `dataset_id`), `extensions` (Standard `csv, txt`), `max_bytes` (Standard 64 MiB), `use_in_advisory` (höchstens eine Frage je Pack) | Datensatzreferenz des Berechnungsdienstes (`dataset_id`, maskierter Dateiname, Importzusammenfassung), nie der Dateiinhalt |

Datei-Fragen brauchen den Pack-Block `tools` (siehe unten; der Block lässt
sich nur im JSON-Draft setzen, nicht im Formular) und einen im Tab
**Werkzeuge** registrierten Server mit freigegebenem Importwerkzeug. GuideOS
prüft die Eingabe deterministisch (Endung, Größe, nicht leer), ruft beim
Speichern der Antwort das Importwerkzeug mit `owner` = Teilnehmer auf und
speichert nur die Referenz; ein fachlicher Fehler des Werkzeugs erscheint
als Validierungsmeldung (`tool_error`), ein nicht erreichbarer Server als
`tool_server_unavailable`, und die Frage bleibt offen. Ersetzen, Überspringen
und Ausblenden einer Datei-Antwort löschen den alten Datensatz auf dem Server.
In Bedingungen und Regeln ist für `file` nur `is_missing` zulässig. Der
Datensatz der Frage mit `use_in_advisory` wird beim Abschluss der Datensatz
des Profils (DATASET für das Modell, erste Analyse im Chat „Ergebnis“);
weitere Datei-Fragen bleiben Angaben ohne Auswertung.

Im Assessment beantworten Nutzerinnen und Nutzer Auswahl- und Ja/Nein-Fragen
über das Widget und speichern per Knopf; Datei-Fragen über den Uploader
(ohne Chat-Feld); Text, Zahlen und Daten kommen über das
Chat-Feld. Dort entscheidet zuerst die deterministische Validierung (etwa
„1“, „12.3.1998“, „unbekannt“); erst bei Rückfragen oder unklaren Eingaben
schlägt das Modell einen Wert vor, den die Validierung prüft.
Bedingungen in `visible_if` und Regeln: `equals` und `in` nur auf
Einzelwert-Feldern, `contains` (Liste enthält Wert) und `contains_any` (Liste
überlappt Liste) nur auf `multiselect`, `lt` und `gte` nur auf Zahlen und
Daten, `is_missing` überall (bei `file` als einziger Operator). Der Loader
lehnt andere Kombinationen ab.


## JSON-Draft bearbeiten

Ein vereinfachtes Frage-Element sieht so aus:

```json
{
  "question_id": "construction_year",
  "field_key": "construction_year",
  "field_type": "integer",
  "text": "In welchem Jahr wurde das Gebäude gebaut?",
  "required": true,
  "allow_unknown": false,
  "validation": {"minimum": 1800, "maximum": 2100}
}
```

Halte diese Regeln ein:

- `question_id`, `field_key` und Regel-IDs müssen innerhalb des Packs eindeutig
  sein.
- `field_type` ist `select`, `multiselect`, `integer`, `number`, `text`,
  `boolean`, `date` oder `file`; `widget` ist optional und muss zum Typ
  passen; `file` braucht den Block `upload` (siehe Tabelle oben).
- `select` und `multiselect` benötigen `options`; andere Typen dürfen keine
  haben. Validierungsschlüssel müssen zum Typ passen (siehe Tabelle oben).
- Eine optionale Sichtbarkeitsregel `visible_if` verweist auf eine frühere
  Frage und unterstützt `equals`, `in`, `is_missing`, `contains` oder
  `contains_any`, passend zum Feldtyp.
- Regeln und Empfehlungen müssen auf existierende Feld- und
  Empfehlungs-IDs verweisen.

Eine Regel prüft eine Antwort und liefert bei einem Treffer einen
Reason Code und Empfehlungen:

```json
{
  "rule_id": "insulation-needs-review",
  "field_key": "insulation_state",
  "operator": "in",
  "value": ["partial", "poor", null],
  "reason_code": "INSULATION_REVIEW",
  "recommendation_ids": ["building-envelope"]
}
```

Regeln vergeben keine Punkte, und ein Assessment hat keinen Score. Ältere
gebündelte Versionen enthalten je Regel noch den Schlüssel `score`; er wird
gelesen und ignoriert. Regeln unterstützen zusätzlich zu den Operatoren der
Sichtbarkeit `lt` und `gte` für Zahlen und Daten. Mit `divisor_field_key`
vergleicht eine Regel das Verhältnis zweier Zahlenfelder, etwa Verbrauch pro
Quadratmeter.

Eine Empfehlung liefert den Text, den Regeln auslösen:

```json
{
  "recommendation_id": "building-envelope",
  "priority": 2,
  "title": "Gebäudehülle prüfen",
  "message": "Lass den Zustand von Dämmung, Fenstern und relevanten Bauteilen fachlich prüfen.",
  "source_reference": "Fachquelle oder Norm (optional)"
}
```

Empfehlungen erscheinen nach `priority` aufsteigend. `source_reference` zeigen
Chat und Bericht als Fachquelle an.

Nach jeder JSON-Änderung: speichern, die neue Draft-Version auswählen und
validieren. Eine Fehlermeldung benennt die verletzte Pack-Struktur oder
Referenz.

## Aktivieren und prüfen

Aktivierung ändert nur, welche Version bei neuen Assessments standardmäßig
verwendet wird. Bereits gestartete oder abgeschlossene Sessions ändern sich
nicht. Eine neue Domäne erscheint im Assessment-Modus erst, wenn eine ihrer
Versionen aktiviert ist.

Vor dem Aktivieren solltest du mindestens:

1. alle Fragen im Assessment manuell durchspielen,
2. einen gültigen und einen ungültigen Wert pro Feld testen,
3. Sichtbarkeitsbedingungen in beiden Richtungen prüfen,
4. erwartete und nicht erwartete Regel-Auslösungen vergleichen,
5. Ergebnis, Reason Codes und Bericht prüfen.

Aktiviere nur eine fachlich geprüfte Version. Die Demo-Packs dürfen nicht als
fachliche Beratung verwendet werden.

## Eine Version löschen

Unter dem Auswahlfeld **Domain-Version** steht der aufklappbare Bereich
**Ausgewählte Version löschen**. Er entfernt genau den gewählten Draft:

1. Wähle die Version unter **Domain-Version**.
2. Klappe den Bereich auf, bestätige das Kontrollkästchen und klicke auf
   **Version löschen**.

Das Löschen lässt sich nicht rückgängig machen. Solange es weitere Versionen
der Domäne gibt, bleibt ihre Wissensbasis erhalten, denn sie gilt für alle
Versionen. Löschst du die letzte Version einer Domäne, werden auch alle
Dokumente und Webseiten ihrer Wissensbasis entfernt; der Bereich nennt die
Anzahl vor dem Löschen.

Nicht löschbar sind:

- **gebündelte Packs**: Sie liegen im Repository unter `domain_packs/` und
  werden dort entfernt,
- **aktive Versionen**: Aktiviere zuerst eine andere Version der Domäne,
- **Versionen mit Assessment-Sessions**: Der Admin nennt die Anzahl und löscht
  nichts, weil sich die Sessions sonst nicht mehr öffnen ließen. Lösche die
  Sessions zuerst im Assessment-Modus.

## SOyA-Export und -Import

Der Tab **SOyA** stellt die Fragen einer Version als Struktur nach der
Semantic Overlay Architecture ([SOyA](https://github.com/OwnYourData/soya))
bereit, im JSON-Format (JSON ist gültiges YAML für `soya init`):

- Eine **Basis** mit den Feldschlüsseln als Attribute (`String`, `Integer`,
  `Float`, `Boolean`, `Date`).
- **OverlayValidation**: `cardinality` aus Pflicht und Unbekannt-erlaubt
  (`1..1`, `0..1`, bei Mehrfachauswahl `1..*` oder `0..*`), `valueOption` aus
  den Optionen, `valueRange` aus Zahlen- und Datumsgrenzen, `length` und
  `pattern` aus Textgrenzen.
- **OverlayForm** als JSON-Forms-Schema und -Layout: Optionen mit
  Bezeichnung (`oneOf`), Mehrfachauswahl als Array, `format: radio` und
  `multi: true` für die Darstellung, Sichtbarkeit als `SHOW`-Regel.
- **OverlayAnnotation**: Fragetext als `label`, Hilfetext als `comment` je
  Locale.

GuideOS-Angaben ohne SOyA-Gegenstück (`question_id`, `required` mit
`allow_unknown`, `widget`, `is_missing`-Sichtbarkeit) liegen je Control unter
`options.guideos`; JSON Forms ignoriert unbekannte Optionen. Regeln und
Empfehlungen sind nicht Teil der Struktur.

Der Import liest eine SOyA-Datei (JSON; YAML vorher etwa mit `yq -o=json`
wandeln), erzeugt daraus die Fragen und speichert sie als Draft unter der
angegebenen Ziel-Version. Fehlen Overlays, gelten Standardwerte (Feldschlüssel
als Text, Typ aus der Basis, optional, Standard-Darstellung). Mit der Option
**Regeln und Empfehlungen übernehmen** bleiben Regeln der ausgewählten Version
erhalten, deren Felder noch existieren; verworfene Regeln werden als Warnung
genannt. Die Pack-Einstellungen der ausgewählten Version (Webquellen,
Beratungsvorgaben, `result_chat`, `knowledge_recommendations`, `tools` und
`topics`) übernimmt der Import immer. Nicht unterstützt: mehrere Basen, `DateTime`, `Time`, Referenzen auf
andere Basen, mehrwertige Attribute ohne Optionen und Regeln außer `SHOW`.

Prüfung gegen die SOyA-Werkzeuge (optional, Docker nötig):

```bash
docker run --rm -i oydeu/soya-cli soya init < verein-demo-1.0.0.soya.json
```

Der Befehl muss JSON-LD ausgeben und mit Exitcode 0 enden.

## Wissensbasis pflegen

Die Wissensbasis enthält Unterlagen, die die Beratung nach dem Assessment
durchsucht und als Quelle nennt. Sie gehört zur Domäne (etwa `verein-demo`)
und gilt für alle Versionen; ein neues Dokument braucht keine neue
Pack-Version. Regeln und Bericht bleiben davon unberührt.

### Dokumente

1. Wähle oben eine Version der Domäne und öffne den Tab **Wissen**.
2. Lade unter **Dokument hochladen** eine PDF-, Markdown- oder Textdatei hoch
   (Textdateien in UTF-8). Optional vergibst du einen **Titel**; sonst gilt
   der Dateiname, bei Markdown die erste Überschrift.
3. Klicke auf **Dokument hinzufügen**. Die Tabelle zeigt Titel, Herkunft,
   Anzahl der Abschnitte, Stand und den Anfang des Datei-Hashes.

GuideOS zerlegt PDFs seitenweise und Markdown nach Überschriften; diese
Fundstelle (`Seite 3`, Überschrift) steht später in der Quellenangabe.
Gescannte PDFs ohne Textebene werden abgelehnt, eine Texterkennung gibt es
nicht. Dieselbe Datei kann pro Domäne nur einmal hinterlegt werden. Für eine
neue Fassung entfernst du das alte Dokument unter **Ausgewähltes Dokument
entfernen** und lädst die neue Datei hoch.

GuideOS bewahrt die hochgeladene Originaldatei auf. Im Beratungschat steht
unter **Quellenauszüge** bei jeder zitierten Stelle der Link **Im Dokument
öffnen**; bei PDFs öffnet der Browser die Datei direkt auf der zitierten
Seite. Der Link führt auf die Seite, nicht auf den einzelnen Satz; den
zitierten Text zeigt der Auszug darunter. Dokumente, die vor dieser Funktion
hinterlegt wurden, haben noch kein Original: Lade dieselbe Datei einfach
noch einmal hoch, GuideOS ergänzt dann nur das Original und indexiert nichts
neu. Dieselbe Datei in mehreren Domänen wird nur einmal gespeichert; das
Original verschwindet, wenn das letzte Dokument mit diesem Inhalt entfernt
wird.

### Webseiten

GuideOS führt keine Live-Websuche aus. Eine Webseite wird beim Hinzufügen
abgerufen, wie ein Dokument zerlegt und mit Adresse und Abrufdatum
gespeichert.

1. Trage unter **Adresse der Webseite** eine vollständige `https://`-Adresse
   ein und klicke auf **Webseite abrufen und hinzufügen**. HTML-Seiten, PDFs
   und Textdateien werden unterstützt.
2. **Neu abrufen** (bei einer ausgewählten Webseite) holt die Seite erneut.
   Hat sich der Inhalt geändert, entsteht eine neue Quellversion mit eigenem
   Hash; die alte erscheint in der Übersicht als „ersetzt“, wird nicht mehr
   durchsucht und bleibt für ältere Zitate nachvollziehbar. Ist die Seite
   unverändert, bleibt die gespeicherte Version. **Ersetzte Versionen
   entfernen** löscht die alten Stände; Zitate in Sessions und Profilen
   behalten Hash und Auszug trotzdem.

Die Beratung verwendet Webseiten nur, wenn die Pack-Version das erlaubt.
Aktiviere dazu in **Metadaten** die Option **Webquellen der Wissensbasis in
der Beratung verwenden** und speichere eine neue Version. Mit **Erlaubte
Web-Domains** beschränkst du die Quellen auf Hostnamen wie `klimaaktiv.at`
(Subdomains eingeschlossen); das gilt beim Hinzufügen und bei der Suche. Im
Pack steht dann:

```json
"web_sources": {
  "enabled": true,
  "allowed_domains": ["klimaaktiv.at"]
}
```

Seiten, deren Inhalt erst per JavaScript geladen wird, liefern wenig oder
keinen Text. Prüfe nach dem Hinzufügen die Anzahl der Abschnitte.

Welche Webquellen eine Domäne braucht, legt die Pack-Version mit
`allowed_domains` fest; die Wissensbasis selbst ist nie gebündelt und wird im
Tab **Wissen** angelegt. Verlangen die Beratungsvorgaben des Packs, Beträge
oder Fristen nur aus der Wissensbasis zu nennen, bleibt die Beratung ohne
diese Quellen bei allgemeinen Hinweisen.


### Themen und Regelarten

Mit dem optionalen Block `topics` legst du Themen fest, denen das Modell jede
KI-Empfehlung zuordnet (`topic_id` oder `other`). Regeln bekommen dann eine
Art (`kind`): ohne Angabe wie bisher eine Pflicht-Empfehlung über
`recommendation_ids`; `exclude` verwirft KI-Empfehlungen zu den genannten
Themen, wenn die Bedingung zutrifft; `prioritize` reiht sie mit `priority`
nach vorne. Beide nennen `topics` statt `recommendation_ids`. Der Loader
prüft, dass die Themen im Katalog stehen. Ein Beispiel für ein Pack, das
Mieterinnen keine Hinweise zum Heizungstausch macht und undichte Fenster
nach vorne reiht:

```json
"topics": [
  { "topic_id": "heating_system", "label": "Heizsystem und Heizungstausch" },
  { "topic_id": "building_envelope", "label": "Gebäudehülle, Fenster und Dämmung" }
],
"rules": [
  { "rule_id": "tenant-no-heating-change", "kind": "exclude", "field_key": "tenure",
    "operator": "equals", "value": "tenant", "reason_code": "TENANT_NO_HEATING_CHANGE",
    "topics": ["heating_system"] },
  { "rule_id": "drafty-envelope-first", "kind": "prioritize", "field_key": "drafty_windows",
    "operator": "equals", "value": true, "reason_code": "ENVELOPE_FIRST",
    "topics": ["building_envelope"], "priority": 1 }
]
```


Im Tab **Regeln** bearbeitest du beides im Formular: Themen als eine Zeile
je Thema (`topic_id: Bezeichnung`, dann **Themen übernehmen**) und je Regel
Art, Feld, Operator, Vergleichswert, Reason Code, Empfehlungen oder Themen,
Priorität, Herkunft und Fachquelle (**Regel übernehmen**). Die Felder
**Werkzeug (Kennzahl)**, **JSON-Pointer (Kennzahl)** und **Bezeichnung
(Kennzahl)** machen aus der Regel eine Kennzahlen-Regel (siehe
[Block `tools` einer Pack-Version](#block-tools-einer-pack-version));
dann ist der Vergleichswert eine Zahl und das Feld die Datei-Frage. **Regeln
als Draft speichern** schreibt den Stand in die Version aus dem Tab
Metadaten. Packs ohne `topics` hashen unverändert.

### Lebenszyklus der Regeln

Jede Regel hat einen Status: **Entwurf**, **In Prüfung** oder
**Freigegeben**. Neue Regeln beginnen als Entwurf; die Schaltflächen **Zur
Prüfung geben**, **Freigeben** und **Zurück auf Entwurf** führen den Status
in dieser Reihenfolge (ein Entwurf geht erst in die Prüfung, dann in die
Freigabe). Entwurfs- und Prüfregeln laufen in Draft-Versionen mit, damit du
sie im Assessment und mit dem Test-Runner prüfen kannst; **Aktivieren**
lehnt eine Version ab, solange eine Regel nicht freigegeben ist, und nennt
die betroffenen Regel-IDs. Im Pack stehen die Felder als `status`, `origin`
(`expert` oder `imported`) und `source_reference`; fehlen sie, gilt
`approved` und `expert`, gebündelte Packs bleiben also unverändert. Wer
freigegeben hat, wird nicht protokolliert, weil der Domain-Admin keine
Anmeldung hat.

### Empfehlungen bearbeiten

Der Tab **Empfehlungen** zeigt den Empfehlungskatalog des Packs: Was eine
Regel der Art Pflicht-Empfehlung auslöst, steht hier mit Empfehlungs-ID,
Priorität, Titel, Text und optionaler Fachquelle. Der Text erscheint
wörtlich im Ergebnis, im Bericht und im Chat „Ergebnis“; das Modell
formuliert ihn nicht um. Die Priorität bestimmt die Reihenfolge (1 ganz
oben), unabhängig davon, welche Regel zuerst zutrifft.

**Neue Empfehlung** legt einen Eintrag mit Platzhaltertexten an, den du im
Formular ausfüllst (**Empfehlung übernehmen**); danach steht die neue ID im
Tab **Regeln** unter **Empfehlungen (Pflicht-Empfehlung)** zur Auswahl.
**Empfehlung löschen** lehnt GuideOS ab, solange eine Regel die Empfehlung
noch nennt, und zeigt die betroffenen Regel-IDs; dasselbe gilt für das
Umbenennen einer verwendeten ID. **Empfehlungsänderungen verwerfen** stellt
den gespeicherten Stand wieder her, **Empfehlungen als Draft speichern**
schreibt den Katalog zusammen mit Fragen, Regeln und Themen in die Version
aus dem Tab Metadaten.

**Kennzahlen im Text.** Hat das Pack den Block `tools` mit einer ersten
Analyse, kann eine Empfehlung die Kennzahlen der Person nennen. Dafür
füllst du zwei Felder gemeinsam aus: **Text mit Kennzahlen (optional)** ist
der Text mit Platzhaltern in geschweiften Klammern, etwa „In {months_above}
von {months_total} Monaten liegt deine Spitze über 10 kW.“, und **Kennzahlen
(optional)** nennt je Zeile, woher ein Platzhalter kommt:

```
name: werkzeug /json/pointer [dezimalstellen] [{"wert": "text"}]
months_above: analyze_dataset /totals/months_above 0
max_above: analyze_dataset /totals/max_above 1
confidence: analyze_dataset /pattern/confidence {"high": "hoch", "medium": "mittel"}
```

Der Name besteht aus Kleinbuchstaben, Ziffern und `_`, das Werkzeug ist das
`dataset_tool` oder ein Schritt aus `after_import`, der JSON-Pointer zeigt in
dessen strukturiertes Ergebnis, die Dezimalstellen (0 bis 6) runden, die
JSON-Zuordnung ersetzt Werte durch Texte. Jeder Platzhalter braucht eine
Kennzahl und jede Kennzahl einen Platzhalter; die **Nachricht** bleibt
Pflicht und erscheint, sobald eine Kennzahl fehlt (kein Datensatz, Werkzeug
nicht gelaufen, Wert leer). GuideOS setzt die Werte deterministisch aus dem
letzten Ergebnis des Werkzeugs ein (Dezimalkomma, Ja/Nein für Wahrheitswerte),
nennt im Chat „Ergebnis“ die Berechnung als Quelle und liefert den Text über
die REST-API; ein Web-Client kann auf der Empfehlungskarte den ersten Satz
hervorheben. Schreib die Kennzahl darum in den ersten Satz und vermeide dort
Abkürzungspunkte wie „Abs.“.


### KI-Empfehlungen beim Abschluss

Beim Abschluss eines Assessments erzeugt GuideOS die ersten KI-Empfehlungen
aus den Angaben und der Wissensbasis und speichert sie im Ergebnis. Im Tab
**Metadaten** schaltet das Häkchen **Zusätzliche KI-Empfehlungen beim
Abschluss erzeugen** das für eine Domäne ab (etwa für reine Formular-Packs),
**Höchstzahl KI-Empfehlungen (1–5)** begrenzt die Anzahl (Standard 5). Im
Pack steht beides als optionaler Block `knowledge_recommendations`; mit den
Standardwerten (eingeschaltet, 5) fehlt der Block, und der Content-Hash
bestehender Versionen ändert sich nicht:

```json
"knowledge_recommendations": {
  "enabled": true,
  "max_count": 3
}
```

### Beratungsvorgaben für das Modell

Die Vorgaben, die GuideOS dem Modell für Beratungsantworten und
KI-Empfehlungen mitgibt, sind domänenneutral. Was nur für deine Domäne gilt,
trägst du in **Metadaten** unter **Beratungsvorgaben für das Modell** ein und
speicherst eine neue Version. Geeignet sind kurze, klare Sätze, zum Beispiel:

```text
Der Kontext ist Österreich. Nenne keine Programme oder Stellen, die nur in
anderen Ländern gelten. Förderungen nennst du nur mit Quelle aus der
Wissensbasis. Unterscheide bei baulichen Maßnahmen zwischen Miete und Eigentum.
```

GuideOS hängt den Text unverändert an die Vorgaben an; er geht dem
Fachwissen des Modells vor. Im Pack steht er als `advisory_guidance`
(höchstens 4000 Zeichen). Ein leeres Feld bedeutet: keine Vorgaben, das Pack
bleibt unverändert. Regeln, Validierung und Fragehilfe nutzen die
Vorgaben nicht.

### Empfehlungen im Chat „Ergebnis“ kompakt anzeigen

Nach dem Abschluss postet der Chat „Ergebnis“ die Regel-Empfehlungen und die
KI-Empfehlungen. Standardmäßig steht jede Empfehlung mit Titel, Text, Quelle,
Gründen und Bezug da; bei Packs mit vielen Regeln wird das lang. Das Häkchen
**Empfehlungen im Chat „Ergebnis“ kompakt anzeigen** im Tab **Metadaten**
schaltet auf eine Liste mit je einer Zeile um (fetter Titel und Kurztext,
darunter der Hinweis auf den Bericht). Die Quellen bleiben als Belege der
Nachricht erhalten (Ausklapper „Quellen“), der Bericht zeigt weiterhin alle
Details, und ungeprüfte Zahlen in KI-Empfehlungen bleiben auch kompakt als
„Ungeprüft“ markiert. Im Pack steht die Einstellung als Block `result_chat`
mit `recommendations: compact`; ohne das Häkchen fehlt der Block, und das
Pack hasht wie zuvor. Die Einstellung gilt je Version.


Im selben Block legst du die Texte fest, mit denen der Chat „Ergebnis“ die
Empfehlungen einrahmt: **Einleitung im Chat „Ergebnis“** ist die erste
Nachricht nach dem Abschluss, **Einleitung nach einem neuen Assessment**
die Nachricht, wenn ein neues Assessment das Profil ersetzt hat, und
**Angebot für KI-Empfehlungen** die Frage, ob zusätzliche KI-Empfehlungen
erzeugt werden sollen (sie erscheint nur, wenn beim Abschluss keine
entstanden sind; ein kurzes Ja oder Nein darauf wertet GuideOS
deterministisch aus, auch mit eigenem Text). Leere Felder bedeuten die
Standardtexte von GuideOS, die der Hilfetext des Feldes zeigt. Im Pack
stehen sie als `intro`, `updated_intro` und `offer` (je höchstens 500
Zeichen) im Block `result_chat`; nicht gesetzte Felder fehlen, damit
bestehende Versionen gleich hashen.

### Suche testen

Gib unter **Suche testen** eine typische Nutzerfrage ein und klicke auf
**Suchen**. Die Liste zeigt die Abschnitte, die die Beratung für diese Frage
erhielte, mit Kennung (`D1` für Dokumente, `W1` für Webseiten), Fundstelle und
Score. Die Suche berücksichtigt die Webquellen-Einstellung der oben gewählten
Version.

Ohne weitere Konfiguration sucht GuideOS lexikalisch. Ist
`GUIDEOS_EMBEDDING_MODEL` auf ein in Ollama geladenes Embedding-Modell gesetzt,
werden neue Dokumente eingebettet und die Suche wird hybrid (lexikalische
und semantische Treffer zusammengeführt). Bereits hinterlegte Dokumente
meldet der Tab dann als nicht indexiert; **Neu indexieren** holt das nach,
bis dahin bleibt die Suche lexikalisch. Mit `GUIDEOS_RERANK_MODEL` sortiert
ein Modell die gefundenen Abschnitte je Anfrage nach; die Kopfzeile des Tabs
nennt den aktiven Suchmodus.

### Was die Beratung daraus macht

Im Beratungschat steht unter jedem Antwortabschnitt die Quelle, zum Beispiel
`Quelle: Dokument „Leitfaden Heizung“, Seite 3` oder `Quelle: KI-Fachwissen`.
Unter **Quellenauszüge** lässt sich der zitierte Text nachlesen. Stützt sich
ein Abschnitt auf keine Regel, kein Dokument und keine Website, kennzeichnet
GuideOS ihn als KI-Fachwissen.

## Werkzeuge (MCP-Server) anbinden

Im Tab **Werkzeuge** registrierst du je Domäne MCP-Server, deren Werkzeuge das
Modell in der Beratung aufrufen darf und über die GuideOS hochgeladene
Datensätze importiert und analysiert. Die Registrierung gehört zur Domäne wie
die Wissensbasis und gilt für alle Versionen; ob und mit welchem Budget die
Beratung Werkzeuge nutzt, legt die Pack-Version im optionalen Block `tools`
fest (siehe unten). Ein Server spricht MCP über HTTP, optional mit einem
statischen Token; liefert ein Werkzeug ein Ausgabeschema, prüft GuideOS jede
Ausgabe dagegen. Die Werkzeugnamen in diesem Abschnitt (`import_dataset`,
`delete_dataset`, `analyze_dataset`) sind Platzhalter für die Werkzeuge
deines Servers.


1. **Server registrieren oder ändern:** Name (Kleinbuchstaben, Ziffern, `-`,
   `_`; eindeutig je Domäne), Adresse (`http://` oder `https://`, Pfad `/mcp`;
   läuft der Server als Dienst im Compose-Stack und registrierst du ihn aus
   einer Host-Instanz, nimm die Gateway-Adresse des Compose-Netzes, die der
   Host-Prozess und die Container gleichermaßen erreichen), Token (maskierte Eingabe,
   Anzeige nur als „gesetzt“), Häkchen **Aktiv**. **Server speichern** legt
   den Server an oder ändert Adresse, Token und Zustand; Freigaben bleiben
   erhalten.
2. **Werkzeuge abrufen** holt die Werkzeugliste des Servers mit Beschreibung,
   Eingabe- und Ausgabeschema und speichert sie als Cache. Ist der Server
   nicht erreichbar oder das Token falsch, erscheint eine Fehlermeldung
   (`tool_server_unavailable`). GuideOS prüft jede Werkzeugausgabe gegen
   dieses gespeicherte Schema: Nach einem Update des Servers, das Felder
   ergänzt, muss die Liste neu abgerufen
   werden, sonst lehnt GuideOS die Ausgabe mit `tool_output_invalid` ab
   („entspricht nicht ihrem Schema: <Feld>: nicht vorgesehen“). Ohne
   Domain-Admin geht das per Skript mit `ToolService.refresh_tools`.
3. **Freigeben:** Je Werkzeug ein Häkchen **Freigeben: <Name>**, dann
   **Freigaben speichern**. Nur freigegebene Werkzeuge aktiver Server sieht
   das Modell; das Feld `owner` ist im angezeigten Schema nicht enthalten,
   weil GuideOS es selbst mit der Teilnehmerkennung setzt. Erwartet die
   Pack-Version Werkzeuge für Upload und Löschung (`dataset_tool`,
   `delete_tool`), die auf keinem aktiven Server freigegeben sind, warnt der
   Tab.
4. **Server entfernen** löscht die Registrierung samt Freigaben.

Gespeichert wird lokal in `.guideos/mcp_servers.json`
(`GUIDEOS_TOOL_SERVERS_PATH`), mit `GUIDEOS_DATABASE_URL` in der Tabelle
`mcp_servers`. Das Token liegt dort im Klartext wie die Pilot-Zugangsdaten in
`.env`; der Domain-Admin ist ein Entwicklungs- und Demo-Werkzeug, kein
Produktionszugang.

### Block `tools` einer Pack-Version

```json
"tools": {
  "enabled": true,
  "dataset_tool": "import_dataset",
  "delete_tool": "delete_dataset",
  "max_calls_per_message": 6,
  "max_seconds_per_message": 90,
  "after_import": [
    {"tool": "analyze_dataset", "arguments": {"threshold": 10},
     "note": "Fachlicher Hinweis der Domäne, der mit der Zusammenfassung des Werkzeugs im Chat „Ergebnis“ erscheint."}
  ],
  "derive": [
    {"field_key": "data_source", "tool": "import_dataset", "value": "upload"},
    {"field_key": "annual_total", "tool": "analyze_dataset",
     "pointer": "/summary/annual_total", "decimals": 0},
    {"field_key": "pattern_detected", "tool": "analyze_dataset",
     "pointer": "/pattern/detected", "map": {"true": true}, "mode": "propose",
     "when": {"pointer": "/pattern/confidence", "operator": "in", "value": ["high", "medium"]},
     "confidence_pointer": "/pattern/confidence",
     "note": "In den Daten wurde ein regelmäßiges Muster erkannt."}
  ]
}
```

Ohne eigene Angaben gelten `import_dataset` und `delete_dataset` als Namen
für `dataset_tool` und `delete_tool`.


`after_import` (optional) ist die erste Analyse nach jedem Upload: ein
Schritt oder eine Liste von Schritten (jedes Werkzeug höchstens einmal).
GuideOS ruft die genannten, freigegebenen Werkzeuge selbst mit der neuen
`dataset_id` und den `arguments` auf (ohne Modell) und schreibt die
Zusammenfassung jedes Werkzeugs plus `note` als Nachricht „Erste Analyse“ in
den Chat **Ergebnis**; die Ergebnisse stehen danach dem Modell als `T1…` und
im Bericht unter „Berechnungen“ zur Verfügung. Für die Datei-Frage des
Assessments läuft die Analyse sofort nach dem Import im Orchestrator
(Ergebnisse an der Session, beim Abschluss am Profil); ein gescheiterter
Schritt wird als Ereignis `analysis_failed` vermerkt, die übrigen laufen
weiter, der Import bleibt gültig. `dataset_id` und `owner` dürfen in
`arguments` nicht stehen. Die Schwelle und der Hinweistext sind
Fachentscheidungen der Domäne, keine Vorgabe des Kerns.


`at_completion` (optional, seit 27.10.2026) ist eine Liste gleich gebauter
Schritte, die nicht nach dem Upload, sondern beim Abschluss des Assessments
laufen (und nach einem Upload im Bereich „Messdaten (CSV)“ mit den Angaben
des Profils), weil sie Antworten brauchen: `arguments_from_answers` nennt je
Argument ein Feld (`field_key`), eine Zuordnung `map` von Antwortwert zu
Argument und einen `default`, der gilt, wenn die Antwort fehlt oder nicht in
der Zuordnung steht (ohne `default` entfällt das Argument). Ein Werkzeug
steht entweder in `after_import` oder in `at_completion`; Kennzahlen-Regeln
und Kennzahlen in Empfehlungen dürfen beide Arten nennen.


`derive` (optional) leitet Antworten aus dem Import und den
Ergebnissen der ersten Analyse ab, jeweils für ein Feld des Packs (keine
Datei-Frage): `tool` ist das `dataset_tool` oder ein Werkzeug aus
`after_import`, `pointer` ein JSON-Zeiger in dessen strukturierte Ausgabe
(oder `value` eine Konstante), `map` übersetzt Rohwerte in Antwortwerte
(nicht aufgeführte Werte ergeben nichts), `decimals` rundet Zahlen, `when`
(Zeiger, `equals`/`in`/`gte`/`lt`, Wert) begrenzt die Ableitung. `mode`
`answer` speichert den Wert als Antwort mit Herkunft `measurement` und dem
Rohwert „Aus den Messdaten abgeleitet“: Die Frage wird übersprungen, bleibt
im Review änderbar, und jede Ableitung durchläuft die Validierung der Frage.
`mode` `propose` zeigt den Wert als Vorschlag zur aktuellen Frage
(`confidence_pointer` nennt die Konfidenz, `note` die Begründung); die
Person übernimmt ihn per Knopf oder antwortet anders. Eine eigene Antwort
wird nie überschrieben, nur „unbekannt“ wird aufgefüllt. Ein neuer Upload
verwirft die früheren Ableitungen und berechnet sie neu.

Regeln können Kennzahlen der ersten Analyse vergleichen: `metric` mit
`tool` (aus `after_import`), `pointer` und `label` (deutsch, für die
Belegzeile), `field_key` ist dann die Datei-Frage, `operator` `equals`, `in`,
`lt` oder `gte`, der Vergleichswert eine Zahl. Im Tab **Regeln** sind das
die Felder **Werkzeug (Kennzahl)**, **JSON-Pointer (Kennzahl)** und
**Bezeichnung (Kennzahl)**. Ohne Datensatz greift eine solche Regel nicht. Der Beleg
nennt die Kennzahl, ihren Wert und das Werkzeug, der Chat zitiert die
Berechnung als Quelle, der Bericht führt sie unter „Berechnungen“.

`enabled` schaltet die Werkzeuge in der Beratung dieser Version ein,
`dataset_tool` und `delete_tool` nennen die Werkzeuge, die GuideOS selbst für
Upload und Löschung von Datensätzen aufruft, `max_calls_per_message` (1 bis 10)
und `max_seconds_per_message` (5 bis 600) begrenzen die Werkzeugschleife je
Nachricht. Ohne den Block bleibt die Beratung wie bisher; bestehende Packs
hashen unverändert. Mit dem Block erscheint im Ergebnisbereich der Upload
**Messdaten (CSV)**, die Beratung ruft freigegebene Werkzeuge auf und zitiert
ihre Ergebnisse als Quelle „Berechnung“ (`T1…`), und Profil löschen,
Assessment löschen, `purge_data` und `retention` löschen die Datensätze auch
auf dem Server. Die Datei-Fragen des Packs importieren beim Speichern der
Antwort über dasselbe Werkzeug: Die Datei-Frage mit `use_in_advisory` (etwa
`dataset_file`) liefert den Datensatz für die erste Analyse und die
Beratung, weitere Datei-Fragen (optional mit festen `arguments`) werden nur
importiert und gespeichert. **Messdaten (CSV)** ersetzt oder löscht danach
denselben Datensatz und hält die Antwort auf die Datei-Frage in Schritt.
`enabled: false` genügt für ein Pack mit Datei-Frage ohne Werkzeuge in der
Beratung.


## Dateien und Grenzen

| Pfad | Inhalt |
|---|---|
| `domain_packs/` | Gebündelte, versionierte Packs im Repository. |
| `.guideos/domain_packs/` | Lokale Drafts außerhalb der Versionskontrolle (ohne PostgreSQL). |
| `.guideos/domain_catalog.json` | Lokale Aktivierungsauswahl (ohne PostgreSQL). |
| `domain_pack_drafts`, `domain_active_versions` | Dieselben Daten in PostgreSQL, wenn `GUIDEOS_DATABASE_URL` gesetzt ist. |
| `.guideos/knowledge.json` | Wissensbasis aller Domänen (ohne PostgreSQL; Pfad über `GUIDEOS_KNOWLEDGE_PATH`). |
| `knowledge_documents` | Die Wissensbasis in PostgreSQL, wenn `GUIDEOS_DATABASE_URL` gesetzt ist. |
| `knowledge_files` | Die hochgeladenen Originaldateien der Wissensbasis in PostgreSQL, je Inhalts-Hash einmal; lokal der Ordner `.guideos/knowledge_files/`. |

Lokale Drafts und der Katalog sind absichtlich nicht in Git. Für eine
nachvollziehbare, gemeinsam nutzbare Domain-Version sollte ein validiertes Pack
nach fachlicher Prüfung als neue Version unter `domain_packs/` in einen
normalen Code-Review aufgenommen werden.

Der Domain-Admin bietet derzeit keine Benutzerrechte, Vier-Augen-Freigaben,
Review-Historie oder produktive Auditierung. Nutze ihn daher nur lokal und mit
nicht-sensiblen Testdaten. Das gilt auch für die Wissensbasis: Wer den Admin
erreicht, kann Dokumente hinzufügen und entfernen und den Server Webadressen
abrufen lassen.
