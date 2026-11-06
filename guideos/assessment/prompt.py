"""Shared prompt building blocks for LLM answer interpretation."""

ANSWER_NORMALIZATION_RULES = (
    "Regeln für Zahlen und Zahlwörter:\n"
    "- Ausgeschriebene deutsche Zahlwörter werden exakt in Ziffern umgesetzt, "
    "ohne Runden und ohne Schätzen. Im Deutschen steht die Einerstelle vor der "
    "Zehnerstelle: 'zweiundsechzig' = 62 (nicht 26), 'achtzig' = 80, "
    "'achtundzwanzigtausend' = 28000.\n"
    "- Jahreszahlen: 'neunzehnhundert' = 1900, der Rest folgt dahinter: "
    "'neunzehnhundertachtzig' = 1980, 'neunzehnhundertzweiundsechzig' = 1962, "
    "'zweitausendfünfzehn' = 2015.\n"
    "- Prüfe vor der Ausgabe: Zerlege das Zahlwort in Tausender, Hunderter und "
    "den Teil 'Einer und Zehner', setze die Ziffern daraus zusammen und gib genau "
    "diese Zahl aus.\n"
    "- Einheiten und Füllwörter ('ca.', 'etwa', 'Stück', 'Euro') entfallen; der "
    "Zahlenwert bleibt unverändert.\n"
    "- Bei Auswahlfragen zählt die Option, die die Frage direkt beantwortet; "
    "Zusatzangaben zum Umfeld ändern die Zuordnung nicht."
)


ANSWER_TYPE_RULES = (
    "\nRegeln je field_type der Frage:\n"
    "- select: normalized_value ist genau ein Optionswert (value).\n"
    "- multiselect: normalized_value ist eine JSON-Liste von Optionswerten "
    "(value), auch bei nur einer genannten Option, z. B. [\"email\"]; jede "
    "Option höchstens einmal.\n"
    "- integer und number: normalized_value ist die Zahl ohne Einheit.\n"
    "- boolean: normalized_value ist true oder false als JSON-Boolean, nie als "
    "Text; 'ja', 'stimmt', 'trifft zu' bedeuten true, 'nein', 'nicht', "
    "'trifft nicht zu' bedeuten false.\n"
    "- date: normalized_value ist ein Datum als Text im Format JJJJ-MM-TT, "
    "z. B. '12.03.2024' -> '2024-03-12'; fehlen Tag oder Monat, ist die "
    "Antwort unsicher.\n"
    "- text: normalized_value ist der Wortlaut des Nutzers ohne Leerzeichen am "
    "Rand; nicht zusammenfassen, nicht korrigieren, nicht übersetzen. Die "
    "Antwort ist gültig, sobald sie nicht leer und keine Rückfrage ist."
)
