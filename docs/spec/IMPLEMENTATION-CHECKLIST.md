# Implementation Checklist & Gap Analysis

This document maps all six technical specifications against the existing codebase, tracks implementation status across three standardized stages, records identified architectural gaps, and defines the immediate next steps for each module.

---

## Specification Status Matrix

| Spec # | Feature Area | Reviewed | Code Mapped | Fully Implemented | Current Status |
|---|---|:---:|:---:|:---:|---|
| **01** | [Desktop Shell & Process Supervisor](01-architecture-desktop-shell.md) | [x] | [x] | [ ] | **In Progress (Slice 1 complete)** |
| **02** | [CAD Engine & Deliverables](02-cad-engine-deliverables.md) | [x] | [x] | [ ] | **Planned (Sprint 1)** |
| **03** | [3D Mesh Optimization & Texture Baking](03-3d-mesh-optimization-texture-baking.md) | [x] | [x] | [ ] | **Planned (Sprint 3)** |
| **04** | [Terrain, Topography & Unreal Heightmaps](04-terrain-topography-heightmaps.md) | [x] | [x] | [ ] | **Planned (Sprint 3)** |
| **05** | [Data Ingestion & Georeferencing](05-data-providers-georeferencing.md) | [x] | [x] | [ ] | **Planned (Sprint 2)** |
| **06** | [Frontend UI & 3D Interactive Viewport](06-frontend-ui-viewport.md) | [x] | [x] | [ ] | **Planned (Sprint 2)** |

---

## Detailed Gap Analysis & Action Plans

### Spec 01: Desktop Shell & Process Supervisor
- [x] **Reviewed**
- [x] **Current Code Mapped**
- [ ] **Fully Implemented**

* **Current Code Mapping:**
  - [`packaging/windows/setup_app.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/packaging/windows/setup_app.py): Spawns backend process, polls port, and creates `pywebview` window.
  - [`packaging/windows/installer.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/packaging/windows/installer.py): Inno/NSIS installer wrapper.
  - [`alephgeo/dashboard.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/dashboard.py): Local `http.server.BaseHTTPRequestHandler` service.
* **Gaps Identified:**
  - Currently relies on `pywebview` over a local socket connection without health verification or version negotiation.
  - No Tauri v2 shell configured (`src-tauri/` does not exist yet).
  - Backend is raw Python standard library rather than a typed FastAPI service.
  - Bundling Python as a Tauri sidecar requires configuring `bundle.externalBin` and setting up `tauri-plugin-shell` permissions per Tauri's sidecar specifications.
* **First Implementation Slice Completed:**
  - Added `/health` and `/api/health` endpoints to `alephgeo/dashboard.py` returning `{"status": "ready", "version": "0.2.0"}`.
  - Updated `packaging/windows/setup_app.py` to poll `/api/health` and verify the expected app version and status before launching the WebView window.
* **Next Implementation Steps:**
  1. Scaffold `src-tauri/` project with Tauri v2 CLI.
  2. Configure `tauri.conf.json` with external sidecar binaries and shell execution permissions.
  3. Implement Rust supervisor for graceful child process lifecycle and signal handling.

---

### Spec 02: Standalone CAD Engine & Deliverables
- [x] **Reviewed**
- [x] **Current Code Mapped**
- [ ] **Fully Implemented**

* **Current Code Mapping:**
  - [`alephgeo/exports.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/exports.py): Contains `_convert_dxf_to_dwg()`, `_autocad_core_console()`, and basic DXF line writer.
* **Gaps Identified:**
  - Hard dependency on Windows registry lookup for Autodesk AutoCAD `accoreconsole.exe`.
  - DXF is written via raw manual text string concatenation rather than a robust, validated CAD model object.
  - Lacks AIA/NCS-compliant layer hierarchies (`SITE-TOPO-MAJR`, `SITE-TOPO-MINR`, `SITE-BLDG-OTLN`).
  - Contours lack 3D polylines with accurate $Z$ coordinates and curve-following text labels.
* **Next Implementation Steps:**
  1. Add `ezdxf` to package dependencies in `pyproject.toml`.
  2. Implement `alephgeo/services/cad/dxf_builder.py` using `ezdxf.new("R2018")`.
  3. Replace `accoreconsole` dependency with standalone DXF output + optional ODA File Converter integration.

---

