<p align="center">
  <img src="resources/level-up-studio-aleph.png" alt="Level Up Studio Aleph" width="260">
</p>

<h1 align="center">Level Up Studio Aleph</h1>

<p align="center">
  Download a piece of the world<br>
  for you (<em>or your LLM</em>)
</p>

Level Up Studio Aleph (Aleph) does two things:

- Lets you draw a rectangle or polygon on the map and downloads **everything** inside it (buildings, elevation, HD satellite imagery, Street View photos, textured 3D models).
- Provides any of the above information for **specific points** on Earth, on the fly, via the CLI.

See the [file guide in Arabic](دليل_الملفات.md) for a plain-language explanation of the project files.

This was born as a way to let LLMs quickly gather information from the physical world, but you can just use it to have piece of the Earth on your laptop.

> [!CAUTION]  
> Data licenses:
> - **Street View, satellite imagery, 3D meshes**: © Google and its providers. Downloading them goes against Google's terms. Not affiliated with Google. You are responsible for how you use this tool and the data it downloads.
> - **OSM data and geocoding**: [ODbL](https://opendatacommons.org/licenses/odbl/). Commercial use allowed with "© OpenStreetMap contributors" credit. Shared modified databases must stay ODbL.
> - **Terrain**: [AWS Terrain Tiles](https://github.com/tilezen/joerd/blob/master/docs/attribution.md), open sources. Commercial use allowed with the credits listed there.
> 
> Also, this tool uses undocumented APIs so it may break or hit rate limits.

## Install

The Windows installer includes Python and opens the dashboard in its own app window. Running from source or on macOS/Linux requires Python 3.11+. The app uses Pillow, numpy, truststore, and pywebview; no API keys are required.

### Windows

1. Download `Setup.exe` from the [project's GitHub Releases page](https://github.com/Belluxx/Aleph/releases/latest).
2. Double-click `Setup.exe` and follow the on-screen steps.
3. Open **Level Up Studio Aleph** from the desktop or Start menu shortcut.

The installer includes Python, so there is no need to install it or use a command window. The dashboard opens in an app window and does not create a browser tab. Windows 11 includes WebView2; if your Windows installation is missing it, Aleph offers Microsoft's download page when you start the app. Captures are saved in `Documents/Level Up Studio Aleph/captures`. An internet connection is needed for online maps and downloads.

If you are running the source ZIP instead of the installer, install Python 3.11 or newer, extract the ZIP, and double-click `Start Aleph.bat`.

When maintainers publish a version tag such as `v1.0.0`, GitHub Actions builds `Setup.exe` and attaches it to that release.

### macOS and Linux

```sh
git clone https://github.com/Belluxx/Aleph.git
cd Aleph
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Then test with

```sh
alephgeo --help
```

## Open the dashboard

```sh
alephgeo dashboard -o captures
```

The source CLI opens its dashboard in your browser at `http://127.0.0.1:8100`. The Windows installer opens the same dashboard inside an app window. Draw a rectangle or polygon on the map, choose the sources and their settings, check the preview and time estimate, then start the capture. Open any capture to show or hide each layer, or to explore its 3D mesh or terrain relief in 3D. Captures are saved in `captures`.

The dashboard downloads an archive named `Level Up Export - YYYY-MM-DD HH-MM-SS.zip`. Its deliverable files start with `Level Up Studio`: the OSM map PDF, topographic PDF with elevations above sea level and contour intervals no greater than 1 m, original 3D mesh GLB, 3D mesh DWG, and topographic DWG when their source data is available. Select OSM and Terrain for the map exports, and 3D mesh for the GLB and its DWG. DWG export requires AutoCAD for Windows.

The basemap comes from [OpenFreeMap](https://openfreemap.org) and needs an internet connection.

## Use the CLI

Download a Street View photo or satellite image by place name:

```sh
alephgeo streetview --place "Colosseum, Rome" --best-match -o captures
alephgeo satellite --place "Colosseum, Rome" --best-match -o captures
```

`--best-match` selects the first match. Each download is saved in a new folder inside `captures`.

See the [CLI docs](docs/cli.md) for examples and details.
