# Specification 02: Standalone CAD Engine & Deliverables

**Module:** CAD Generation & Vector Exports  
**Core Technologies:** `ezdxf` (DXF R2018), `shapely`, `trimesh`, Optional ODA File Converter

---

## 1. Overview & Objectives

In the legacy implementation, generating `.dwg` files required a full, licensed copy of Autodesk AutoCAD installed on the user's Windows PC. The script scraped Windows Registry (`winreg`) for `accoreconsole.exe` and injected temporary script files (`convert.scr`). If AutoCAD was absent, CAD export failed completely.

This specification establishes a **100% standalone, zero-AutoCAD CAD Engine** that:
1. Natively writes industry-standard **AutoCAD R2018 DXF** files containing true 3D polylines, layers, line weights, and text annotations.
2. Follows **AIA (American Institute of Architects) & NCS (National CAD Standard)** layer naming conventions for instant, seamless integration into AutoCAD, Civil 3D, Revit, Rhino, Archicad, and SketchUp.
3. Automatically embeds 3D elevation contours with true $Z$ heights, allowing civil engineers and architects to generate digital site terrain meshes in Revit or Civil 3D with one click.
4. Provides an optional automated background converter using the free **ODA File Converter** for users who strictly require binary `.dwg` deliverables.

---

## 2. Standardized CAD Layer Architecture

All entities are written to strict, color-coded, standardized layers:

| Layer Name | Description | Color (AutoCAD ACI) | Entity Type | Elevation ($Z$) |
|---|---|---|---|---|
| `SITE-TOPO-MAJR` | Index Contours (e.g., every 5 m) | Color 30 (Orange/Brown) | `POLYLINE3D` / `LWPOLYLINE` | True AMSL Height |
| `SITE-TOPO-MINR` | Intermediate Contours (e.g., 0.5 m / 1 m) | Color 32 (Light Tan) | `POLYLINE3D` / `LWPOLYLINE` | True AMSL Height |
| `SITE-TOPO-ANNO` | Elevation Text Labels | Color 7 (White/Black) | `TEXT` / `MTEXT` | True AMSL Height |
| `SITE-BLDG-OTLN` | Building Footprints from OSM | Color 1 (Red) | Closed `LWPOLYLINE` | Ground Elevation |
| `SITE-BLDG-MASS` | 3D Building Extrusions (if enabled) | Color 252 (Light Gray) | `3DFACE` / `MESH` | Base to Roof Height |
| `SITE-ROAD-CNTR` | Road Centerlines | Color 8 (Dark Gray) | `LWPOLYLINE` (Dashed) | Projected Surface |
| `SITE-ROAD-EDGE` | Pavement / Curb Outlines | Color 7 (Continuous) | `LWPOLYLINE` | Projected Surface |
| `SITE-PATH-WALK` | Pedestrian Walkways & Trails | Color 9 (Light Gray) | `LWPOLYLINE` | Projected Surface |
| `SITE-WATR-BODY` | Lakes, Rivers, Shorelines | Color 5 (Blue) | Closed `LWPOLYLINE` | Water Level |
| `SITE-BNDY-PROP` | User Capture Area Boundary | Color 1 (Red / Phantom) | Closed `LWPOLYLINE` | Base ($Z=0$) |
| `SITE-SPTS-ELEV` | High/Low Spot Elevation Markers | Color 4 (Cyan) | `POINT` + `TEXT` | True AMSL Height |

---

## 3. 3D Contour Line Generation & Slicing Algorithm

Contour lines are mathematically sliced from the Digital Elevation Model (DEM) using high-precision grid marching squares and spline smoothing:

