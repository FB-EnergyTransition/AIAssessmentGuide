# Sicherheitshinweise

GuideOS ist ein Forschungsprototyp. Bitte beachte vor jedem Betrieb außerhalb
einer lokalen Entwicklungsumgebung:

- **Kein Zugriffsschutz für den Domain-Admin:** Der Modus Domain-Admin
  (`GUIDEOS_ADMIN_ENABLED=1`) und damit die Wissensbasis und die Registrierung
  von MCP-Servern sind nicht durch eine Anmeldung geschützt. Starte ihn nur
  lokal oder in einem geschützten Netz.
- **Pilot-Login ist keine Authentifizierung:** Der Pilotmodus
  (`GUIDEOS_PILOT_MODE=1`) verlangt einen frei wählbaren Benutzernamen und ein
  gemeinsames Testpasswort. Der Benutzername trennt nur die Daten; er weist
  keine Identität nach. Der Pilot ist für synthetische Testdaten hinter einem
  HTTPS-Reverse-Proxy gedacht.
- **Teilnehmer-Header ohne Prüfung:** Ohne Pilotmodus ordnet die REST-API
  Daten über den Header `X-GuideOS-Participant` zu, prüft ihn aber nicht.
- **Hochgeladene Dokumente:** Die Wissensbasis bewahrt Originaldateien auf
  und liefert sie in der Streamlit-App über einen nicht erratbaren, aber nicht
  durch den Login geschützten Medien-Endpunkt aus. Vertrauliche Unterlagen
  gehören nicht in die Wissensbasis eines Piloten.
- **Werkzeugaufrufe:** GuideOS ruft registrierte MCP-Server nur mit
  freigegebenen Werkzeugen, geprüften Argumenten und einem Budget je Nachricht
  auf. Die Server selbst liegen in deiner Verantwortung.
- **Sprachmodell:** Modellantworten werden als Vorschläge behandelt und mit
  Quellen versehen; sie bleiben trotzdem Modellausgaben und können falsch
  sein. Die mitgelieferten Packs sind keine fachliche Beratung.

## Schwachstellen melden

Bitte melde Sicherheitsprobleme nicht als öffentliches Issue, sondern über
die Funktion „Report a vulnerability“ (Private Vulnerability Reporting) dieses
GitHub-Repositorys. Beschreibe den betroffenen Bestandteil, die Schritte zur
Nachstellung und die mögliche Auswirkung. Wir bestätigen den Eingang und
melden uns mit einer Einschätzung; eine feste Reaktionszeit können wir im
Forschungsbetrieb nicht zusagen.
