from guideos.context.verification import unverified_amounts, unverified_numbers


def test_numbers_found_in_the_sources_are_verified():
    sources = ["Undichte Fenster verursachen 10 bis 15 Prozent mehr Verbrauch (Stand 2022)."]

    assert unverified_numbers("Das kostet 10 bis 15 % mehr, Stand 2022.", sources) == []


def test_numbers_missing_from_the_sources_are_reported_as_written():
    sources = ["Einkommensschwache Haushalte erhalten Zuschüsse bis zu 100 Prozent."]

    assert unverified_numbers(
        "Bis zu 100 % Zuschuss, höchstens 10 000 € und 7,5 Jahre Laufzeit.", sources
    ) == ["10 000", "7,5"]


def test_number_formats_are_compared_by_value():
    sources = ["Verbrauch 28000 kWh auf 140 m², Temperatur 21.5 Grad, Kennzahl 200.0"]

    assert unverified_numbers(
        "28.000 kWh, 28 000 kWh, 140m², 21,5 °C und rund 200 kWh pro m².", sources
    ) == []


def test_number_words_in_the_sources_count_as_numbers():
    sources = ["So sparen Sie gut fünf Prozent der Heizenergie und bis zu zwanzig Euro."]

    assert unverified_numbers("Das spart 5 % und 20 Euro, nicht 30.", sources) == ["30"]


def test_list_numbering_and_identifiers_are_not_numbers():
    text = "1. Erstens\n2) Zweitens\nDrittens: siehe D1 und CO2."

    assert unverified_numbers(text, ["ohne Zahlen"]) == []


def test_each_unverified_number_is_reported_once():
    assert unverified_numbers("30 % hier und 30 % dort.", [""]) == ["30"]


def test_space_separated_figures_in_a_source_also_count_individually():
    sources = ["Tabelle: 10 150 200 und 13,4 % 2 800"]

    assert unverified_numbers("Zwischen 10 und 150, höchstens 200 oder 2 800.", sources) == []


def test_unverified_amounts_reports_only_money_figures_missing_from_the_sources():
    text = (
        "LED-Lampen kosten 2 € bis 10 €, ein Messgerät 10 – 30 Euro, die Wartung etwa 150 EUR "
        "und ein Tausch 8 000 – 15 000 €. Stelle den Boiler auf 60 °C und spare 5 %."
    )

    assert unverified_amounts(text, ["Ein Messgerät kostet ab 10 Euro."]) == [
        "2", "30", "150", "8 000", "15 000",
    ]


def test_unverified_amounts_accepts_amounts_from_the_sources_and_other_notations():
    sources = ["Der Zuschuss beträgt höchstens 7.500 Euro, die Abgabe 12 Cent je Einheit."]

    assert unverified_amounts("Bis zu 7500 € Zuschuss und 12 ct je Einheit.", sources) == []
    assert unverified_amounts("Das kostet € 49,90 oder EUR 80.", sources) == ["49,90", "80"]
    assert unverified_amounts("Mehrere tausend Euro, ab 2025 bei 20 Grad.", sources) == []


def test_an_amount_is_only_verified_by_the_same_amount_of_money_in_a_source():
    # Found in the second advisory test: "ab etwa 5 €" passed because some passage
    # contained the bare number 5.
    sources = ["Ein Haushalt mit 5 Personen spart 2 Prozent; die Leiste kostet zwanzig Euro."]

    assert unverified_amounts("Steckerleisten gibt es ab 5 €, Lampen für 2 € bis 20 €.", sources) == ["5", "2"]


def test_both_ends_of_a_range_joined_by_und_or_oder_are_checked():
    assert unverified_amounts("Das kostet zwischen 100 und 200 Euro.", [""]) == ["100", "200"]
    assert unverified_amounts("Rechne mit 30 oder 40 €.", ["Das kostet 40 €."]) == ["30"]
