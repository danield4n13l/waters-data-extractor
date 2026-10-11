# Waters Data Extractor & Integrator

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A modern desktop GUI and processing utility to extract, integrate, and visualize chromatographic and mass spectrometry data from Waters MassLynx `.raw` directories.

---

## Acknowledgements

This project was originally made by a third-party, however, it has been modified to better suit our needs and to include additional features.

If you are the original author of the project and would like to be credited, please let me know.

> [!NOTE]
> This codebase is heavily authoured by Google Antigravity, using Gemini 3.8 Flash on high priority, and as such, may have bugs or issues.

> [!WARNING]
> **Please note that the code is currently work in progress and may not be fully functional**

## Features

- **Automated MassLynx `.raw` Data Extraction**:
  - Recursively finds and parses Waters `.raw` folders.
  - **MS / TIC (`_FUNC001.DAT`)**: Extracts complete m/z matrices and computes Total Ion Chromatograms (TIC).
  - **UV-Vis Photodiode Array (`_FUNC002.DAT`)**: Extracts absorbance traces per discrete wavelength (e.g., 210 nm, 254 nm).
  - Adaptive parser fallback (`_safe_parse_function`) ensures compatibility across different `rainbow-api` versions.
- **Wavelength Merging**:
  - Specify comma-separated wavelengths (e.g., `210, 254`) to combine selected channels into unified multi-column CSV files (`<sample>_<wl1>_<wl2>_nm.csv`) alongside or in place of individual single-wavelength exports.
