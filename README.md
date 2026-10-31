# Level Up Studio Aleph (level-up-export)

<p align="center">
  <img src="level up export/resources/level-up-studio-aleph.png" alt="Level Up Studio Aleph" width="260">
</p>

Geospatial context capture and CAD/3D site generation platform. Capture buildings, terrain elevation, satellite imagery, Street View panoramas, and textured 3D meshes for architects, civil engineers, urban planners, and game developers.

---

## Production Feature Specifications & Roadmap

Detailed technical specifications and architecture blueprints are located in [`level up export/docs/spec/`](level%20up%20export/docs/spec/):

- [**00 - Master Architecture & Specification Index**](level%20up%20export/docs/spec/00-overview.md)
- [**01 - Desktop Shell & Process Supervisor**](level%20up%20export/docs/spec/01-architecture-desktop-shell.md) (Tauri v2 + React/TypeScript + FastAPI Python)
- [**02 - Standalone CAD Engine & Deliverables**](level%20up%20export/docs/spec/02-cad-engine-deliverables.md) (Zero-AutoCAD DXF R2018, 3D contours, building footprints)
- [**03 - 3D Mesh Optimization & Texture Atlas Baking**](level%20up%20export/docs/spec/03-3d-mesh-optimization-texture-baking.md) (QEM decimation, 4K PBR texture atlas, glTF/FBX)
- [**04 - Terrain, Topography & Unreal Heightmaps**](level%20up%20export/docs/spec/04-terrain-topography-heightmaps.md) (16-bit Unreal Engine heightmaps, slope heatmaps)
- [**05 - Data Ingestion & Georeferencing Pipeline**](level%20up%20export/docs/spec/05-data-providers-georeferencing.md) (Hybrid Open Data + BYOK official APIs, Local $(0,0,0)$ datum)
- [**06 - Frontend UI & 3D Interactive Viewport**](level%20up%20export/docs/spec/06-frontend-ui-viewport.md) (MapLibre GL 2D + Three.js 3D inspection, solar shadow rig)
