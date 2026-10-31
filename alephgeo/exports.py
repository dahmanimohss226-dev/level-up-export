"""Small, printable map exports and CAD deliverables for a capture."""

from contextlib import contextmanager
import json
import math
import mmap
from pathlib import Path
import struct

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import layers
from .common import atomic_path, write_json
from .geo import MERCATOR_RADIUS

MAP_PDF = "map-osm.pdf"
TOPO_PDF = "topographic.pdf"
MESH_DXF = "mesh-3d.dxf"
TOPO_DXF = "topographic.dxf"
PDF_SIZE = (1684, 1191)  # A4 landscape at 144 dpi.
CONTOUR_PIXELS = 1200
NODATA = -1_000_000
MERCATOR_LIMIT = 85.0511287798066
WORLD_METERS = 2 * math.pi * MERCATOR_RADIUS

CAD_LAYERS = {
    "SITE-TOPO-MAJR": (30, "CONTINUOUS", 35),
    "SITE-TOPO-MINR": (32, "CONTINUOUS", 18),
    "SITE-TOPO-ANNO": (7, "CONTINUOUS", 15),
    "SITE-BLDG-OTLN": (1, "CONTINUOUS", 25),
    "SITE-ROAD-CNTR": (8, "DASHED", 18),
    "SITE-ROAD-EDGE": (7, "CONTINUOUS", 18),
    "SITE-PATH-WALK": (9, "CONTINUOUS", 18),
    "SITE-WATR-BODY": (5, "CONTINUOUS", 25),
    "SITE-BNDY-PROP": (1, "PHANTOM", 50),
    "SITE-LAND-AREA": (3, "CONTINUOUS", 18),
    "SITE-MESH-CONTEXT": (8, "CONTINUOUS", 18),
}
FEATURE_LAYERS = {
    "BOUNDARY": "SITE-BNDY-PROP",
    "BUILDING": "SITE-BLDG-OTLN",
    "CONTOUR": "SITE-TOPO-MINR",
    "ELEVATION_LABEL": "SITE-TOPO-ANNO",
    "INDEX_CONTOUR": "SITE-TOPO-MAJR",
    "LAND": "SITE-LAND-AREA",
    "PATH": "SITE-PATH-WALK",
    "ROAD_MAJOR": "SITE-ROAD-CNTR",
    "ROAD_MINOR": "SITE-ROAD-EDGE",
    "WATER": "SITE-WATR-BODY",
}


def _mercator(lat, lon):
    lat = math.radians(max(-MERCATOR_LIMIT, min(MERCATOR_LIMIT, lat)))
    return MERCATOR_RADIUS * math.radians(lon), MERCATOR_RADIUS * math.log(math.tan(math.pi / 4 + lat / 2))


def _osm_point(point):
    return _mercator(float(point[1]), float(point[0]))


def _area_polygon(run):
    return [_mercator(float(lat), float(lon)) for lat, lon in run["polygon"]]


def _site_origin(polygon, dem=None, grid=None):
    """Choose a stable local drawing origin and return its projected and geographic coordinates."""
    twice_area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))
    if abs(twice_area) > 1e-8:
        x = sum((a[0] + b[0]) * (a[0] * b[1] - b[0] * a[1]) for a, b in zip(
            polygon, polygon[1:] + polygon[:1])) / (3 * twice_area)
        y = sum((a[1] + b[1]) * (a[0] * b[1] - b[0] * a[1]) for a, b in zip(
            polygon, polygon[1:] + polygon[:1])) / (3 * twice_area)
    else:
        x0, y0, x1, y1 = _bounds(polygon)
        x, y = (x0 + x1) / 2, (y0 + y1) / 2
    latitude = math.degrees(2 * math.atan(math.exp(y / MERCATOR_RADIUS)) - math.pi / 2)
    longitude = math.degrees(x / MERCATOR_RADIUS)
    elevation = 0.0
    if dem is not None and np.isfinite(dem).any():
        elevation = float(np.nanmean(dem))
        if grid:
            unit = WORLD_METERS / (512 * 2 ** grid["zoom"])
            pixel_x = ((x + WORLD_METERS / 2) / unit - grid["x0"] * 512) / (grid["columns"] * 512) * dem.shape[1] - 0.5
            pixel_y = ((WORLD_METERS / 2 - y) / unit - grid["y0"] * 512) / (grid["rows"] * 512) * dem.shape[0] - 0.5
            column = max(0, min(dem.shape[1] - 1, round(pixel_x)))
            row = max(0, min(dem.shape[0] - 1, round(pixel_y)))
            if np.isfinite(dem[row, column]):
                elevation = float(dem[row, column])
    return x, y, elevation, latitude, longitude


