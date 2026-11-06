from guideos.tools.models import mask_identifiers


def test_mask_identifiers_hides_long_numbers_with_their_prefix():
    assert mask_identifiers("Export-AT0000000000000000000000000000000.csv") == "Export-AT….csv"
    assert mask_identifiers("15_10_2026-AT-Format-AT0000000000000000000000000000000-01_01_2026-QH.csv") == \
        "15_10_2026-AT-Format-AT…-01_01_2026-QH.csv"
    assert mask_identifiers("Kunde-123456789012-2025.csv") == "Kunde-…-2025.csv"
    assert mask_identifiers("Verein-ID1234567890123456.csv") == "Verein-ID….csv"


def test_mask_identifiers_keeps_dates_and_short_numbers():
    assert mask_identifiers("Jahresverbrauch-2025.csv") == "Jahresverbrauch-2025.csv"
    assert mask_identifiers("daten-20251025-20251027.csv") == "daten-20251025-20251027.csv"
    assert mask_identifiers("messung_2025-10-25_27.csv") == "messung_2025-10-25_27.csv"
    assert mask_identifiers("") == ""
