"""Offline vector overview of the stored municipal and selected-network geometry."""

import json
import math

from reportlab.lib.colors import HexColor, white
from reportlab.platypus import Flowable
from sqlalchemy import func

from app.models.tables import HINSegment, Municipality, RoadSegment


def _geometry(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def geometry_parts(value):
    """Return drawable paths and polygons, preserving multipart rings and holes."""
    geometry = _geometry(value)
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []

    def points(items):
        return [
            (float(p[0]), float(p[1])) for p in items
            if isinstance(p, (list, tuple)) and len(p) >= 2
            and all(isinstance(v, (float, int)) and math.isfinite(v) for v in p[:2])
        ]

    if kind == "LineString":
        return [points(coordinates)], []
    if kind == "MultiLineString":
        return [points(line) for line in coordinates], []
    if kind == "Polygon":
        return [], [[points(ring) for ring in coordinates]]
    if kind == "MultiPolygon":
        return [], [[points(ring) for ring in polygon] for polygon in coordinates]
    if kind == "Feature":
        return geometry_parts(geometry.get("geometry"))
    children = geometry.get("geometries", []) if kind == "GeometryCollection" else []
    if kind == "FeatureCollection":
        children = geometry.get("features", [])
    lines, polygons = [], []
    for child in children:
        child_lines, child_polygons = geometry_parts(child)
        lines.extend(child_lines)
        polygons.extend(child_polygons)
    return lines, polygons


def _paths(parts):
    lines, polygons = parts
    return [path for path in lines + [ring for polygon in polygons for ring in polygon] if len(path) >= 2]


def map_projection(points, rectangle):
    """Fit longitude/latitude to a box using a local equirectangular projection."""
    west, east = min(p[0] for p in points), max(p[0] for p in points)
    south, north = min(p[1] for p in points), max(p[1] for p in points)
    longitude_factor = max(0.01, math.cos(math.radians((south + north) / 2)))
    span_x, span_y = max((east - west) * longitude_factor, 1e-9), max(north - south, 1e-9)
    x, y, width, height = rectangle
    scale = min(width / span_x, height / span_y)
    offset_x = x + (width - span_x * scale) / 2
    offset_y = y + (height - span_y * scale) / 2

    def project(point):
        return offset_x + (point[0] - west) * longitude_factor * scale, offset_y + (point[1] - south) * scale

    return project


def _line_midpoint(lines):
    """Place a marker halfway along the longest visible multipart component."""
    candidates = []
    for line in lines:
        distances = [math.dist(a, b) for a, b in zip(line, line[1:])]
        if line:
            candidates.append((sum(distances), line, distances))
    if not candidates:
        return None
    total, line, distances = max(candidates, key=lambda item: item[0])
    remaining = total / 2
    for start, end, distance in zip(line, line[1:], distances):
        if distance and remaining <= distance:
            ratio = remaining / distance
            return start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio
        remaining -= distance
    return line[0]


def place_rank_labels(anchors, rectangle):
    """Choose separated marker centers; leader lines retain exact segment anchors."""
    x, y, width, height = rectangle
    radius, spacing = 8, 19
    candidates = [
        (x + radius + column * spacing, y + radius + row * spacing)
        for column in range(max(0, int((width - 2 * radius) / spacing) + 1))
        for row in range(max(0, int((height - 2 * radius) / spacing) + 1))
    ]
    placed = []
    for rank, anchor in anchors:
        clamped = (min(max(anchor[0], x + radius), x + width - radius), min(max(anchor[1], y + radius), y + height - radius))
        options = [clamped] + sorted(candidates, key=lambda p: math.dist(anchor, p))
        chosen = next((p for p in options if all(math.dist(p, prior[2]) >= spacing for prior in placed)), None)
        if chosen is not None:
            placed.append((rank, anchor, chosen))
    return placed


class NetworkMap(Flowable):
    """Vector map accepting GeoJSON directly, also suitable for offline previews."""

    def __init__(self, boundary=None, roads=(), hin_segments=(), rank_labels=None, width=504, height=300):
        super().__init__()
        self.width, self.height = width, height
        self.boundary = geometry_parts(boundary)
        self.roads = [geometry_parts(road) for road in roads]
        self.hin_segments = [(segment_id, geometry_parts(geom)) for segment_id, geom in hin_segments]
        self.rank_labels = rank_labels or {}

    def draw(self):
        canvas = self.canv
        navy, teal = HexColor("#18364A"), HexColor("#007F83")
        plot = (1, 38, self.width - 2, self.height - 39)
        canvas.saveState()
        canvas.setFillColor(HexColor("#F0F5F8"))
        canvas.setStrokeColor(HexColor("#D6E1E7"))
        canvas.roundRect(0, 0, self.width, self.height, 5, fill=1, stroke=1)
        boundary_paths = _paths(self.boundary)
        hin_paths = [path for _, parts in self.hin_segments for path in _paths(parts)]
        road_paths = [path for parts in self.roads for path in _paths(parts)]
        all_points = [p for path in boundary_paths + hin_paths + road_paths for p in path]
        if not all_points:
            canvas.setFillColor(navy)
            canvas.setFont("Helvetica", 10)
            canvas.drawCentredString(self.width / 2, self.height / 2, "Map unavailable: no stored geometry.")
            canvas.restoreState()
            return
        project = map_projection(all_points, (20, 56, self.width - 64, self.height - 80))
        canvas.saveState()
        clip = canvas.beginPath()
        clip.rect(*plot)
        canvas.clipPath(clip, stroke=0, fill=0)

        def draw_paths(paths, closed=False, fill=False):
            path = canvas.beginPath()
            for coordinates in paths:
                if not coordinates:
                    continue
                path.moveTo(*project(coordinates[0]))
                for point in coordinates[1:]:
                    path.lineTo(*project(point))
                if closed:
                    path.close()
            canvas.drawPath(path, stroke=1, fill=int(fill), fillMode=0)

        canvas.setFillColor(white)
        canvas.setStrokeColor(HexColor("#9BAFBC"))
        canvas.setLineWidth(0.8)
        for polygon in self.boundary[1]:
            draw_paths(polygon, closed=True, fill=True)
        canvas.setStrokeColor(HexColor("#D3DEE4"))
        canvas.setLineWidth(0.45)
        draw_paths(road_paths)
        canvas.setStrokeColor(teal)
        canvas.setLineWidth(2)
        canvas.setLineCap(1)
        draw_paths(hin_paths)
        anchors = []
        for segment_id, parts in self.hin_segments:
            if segment_id in self.rank_labels:
                anchor = _line_midpoint([[project(point) for point in line] for line in _paths(parts)])
                if anchor:
                    anchors.append((self.rank_labels[segment_id], anchor))
        markers = place_rank_labels(sorted(anchors), (10, 49, self.width - 54, self.height - 62))
        for _, anchor, center in markers:
            canvas.setStrokeColor(navy)
            canvas.setLineWidth(0.6)
            canvas.line(*anchor, *center)
        for rank, _, center in markers:
            canvas.setFillColor(navy)
            canvas.setStrokeColor(white)
            canvas.setLineWidth(1.3)
            canvas.circle(*center, 8, fill=1, stroke=1)
            canvas.setFillColor(white)
            canvas.setFont("Helvetica-Bold", 7)
            canvas.drawCentredString(center[0], center[1] - 2.5, str(rank))
        canvas.restoreState()
        canvas.setFillColor(navy)
        canvas.setStrokeColor(navy)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawCentredString(self.width - 20, self.height - 19, "N")
        canvas.setLineWidth(1)
        canvas.line(self.width - 20, self.height - 38, self.width - 20, self.height - 24)
        canvas.line(self.width - 23, self.height - 28, self.width - 20, self.height - 24)
        canvas.line(self.width - 17, self.height - 28, self.width - 20, self.height - 24)
        canvas.setFont("Helvetica", 7.5)
        canvas.setStrokeColor(teal)
        canvas.setLineWidth(2)
        canvas.line(12, 24, 28, 24)
        canvas.drawString(34, 21, "Selected network")
        canvas.setStrokeColor(HexColor("#9BAFBC"))
        canvas.setLineWidth(0.7)
        canvas.line(119, 24, 135, 24)
        canvas.drawString(141, 21, "Municipal boundary")
        canvas.setStrokeColor(HexColor("#D3DEE4"))
        canvas.line(260, 24, 276, 24)
        canvas.drawString(282, 21, "Road context")
        canvas.setFont("Helvetica", 6.5)
        canvas.drawRightString(self.width - 12, 8, "Numbers match the ranked segment table")
        canvas.setFont("Helvetica", 7)
        if not hin_paths:
            note = "No selected network segments." if not self.hin_segments else "Selected network geometry unavailable."
        elif not boundary_paths:
            note = "Municipal boundary unavailable."
        else:
            note = "Local geographic overview."
        canvas.drawString(12, 8, note)
        canvas.restoreState()


def build_network_map(db, analysis, municipality, ranked_segments, width=504, height=300):
    """Fetch each layer once, always restricting the network to this analysis."""
    boundary = db.query(func.ST_AsGeoJSON(Municipality.geom)).filter(Municipality.muni_id == analysis.muni_id).scalar()
    roads = db.query(func.ST_AsGeoJSON(RoadSegment.geom)).filter(RoadSegment.muni_id == analysis.muni_id).all()
    selected = db.query(RoadSegment.segment_id, func.ST_AsGeoJSON(RoadSegment.geom)).join(
        HINSegment, HINSegment.segment_id == RoadSegment.segment_id,
    ).filter(
        RoadSegment.muni_id == analysis.muni_id,
        HINSegment.analysis_id == analysis.analysis_id,
        HINSegment.is_significant.is_(True),
    ).distinct().all()
    ranks = {road.segment_id: rank for rank, (_, road) in enumerate(ranked_segments, 1)}
    return NetworkMap(boundary, [row[0] for row in roads], selected, ranks, width, height)
