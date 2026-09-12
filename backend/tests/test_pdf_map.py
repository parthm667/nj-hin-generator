import io
import math

import pytest
from reportlab.pdfgen.canvas import Canvas

from app.services.pdf_map import NetworkMap, geometry_parts, map_projection, place_rank_labels


def test_projection_preserves_local_aspect_and_centers_inside_rectangle():
    points = [(-75, 40), (-74, 41)]
    project = map_projection(points, (10, 20, 200, 200))
    first, last = project(points[0]), project(points[1])
    assert (last[0] - first[0]) / (last[1] - first[1]) == pytest.approx(math.cos(math.radians(40.5)))
    assert first[1] == pytest.approx(20)
    assert last[1] == pytest.approx(220)
    assert first[0] + last[0] == pytest.approx(220)


def test_geojson_supports_multipart_and_preserves_polygon_holes():
    outer = [[0, 0], [3, 0], [3, 3], [0, 0]]
    hole = [[1, 1], [2, 1], [2, 2], [1, 1]]
    lines, polygons = geometry_parts({"type": "GeometryCollection", "geometries": [
        {"type": "MultiPolygon", "coordinates": [[outer, hole], [outer]]},
        {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]},
    ]})
    assert len(lines) == 2
    assert [len(polygon) for polygon in polygons] == [2, 1]


@pytest.mark.parametrize("geometry", [None, "bad json", "null", {}, {"type": "LineString", "coordinates": []}])
def test_missing_geometry_renders_without_invented_features(geometry):
    output = io.BytesIO()
    canvas = Canvas(output, pageCompression=0)
    flowable = NetworkMap(boundary=geometry)
    flowable.drawOn(canvas, 0, 0)
    canvas.save()
    assert b"Map unavailable: no stored geometry." in output.getvalue()


def test_collocated_ranks_are_separated_and_stay_inside_map():
    markers = place_rank_labels([(rank, (50, 50)) for rank in range(1, 11)], (0, 0, 200, 200))
    assert len(markers) == 10
    for index, (_, anchor, center) in enumerate(markers):
        assert anchor == (50, 50)
        assert all(8 <= value <= 192 for value in center)
        assert all(math.dist(center, other[2]) >= 19 for other in markers[:index])


def test_network_without_boundary_and_missing_selected_geometry_are_explicit():
    road = {"type": "LineString", "coordinates": [[-74, 40], [-74.01, 40.01]]}
    empty = {"type": "LineString", "coordinates": []}
    for selected, expected in [([], b"No selected network segments."), ([(1, None)], b"Selected network geometry unavailable."), ([(1, empty)], b"Selected network geometry unavailable."), ([(1, road)], b"Municipal boundary unavailable")]:
        output = io.BytesIO()
        canvas = Canvas(output, pageCompression=0)
        NetworkMap(roads=[road], hin_segments=selected, rank_labels={1: 1}).drawOn(canvas, 0, 0)
        canvas.save()
        assert expected in output.getvalue()
