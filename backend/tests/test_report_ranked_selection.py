"""Exercise ranking against real SQL rows; no PostGIS operations are needed."""

from types import SimpleNamespace

from geoalchemy2 import Geometry
from sqlalchemy import Column, LargeBinary, MetaData, Table, create_engine
from sqlalchemy.orm import Session

from app.models.tables import HINSegment, Municipality, RoadSegment
from app.services.pdf_map import build_network_map
from app.services.pdf_service import PDFReportGenerator


def test_ranked_rows_are_scoped_unique_and_limited_after_latest_selection():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    # Keep the mapped scalar columns, while replacing unused PostGIS geometry
    # storage so this non-spatial query can be exercised without a server.
    tables = {}
    for model in (Municipality, RoadSegment, HINSegment):
        tables[model] = Table(model.__tablename__, metadata, *[
            Column(column.name,
                   LargeBinary() if isinstance(column.type, Geometry) else column.type,
                   primary_key=column.primary_key)
            for column in model.__table__.columns
        ])
    with engine.connect() as connection:
        connection.connection.driver_connection.create_function("AsEWKB", 1, lambda value: value)
        connection.connection.driver_connection.create_function("AsGeoJSON", 1, lambda value: value)
        metadata.create_all(connection)
        connection.execute(tables[RoadSegment].insert(), [
            {"segment_id": segment, "muni_id": 1 if segment != 99 else 2,
             "road_name": f"Road {segment}", "length_miles": 1}
            for segment in [*range(1, 13), 99]
        ])
        rows = [
            # The older high rate must not control rank or consume a table row.
            (1, 1, 42, True, 1000), (2, 1, 42, True, 5),
            (3, 2, 42, True, 50), (4, 2, 42, False, 2000),
            # Neither a different municipality nor a different analysis belongs.
            (5, 99, 42, True, 9999), (6, 1, 43, True, 9999),
        ]
        rows += [(10 + segment, segment, 42, True, segment) for segment in range(3, 13)]
        connection.execute(tables[HINSegment].insert(), [
            {"hin_id": hin, "segment_id": segment, "analysis_id": analysis,
             "is_significant": selected, "crash_rate": rate, "severity_score": 1}
            for hin, segment, analysis, selected, rate in rows
        ])
        with Session(bind=connection) as db:
            result = PDFReportGenerator(db)._get_ranked_segments(
                SimpleNamespace(analysis_id=42, muni_id=1))
            assert [road.segment_id for _, road in result] == [2, 12, 11, 10, 9, 8, 7, 6, 1, 5]
            assert result[8][0].hin_id == 2
            assert result[8][0].crash_rate == 5
            assert result[0][0].hin_id == 3
            network = build_network_map(db, SimpleNamespace(analysis_id=42, muni_id=1), None, result)
            assert sorted(segment_id for segment_id, _ in network.hin_segments) == list(range(1, 13))
    engine.dispose()
