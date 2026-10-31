# Specification 06: Frontend UI & 3D Interactive Viewport

**Module:** User Interface & Viewport Experience  
**Core Technologies:** Vite, React 19, TypeScript, Tailwind CSS, shadcn/ui, MapLibre GL JS, Three.js

---

## 1. Overview & Objectives

The legacy frontend consisted of a 1,522-line monolithic JavaScript file ([`app.js`](file:///c:/Users/MOZ/Desktop/Level-Up-Studio-Aleph/Level-Up-Studio-Aleph/alephgeo/web/app.js)) with direct DOM queries (`$`, `$$`), un-typed event listeners, and global state in `localStorage`.

This specification defines a modern **React 19 + TypeScript** component architecture featuring:
1. **Dual 2D/3D Viewport:** Instant toggling between 2D selection (MapLibre GL) and 3D site inspection (Three.js).
2. **Export Preset Workflows:** One-click presets tailored to Architects, 3D Artists, and GIS Analysts.
3. **Solar & Shadow Simulation:** Real-time sun angle and shadow casting for site feasibility analysis.
4. **Interactive Measurement Tools:** 3D distance, elevation difference, and surface area calculators directly on the live mesh.

---

## 2. Component Hierarchy

```
<AppLayout>
├── <TopNavigationBar>
│   ├── <SearchBar> (Photon / Mapbox geocoder)
│   ├── <ViewportToggle> (2D Map vs. 3D Inspection)
│   └── <ProjectSettingsModal> (API keys, cache directories, CRS preferences)
│
├── <MainViewport>
│   ├── <MapLibreCanvas> (2D Vector Basemap & Drawing Controls)
│   │   ├── <PolygonDrawer> (Rectangle & freeform polygon vertices)
│   │   └── <AreaStatsOverlay> (Surface area in m² / hectares, dimensions)
│   │
│   └── <ThreeJSCanvas> (3D Photogrammetry & Mesh Inspector)
│       ├── <MeshRenderer> (PBR / Wireframe / Clay render modes)
│       ├── <SolarShadowRig> (Sun position based on lat/lon, date, and hour)
│       └── <MeasurementGizmo> (Point-to-point 3D measurement)
│
└── <SidebarControlPanel>
    ├── <PresetSelector> (AEC Site vs. 3D Game vs. Full GIS)
    ├── <LayerConfigAccordion>
    │   ├── <MeshSettings> (LOD Decimation: 25k to 250k tris, 4K Atlas toggle)
    │   ├── <CadSettings> (Contour interval: 0.5m / 1m / 5m, Building massing)
    │   └── <TerrainSettings> (16-bit Unreal heightmap toggle)
    ├── <DownloadEstimateCard> (Node counts, file sizes, estimated seconds)
    ├── <ActionButtons> (Start Capture / Cancel / Export)
    └── <LiveJobTracker> (Multi-stage animated progress bar)
```

---

## 3. Export Preset Matrix

To remove cognitive load, the UI offers three standardized presets while maintaining a "Custom" configuration mode:

| Setting / Feature | AEC / Site Plan Preset | 3D Game / VFX Preset | GIS Analyst Preset |
|---|---|---|---|
| **Primary CAD File** | Layered DXF R2018 (3D Polylines) | None | None |
| **3D Mesh Format** | None / Low Massing OBJ | glTF 2.0 Binary (`.glb`) + FBX | None |
| **Mesh Decimation** | Not applicable | Optimized (75k–250k tris) | Not applicable |
| **Texture Atlas** | None | 4K Unified PBR Atlas | None |
| **Contour Map** | Topographic PDF (0.5m/1m intervals) | None | GeoPackage Contours |
| **Terrain Elevation** | Spot Elevations + CAD Contours | 16-bit Grayscale PNG (`.r16`) | Cloud-Optimized GeoTIFF |
| **OSM Vectors** | DXF Buildings, Roads, Paths | None | GeoJSON / Shapefile |
| **Unreal Config File** | None | `unreal_landscape_config.json` | None |
| **Coordinate System** | Local Origin $(0, 0, 0)$ + PRJ | Local Origin $(0, 0, 0)$ | Projected UTM / EPSG |

---

## 4. Real-Time Solar & Shadow Simulator

An interactive sun slider allows architects to evaluate site solar access directly in the 3D inspection viewport:

```typescript
import * as THREE from 'three';

export function calculateSunPosition(latitude: number, longitude: number, date: Date): THREE.Vector3 {
    // Astronomical solar position calculation (Declination & Hour Angle)
    const dayOfYear = Math.floor((date.getTime() - new Date(date.getFullYear(), 0, 0).getTime()) / 86400000);
    const timeMinutes = date.getHours() * 60 + date.getMinutes();
    
    // Solar declination in radians
    const declination = 23.45 * Math.sin((360 / 365) * (dayOfYear - 81) * (Math.PI / 180)) * (Math.PI / 180);
    
    // Solar hour angle
    const solarTime = timeMinutes / 60 + (longitude / 15);
    const hourAngle = (solarTime - 12) * 15 * (Math.PI / 180);
    const latRad = latitude * (Math.PI / 180);
    
    // Solar altitude (elevation angle above horizon)
    const altitude = Math.asin(
        Math.sin(latRad) * Math.sin(declination) +
        Math.cos(latRad) * Math.cos(declination) * Math.cos(hourAngle)
    );
    
    // Solar azimuth (clockwise from North)
    const azimuth = Math.atan2(
        -Math.sin(hourAngle),
        Math.tan(declination) * Math.cos(latRad) - Math.sin(latRad) * Math.cos(hourAngle)
    );
    
    // Convert spherical coordinates to Three.js directional light vector
    const radius = 1000.0;
    const x = radius * Math.cos(altitude) * Math.sin(azimuth);
    const y = radius * Math.sin(altitude); // Up axis
    const z = -radius * Math.cos(altitude) * Math.cos(azimuth);
    
    return new THREE.Vector3(x, y, z);
}
```

---

## 5. State Management & Real-Time Event Handling

- **Zustand Store (`useCaptureStore`):** Manages active polygon coordinates, active layer toggles, selected preset, and cached runs.
- **WebSocket Event Listener (`useCaptureEvents`):**
  - Listens to backend progress ticks:
    ```typescript
    interface CaptureEvent {
      job_id: number;
      stage: 'mesh' | 'terrain' | 'osm' | 'cad' | 'atlas';
      progress: number; // 0 to 100
      message: string;
      eta_seconds: number;
    }
    ```
  - Directly updates smooth, animated progress indicators on the UI without polling the server.