### Spec 03: 3D Mesh Optimization & Texture Atlas Baking
- [x] **Reviewed**
- [x] **Current Code Mapped**
- [ ] **Fully Implemented**

* **Current Code Mapping:**
  - [`alephgeo/mesh.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/mesh.py): Parses Google Earth octree protobuf stream and writes individual glTF binary chunks.
* **Gaps Identified:**
  - Raw tiles leave hundreds of individual $256 \times 256$ texture maps, generating excessive draw calls in Blender/Unreal.
  - No mesh decimation (LOD) support; models are excessively dense for real-time visualization.
  - Perimeter boundaries are open and ragged; lacks watertight architectural base skirts.
* **Next Implementation Steps:**
  1. Integrate `trimesh` for Quadric Error Metrics (QEM) decimation.
  2. Build MaxRects 2D texture atlas packer with vertex UV remapping into a unified $4\text{K}$ PBR map.
  3. Add perimeter boundary edge detection and vertical skirt extrusion down to base elevation.

---

### Spec 04: Terrain, Topography & Unreal Heightmaps
- [x] **Reviewed**
- [x] **Current Code Mapped**
- [ ] **Fully Implemented**

* **Current Code Mapping:**
  - [`alephgeo/terrain.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/terrain.py): Downloads Terrarium elevation tiles and stitches GeoTIFF.
  - [`alephgeo/exports.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/exports.py): Extracts 2D contour paths using custom numpy slicing.
* **Gaps Identified:**
  - No 16-bit unsigned integer grayscale PNG or RAW (`.r16`) heightmap export for game engines.
  - No Unreal Engine 5 scale calibration configuration sidecar (`unreal_landscape_config.json`).
  - No slope gradient analysis or color-coded civil feasibility heatmaps.
* **Next Implementation Steps:**
  1. Implement `alephgeo/services/terrain/heightmap.py` with 16-bit normalization formula:
     $$\text{Pixel}_{16} = 65535 \times \frac{Z - Z_{\min}}{Z_{\max} - Z_{\min}}$$
  2. Implement Sobel-based slope calculation in degrees and municipal grading brackets.
  3. Generate Unreal Engine 5 scale calibration sidecar metadata.

---

### Spec 05: Data Ingestion & Georeferencing Pipeline
- [x] **Reviewed**
- [x] **Current Code Mapped**
- [ ] **Fully Implemented**

* **Current Code Mapping:**
  - [`alephgeo/geo.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/geo.py): Spherical geometry, Web Mercator projections, and tile grid math.
  - [`alephgeo/osm.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/osm.py) & [`pbf.py`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/pbf.py): Overpass / Geofabrik PBF parser.
* **Gaps Identified:**
  - Coordinates are stored as raw Web Mercator or rough meters from center, causing floating-point jitter in 32-bit CAD/3D software.
  - No standard ESRI Well-Known Text (WKT) `.prj` file generation.
  - Lacks BYOK (Bring Your Own Key) support for official Google Photorealistic 3D Tiles and Mapbox.
* **Next Implementation Steps:**
  1. Implement `pyproj` datum transformation with Local Site Origin $(0, 0, 0)$ anchor.
  2. Generate `georeference.json` and standard ESRI `.prj` sidecar files on every export.
  3. Add provider routing for official OGC 3D Tiles alongside fallback scrapers.

---

### Spec 06: Frontend UI & 3D Interactive Viewport
- [x] **Reviewed**
- [x] **Current Code Mapped**
- [ ] **Fully Implemented**

* **Current Code Mapping:**
  - [`alephgeo/web/index.html`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/web/index.html): HTML shell.
  - [`alephgeo/web/app.js`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/web/app.js): 1,522-line monolithic vanilla JavaScript file.
  - [`alephgeo/web/style.css`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/web/style.css): Custom stylesheet.
* **Gaps Identified:**
  - Monolithic single-file architecture with zero modularity or type safety.
  - No export preset selector (AEC vs 3D Game vs GIS).
  - No solar position / shadow casting simulation.
  - Direct DOM mutation and polling rather than reactive state and WebSocket streaming.
* **Next Implementation Steps:**
  1. Scaffold `frontend/` workspace with Vite + React 19 + TypeScript + Tailwind CSS.
  2. Implement `<MapViewport />` with MapLibre GL and `<Viewport3D />` with Three.js.
  3. Build 1-click Preset Selector and Solar Shadow Rig.
