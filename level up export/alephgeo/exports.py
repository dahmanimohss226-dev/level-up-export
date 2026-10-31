"""Small, printable map exports and CAD deliverables for a capture."""

from contextlib import contextmanager
import json
import math
import mmap
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import layers
from .common import atomic_path
from .geo import MERCATOR_RADIUS

MAP_PDF = "map-osm.pdf"
TOPO_PDF = "topographic.pdf"
MESH_DWG = "mesh-3d.dwg"
TOPO_DWG = "topographic.dwg"
PDF_SIZE = (1684, 1191)  # A4 landscape at 144 dpi.
CONTOUR_PIXELS = 1200
NODATA = -1_000_000
MERCATOR_LIMIT = 85.0511287798066
WORLD_METERS = 2 * math.pi * MERCATOR_RADIUS

LAYER_COLORS = {
    "BOUNDARY": 1,
    "BUILDING": 30,
    "CONTOUR": 32,
    "ELEVATION_LABEL": 7,
    "INDEX_CONTOUR": 30,
    "LAND": 3,
    "PATH": 8,
    "ROAD_MAJOR": 7,
    "ROAD_MINOR": 8,
    "WATER": 5,
}


def _mercator(lat, lon):
    lat = math.radians(max(-MERCATOR_LIMIT, min(MERCATOR_LIMIT, lat)))
    return MERCATOR_RADIUS * math.radians(lon), MERCATOR_RADIUS * math.log(math.tan(math.pi / 4 + lat / 2))


def _osm_point(point):
    return _mercator(float(point[1]), float(point[0]))


