# Mitwirken

Danke für dein Interesse an GuideOS.

## Snapshot-Repository

Dieses Repository ist ein Export des GuideOS-Kerns aus einem nicht
öffentlichen Monorepo, in dem auch produktspezifische Teile liegen. Issues
und Pull Requests werden hier gelesen und geprüft; Änderungen werden im
Quell-Repository übernommen und kommen mit dem nächsten Export zurück. Ein
Pull Request wird deshalb nicht direkt gemergt, sondern mit Verweis auf den
Export geschlossen. Antwortzeiten können schwanken.

## Arbeitsweise

- **Kleinste Änderung:** Behebe das Problem oder setze die Funktion so knapp
  wie möglich um; keine Umbauten nebenbei, keine neuen Abhängigkeiten ohne
  Notwendigkeit.
- **Testgetrieben:** Zu jeder Verhaltensänderung gehört ein gezielter Test,
  der vorher fehlschlägt. Bestehende Tests werden nicht abgeschwächt oder
  gelöscht, um die Suite grün zu bekommen.
- **Prüfkette vor dem Pull Request:**

  ```bash
  uv run ruff check .
  uv run pytest
  uv run python -m testsuite.runner --report html
  ```

- **Sprache:** Alle Texte für Nutzerinnen und Nutzer, Fehlermeldungen,
  Pack-Texte und Dokumentation sind Deutsch mit echten Umlauten (`ä`, `ö`,
  `ü`, `ß`). Bezeichner, Optionswerte und Codes bleiben ASCII.
- **Domain-Packs sind unveränderlich:** Gebündelte Versionen unter
  `domain_packs/<id>/<version>/pack.json` werden nie in place geändert, auch
  nicht für Formulierungen. Jede Änderung ist ein neues Versionsverzeichnis,
  weil Sessions den Inhalts-Hash prüfen.
- **Das Modell entscheidet nichts:** Workflow, Validierung, Regeln und
  Quellenzuordnung bleiben deterministisch und ohne Modell testbar; das
  Sprachmodell interpretiert und formuliert nur. Vorgaben für das Modell im
  Kern sind domänenneutral; fachliche Vorgaben gehören in das Pack
  (`advisory_guidance`).
- **Kontext Österreich:** Beispiele und Pack-Texte verwenden österreichische
  Begriffe und Bezüge.

Lies vor Strukturänderungen
`docs/entwicklung/target-architecture-overview.md`.