def _local_xy(point, origin):
    scale = math.cos(math.radians(origin[3]))
    return (point[0] - origin[0]) * scale, (point[1] - origin[1]) * scale


def _bounds(points):
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _font(size, bold=False):
    names = ("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")
    paths = [Path("C:/Windows/Fonts") / names[0], Path("/usr/share/fonts/truetype/dejavu") / names[-1],
             Path("/System/Library/Fonts/Supplemental") / names[0]]
    for path in paths:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _layout(polygon):
    width, height = PDF_SIZE
    frame = (70, 104, width - 70, height - 92)
    left, top, right, bottom = frame
    x0, y0, x1, y1 = _bounds(polygon)
    span_x, span_y = max(1.0, x1 - x0), max(1.0, y1 - y0)
    scale = min((right - left) / span_x, (bottom - top) / span_y)
    ox = (left + right - scale * (x0 + x1)) / 2
    oy = (top + bottom + scale * (y0 + y1)) / 2

    def pixel(point):
        return round(ox + scale * point[0]), round(oy - scale * point[1])

    return dict(width=width, height=height, frame=frame, scale=scale, pixel=pixel, polygon=[pixel(p) for p in polygon])


def _geometry_lines(geometry):
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "LineString":
        yield coordinates
    elif kind == "MultiLineString":
        yield from coordinates
    elif kind == "Polygon":
        yield from coordinates
    elif kind == "MultiPolygon":
        for polygon in coordinates:
            yield from polygon


def _polygons(geometry):
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "Polygon":
        yield coordinates
    elif kind == "MultiPolygon":
        yield from coordinates


def _feature_layer(properties):
    kind = properties.get("kind")
    if kind == "building":
        return "BUILDING"
    if kind in ("water", "waterway"):
        return "WATER"
    if kind == "land":
        return "LAND"
    if kind == "road":
        road = properties.get("road")
        return "ROAD_MAJOR" if road == "major" else "PATH" if road == "path" else "ROAD_MINOR"
    return None


def _inside(point, polygon):
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        ax, ay = previous
        bx, by = current
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
        previous = current
    return inside