def _area_polygon(run):
    return [_mercator(float(lat), float(lon)) for lat, lon in run["polygon"]]


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
        layers_table = {"0": 7, **layers_table}
        stream.write(f"999\n{comment}\n0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n"
                     "0\nENDSEC\n0\nSECTION\n2\nTABLES\n"
                     "0\nTABLE\n2\nLTYPE\n70\n1\n0\nLTYPE\n2\nCONTINUOUS\n70\n0\n3\nSolid line\n72\n65\n73\n0\n40\n0.0\n0\nENDTAB\n"
                     f"0\nTABLE\n2\nLAYER\n70\n{len(layers_table)}\n")
        for name, color in layers_table.items():
            stream.write(f"0\nLAYER\n2\n{name}\n70\n0\n62\n{color}\n6\nCONTINUOUS\n")
        stream.write("0\nENDTAB\n0\nENDSEC\n0\nSECTION\n2\nBLOCKS\n0\nENDSEC\n"
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


def _dxf_text(stream, layer_name, text, point, elevation, height):
    stream.write(f"0\nTEXT\n8\n{layer_name}\n10\n{_dxf_float(point[0])}\n20\n{_dxf_float(point[1])}\n"
                 f"30\n{_dxf_float(elevation)}\n40\n{_dxf_float(height)}\n1\n{text}\n7\nSTANDARD\n"
                 f"72\n1\n73\n2\n11\n{_dxf_float(point[0])}\n21\n{_dxf_float(point[1])}\n"
                 f"31\n{_dxf_float(elevation)}\n")


def _dxf_face(stream, points):
    stream.write("0\n3DFACE\n8\nMESH\n")
    for code, point in zip((10, 11, 12, 13), (*points, points[-1])):
        x, y, z = point
        stream.write(f"{code}\n{_dxf_float(x)}\n{code + 10}\n{_dxf_float(y)}\n{code + 20}\n{_dxf_float(z)}\n")


def _accessor_offset(gltf, binary_offset, accessor):
    view = gltf["bufferViews"][accessor["bufferView"]]
    return binary_offset + view.get("byteOffset", 0) + accessor.get("byteOffset", 0), view.get("byteStride")


def _write_mesh_dxf(path, mesh_path):
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
            origin = f"Aleph local ENU mesh; center {extras.get('latitude', 0):.6f}, {extras.get('longitude', 0):.6f}; meters"
            with _dxf_writer(path, {"MESH": 8}, origin) as output:
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
                                face.append((x, -south, up + base))
                            _dxf_face(output, face)
                        if len(indices):
                            del triangle
                        del positions, indices


def _write_osm_dxf(stream, features, polygon):
    for item in features.get("features", []):
        geometry = item.get("geometry") or {}
        layer_name = _feature_layer(item.get("properties", {}))
        if not layer_name:
            continue
        for line in _geometry_lines(geometry):
            points = [_osm_point(point) for point in line]
            for start, end in zip(points, points[1:]):
                for first, second in _clipped_segment(start, end, polygon):
                    _dxf_line(stream, layer_name, first, second)


def _write_topographic_dxf(path, features, polygon, dem, interval, grid):
    with _dxf_writer(path, LAYER_COLORS, f"Topographic map; EPSG:3857 meters; contours every {interval:g} m AMSL") as output:
        for start, end in zip(polygon, polygon[1:] + polygon[:1]):
            _dxf_line(output, "BOUNDARY", start, end)
        _write_osm_dxf(output, features, polygon)
        label_height = 13 / max(_layout(polygon)["scale"], 1e-12)
        for elevation, points, index in _contour_paths(dem, interval, grid):
            layer_name = "INDEX_CONTOUR" if index else "CONTOUR"
            for clipped in _clipped_paths(points, polygon):
                _dxf_polyline(output, layer_name, clipped, elevation)
                midpoint = _path_midpoint(clipped)
                _dxf_text(output, "ELEVATION_LABEL", f"{elevation:g} m AMSL", midpoint, elevation, label_height)


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


def _autocad_core_console():
    """Find AutoCAD's native DWG writer on Windows."""
    configured = os.environ.get("ALEPH_ACCORECONSOLE")
    candidates = [Path(configured)] if configured else []
    path_executable = shutil.which("accoreconsole.exe") or shutil.which("accoreconsole")
    if path_executable:
        candidates.append(Path(path_executable))

    if sys.platform == "win32":
        try:
            import winreg

            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Autodesk\AutoCAD",
                                          0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            release_name = winreg.EnumKey(root, index)
                            release = winreg.OpenKey(root, release_name)
                        except OSError:
                            continue
                        with release:
                            for product_index in range(winreg.QueryInfoKey(release)[0]):
                                try:
                                    product_name = winreg.EnumKey(release, product_index)
                                    product = winreg.OpenKey(release, product_name)
                                except OSError:
                                    continue
                                with product:
                                    for value_name in ("GlobUPILocation", "AcadLocation", "InstallLocation"):
                                        try:
                                            value, _ = winreg.QueryValueEx(product, value_name)
                                        except OSError:
                                            continue
                                        candidates.append(Path(value) / "accoreconsole.exe")
        except (ImportError, OSError):
            pass

    for program_files in (os.environ.get("ProgramW6432"), os.environ.get("PROGRAMFILES")):
        if program_files:
            candidates.extend(Path(program_files).glob("Autodesk/AutoCAD */accoreconsole.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("DWG export requires AutoCAD for Windows. Set ALEPH_ACCORECONSOLE to accoreconsole.exe.")


def _convert_dxf_to_dwg(path, write_dxf):
    """Create a temporary DXF and use AutoCAD Core Console to write a real DWG."""
    executable = _autocad_core_console()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".aleph-dwg-", dir=path.parent) as temporary:
        work = Path(temporary)
        source = work / "source.dxf"
        target = work / "output.dwg"
        script = work / "convert.scr"
        write_dxf(source)
        script.write_text("_.SETVAR\nFILEDIA\n0\n_.SETVAR\nCMDECHO\n0\n_.SETVAR\nINSUNITS\n6\n"
                          "_.SAVEAS\n2018\noutput.dwg\n_.QUIT\n", encoding="ascii")
        try:
            result = subprocess.run(
                [str(executable), "/i", str(source.resolve()), "/s", str(script.resolve())],
                cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("AutoCAD DWG conversion timed out. Reduce the 3D mesh area and try again.") from error
        if result.returncode or not target.is_file() or target.stat().st_size < 64:
            details = (result.stdout + "\n" + result.stderr).strip()[-1200:]
            raise RuntimeError(f"AutoCAD could not create {path.name}. {details}".strip())
        target.replace(path)


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
    if terrain_stage and len(terrain_stage.get("results", [])) == terrain_stage["grid"]["count"]:
        topo_pdf, topo_dwg = folder / TOPO_PDF, folder / TOPO_DWG
        if rebuild or not topo_pdf.is_file() or not topo_dwg.is_file():
            progress("Preparing topographic contours")
            dem, interval = _sample_terrain(folder, terrain_stage)
            if rebuild or not topo_pdf.is_file():
                progress("Building topographic PDF")
                _save_topographic_pdf(topo_pdf, features, polygon, dem, interval, terrain_stage["grid"])
            if rebuild or not topo_dwg.is_file():
                progress("Building topographic DWG")
                _convert_dxf_to_dwg(topo_dwg, lambda path: _write_topographic_dxf(
                    path, features, polygon, dem, interval, terrain_stage["grid"]))

    mesh_path = folder / "mesh.glb"
    mesh_dwg = folder / MESH_DWG
    if mesh_path.is_file() and (rebuild or not mesh_dwg.is_file()):
        progress("Building 3D DWG")
        _convert_dxf_to_dwg(mesh_dwg, lambda path: _write_mesh_dxf(path, mesh_path))

