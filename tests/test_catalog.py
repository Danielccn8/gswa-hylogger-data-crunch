from pathlib import Path

from hylogger.catalog import parse_thredds_catalog


def test_parse_thredds_catalog() -> None:
    fixture = Path(__file__).parent / "fixtures" / "thredds_catalog.xml"
    frame = parse_thredds_catalog(
        fixture.read_bytes(), "https://thredds.nci.org.au/thredds/fileServer/"
    )

    assert frame["hole_id"].tolist() == ["05GJD001", "SMALL"]
    first = frame.iloc[0]
    assert first["raw_size_bytes"] == 782_400_000
    assert first["raw_url"] == (
        "https://thredds.nci.org.au/thredds/fileServer/rs07/WA/05GJD001.zip"
    )