- **Rule-Based Sample Grouping & Routing**:
  - Organize extracted files into structured subfolders using pattern rules (`pattern => subfolder`).
  - Supports **Wildcard** (glob) and **Regex** matching modes with capture group expansion (e.g., `\1` or `\g<name>`) and nested directory paths (`/` or `\`).
  - Interactive sample preview table allows checking destination folder mappings before extraction.
- **Peak Integration with Real-Time Preview**:
  - Define custom peak retention times with **asymmetric tolerance windows** (`Tol −` and `Tol +`).
  - Automatic apex detection, moving-average smoothing, and linear baseline subtraction.
  - **Live Integration Preview**: Toggle the interactive sample preview canvas to visualize peak apex, baseline subtraction, integration bounds, and computed peak area in real time.
  - **Target Presets & History**: Save, load, and delete named target templates (stored in JSON), and quickly reload recently used target configurations.
  - Recursively processes CSVs across subfolders and evaluates multiple channels per sample (labeling individual rows e.g. `<sample>_<channel>`), exporting results to `00_evaluated_data.csv`.
- **Multi-Format CSV Inspector & Specialized Visualizations**:
  - Automatic dataset classification: Identifies **DAD**, **TIC**, **m/z**, and **Integrated areas** CSVs.
  - Dual-tab interface offering simultaneous **Plot preview** and raw tabular **Table preview** with sortable columns.
  - **m/z Mass Spectra & RT Bar Series**: Plot ion intensity bar charts across retention times or inspect discrete mass spectra across m/z bins at any selected RT scan.
  - **Integrated Areas Comparison**: Visualize and compare peak area series across multiple samples using aggregated bar charts.
  - **High-DPI Plot Export**: Export publication-quality charts to PNG or vector SVG with customizable resolution (72–2400 DPI).
- **Publication-Ready Veusz & LabPlot Document Export**:
  - Generates `.vsz` (Veusz v3.6+) project files with embedded datasets (`SetData`) or linked CSVs (`ImportFileCSV`), automatic axis scaling, and curated scientific color palettes.
  - Export directly from Section 04 of the main window or launch the dedicated standalone GUI / CLI utility (`Waters_Integrator_exporter.py`).
  - Fallback legacy LabPlot XML support (`.lml`).
- **Interactive Runtime Console Drawer & Modern UI**:
  - Embedded collapsible debugging console drawer capturing stdout/stderr with timestamped categories, filter levels, and quick copy/clear tools.
  - Visual **GripSplitters** with draggable dot grip styling.
  - Quick **Open** buttons to launch directories in native system file explorers (Explorer, Finder, xdg-open).
  - Built-in theme switcher (**Light**, **Dark**, and **System default**) with persistent user preferences and JSON backup.

---

## Directory Structure of Extracted Data

When you extract a folder containing MassLynx runs, the application creates a structured `csvs/` folder:

```text
<data_directory>/
├── Sample_01.raw/
│   ├── _FUNC001.DAT           # Mass spectrometry data
│   └── _FUNC002.DAT           # UV/Vis spectral data
├── Sample_02.raw/
└── csvs/
    ├── chrom/                 # Wavelength chromatograms (single or merged)
    │   ├── Group_A/           # Subfolders generated via grouping rules
    │   │   └── Sample_01_210_254_nm.csv
    │   └── Sample_02_254_nm.csv
    ├── mz/                    # Full m/z matrices (retains group subfolders)
    ├── tic/                   # Total Ion Chromatograms (retains group subfolders)
    └── 00_evaluated_data.csv  # Output summary generated after integration
```

---

## Installation

### 1. Prerequisites
- Python 3.8 or higher.

### 2. Clone Repository
```bash
git clone https://github.com/<your-username>/waters-data-extractor.git
cd waters-data-extractor
```

### 3. Create a Virtual Environment (Recommended)
```bash
python -m venv venv

# On Windows:
venv\Scripts\activate

# On macOS/Linux:
source venv/bin/activate
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

> **Note**: Waters `.raw` parsing is powered by [`rainbow-api`](https://github.com/theochem/rainbow).

---

## Usage Guide

### Main Application Workspace

Launch the primary graphical interface:

```bash
python Waters_Integrator_plot.py
```

#### Workflow Steps

1. **Section 01 &mdash; Extraction and Sample Organization**:
   - Click **Browse** to select the root directory containing MassLynx `.raw` run folders (or click **Open** to inspect it in the file manager).
   - *(Optional)* **Merge wavelengths**: Enter comma-separated wavelengths in nm (e.g., `210, 254`) to merge those traces into unified multi-column CSV files.
   - *(Optional)* **Folder rules**: Enter one rule per line using `pattern => subfolder`.
     - Choose **Wildcard** (e.g., `*Control* => Controls`) or **Regex** (e.g., `^(?P<batch>[^_]+)_(\d+)\.raw$ => \g<batch>/rep_\2`).
     - Regex mode supports capture references (`\1`, `\g<name>`) and nested folders (`/` or `\`).
     - Click **Preview samples** to inspect the mapping of RAW samples to destination folders.
   - Click **Extract files** to parse `_FUNC001.DAT` and `_FUNC002.DAT` files and create the `csvs/` directory hierarchy.

2. **Section 02 &mdash; Peak Integration**:
   - Select the CSV folder (defaults to `<root>/csvs/chrom`). Subfolders are discovered and processed recursively.
   - **Define Peak Targets**: Enter the expected peak **Retention time (min)**, **Tolerance − (min)**, and **Tolerance + (min)**, then click **Add target**.
   - **Target Templates & Recent History**:
     - Use the top-bar **Templates** menu to save the current target configuration as a named template, load saved presets, or delete old ones.
     - Use the **Recent targets** dropdown to reload recently integrated peak configurations.
   - **Interactive Peak Preview**:
     - Check **Open sample preview** to inspect peak integration in real time.
     - Choose a sample from the dropdown to visualize apex detection, baseline subtraction, and window boundaries before running batch integration.
   - Click **Integrate CSVs** to calculate peak areas across all samples and save results into `00_evaluated_data.csv`.

3. **Section 03 &mdash; CSV Data Preview & Export**:
   - Browse to any CSV directory (e.g. `csvs/chrom`, `csvs/tic`, `csvs/mz`, or root `csvs`).
   - The file table displays the detected dataset type (**DAD**, **TIC**, **m/z**, or **Integrated areas**) and relative subfolder.
   - Switch between **Plot preview** (graphical canvas) and **Table preview** (raw tabular data rows).
   - For **m/z** data, switch between **Retention time** ion traces and **m/z values** scan spectrum bar charts.
   - For **Integrated areas**, view multi-sample comparative bar plots.
   - Click **Export plot** to save the visualization to **PNG** or **SVG** format with configurable DPI resolution (72–2400 DPI).

4. **Section 04 &mdash; Veusz / LabPlot Document Export**:
   - Select a CSV folder and target output path for publication project documents (`.vsz` or `.lml`).
   - Click **Export to Veusz (.vsz)** for clean, auto-formatted multi-page Veusz projects.
   - Click **Launch Standalone Exporter** to open the dedicated Veusz Exporter tool with selective dataset checkboxes and embedded data options.

---

### Standalone Exporter Tool

Launch the dedicated export workspace:

```bash
python Waters_Integrator_exporter.py
```

Or run headless batch exports from the command line:

```bash
# Export all datasets in a folder into a self-contained Veusz project
python Waters_Integrator_exporter.py --input path/to/csvs --output Project.vsz --embed-data

# Headless batch export linking external CSV files
python Waters_Integrator_exporter.py -i path/to/csvs -o Project.vsz --link-csv
```

---

## Integration Algorithm Details

For each target peak in window $[t_{\text{target}} - \text{tol}_-, t_{\text{target}} + \text{tol}_+]$:
1. **Apex Detection**: Identifies local maximum after moving average smoothing (default window = 5 points).
2. **Peak Bounds**: Finds surrounding local minima $(x_0, y_0)$ and $(x_1, y_1)$ to the left and right of the apex.
3. **Baseline Subtraction**: Constructs a linear baseline between the boundaries:
   $$y_{\text{baseline}}(x) = y_0 + (y_1 - y_0) \cdot \frac{x - x_0}{x_1 - x_0}$$
4. **Area Calculation**: Integrates positive net signal $\max(y(x) - y_{\text{baseline}}(x), 0)$ using the trapezoidal rule.

---

## Project Structure

```text
waters-data-extractor/
├── Waters_Integrator_plot.py      # Main application workspace with PySide6 desktop GUI
├── Waters_Integrator_exporter.py  # Standalone Veusz / LabPlot document exporter & CLI
├── requirements.txt               # Python dependencies (pandas, matplotlib, PySide6, rainbow-api)
├── pyproject.toml                 # Project metadata and packaging config
├── .gitignore                     # Git ignore file for Python and raw lab data
├── .gitattributes                 # Git line-ending and binary tracking
├── LICENSE                        # MIT License
└── README.md                      # Project documentation
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