```python
import ezdxf
import numpy as np
from shapely.geometry import LineString, Polygon
from scipy.ndimage import gaussian_filter

class CadDeliverableBuilder:
    def __init__(self, site_polygon, origin_utm, crs_name="UTM Zone 31N"):
        self.doc = ezdxf.new("R2018")
        self.msp = self.doc.modelspace()
        self.origin = origin_utm  # (x, y) offset for local (0,0) origin
        self.setup_layers()
        self.setup_styles()
        
    def setup_layers(self):
        layers = [
            ("SITE-TOPO-MAJR", 30, "CONTINUOUS", 0.35),
            ("SITE-TOPO-MINR", 32, "CONTINUOUS", 0.18),
            ("SITE-TOPO-ANNO", 7,  "CONTINUOUS", 0.15),
            ("SITE-BLDG-OTLN", 1,  "CONTINUOUS", 0.25),
            ("SITE-ROAD-CNTR", 8,  "DASHED",     0.18),
            ("SITE-WATR-BODY", 5,  "CONTINUOUS", 0.25),
            ("SITE-BNDY-PROP", 1,  "PHANTOM",    0.50),
        ]
        for name, color, ltype, lineweight in layers:
            layer = self.doc.layers.add(name, color=color)
            layer.lineweight = int(lineweight * 100)
            
    def add_contour(self, points_3d, elevation, is_major=False):
        """
        Writes a true 3D polyline where every vertex has (X_local, Y_local, Z_elevation).
        """
        layer_name = "SITE-TOPO-MAJR" if is_major else "SITE-TOPO-MINR"
        
        # Shift vertices relative to local site origin (0, 0)
        local_coords = [(pt[0] - self.origin[0], pt[1] - self.origin[1], elevation) 
                        for pt in points_3d]
        
        if len(local_coords) > 1:
            self.msp.add_polyline3d(local_coords, dxfattribs={"layer": layer_name})
            
            # Add oriented elevation label on major contours
            if is_major and len(local_coords) > 5:
                mid_idx = len(local_coords) // 2
                mid_pt = local_coords[mid_idx]
                prev_pt = local_coords[mid_idx - 1]
                
                # Compute angle along contour curve
                angle_deg = np.degrees(np.arctan2(mid_pt[1] - prev_pt[1], mid_pt[0] - prev_pt[0]))
                
                self.msp.add_text(
                    f"{elevation:.1f} m",
                    dxfattribs={
                        "layer": "SITE-TOPO-ANNO",
                        "height": 1.2,  # 1.2m drawing scale height
                        "rotation": angle_deg,
                    }
                ).set_placement((mid_pt[0], mid_pt[1], elevation + 0.1))

    def add_building_footprint(self, polygon_coords, ground_elevation, height=0):
        """
        Adds 2D closed footprint outline, plus optional 3D extrusion walls.
        """
        local_coords = [(pt[0] - self.origin[0], pt[1] - self.origin[1]) for pt in polygon_coords]
        
        # 2D base polyline
        self.msp.add_lwpolyline(
            local_coords,
            close=True,
            dxfattribs={
                "layer": "SITE-BLDG-OTLN",
                "elevation": ground_elevation
            }
        )
        
        # If 3D height is provided, create 3DFaces for exterior walls
        if height > 0:
            for i in range(len(local_coords) - 1):
                p1, p2 = local_coords[i], local_coords[i+1]
                z_bot = ground_elevation
                z_top = ground_elevation + height
                
                # 4-corner wall quad
                self.msp.add_3dface(
                    [(p1[0], p1[1], z_bot),
                     (p2[0], p2[1], z_bot),
                     (p2[0], p2[1], z_top),
                     (p1[0], p1[1], z_top)],
                    dxfattribs={"layer": "SITE-BLDG-MASS"}
                )
```

---

## 4. Headless DWG Conversion (Optional ODA Integration)

For enterprise users requiring native binary `.dwg` files instead of `.dxf`:
1. The app detects if **Open Design Alliance (ODA) File Converter** is installed (or allows specifying its path in Settings).
2. Runs headless conversion:
   ```bash
   ODAFileConverter.exe "input_folder" "output_folder" "ACAD2018" "DWG" "0" "1" "source.dxf"
   ```
3. If ODA is not installed, the app delivers the `.dxf` directly with a helpful prompt explaining that all major CAD tools open `.dxf` natively without loss of quality.