def _clipped_segment(start, end, polygon):
    """Clip one straight segment to a simple polygon and yield its inside pieces."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    if math.hypot(dx, dy) < 1e-7:
        return
    cuts = [0.0, 1.0]
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        ex, ey = b[0] - a[0], b[1] - a[1]
        denominator = dx * ey - dy * ex
        if abs(denominator) < 1e-12:
            continue
        ax, ay = a[0] - start[0], a[1] - start[1]
        t = (ax * ey - ay * ex) / denominator
        u = (ax * dy - ay * dx) / denominator
        if -1e-10 <= t <= 1 + 1e-10 and -1e-10 <= u <= 1 + 1e-10:
            cuts.append(max(0.0, min(1.0, t)))
    cuts = sorted(set(round(value, 12) for value in cuts))
    for first, second in zip(cuts, cuts[1:]):
        if second - first < 1e-10:
            continue
        middle = (first + second) / 2
        if _inside((start[0] + middle * dx, start[1] + middle * dy), polygon):
            yield ((start[0] + first * dx, start[1] + first * dy),
                   (start[0] + second * dx, start[1] + second * dy))


def _nice_step(span, target=18):
    raw = max(span / target, 0.01)
    power = 10 ** math.floor(math.log10(raw))
    return next(value * power for value in (1, 2, 5, 10) if value * power >= raw)


def _sample_terrain(folder, stage):
    grid = stage["grid"]
    columns, rows = grid["columns"], grid["rows"]
    scale = min(1.0, CONTOUR_PIXELS / max(columns * 512, rows * 512))
    width = max(2, round(columns * 512 * scale))
    height = max(2, round(rows * 512 * scale))
    source_x = (np.arange(width, dtype=np.float64) + 0.5) * (columns * 512 / width)
    source_y = (np.arange(height, dtype=np.float64) + 0.5) * (rows * 512 / height)
    tile_x = np.minimum(source_x.astype(np.int64) // 512, columns - 1)
    tile_y = np.minimum(source_y.astype(np.int64) // 512, rows - 1)
    local_x = np.minimum((source_x - tile_x * 512).astype(np.int64), 511)
    local_y = np.minimum((source_y - tile_y * 512).astype(np.int64), 511)
    by_position = {(result["row"], result["column"]): result for result in stage["results"]
                   if result.get("filename")}
    dem = np.full((height, width), NODATA, dtype=np.float32)
    x_groups = {int(value): np.flatnonzero(tile_x == value) for value in np.unique(tile_x)}
    y_groups = {int(value): np.flatnonzero(tile_y == value) for value in np.unique(tile_y)}
    for row, output_rows in y_groups.items():
        for column, output_columns in x_groups.items():
            result = by_position.get((row, column))
            if not result:
                continue
            tile_image = layers.heights(folder / result["filename"])
            try:
                values = np.asarray(tile_image, dtype=np.float32)
                dem[np.ix_(output_rows, output_columns)] = values[np.ix_(local_y[output_rows], local_x[output_columns])]
            finally:
                tile_image.close()
    valid = np.isfinite(dem) & (dem > NODATA / 2)
    if not valid.any():
        raise ValueError("No valid elevation values are available for the contour map.")
    dem[~valid] = np.nan
    return dem, 1.0


def _pixel_to_mercator(x, y, grid, width, height):
    source_x = grid["x0"] * 512 + (x + 0.5) * (grid["columns"] * 512 / width)
    source_y = grid["y0"] * 512 + (y + 0.5) * (grid["rows"] * 512 / height)
    unit = WORLD_METERS / (512 * 2 ** grid["zoom"])
    return -WORLD_METERS / 2 + source_x * unit, WORLD_METERS / 2 - source_y * unit


def _contour_segments(dem, interval, grid):
    """Yield (elevation, start, end) segments with coordinates in EPSG:3857 meters."""
    top_left, top_right = dem[:-1, :-1], dem[:-1, 1:]
    bottom_right, bottom_left = dem[1:, 1:], dem[1:, :-1]
    valid = np.isfinite(top_left) & np.isfinite(top_right) & np.isfinite(bottom_right) & np.isfinite(bottom_left)
    minimum = float(np.nanmin(dem))
    maximum = float(np.nanmax(dem))
    first = math.ceil(minimum / interval) * interval
    last = math.floor(maximum / interval) * interval
    width, height = dem.shape[1], dem.shape[0]
    for index, level in enumerate(np.arange(first, last + interval * 0.5, interval)):
        low_a, low_b, low_c, low_d = top_left < level, top_right < level, bottom_right < level, bottom_left < level
        top = low_a != low_b
        right = low_b != low_c
        bottom = low_d != low_c
        left = low_a != low_d
        candidates = valid & (top | right | bottom | left)
        rows, columns = np.nonzero(candidates)
        for row, column in zip(rows, columns):
            a, b = float(top_left[row, column]), float(top_right[row, column])
            c, d = float(bottom_right[row, column]), float(bottom_left[row, column])
            intersections = {}

            def interpolate(key, p0, p1, v0, v1):
                if (v0 < level) == (v1 < level) or v0 == v1:
                    return
                fraction = max(0.0, min(1.0, (level - v0) / (v1 - v0)))
                intersections[key] = (p0[0] + fraction * (p1[0] - p0[0]),
                                      p0[1] + fraction * (p1[1] - p0[1]))

            interpolate("top", (column, row), (column + 1, row), a, b)
            interpolate("right", (column + 1, row), (column + 1, row + 1), b, c)
            interpolate("bottom", (column, row + 1), (column + 1, row + 1), d, c)
            interpolate("left", (column, row), (column, row + 1), a, d)
            edges = list(intersections)
            if len(edges) == 2:
                pairs = [edges]
            elif len(edges) == 4:
                center_high = (a + b + c + d) / 4 >= level
                top_left_high = a >= level
                pairs = [("top", "right"), ("bottom", "left")] if center_high == top_left_high else [
                    ("top", "left"), ("right", "bottom")]
            else:
                continue
            for first_edge, second_edge in pairs:
                first_point = intersections[first_edge]
                second_point = intersections[second_edge]
                if math.dist(first_point, second_point) < 1e-8:
                    continue
                yield (float(level), _pixel_to_mercator(*first_point, grid, width, height),
                       _pixel_to_mercator(*second_point, grid, width, height), index % 5 == 0)


def _join_contour_segments(segments):
    """Join marching-squares edges into complete contour paths."""
    points, neighbors, edges = {}, {}, set()
    for start, end in segments:
        first = (round(start[0], 6), round(start[1], 6))
        second = (round(end[0], 6), round(end[1], 6))
        if first == second:
            continue
        points.setdefault(first, start)
        points.setdefault(second, end)
        neighbors.setdefault(first, set()).add(second)
        neighbors.setdefault(second, set()).add(first)
        edges.add(tuple(sorted((first, second))))

    visited = set()

    def walk(start, next_point):
        path = [start]
        previous, current = start, next_point
        while True:
            edge = tuple(sorted((previous, current)))
            if edge in visited:
                break
            visited.add(edge)
            path.append(current)
            if current == start or len(neighbors[current]) != 2:
                break
            following = next(point for point in neighbors[current] if point != previous)
            previous, current = current, following
        return [points[point] for point in path]

    paths = []
    for start, connected in neighbors.items():
        if len(connected) == 2:
            continue
        for next_point in connected:
            if tuple(sorted((start, next_point))) not in visited:
                paths.append(walk(start, next_point))
    for edge in edges:
        if edge not in visited:
            paths.append(walk(edge[0], edge[1]))
    return paths


def _contour_paths(dem, interval, grid):
    """Yield (elevation, points, index_contour) for each joined contour path."""
    level, segments, index = None, [], False
    for elevation, start, end, is_index in _contour_segments(dem, interval, grid):
        if level is not None and elevation != level:
            for path in _join_contour_segments(segments):
                yield level, path, index
            segments = []
        level, index = elevation, is_index
        segments.append((start, end))
    if level is not None:
        for path in _join_contour_segments(segments):
            yield level, path, index


def _clipped_paths(points, polygon):
    """Clip a joined contour to the selection, retaining each continuous piece."""
    current = []
    for start, end in zip(points, points[1:]):
        pieces = list(_clipped_segment(start, end, polygon))
        if not pieces:
            if len(current) > 1:
                yield current
            current = []
            continue
        for first, second in pieces:
            if current and math.dist(current[-1], first) < 0.001:
                current.append(second)
            else:
                if len(current) > 1:
                    yield current
                current = [first, second]
    if len(current) > 1:
        yield current


def _path_midpoint(points):
    lengths = [math.dist(start, end) for start, end in zip(points, points[1:])]
    half, total = sum(lengths) / 2, sum(lengths)
    if total <= 0:
        return points[0]
    for (start, end), length in zip(zip(points, points[1:]), lengths):
        if half <= length:
            fraction = half / max(length, 1e-12)
            return start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction
        half -= length
    return points[-1]


def _draw_elevation_label(draw, layout, elevation, point):
    label = f"{elevation:g} m"
    x, y = layout["pixel"](point)
    font = _font(13, True)
    box = draw.textbbox((0, 0), label, font=font)
    width, height = box[2] - box[0], box[3] - box[1]
    draw.rectangle((x - width / 2 - 3, y - height / 2 - 2, x + width / 2 + 3, y + height / 2 + 2),
                   fill=(249, 247, 238))
    draw.text((x - width / 2, y - height / 2 - box[1]), label, fill=(107, 74, 43), font=font)


def _map_base(features, polygon, title, subtitle, interval=None):
    layout = _layout(polygon)
    width, height = layout["width"], layout["height"]
    background = (241, 241, 233)
    layer = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(layer)
    # Fill OSM areas first, then lines. The selection mask clips the whole map to the drawn polygon.
    for item in features.get("features", []):
        geometry = item.get("geometry") or {}
        kind = item.get("properties", {}).get("kind")
        fill = {"water": (177, 214, 232), "land": (222, 232, 213), "building": (207, 195, 184)}.get(kind)
        if fill is None:
            continue
        for rings in _polygons(geometry):
            for index, ring in enumerate(rings):
                points = [layout["pixel"](_osm_point(point)) for point in ring]
                if len(points) >= 3:
                    draw.polygon(points, fill=fill if index == 0 else background)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).polygon(layout["polygon"], fill=255)
    page = Image.new("RGB", (width, height), "white")
    page.paste(layer, (0, 0), mask)
    layer.close()
    page_draw = ImageDraw.Draw(page)
    page_draw.line(layout["polygon"] + layout["polygon"][:1], fill=(68, 88, 71), width=3)
    page_draw.rectangle(layout["frame"], outline=(135, 143, 132), width=1)
    page_draw.text((70, 34), title, fill=(30, 44, 38), font=_font(30, True))
    page_draw.text((70, 70), subtitle, fill=(86, 96, 90), font=_font(16))
    # North arrow.
    right = layout["frame"][2] - 28
    page_draw.line((right, 150, right, 112), fill=(44, 56, 48), width=4)
    page_draw.polygon([(right, 102), (right - 9, 118), (right + 9, 118)], fill=(44, 56, 48))
    page_draw.text((right - 8, 154), "N", fill=(44, 56, 48), font=_font(17, True))
    if interval is not None:
        page_draw.line((70, height - 58, 210, height - 58), fill=(147, 91, 42), width=4)
        page_draw.text((70, height - 83), f"Contours every {interval:g} m | labels AMSL",
                       fill=(102, 76, 51), font=_font(16, True))
    else:
        page_draw.text((70, height - 83), "OpenStreetMap vector data", fill=(86, 96, 90), font=_font(16))
    bar_meters = _nice_step(140 / max(layout["scale"], 1e-12))
    bar_pixels = max(30, round(bar_meters * layout["scale"]))
    page_draw.line((70, height - 40, 70 + bar_pixels, height - 40), fill=(44, 56, 48), width=3)
    page_draw.line((70, height - 45, 70, height - 35), fill=(44, 56, 48), width=2)
    page_draw.line((70 + bar_pixels, height - 45, 70 + bar_pixels, height - 35), fill=(44, 56, 48), width=2)
    scale_label = f"{bar_meters:g} m" if bar_meters < 1000 else f"{bar_meters / 1000:g} km"
    page_draw.text((78 + bar_pixels, height - 49), scale_label, fill=(44, 56, 48), font=_font(13))
    page_draw.text((width - 720, height - 57), "Map data: OpenStreetMap contributors (ODbL)",
                   fill=(76, 82, 77), font=_font(14))
    if interval is not None:
        page_draw.text((width - 720, height - 36),
                       "Heights above sea level (m); AWS Terrain Tiles / github.com/tilezen/joerd",
                       fill=(76, 82, 77), font=_font(12))
    return page, ImageDraw.Draw(page), layout, mask


def _draw_roads(draw, layout, features, polygon):
    styles = {
        "major": ((255, 255, 255), (202, 135, 92), 6),
        "minor": ((255, 255, 255), (184, 179, 168), 3),
        "path": ((255, 255, 255), (151, 149, 138), 2),
    }
    for item in features.get("features", []):
        geometry = item.get("geometry") or {}
        properties = item.get("properties", {})
        kind = properties.get("kind")
        if kind == "road":
            casing, color, base_width = styles.get(properties.get("road"), styles["minor"])
        elif kind == "waterway":
            casing, color, base_width = (210, 231, 240), (125, 184, 211), 2
        else:
            continue
        width = max(1, min(10, round(layout["scale"] * base_width)))
        for line in _geometry_lines(geometry):
            points = [_osm_point(point) for point in line]
            for start, end in zip(points, points[1:]):
                for first, second in _clipped_segment(start, end, polygon):
                    pixels = (layout["pixel"](first), layout["pixel"](second))
                    draw.line(pixels, fill=casing, width=width + 2)
                    draw.line(pixels, fill=color, width=width)


def _save_pdf(image, path):
    with atomic_path(path) as temporary:
        printable = image.convert("RGB")
        printable.save(temporary, format="PDF", resolution=144)
        printable.close()
    image.close()


def _dxf_float(value):
    return f"{value:.3f}"


@contextmanager
def _dxf_writer(path, layers_table, comment):
    with atomic_path(path) as temporary, temporary.open("w", encoding="ascii", newline="\n") as stream:
        layers_table = {"0": (7, "CONTINUOUS", 25), **layers_table}
        linetypes = {"CONTINUOUS": ("Solid line", ()), "DASHED": ("Dashed __ __ __", (0.5, -0.25)),
                     "PHANTOM": ("Phantom ____ _ ____", (0.5, -0.125, 0.125, -0.125))}
        stream.write(f"999\n{comment}\n0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n"
                     "9\n$INSUNITS\n70\n6\n9\n$MEASUREMENT\n70\n1\n"
                     "0\nENDSEC\n0\nSECTION\n2\nTABLES\n"
                     f"0\nTABLE\n2\nLTYPE\n70\n{len(linetypes)}\n")
        for name, (description, pattern) in linetypes.items():
            total = sum(abs(length) for length in pattern)
            stream.write(f"0\nLTYPE\n2\n{name}\n70\n0\n3\n{description}\n72\n65\n73\n{len(pattern)}\n40\n{total:g}\n")
            for length in pattern:
                stream.write(f"49\n{length:g}\n")
        stream.write("0\nENDTAB\n" + f"0\nTABLE\n2\nLAYER\n70\n{len(layers_table)}\n")
        for name, (color, linetype, lineweight) in layers_table.items():
            stream.write(f"0\nLAYER\n2\n{name}\n70\n0\n62\n{color}\n6\n{linetype}\n370\n{lineweight}\n")
        stream.write("0\nENDTAB\n0\nTABLE\n2\nSTYLE\n70\n1\n0\nSTYLE\n2\nSTANDARD\n70\n0\n40\n0.0\n41\n1.0\n50\n0.0\n71\n0\n42\n2.5\n3\ntxt.shx\n4\n\n0\nENDTAB\n0\nENDSEC\n0\nSECTION\n2\nBLOCKS\n0\nENDSEC\n"
                     "0\nSECTION\n2\nENTITIES\n")
        yield stream
        stream.write("0\nENDSEC\n0\nEOF\n")


def _dxf_line(stream, layer_name, start, end, start_z=0.0, end_z=None):
    if end_z is None:
        end_z = start_z
    stream.write(f"0\nLINE\n8\n{layer_name}\n10\n{_dxf_float(start[0])}\n20\n{_dxf_float(start[1])}\n"
                 f"30\n{_dxf_float(start_z)}\n11\n{_dxf_float(end[0])}\n21\n{_dxf_float(end[1])}\n"
                 f"31\n{_dxf_float(end_z)}\n")


def _dxf_polyline(stream, layer_name, points, elevation):
    stream.write(f"0\nPOLYLINE\n8\n{layer_name}\n66\n1\n70\n8\n10\n0.0\n20\n0.0\n30\n0.0\n")
    for x, y in points:
        stream.write(f"0\nVERTEX\n8\n{layer_name}\n10\n{_dxf_float(x)}\n20\n{_dxf_float(y)}\n"
                     f"30\n{_dxf_float(elevation)}\n70\n32\n")
    stream.write(f"0\nSEQEND\n8\n{layer_name}\n")


def _dxf_lwpolyline(stream, layer_name, points):
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        return
    stream.write(f"0\nLWPOLYLINE\n100\nAcDbEntity\n8\n{layer_name}\n100\nAcDbPolyline\n90\n{len(points)}\n70\n1\n")
    for x, y in points:
        stream.write(f"10\n{_dxf_float(x)}\n20\n{_dxf_float(y)}\n")


def _dxf_text(stream, layer_name, text, point, elevation, height):
    stream.write(f"0\nTEXT\n8\n{layer_name}\n10\n{_dxf_float(point[0])}\n20\n{_dxf_float(point[1])}\n"
                 f"30\n{_dxf_float(elevation)}\n40\n{_dxf_float(height)}\n1\n{text}\n7\nSTANDARD\n"
                 f"72\n1\n73\n2\n11\n{_dxf_float(point[0])}\n21\n{_dxf_float(point[1])}\n"
                 f"31\n{_dxf_float(elevation)}\n")


def _dxf_face(stream, points, layer_name="SITE-MESH-CONTEXT"):
    stream.write(f"0\n3DFACE\n8\n{layer_name}\n")
    for code, point in zip((10, 11, 12, 13), (*points, points[-1])):
        x, y, z = point
        stream.write(f"{code}\n{_dxf_float(x)}\n{code + 10}\n{_dxf_float(y)}\n{code + 20}\n{_dxf_float(z)}\n")


def _accessor_offset(gltf, binary_offset, accessor):
    view = gltf["bufferViews"][accessor["bufferView"]]
    return binary_offset + view.get("byteOffset", 0) + accessor.get("byteOffset", 0), view.get("byteStride")


def _mesh_base(path):
    with Path(path).open("rb") as source:
        header = source.read(20)
        if len(header) < 20:
            raise ValueError("The saved 3D mesh is incomplete.")
        magic, version, total, chunk_size, chunk_type = struct.unpack_from("<IIIII", header)
        if magic != 0x46546C67 or version != 2 or total != Path(path).stat().st_size or chunk_type != 0x4E4F534A:
            raise ValueError("The saved 3D mesh is not a valid GLB file.")
        if chunk_size > total - 20:
            raise ValueError("The saved 3D mesh has a truncated GLB chunk.")
        gltf = json.loads(source.read(chunk_size).decode("utf-8").rstrip(" \t\r\n\0"))
        return float(gltf.get("asset", {}).get("extras", {}).get("base", 0))


def _write_mesh_dxf(path, mesh_path, site_origin):
    with mesh_path.open("rb") as source:
        size = mesh_path.stat().st_size
        if size < 20:
            raise ValueError("The saved 3D mesh is incomplete.")
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
            magic, version, total = struct.unpack_from("<III", data, 0)
            if magic != 0x46546C67 or version != 2 or total != size:
                raise ValueError("The saved 3D mesh is not a valid GLB file.")
            offset, gltf, binary_offset = 12, None, None
            while offset + 8 <= size:
                chunk_size, chunk_type = struct.unpack_from("<II", data, offset)
                chunk_start = offset + 8
                chunk_end = chunk_start + chunk_size
                if chunk_end > size:
                    raise ValueError("The saved 3D mesh has a truncated GLB chunk.")
                if chunk_type == 0x4E4F534A:
                    gltf = json.loads(data[chunk_start:chunk_end].decode("utf-8").rstrip(" \t\r\n\0"))
                elif chunk_type == 0x004E4942:
                    binary_offset = chunk_start
                offset = chunk_end
            if gltf is None or binary_offset is None:
                raise ValueError("The saved 3D mesh is missing its geometry data.")
            extras = gltf.get("asset", {}).get("extras", {})
            base = float(extras.get("base", 0))
            center = _mercator(float(extras.get("latitude", 0)), float(extras.get("longitude", 0)))
            local_center = _local_xy(center, site_origin)
            comment = f"Aleph local ENU mesh; site origin {site_origin[3]:.6f}, {site_origin[4]:.6f}; meters"
            with _dxf_writer(path, {"SITE-MESH-CONTEXT": CAD_LAYERS["SITE-MESH-CONTEXT"]}, comment) as output:
                for mesh in gltf.get("meshes", []):
                    for primitive in mesh.get("primitives", []):
                        position_index = primitive.get("attributes", {}).get("POSITION")
                        if position_index is None:
                            continue
                        position_accessor = gltf["accessors"][position_index]
                        if position_accessor.get("componentType") != 5126 or position_accessor.get("type") != "VEC3":
                            raise ValueError("The 3D mesh has an unsupported vertex format.")
                        positions_offset, stride = _accessor_offset(gltf, binary_offset, position_accessor)
                        count = int(position_accessor["count"])
                        positions = np.ndarray((count, 3), dtype="<f4", buffer=data, offset=positions_offset,
                                               strides=(stride or 12, 4))
                        if "indices" in primitive:
                            index_accessor = gltf["accessors"][primitive["indices"]]
                            dtype = {5123: "<u2", 5125: "<u4"}.get(index_accessor.get("componentType"))
                            if not dtype or index_accessor.get("type") != "SCALAR":
                                raise ValueError("The 3D mesh has an unsupported index format.")
                            indices_offset, _ = _accessor_offset(gltf, binary_offset, index_accessor)
                            indices = np.frombuffer(data, dtype=dtype, count=index_accessor["count"], offset=indices_offset)
                        else:
                            indices = np.arange(count, dtype=np.uint32)
                        if len(indices) % 3:
                            raise ValueError("The 3D mesh has an incomplete triangle.")
                        for triangle in indices.reshape(-1, 3):
                            face = []
                            for vertex_index in triangle:
                                if vertex_index >= count:
                                    raise ValueError("The 3D mesh contains an invalid vertex index.")
                                x, up, south = map(float, positions[int(vertex_index)])
                                face.append((x + local_center[0], -south + local_center[1], up + base - site_origin[2]))
                            _dxf_face(output, face)
                        if len(indices):
                            del triangle
                        del positions, indices


def _write_osm_dxf(stream, features, polygon, origin):
    for item in features.get("features", []):
        geometry = item.get("geometry") or {}
        source_layer = _feature_layer(item.get("properties", {}))
        if not source_layer:
            continue
        layer_name = FEATURE_LAYERS[source_layer]
        for line in _geometry_lines(geometry):
            points = [_osm_point(point) for point in line]
            if (geometry.get("type") in ("Polygon", "MultiPolygon") and len(points) > 3
                    and points[0] == points[-1] and all(_inside(point, polygon) for point in points)):
                _dxf_lwpolyline(stream, layer_name, [_local_xy(point, origin) for point in points])
                continue
            for start, end in zip(points, points[1:]):
                for first, second in _clipped_segment(start, end, polygon):
                    _dxf_line(stream, layer_name, _local_xy(first, origin), _local_xy(second, origin))


def _write_topographic_dxf(path, features, polygon, dem, interval, grid, origin):
    with _dxf_writer(path, CAD_LAYERS, f"Topographic map; local meters; contours every {interval:g} m AMSL") as output:
        for start, end in zip(polygon, polygon[1:] + polygon[:1]):
            _dxf_line(output, "SITE-BNDY-PROP", _local_xy(start, origin), _local_xy(end, origin))
        _write_osm_dxf(output, features, polygon, origin)
        label_height = 13 / max(_layout(polygon)["scale"], 1e-12) * math.cos(math.radians(origin[3]))
        for elevation, points, index in _contour_paths(dem, interval, grid):
            layer_name = "SITE-TOPO-MAJR" if index else "SITE-TOPO-MINR"
            for clipped in _clipped_paths(points, polygon):
                local = [_local_xy(point, origin) for point in clipped]
                _dxf_polyline(output, layer_name, local, elevation - origin[2])
                midpoint = _path_midpoint(clipped)
                _dxf_text(output, "SITE-TOPO-ANNO", f"{elevation:g} m AMSL", _local_xy(midpoint, origin),
                          elevation - origin[2], label_height)


def _write_georeference(folder, origin):
    x, y, elevation, latitude, longitude = origin
    write_json(folder / "georeference.json", {
        "crs": {"projected_epsg": 3857, "projected_name": "WGS 84 / Pseudo-Mercator",
                "geographic_epsg": 4326, "geographic_name": "WGS 84"},
        "local_origin": {"local_x": 0.0, "local_y": 0.0, "local_z": 0.0,
                         "projected_easting": x, "projected_northing": y, "amsl_elevation": elevation,
                         "latitude_wgs84": latitude, "longitude_wgs84": longitude},
        "cartography": {"grid_convergence_angle_deg": 0.0,
                        "projected_to_local_scale_factor": math.cos(math.radians(latitude))},
    })
    wkt = ('PROJCS["WGS 84 / Pseudo-Mercator",GEOGCS["WGS 84",DATUM["WGS_1984",'
           'SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
           'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
           'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]],'
           'PROJECTION["Mercator_1SP"],PARAMETER["central_meridian",0],PARAMETER["scale_factor",1],'
           'PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
           'UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG","3857"]]')
    with atomic_path(folder / "model.prj") as temporary:
        temporary.write_text(wkt + "\n", encoding="utf-8")


def _save_osm_pdf(path, features, polygon):
    page, _, layout, mask = _map_base(features, polygon, "OpenStreetMap", "Selected area | north-up")
    _draw_roads(ImageDraw.Draw(page), layout, features, polygon)
    ImageDraw.Draw(page).line(layout["polygon"] + layout["polygon"][:1], fill=(68, 88, 71), width=3)
    _save_pdf(page, path)
    mask.close()


def _save_topographic_pdf(path, features, polygon, dem, interval, grid):
    page, _, layout, mask = _map_base(features, polygon, "Topographic map", "OpenStreetMap with elevation contours",
                                     interval=interval)
    contour_layer = page.copy()
    contour_draw = ImageDraw.Draw(contour_layer)
    for elevation, points, index in _contour_paths(dem, interval, grid):
        for clipped in _clipped_paths(points, polygon):
            contour_draw.line([layout["pixel"](point) for point in clipped],
                              fill=(146, 104, 61) if index else (185, 144, 95), width=2 if index else 1)
            _draw_elevation_label(contour_draw, layout, elevation, _path_midpoint(clipped))
    page.paste(contour_layer, (0, 0), mask)
    _draw_roads(ImageDraw.Draw(page), layout, features, polygon)
    ImageDraw.Draw(page).line(layout["polygon"] + layout["polygon"][:1], fill=(68, 88, 71), width=3)
    _save_pdf(page, path)
    mask.close()
    contour_layer.close()


def build(folder, run, progress=None, *, rebuild=True):
    """Create printable and CAD files from completed source layers, leaving source files untouched."""
    progress = progress or (lambda *args: None)
    folder = Path(folder)
    polygon = _area_polygon(run)
    map_path = folder / "map.osm"
    features = layers.osm(map_path) if map_path.is_file() else {"type": "FeatureCollection", "features": []}
    map_pdf = folder / MAP_PDF
    if map_path.is_file() and (rebuild or not map_pdf.is_file()):
        progress("Building OSM map PDF")
        _save_osm_pdf(map_pdf, features, polygon)

    terrain_stage = next((stage for stage in run.get("stages", []) if stage.get("mode") == "terrain"), None)
    dem = None
    origin = _site_origin(polygon)
    if terrain_stage and len(terrain_stage.get("results", [])) == terrain_stage["grid"]["count"]:
        topo_pdf, topo_dxf = folder / TOPO_PDF, folder / TOPO_DXF
        progress("Preparing topographic contours")
        dem, interval = _sample_terrain(folder, terrain_stage)
        origin = _site_origin(polygon, dem, terrain_stage["grid"])
        if rebuild or not topo_pdf.is_file():
            progress("Building topographic PDF")
            _save_topographic_pdf(topo_pdf, features, polygon, dem, interval, terrain_stage["grid"])
        if rebuild or not topo_dxf.is_file():
            progress("Building topographic DXF")
            _write_topographic_dxf(topo_dxf, features, polygon, dem, interval, terrain_stage["grid"], origin)

    mesh_path = folder / "mesh.glb"
    if dem is None and mesh_path.is_file():
        origin = (*origin[:2], _mesh_base(mesh_path), *origin[3:])
    mesh_dxf = folder / MESH_DXF
    if mesh_path.is_file() and (rebuild or not mesh_dxf.is_file()):
        progress("Building 3D DXF")
        _write_mesh_dxf(mesh_dxf, mesh_path, origin)
    _write_georeference(folder, origin)

