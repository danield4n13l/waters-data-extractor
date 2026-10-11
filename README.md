# Waters Data Extractor & Integrator

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A lightweight desktop GUI and processing utility to extract, integrate, and visualize chromatographic and mass spectrometry data from Waters MassLynx `.raw` directories.

---

## Acknowledgements

This project was originally made by a third-party, however, it has been modified to better suit our needs and to include additional features.

If you are the original author of the project and would like to be credited, please let me know.

## Features

- **Automated MassLynx `.raw` Data Extraction**:
  - Recursively finds and parses Waters `.raw` folders.
  - **MS / TIC (`_FUNC001.DAT`)**: Extracts complete m/z matrices and computes Total Ion Chromatograms (TIC).
  - **UV-Vis Photodiode Array (`_FUNC002.DAT`)**: Extracts absorbance traces per discrete wavelength (e.g., 210 nm, 254 nm).
- **Wavelength Merging**:
  - Specify comma-separated wavelengths (e.g., `210, 254`) to combine selected channels into unified multi-column CSV files (`<sample>_<wl1>_<wl2>_nm.csv`) alongside or in place of individual single-wavelength exports.
- **Rule-Based Sample Grouping & Routing**:
  - Organize extracted files into structured subfolders using pattern rules (`pattern => subfolder`).
  - Supports **Wildcard** (glob) and **Regex** matching modes with capture group expansion (e.g., `\1` or `\g<name>`) and nested directory paths (`/` or `\`).
  - Interactive sample preview table (`ttk.Treeview`) allows checking destination folder mappings before extraction.
- **Multi-Channel & Recursive Peak Integration**:
  - Define custom peak retention times with **asymmetric tolerance windows** (`Tol −` and `Tol +`).
  - Automatic apex detection, moving-average smoothing, and linear baseline subtraction.
  - Recursively processes CSVs across subfolders and evaluates multiple channels per sample (labeling individual rows e.g. `<sample>_<channel>`).
  - Exports aggregated peak areas into `00_evaluated_data.csv`.
- **Embedded Multi-Channel Chromatogram Preview**:
  - Built-in Matplotlib canvas embedded directly into the application window.
  - Preview single traces or check **Show all signals** to overlay multiple channels on a single plot with automatic legends and grid lines.
- **Minimal Dependencies & Native GUI**:
  - Built on Tkinter with zero heavy desktop framework overhead.

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
- *(Linux only)*: Ensure Tkinter is installed:
  ```bash
  sudo apt-get install python3-tk
  ```

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

Launch the graphical interface:

```bash
python Waters_Integrator_plot.py
```

### Workflow Steps

1. **Data Extraction (MassLynx `.raw` &rarr; CSVs)**:
   - Click **Browse…** in Section 1 to select the folder containing your `.raw` runs.
   - *(Optional)* **Merge wavelengths**: Enter comma-separated wavelengths in nm (e.g., `210, 254`) to merge those traces into multi-column CSV files.
   - *(Optional)* **Folder rules**: Enter one rule per line using `pattern => subfolder`.
     - Choose **Wildcard** (e.g., `*Control* => Controls`) or **Regex** (e.g., `^Batch(\d+)_(.*) => Batch_\1/\2`).
     - Regex mode supports capture references (`\1`, `\g<name>`) and nested folders (`/` or `\`).
     - Click **Preview** to inspect the mapping of RAW samples to destination folders.
   - Click **Extract**. The application will parse `_FUNC001.DAT` and `_FUNC002.DAT` files and create the `csvs/` directory structure.

2. **Integration Target Folder**:
   - The CSV folder automatically populates with `<root>/csvs/chrom` after extraction, or you can select any folder containing chromatographic CSV files.
   - Subdirectories within the selected folder are automatically discovered and processed recursively.

3. **Define Peak Targets**:
   - Enter the expected peak **Retention time (min)**.
   - Specify asymmetric tolerances: **Tol − (min)** (window left of peak) and **Tol + (min)** (window right of peak).
   - Click **Add**. Repeat for all compounds of interest.
   - To remove a target, select it from the list and click **Delete**.

4. **Run Integration**:
   - Click **Integrate**.
   - The tool iterates through all chromatograms recursively, integrating each signal channel independently (multi-channel CSVs output separate rows for each channel e.g., `<sample>_<channel>`), and saves peak areas to `00_evaluated_data.csv`.

5. **CSV Chromatogram Preview**:
   - In Section 4, select a folder containing CSV files.
   - Select a CSV file from the dropdown (subfolders are preserved in the list).
   - Choose a specific trace from the **Signal** dropdown or check **Show all signals** to overlay all available traces on the embedded Matplotlib figure.
   - Click **Reload** to refresh the current file and plot view.

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
├── Waters_Integrator_plot.py  # Main application script with Tkinter GUI
├── requirements.txt           # Python dependencies
├── pyproject.toml             # Project metadata and packaging config
├── .gitignore                 # Git ignore file for Python and raw lab data
├── .gitattributes             # Git line-ending and binary tracking
├── LICENSE                    # MIT License
└── README.md                  # Project documentation
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
