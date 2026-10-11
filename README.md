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
- **Batch Peak Integration**:
  - Define custom peak retention times with **asymmetric tolerance windows** (`Tol −` and `Tol +`).
  - Automatic apex detection, moving-average smoothing, and linear baseline subtraction.
  - Processes batches of chromatograms and exports aggregated peak areas to `00_evaluated_data.csv`.
- **Integrated Chromatogram Viewer**:
  - Quickly plot extracted chromatograms and spectra directly within the application using Matplotlib.
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
    ├── chrom/                 # Wavelength-specific chromatograms (e.g., Sample_01_254_nm.csv)
    ├── mz/                    # Full m/z matrices (e.g., Sample_01_mz.csv)
    ├── tic/                   # Total Ion Chromatograms (e.g., Sample_01_tic.csv)
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
   - Click **Extract**. The application will parse `_FUNC001.DAT` and `_FUNC002.DAT` files and create the `csvs/` directory.

2. **Integration Target Folder**:
   - The CSV folder automatically populates with `<root>/csvs/chrom` after extraction, or you can select any folder with chromatogram CSVs.

3. **Define Peak Targets**:
   - Enter the expected peak **Retention time (min)**.
   - Specify asymmetric tolerances: **Tol − (min)** (window left of peak) and **Tol + (min)** (window right of peak).
   - Click **Add**. Repeat for all compounds of interest.
   - To remove a target, select it from the list and click **Delete**.

4. **Run Integration**:
   - Click **Integrate**.
   - The tool iterates through all chromatograms, computes baseline-corrected peak areas via trapezoidal integration, and saves the results to `00_evaluated_data.csv`.

5. **Quick CSV Plot**:
   - In Section 4, select a folder and a CSV file from the dropdown.
   - Click **Plot** to inspect the trace with interactive zoom and pan controls.

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
