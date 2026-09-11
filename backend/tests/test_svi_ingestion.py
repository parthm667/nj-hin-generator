import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from scripts.arcgis_client import ArcGISResponseError
from scripts.ingest_svi import (
    CDC2020SVIIngester,
    CDC_SVI_2020_SOURCE_NAME,
    SVIReport,
    SVITract,
    load_svi_batch,
    normalize_svi_feature,
)
import scripts.ingest_svi as svi_ingestion


def make_svi_feature(**property_overrides):
    properties = {
        "FID": 46755,
        "FIPS": "34001000100",
        "ST_ABBR": "NJ",
        "E_TOTPOP": 2157,
        "EP_NOVEH": 27.0,
        "EP_MINRTY": 85.9,
        "RPL_THEMES": 0.9249,
    }
    properties.update(property_overrides)
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-74.80, 40.20],
                    [-74.70, 40.20],
                    [-74.70, 40.30],
                    [-74.80, 40.30],
                    [-74.80, 40.20],
                ]
            ],
        },
    }


def test_svi_feature_uses_official_national_percentile_and_provenance():
    record = normalize_svi_feature(make_svi_feature())

    assert record == SVITract(
        tract_id="34001000100",
        county_fips="001",
        total_population=2157,
        median_income=None,
        pct_no_vehicle=27.0,
        pct_minority=85.9,
        svi_score=92.49,
        svi_percentile=92.49,
        source_year=2020,
        source_name=CDC_SVI_2020_SOURCE_NAME,
        geometry=make_svi_feature()["geometry"],
    )


def test_svi_feature_preserves_cdc_no_data_sentinel_as_unknown():
    record = normalize_svi_feature(
        make_svi_feature(
            E_TOTPOP=-999,
            EP_NOVEH=-999,
            EP_MINRTY=-999,
            RPL_THEMES=-999,
        )
    )

    assert record.total_population is None
    assert record.median_income is None
    assert record.pct_no_vehicle is None
    assert record.pct_minority is None
    assert record.svi_score is None
    assert record.svi_percentile is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"ST_ABBR": "NY"}, "state"),
        ({"FIPS": "34001"}, "tract_id"),
        ({"RPL_THEMES": 1.1}, "svi_percentile"),
        ({"E_TOTPOP": -2}, "total_population"),
    ],
)
def test_svi_feature_rejects_incompatible_values(changes, reason):
    with pytest.raises(ValueError, match=reason):
        normalize_svi_feature(make_svi_feature(**changes))


def test_svi_batch_upsert_is_idempotent_and_refreshes_official_values():
    database_url = os.getenv("DATA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("DATA_TEST_DATABASE_URL is not configured")

    engine = create_engine(Settings(database_url=database_url, _env_file=None).database_url)
    connection = engine.connect()
    transaction = connection.begin()
    database = Session(bind=connection)
    try:
        first = normalize_svi_feature(make_svi_feature())
        report = SVIReport()
        load_svi_batch(database, [first], report)

        refreshed = normalize_svi_feature(
            make_svi_feature(E_TOTPOP=2200, RPL_THEMES=0.8)
        )
        load_svi_batch(database, [refreshed], report)

        row = connection.execute(
            text(
                """
                SELECT COUNT(*)::integer AS records, total_population,
                       svi_percentile, source_year, source_name,
                       ST_GeometryType(geom) AS geometry_type
                FROM census_tracts
                WHERE tract_id = '34001000100'
                GROUP BY total_population, svi_percentile, source_year,
                         source_name, geom
                """
            )
        ).mappings().one()
        assert dict(row) == {
            "records": 1,
            "total_population": 2200,
            "svi_percentile": pytest.approx(80.0),
            "source_year": 2020,
            "source_name": CDC_SVI_2020_SOURCE_NAME,
            "geometry_type": "ST_MultiPolygon",
        }
        assert report.upserted == 2
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_svi_ingester_requires_complete_nj_source_snapshot(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.metadata_options = None
            self.iter_options = None

        def metadata(self, **options):
            self.metadata_options = options
            return {}

        def count(self, where):
            assert where == "ST_ABBR = 'NJ'"
            return 2

        def iter_features(self, **options):
            self.iter_options = options
            return iter([])

    monkeypatch.setattr(svi_ingestion, "EXPECTED_NJ_TRACTS", 1)
    client = FakeClient()

    with pytest.raises(ArcGISResponseError, match="tract count changed"):
        CDC2020SVIIngester(client).ingest(None, SVIReport())

    assert client.metadata_options == {
        "geometry_type": "esriGeometryPolygon",
        "required_fields": svi_ingestion.OUT_FIELDS,
    }
    assert client.iter_options is None
