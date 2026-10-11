#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import csv
import json
import math
import fnmatch
from typing import List, Optional

import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QAbstractItemView,
    QBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QStyleFactory,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from rainbow.waters import masslynx

# ---------------------------
# Utility helpers
# ---------------------------

def sanitize_filename(name: str, default: str = "sample") -> str:
    """Remove problematic characters for filenames."""
    name = re.sub(r'[\\/*?:"<>|]+', "_", name.strip())
    name = name.strip(" .")
    return name or default


def sanitize_folder_path(path: str, default: str = "group") -> str:
    """Sanitize each folder component while retaining recursive separators."""
    parts = []
    for part in re.split(r"(?!\\\d+|\\g<.+>)[\\/]+", path.strip()):
        if part in {"", ".", ".."}:
            continue
        safe_part = sanitize_filename(part, default="")
        if safe_part:
            parts.append(safe_part)
    return os.path.join(*parts) if parts else default


def find_rt_column(pdf: pd.DataFrame) -> str:
    """
    Try to find the RT column, defaulting to the first column if needed.
    - Prefer a column whose name starts with 'rt' (case-insensitive).
    """
    for c in pdf.columns:
        if c.lower().startswith("rt"):
            return c
    # fallback: first column
    return pdf.columns[0]


def parse_wavelength_label(col_name: str) -> str:
    """
    Turn a wavelength column label into a safe suffix for filenames.
    In typical MassLynx spectral data, columns are something like '210', '254', etc.
    We try to interpret it as a float (in nm), round to nearest integer,
    and return e.g. '210'. If that fails, we return a sanitized alphanumeric.
    """
    cleaned = re.sub(r"[^\d.]+", "", col_name)
    if cleaned:
        try:
            val = float(cleaned)
            return str(int(round(val)))
        except Exception:
            pass
    return re.sub(r"[^0-9A-Za-z]+", "_", col_name) or "wl"


def parse_merge_wavelengths(value: str) -> List[str]:
    """Parse a comma-separated wavelength selection into normalized labels."""
    wavelengths = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            wavelength = float(item)
        except ValueError as exc:
            raise ValueError(f"Invalid wavelength: {item}") from exc
        if not math.isfinite(wavelength) or wavelength <= 0:
            raise ValueError(f"Invalid wavelength: {item}")
        label = str(int(round(wavelength)))
        if label not in wavelengths:
            wavelengths.append(label)
    return wavelengths


def parse_group_rules(value: str, match_mode: str) -> List[tuple]:
    """Parse ordered pattern => subfolder rules; regex folders allow match.expand references."""
    rules = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if "=>" not in line:
            raise ValueError(f"Rule on line {line_number} must use pattern => subfolder.")
        pattern, folder = (part.strip() for part in line.split("=>", 1))
        if not pattern or not folder:
            raise ValueError(f"Rule on line {line_number} needs both a pattern and a subfolder.")
        if match_mode == "Regex":
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"Invalid regex on line {line_number}: {exc}") from exc
        rules.append((pattern, folder))
    return rules


def match_group(raw_name: str, rules: List[tuple], match_mode: str) -> Optional[str]:
    """Return the destination from the first matching rule, if any."""
    for pattern, folder in rules:
        if match_mode == "Regex":
            match = re.search(pattern, raw_name, re.IGNORECASE)
            if match is None:
                continue
            try:
                folder = match.expand(folder)
            except re.error as exc:
                raise ValueError(f"Invalid group reference in folder for pattern {pattern!r}: {exc}") from exc
        else:
            matches = fnmatch.fnmatchcase(raw_name.casefold(), pattern.casefold())
            if not matches:
                continue
        return sanitize_folder_path(folder)
    return None


# ---------------------------
# MassLynx extraction helpers
# ---------------------------

def iter_raw_dirs(root: str) -> List[str]:
    """
    Yield directories that look like MassLynx .raw folders (name ends with .raw)
    and contain at least one .DAT file.
    """
    raw_dirs = []
    for dirpath, dirnames, filenames in os.walk(root):
        base = os.path.basename(dirpath)
        if base.lower().endswith(".raw"):
            if any(fn.upper().endswith(".DAT") for fn in filenames):
                raw_dirs.append(dirpath)
    return raw_dirs


def process_func001(dat_path: str, base_name: str, mz_dir: str, tic_dir: str, prec: int = 0):
    """
    Process _FUNC001.DAT:
      - Export full m/z CSV as <mz_dir>/<base_name>_mz.csv
      - Create TIC CSV as <tic_dir>/<base_name>_tic.csv by summing all intensity
        columns (all columns except RT) row-wise.
    """
    os.makedirs(mz_dir, exist_ok=True)
    os.makedirs(tic_dir, exist_ok=True)

    mz_csv = os.path.join(mz_dir, f"{base_name}_mz.csv")

    df = masslynx.parse_function(str(dat_path), prec=prec)
    df.export_csv(mz_csv)

    pdf = pd.read_csv(mz_csv)

    rt_col = find_rt_column(pdf)
    intensity_cols = [c for c in pdf.columns if c != rt_col]

    if not intensity_cols:
        tic_series = pd.Series([0.0] * len(pdf))
    else:
        tic_series = pdf[intensity_cols].sum(axis=1)

    tic_df = pd.DataFrame({
        rt_col: pdf[rt_col],
        "TIC": tic_series
    })

    tic_csv = os.path.join(tic_dir, f"{base_name}_tic.csv")
    tic_df.to_csv(tic_csv, index=False)


def process_func002(
    dat_path: str,
    base_name: str,
    chrom_dir: str,
    prec: int = 0,
    merge_wavelengths: Optional[List[str]] = None,
):
    """
    Process _FUNC002.DAT (spectral UV-Vis):
      - First column is 'RT (min)', remaining columns are wavelengths (nm).
      - For each wavelength column, create:
            <chrom_dir>/<base_name>_<wl>_nm.csv
        with columns:
            RT (min), absorbance
    """
    os.makedirs(chrom_dir, exist_ok=True)

    temp_csv = os.path.join(chrom_dir, f"__temp_{base_name}_FUNC002.csv")
    df = masslynx.parse_function(str(dat_path), prec=prec)
    df.export_csv(temp_csv)

    pdf = pd.read_csv(temp_csv)
    try:
        os.remove(temp_csv)
    except Exception:
        pass

    if pdf.empty or len(pdf.columns) < 2:
        return

    rt_col = find_rt_column(pdf)
    spec_cols = [c for c in pdf.columns if c != rt_col]
    selected = set(merge_wavelengths or [])
    merged_cols = {}

    for col in spec_cols:
        wl_suffix = parse_wavelength_label(col)
        if wl_suffix in selected:
            merged_cols[wl_suffix] = pdf[col]
            continue
        out_csv = os.path.join(chrom_dir, f"{base_name}_{wl_suffix}_nm.csv")
        out_df = pd.DataFrame({
            rt_col: pdf[rt_col],
            "absorbance": pdf[col]
        })
        out_df.to_csv(out_csv, index=False)

    available = [wl for wl in (merge_wavelengths or []) if wl in merged_cols]
    if available:
        merged_df = pd.DataFrame({rt_col: pdf[rt_col]})
        for wl in available:
            merged_df[f"{wl} nm"] = merged_cols[wl]
        suffix = "_".join(merge_wavelengths or available)
        merged_csv = os.path.join(chrom_dir, f"{base_name}_{suffix}_nm.csv")
        merged_df.to_csv(merged_csv, index=False)


def extract_masslynx_folder(
    root: str,
    status_cb=None,
    prec: int = 0,
    merge_wavelengths: Optional[List[str]] = None,
    group_rules: Optional[List[tuple]] = None,
    match_mode: str = "Regex",
) -> str:
    """
    Extract MassLynx .raw folders beneath 'root' into:
        <root>/csvs/chrom  (chromatographic *_nm.csv)
        <root>/csvs/mz     (*_mz.csv)
        <root>/csvs/tic    (*_tic.csv)

    Returns the path to the chromatographic folder (chrom_dir).
    """
    root = os.path.abspath(root)
    out_root = os.path.join(root, "csvs")
    chrom_dir = os.path.join(out_root, "chrom")
    mz_dir = os.path.join(out_root, "mz")
    tic_dir = os.path.join(out_root, "tic")

    os.makedirs(chrom_dir, exist_ok=True)
    os.makedirs(mz_dir, exist_ok=True)
    os.makedirs(tic_dir, exist_ok=True)

    raw_dirs = iter_raw_dirs(root)
    for raw_dir in raw_dirs:
        match_group(os.path.basename(raw_dir), group_rules or [], match_mode)

    if status_cb:
        status_cb(f"Found {len(raw_dirs)} .raw folder(s).")

    processed = 0
    failed = 0

    for idx, raw_dir in enumerate(sorted(raw_dirs)):
        base_raw = os.path.basename(raw_dir)
        base_name = base_raw[:-4] if base_raw.lower().endswith(".raw") else base_raw
        base_name = sanitize_filename(base_name, default="sample")

        if status_cb:
            status_cb(f"[{idx+1}/{len(raw_dirs)}] {base_raw}")

        func001 = None
        func002 = None
        for fn in os.listdir(raw_dir):
            up = fn.upper()
            if up.endswith(".DAT"):
                if "_FUNC001" in up and func001 is None:
                    func001 = os.path.join(raw_dir, fn)
                elif "_FUNC002" in up and func002 is None:
                    func002 = os.path.join(raw_dir, fn)

        try:
            group = match_group(base_raw, group_rules or [], match_mode)
            sample_mz_dir = os.path.join(mz_dir, group) if group else mz_dir
            sample_tic_dir = os.path.join(tic_dir, group) if group else tic_dir
            sample_chrom_dir = os.path.join(chrom_dir, group) if group else chrom_dir
            if func001:
                process_func001(func001, base_name, sample_mz_dir, sample_tic_dir, prec=prec)
            if func002:
                process_func002(
                    func002,
                    base_name,
                    sample_chrom_dir,
                    prec=prec,
                    merge_wavelengths=merge_wavelengths,
                )
            processed += 1
        except Exception:
            failed += 1

    if status_cb:
        status_cb(f"Done: processed {processed}, failed {failed}.")
    return chrom_dir


# ---------------------------
# Integration helpers
# ---------------------------

def read_chrom_csv(path: str):
    """
    Read chromatographic CSV.

    Tries to find 'retention_time_min' and 'intensity' headers; if not found,
    falls back to columns 0 (time) and 1 (signal). This makes it compatible
    with the generated UV chromatogram files:
        RT (min), absorbance
    """
    times, intens = [], []
    with open(path, "r", newline="", encoding="utf-8") as f:
        rdr = csv.reader(f)
        header = next(rdr, None)
        if header is None:
            return times, intens
        try:
            t_idx = header.index("retention_time_min")
            y_idx = header.index("intensity")
        except ValueError:
            t_idx, y_idx = 0, 1
        for row in rdr:
            if len(row) <= max(t_idx, y_idx):
                continue
            try:
                t = float(row[t_idx])
                y = float(row[y_idx])
            except Exception:
                continue
            times.append(t)
            intens.append(y)
    if len(times) > 1 and any(times[i] > times[i + 1] for i in range(len(times) - 1)):
        paired = sorted(zip(times, intens), key=lambda x: x[0])
        times, intens = map(list, zip(*paired))
    return times, intens


def read_chrom_csv_channels(path: str):
    """Read every signal column in a chromatogram CSV as a separate trace."""
    channels = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        rdr = csv.reader(f)
        header = next(rdr, None)
        if header is None or len(header) < 2:
            return channels
        try:
            time_idx = header.index("retention_time_min")
        except ValueError:
            time_idx = 0
        signal_indices = [idx for idx in range(len(header)) if idx != time_idx]
        channel_data = {idx: ([], []) for idx in signal_indices}

        for row in rdr:
            if len(row) <= time_idx:
                continue
            try:
                time = float(row[time_idx])
            except (ValueError, IndexError):
                continue
            for idx in signal_indices:
                if len(row) <= idx:
                    continue
                try:
                    intensity = float(row[idx])
                except ValueError:
                    continue
                channel_times, intensities = channel_data[idx]
                channel_times.append(time)
                intensities.append(intensity)

        for idx in signal_indices:
            channel_times, intensities = channel_data[idx]
            if len(channel_times) > 1 and any(
                channel_times[i] > channel_times[i + 1]
                for i in range(len(channel_times) - 1)
            ):
                paired = sorted(zip(channel_times, intensities), key=lambda pair: pair[0])
                channel_times, intensities = map(list, zip(*paired))
            if channel_times:
                channels.append((header[idx], channel_times, intensities))
    return channels


def find_chrom_csv_files(root: str, include_evaluated: bool = False) -> List[tuple]:
    """Find chromatogram CSVs recursively, retaining subfolders in sample names."""
    files = []
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.lower().endswith(".csv") and (
                include_evaluated or filename.lower() != "00_evaluated_data.csv"
            ):
                path = os.path.join(dirpath, filename)
                relative = os.path.relpath(path, root)
                sample_name = os.path.splitext(relative)[0]
                files.append((sample_name, path))
    return sorted(files, key=lambda item: item[0].casefold())


def classify_csv_type(path: str, headers=None) -> str:
    """Identify MassLynx DAD, TIC, and m/z CSVs from names and headers."""
    filename = os.path.basename(path).casefold()
    if filename == "00_evaluated_data.csv":
        return "Integrated areas"
    path_parts = {part.casefold() for part in path.replace("\\", "/").split("/")}
    if filename.endswith("_mz.csv") or "mz" in path_parts or "m/z" in path_parts:
        return "m/z"
    if filename.endswith("_tic.csv") or "tic" in path_parts:
        return "TIC"

    if headers is None:
        try:
            with open(path, "r", newline="", encoding="utf-8-sig") as source:
                headers = next(csv.reader(source), [])
        except (OSError, UnicodeError, csv.Error):
            headers = []
    normalized_headers = [str(header).strip().casefold() for header in headers]
    if any(header in {"tic", "total ion current"} for header in normalized_headers):
        return "TIC"
    if filename.endswith("_nm.csv") or any(
        header == "absorbance" or re.fullmatch(r"\d+(?:\.\d+)?\s*nm", header)
        for header in normalized_headers
    ):
        return "DAD"
    numeric_signals = sum(
        bool(re.fullmatch(r"\d+(?:\.\d+)?", header))
        for header in normalized_headers[1:]
    )
    if numeric_signals >= 2:
        return "m/z"
    return "CSV"


def moving_average(seq, window=5):
    n = len(seq)
    if n == 0 or window <= 1:
        return seq[:]
    if window % 2 == 0:
        window += 1
    half = window // 2
    out = []
    csum = [0.0]
    for v in seq:
        csum.append(csum[-1] + v)
    for i in range(n):
        a = max(0, i - half)
        b = min(n - 1, i + half)
        s = csum[b + 1] - csum[a]
        out.append(s / (b - a + 1))
    return out


def find_indices_in_window_lr(times, left, right):
    """Return (L, R) indices spanning [left, right], inclusive."""
    n = len(times)
    if n == 0 or left > right:
        return None
    L = 0
    while L < n and times[L] < left:
        L += 1
    R = L
    while R < n and times[R] <= right:
        R += 1
    L = max(0, L)
    R = max(L, R - 1)
    if L >= n or L > R:
        return None
    return (L, R)


def integrate_peak_asym(times, intens, center, tol_minus, tol_plus, smooth_win=5):
    """
    Asymmetric tolerance: window = [center - tol_minus, center + tol_plus]
    Baseline/minima method as in the original app.
    """
    if not times or not intens:
        return 0.0
    left = center - max(tol_minus, 0.0)
    right = center + max(tol_plus, 0.0)
    bounds = find_indices_in_window_lr(times, left, right)
    if not bounds:
        return 0.0
    L, R = bounds
    if R - L < 2:
        return 0.0

    y_win = intens[L:R + 1]
    y_smooth = moving_average(y_win, window=smooth_win)

    i_apex_rel = max(range(len(y_smooth)), key=lambda i: y_smooth[i])

    left_min_rel = min(range(i_apex_rel + 1), key=lambda i: y_smooth[i])
    right_min_rel = i_apex_rel + min(
        range(len(y_smooth) - i_apex_rel),
        key=lambda j: y_smooth[i_apex_rel + j]
    )

    i_left = L + left_min_rel
    i_right = L + right_min_rel
    if i_right <= i_left:
        i_left, i_right = L, R
        if i_right <= i_left:
            return 0.0

    x0, x1 = times[i_left], times[i_right]
    y0, y1 = intens[i_left], intens[i_right]
    denom = (x1 - x0) if x1 != x0 else 1e-12

    y_corr = []
    for i in range(i_left, i_right + 1):
        x = times[i]
        yb = y0 + (y1 - y0) * (x - x0) / denom
        y_corr.append(max(intens[i] - yb, 0.0))

    area = 0.0
    for k in range(len(y_corr) - 1):
        dt = times[i_left + k + 1] - times[i_left + k]
        if dt > 0:
            area += 0.5 * (y_corr[k] + y_corr[k + 1]) * dt
    return area

class CollapsibleSection(QWidget):
    expandedChanged = Signal(object, bool)

    def __init__(self, title: str, expanded: bool = True):
        super().__init__()
        self.setObjectName("collapsibleSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toggle = QToolButton()
        self.toggle.setObjectName("sectionToggle")
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.setSizePolicy(self.toggle.sizePolicy().horizontalPolicy(), self.toggle.sizePolicy().verticalPolicy())
        layout.addWidget(self.toggle)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 10, 14, 14)
        self.body_layout.setSpacing(10)
        self.body.setVisible(expanded)
        layout.addWidget(self.body)
        self.toggle.toggled.connect(self._set_expanded)

    def _set_expanded(self, expanded: bool):
        self.body.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.expandedChanged.emit(self, expanded)


class MassLynxWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MassLynx | Chromatography workspace")
        self.resize(1280, 900)
        self.setMinimumSize(640, 440)

        self.targets = []
        self.plot_paths = {}
        self.plot_dataframe = None
        self.plot_csv_type = "CSV"
        self.settings = QSettings("VBK_BSc_Thesis_FDA2026", "MassLynxIntegrator")
        try:
            self.recent_targets = json.loads(self.settings.value("recentTargets", "[]"))
        except (TypeError, json.JSONDecodeError):
            self.recent_targets = []
        try:
            self.templates = json.loads(self.settings.value("templates", "{}"))
        except (TypeError, json.JSONDecodeError):
            self.templates = {}
        if not isinstance(self.recent_targets, list):
            self.recent_targets = []
        if not isinstance(self.templates, dict):
            self.templates = {}
        self.native_style_name = QApplication.style().objectName()
        self.system_palette = QPalette(QApplication.palette())
        self._responsive_narrow = None
        self.figure = Figure(figsize=(8, 4), dpi=100, constrained_layout=True)
        self.axes = self.figure.add_subplot(111)

        self._build_ui()

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(22, 18, 22, 18)
        content_layout.setSpacing(12)

        title = QLabel("MassLynx data workspace")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Extract RAW runs, organize samples, integrate peaks, and inspect chromatograms.")
        subtitle.setObjectName("pageSubtitle")
        heading = QVBoxLayout()
        heading.addWidget(title)
        heading.addWidget(subtitle)

        appearance = QHBoxLayout()
        appearance.addWidget(QLabel("Style"))
        self.theme_style_combo = QComboBox()
        self.theme_style_combo.addItem("Qt default", "qt")
        self.theme_style_combo.addItem("Windows 11", "windows11")
        style_index = self.theme_style_combo.findData(self.settings.value("theme/style", "qt"))
        self.theme_style_combo.setCurrentIndex(max(style_index, 0))
        appearance.addWidget(self.theme_style_combo)
        appearance.addWidget(QLabel("Mode"))
        self.theme_mode_combo = QComboBox()
        self.theme_mode_combo.addItem("System default", "system")
        self.theme_mode_combo.addItem("Light", "light")
        self.theme_mode_combo.addItem("Dark", "dark")
        mode_index = self.theme_mode_combo.findData(self.settings.value("theme/mode", "system"))
        self.theme_mode_combo.setCurrentIndex(max(mode_index, 0))
        appearance.addWidget(self.theme_mode_combo)
        self._build_template_menu(appearance)

        header = QHBoxLayout()
        header.addLayout(heading, 1)
        header.addLayout(appearance)
        content_layout.addLayout(header)

        self._build_extraction_section(content_layout)
        self._build_integration_section(content_layout)
        self._build_plot_section(content_layout)
        self.sections = content.findChildren(CollapsibleSection)
        for section in self.sections:
            section.expandedChanged.connect(self.handle_section_expanded)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        self.setCentralWidget(scroll)
        self.statusBar().showMessage("Ready")
        self.theme_style_combo.currentIndexChanged.connect(self.apply_theme)
        self.theme_mode_combo.currentIndexChanged.connect(self.apply_theme)
        QApplication.styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.apply_theme()

    def apply_theme(self, *_args):
        app = QApplication.instance()
        mode = self.theme_mode_combo.currentData() or "system"
        theme = self.theme_style_combo.currentData()
        available_styles = {name.casefold(): name for name in QStyleFactory.keys()}
        if theme == "windows11":
            style_name = available_styles.get("windows11", available_styles.get("windows", "Fusion"))
        elif mode != "system":
            style_name = available_styles.get("fusion", "Fusion")
        else:
            style_name = available_styles.get(self.native_style_name.casefold(), self.native_style_name)
        style = QStyleFactory.create(style_name) or QStyleFactory.create("Fusion")
        if style is not None:
            app.setStyle(style)

        if mode == "system":
            system_scheme = QApplication.styleHints().colorScheme()
            if system_scheme == Qt.ColorScheme.Unknown:
                system_dark = self.system_palette.color(QPalette.ColorRole.Window).lightness() < 128
            else:
                system_dark = system_scheme == Qt.ColorScheme.Dark
        else:
            system_dark = mode == "dark"

        if theme == "qt" and mode == "system":
            self.setStyleSheet("")
            app.setPalette(self.system_palette)
            self.settings.setValue("theme/style", theme)
            self.settings.setValue("theme/mode", mode)
            return

        if system_dark:
            colors = {
                "window": "#202124",
                "text": "#e8eaed",
                "base": "#17191c",
                "alternate": "#25282d",
                "button": "#2b2e33",
                "border": "#41464d",
                "muted": "#aab2bd",
                "accent": "#4cc2ff" if theme == "windows11" else "#62a0ea",
                "accent_text": "#10202a",
            }
        else:
            colors = {
                "window": "#f3f5f7" if theme == "windows11" else "#f0f0f0",
                "text": "#202124",
                "base": "#ffffff",
                "alternate": "#f3f5f7",
                "button": "#f5f6f7",
                "border": "#d4d9df",
                "muted": "#606a75",
                "accent": "#0067c0" if theme == "windows11" else "#3584e4",
                "accent_text": "#ffffff",
            }

        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["base"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["alternate"]))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["base"]))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(colors["button"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Link, QColor(colors["accent"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["accent_text"]))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["muted"]))
        app.setPalette(palette)

        if theme == "windows11":
            self.setStyleSheet(
                "QMainWindow, QScrollArea { background: %(window)s; }"
                "QLabel { color: %(text)s; }"
                "QLabel#pageTitle { font-size: 22px; font-weight: 700; }"
                "QLabel#pageSubtitle { color: %(muted)s; padding-bottom: 5px; }"
                "QWidget#collapsibleSection { background: %(base)s; border: 1px solid %(border)s; border-radius: 8px; }"
                "QToolButton#sectionToggle { text-align: left; padding: 12px 14px; border: 0;"
                " border-top-left-radius: 8px; border-top-right-radius: 8px; background: %(alternate)s;"
                " color: %(text)s; font-size: 14px; font-weight: 650; }"
                "QLineEdit, QTextEdit, QComboBox, QTableWidget { background: %(base)s; color: %(text)s;"
                " border: 1px solid %(border)s; border-radius: 6px; padding: 7px;"
                " selection-background-color: %(accent)s; }"
                "QPushButton { background: %(accent)s; color: %(accent_text)s; border: 0; border-radius: 6px;"
                " padding: 8px 13px; font-weight: 600; }"
                "QPushButton:hover { background: %(accent_hover)s; }"
                "QPushButton#secondaryButton { background: %(alternate)s; color: %(text)s; border: 1px solid %(border)s; }"
                "QHeaderView::section { background: %(alternate)s; color: %(text)s; padding: 7px; border: 0; }"
                "QStatusBar { background: %(alternate)s; color: %(text)s; }"
                % {
                    **colors,
                    "accent_hover": "#78d0ff" if system_dark else "#2284d6",
                }
            )
        else:
            self.setStyleSheet("")

        self.settings.setValue("theme/style", theme)
        self.settings.setValue("theme/mode", mode)

    def handle_section_expanded(self, source, expanded: bool):
        if expanded:
            for section in self.sections:
                if section is not source and section.toggle.isChecked():
                    section.toggle.setChecked(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 900
        if narrow == self._responsive_narrow:
            return
        self._responsive_narrow = narrow
        if hasattr(self, "integration_splitter"):
            self.integration_splitter.setOrientation(
                Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
            )
            if narrow:
                target_height = max(280, self.integration_target_panel.sizeHint().height())
                preview_height = 360
                self.integration_target_panel.setMinimumHeight(target_height)
                self.integration_preview_panel.setMinimumHeight(preview_height)
                self.integration_splitter.setMinimumHeight(
                    target_height + preview_height + self.integration_splitter.handleWidth()
                )
                self.integration_splitter.setSizes([target_height, preview_height])
            else:
                self.integration_target_panel.setMinimumHeight(0)
                self.integration_preview_panel.setMinimumHeight(0)
                self.integration_splitter.setMinimumHeight(0)
                self.integration_splitter.setSizes([680, 500])
        if hasattr(self, "csv_preview_splitter"):
            self.csv_preview_splitter.setOrientation(
                Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
            )
            self.csv_preview_splitter.setMaximumWidth(16777215)
            self.csv_preview_splitter.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
            if narrow:
                self.csv_preview_controls_panel.setMaximumWidth(16777215)
                controls_height = max(500, self.csv_preview_controls_panel.sizeHint().height())
                graph_height = 380
                self.csv_preview_controls_panel.setMinimumHeight(controls_height)
                self.csv_preview_graph_panel.setMinimumHeight(graph_height)
                self.csv_preview_splitter.setMinimumHeight(
                    controls_height + graph_height + self.csv_preview_splitter.handleWidth()
                )
                self.csv_preview_splitter.setSizes([controls_height, graph_height])
            else:
                self.csv_preview_controls_panel.setMaximumWidth(380)
                self.csv_preview_controls_panel.setMinimumHeight(0)
                self.csv_preview_graph_panel.setMinimumHeight(0)
                self.csv_preview_splitter.setMinimumHeight(0)
                self.csv_preview_splitter.setSizes([420, 850])
        if hasattr(self, "integration_preview_controls_layout"):
            self.integration_preview_controls_layout.setDirection(
                QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
            )

    def _build_extraction_section(self, parent_layout):
        section = CollapsibleSection("01  Extraction and sample organization", expanded=True)
        parent_layout.addWidget(section)
        form = QGridLayout()
        section.body_layout.addLayout(form)

        self.raw_root_edit = QLineEdit()
        self.raw_root_edit.setPlaceholderText("Folder containing MassLynx .raw runs")
        browse_root = QPushButton("Browse")
        browse_root.setObjectName("secondaryButton")
        browse_root.clicked.connect(self.browse_raw_root)
        form.addWidget(QLabel("RAW root"), 0, 0)
        form.addWidget(self.raw_root_edit, 0, 1)
        form.addWidget(browse_root, 0, 2)

        self.merge_wavelengths_edit = QLineEdit()
        self.merge_wavelengths_edit.setPlaceholderText("e.g. 240, 270")
        form.addWidget(QLabel("Merge wavelengths (nm)"), 1, 0)
        form.addWidget(self.merge_wavelengths_edit, 1, 1, 1, 2)

        self.group_rules_edit = QTextEdit()
        self.group_rules_edit.setPlaceholderText(
            "One rule per line: pattern => folder\n"
            "Examples: *blank* => controls\n"
            r"^(?P<batch>[^_]+)_(\d+)\.raw$ => \g<batch>/rep_\2"
        )
        self.group_rules_edit.setMaximumHeight(88)
        form.addWidget(QLabel("Folder rules"), 2, 0, Qt.AlignmentFlag.AlignTop)
        form.addWidget(self.group_rules_edit, 2, 1, 1, 2)

        mode_layout = QHBoxLayout()
        self.wildcard_radio = QRadioButton("Wildcard")
        self.regex_radio = QRadioButton("Regex")
        self.regex_radio.setChecked(True)
        mode_layout.addWidget(QLabel("Match mode"))
        mode_layout.addWidget(self.wildcard_radio)
        mode_layout.addWidget(self.regex_radio)
        mode_layout.addStretch(1)
        section.body_layout.addLayout(mode_layout)

        preview_toolbar = QHBoxLayout()
        preview_button = QPushButton("Preview samples")
        preview_button.setObjectName("secondaryButton")
        preview_button.clicked.connect(self.refresh_raw_preview)
        self.raw_preview_summary = QLabel("Select a root folder to preview samples.")
        preview_toolbar.addWidget(preview_button)
        preview_toolbar.addWidget(self.raw_preview_summary, 1)
        section.body_layout.addLayout(preview_toolbar)

        self.raw_preview_table = QTableWidget(0, 2)
        self.raw_preview_table.setHorizontalHeaderLabels(["RAW sample", "Destination"])
        self.raw_preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.raw_preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.raw_preview_table.setMaximumHeight(175)
        section.body_layout.addWidget(self.raw_preview_table)

        extract_button = QPushButton("Extract files")
        extract_button.clicked.connect(self.extract_clicked)
        section.body_layout.addWidget(extract_button, 0, Qt.AlignmentFlag.AlignRight)

    def _build_integration_section(self, parent_layout):
        section = CollapsibleSection("02  Peak integration", expanded=False)
        parent_layout.addWidget(section)

        folder_row = QHBoxLayout()
        self.csv_folder_edit = QLineEdit()
        self.csv_folder_edit.setPlaceholderText("Chromatogram CSV folder")
        browse = QPushButton("Browse")
        browse.setObjectName("secondaryButton")
        browse.clicked.connect(self.browse_csv_folder)
        folder_row.addWidget(QLabel("CSV folder"))
        folder_row.addWidget(self.csv_folder_edit, 1)
        folder_row.addWidget(browse)
        section.body_layout.addLayout(folder_row)

        preview_controls = QHBoxLayout()
        self.integration_preview_controls_layout = preview_controls
        self.integration_preview_toggle = QCheckBox("Open sample preview")
        self.integration_preview_toggle.toggled.connect(self.set_integration_preview_visible)
        self.integration_plot_controls = QCheckBox("Plot controls")
        self.integration_preview_combo = QComboBox()
        self.integration_preview_combo.currentIndexChanged.connect(self.update_integration_preview)
        refresh_samples = QPushButton("Refresh samples")
        refresh_samples.setObjectName("secondaryButton")
        refresh_samples.clicked.connect(self.refresh_integration_preview_files)
        preview_controls.addWidget(self.integration_preview_toggle)
        preview_controls.addWidget(self.integration_plot_controls)
        preview_controls.addWidget(QLabel("Sample"))
        preview_controls.addWidget(self.integration_preview_combo, 1)
        self.integration_mz_axis_combo = QComboBox()
        self.integration_mz_axis_combo.addItem("RT", "rt")
        self.integration_mz_axis_combo.addItem("m/z", "mz")
        self.integration_mz_axis_combo.currentIndexChanged.connect(self.update_integration_preview)
        self.integration_mz_scan_combo = QComboBox()
        self.integration_mz_scan_combo.currentIndexChanged.connect(self.update_integration_preview)
        self.integration_mz_axis_combo.setVisible(False)
        self.integration_mz_scan_combo.setVisible(False)
        preview_controls.addWidget(self.integration_mz_axis_combo)
        preview_controls.addWidget(self.integration_mz_scan_combo)
        preview_controls.addWidget(refresh_samples)
        section.body_layout.addLayout(preview_controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.integration_splitter = splitter
        target_panel = QWidget()
        self.integration_target_panel = target_panel
        target_layout = QVBoxLayout(target_panel)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_form = QGridLayout()
        target_form.addWidget(QLabel("Retention time (min)"), 0, 0)
        target_form.addWidget(QLabel("Tolerance - (min)"), 0, 1)
        target_form.addWidget(QLabel("Tolerance + (min)"), 0, 2)
        self.center_edit = QLineEdit()
        self.tol_minus_edit = QLineEdit()
        self.tol_plus_edit = QLineEdit()
        for column, edit in enumerate((self.center_edit, self.tol_minus_edit, self.tol_plus_edit)):
            edit.setPlaceholderText("0.000")
            target_form.addWidget(edit, 1, column)
        add_target_button = QPushButton("Add target")
        add_target_button.setObjectName("secondaryButton")
        add_target_button.clicked.connect(self.add_target)
        target_form.addWidget(add_target_button, 1, 3)
        target_form.addWidget(QLabel("Recent targets"), 2, 0)
        self.recent_target_combo = QComboBox()
        self.recent_target_combo.addItem("Select a saved target", None)
        self.refresh_recent_targets()
        target_form.addWidget(self.recent_target_combo, 2, 1, 1, 2)
        use_recent = QPushButton("Use selected")
        use_recent.setObjectName("secondaryButton")
        use_recent.clicked.connect(self.load_recent_target)
        target_form.addWidget(use_recent, 2, 3)
        target_layout.addLayout(target_form)

        target_row = QHBoxLayout()
        self.target_table = QTableWidget(0, 3)
        self.target_table.setHorizontalHeaderLabels(["Center (min)", "Tol -", "Tol +"])
        self.target_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.target_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.target_table.setMaximumHeight(150)
        target_row.addWidget(self.target_table, 1)
        target_actions = QVBoxLayout()
        delete_target = QPushButton("Delete selected")
        delete_target.setObjectName("secondaryButton")
        delete_target.clicked.connect(self.delete_selected_targets)
        run_integration = QPushButton("Integrate CSVs")
        run_integration.clicked.connect(self.integrate_clicked)
        target_actions.addWidget(delete_target)
        target_actions.addStretch(1)
        target_actions.addWidget(run_integration)
        target_row.addLayout(target_actions)
        target_layout.addLayout(target_row)
        splitter.addWidget(target_panel)

        self.integration_preview_panel = QWidget()
        integration_plot_layout = QVBoxLayout(self.integration_preview_panel)
        integration_plot_layout.setContentsMargins(0, 0, 0, 0)
        self.integration_preview_signal = QComboBox()
        self.integration_preview_signal.currentIndexChanged.connect(self.update_integration_preview)
        integration_plot_layout.addWidget(self.integration_preview_signal)
        self.integration_figure = Figure(figsize=(5, 3.5), dpi=100, constrained_layout=True)
        self.integration_axes = self.integration_figure.add_subplot(111)
        self.integration_canvas = FigureCanvasQTAgg(self.integration_figure)
        self.integration_toolbar = NavigationToolbar2QT(self.integration_canvas, self.integration_preview_panel)
        self.integration_toolbar.setVisible(False)
        self.integration_plot_controls.toggled.connect(self.integration_toolbar.setVisible)
        integration_plot_layout.addWidget(self.integration_toolbar)
        integration_plot_layout.addWidget(self.integration_canvas, 1)
        self.integration_preview_status = QLabel("Choose a sample to preview its integration window.")
        self.integration_preview_status.setWordWrap(True)
        integration_plot_layout.addWidget(self.integration_preview_status)
        self.integration_preview_panel.setMinimumWidth(260)
        self.integration_preview_panel.setVisible(False)
        splitter.addWidget(self.integration_preview_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setHandleWidth(8)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes([300, 360])
        section.body_layout.addWidget(splitter, 1)

    def set_integration_preview_visible(self, visible: bool):
        self.integration_preview_panel.setVisible(visible)
        if visible:
            if self.integration_preview_combo.count() == 0:
                self.refresh_integration_preview_files()
            else:
                self.update_integration_preview()

    def refresh_integration_preview_files(self):
        folder = self.csv_folder_edit.text().strip()
        files = find_chrom_csv_files(folder) if os.path.isdir(folder) else []
        self.integration_preview_combo.blockSignals(True)
        self.integration_preview_combo.clear()
        for sample_name, path in files:
            self.integration_preview_combo.addItem(sample_name, path)
        self.integration_preview_combo.blockSignals(False)
        self.update_integration_preview()

    def update_integration_preview(self, *_args):
        path = self.integration_preview_combo.currentData()
        if not path:
            self.integration_axes.clear()
            self.integration_axes.set_title("Integration sample preview")
            self.integration_canvas.draw_idle()
            self.integration_preview_signal.clear()
            self.integration_mz_axis_combo.setVisible(False)
            self.integration_mz_scan_combo.setVisible(False)
            self.integration_preview_status.setText("No chromatogram CSV samples found.")
            return
        try:
            channels = read_chrom_csv_channels(path)
        except (OSError, csv.Error) as exc:
            self.integration_preview_status.setText(f"Could not read sample: {exc}")
            return
        csv_type = classify_csv_type(path)
        is_mz = csv_type == "m/z"
        self.integration_mz_axis_combo.setVisible(is_mz)
        self.integration_mz_scan_combo.setVisible(is_mz)
        self.integration_preview_signal.blockSignals(True)
        previous_signal = self.integration_preview_signal.currentText()
        self.integration_preview_signal.clear()
        self.integration_preview_signal.addItems([name for name, _, _ in channels])
        if previous_signal in [name for name, _, _ in channels]:
            self.integration_preview_signal.setCurrentText(previous_signal)
        self.integration_preview_signal.blockSignals(False)

        if is_mz:
            try:
                frame = pd.read_csv(path)
                rt_column = find_rt_column(frame)
                self.integration_mz_scan_combo.blockSignals(True)
                self.integration_mz_scan_combo.clear()
                for row_index, rt_value in enumerate(pd.to_numeric(frame[rt_column], errors="coerce")):
                    if pd.notna(rt_value):
                        self.integration_mz_scan_combo.addItem(f"{rt_value:g} min", row_index)
                self.integration_mz_scan_combo.blockSignals(False)
            except (OSError, ValueError, KeyError) as exc:
                self.integration_preview_status.setText(f"Could not load m/z scans: {exc}")
                return

        selected_name = self.integration_preview_signal.currentText()
        selected_channel = next((channel for channel in channels if channel[0] == selected_name), None)
        self.integration_axes.clear()
        if selected_channel is None:
            self.integration_axes.set_title("Integration sample preview")
            self.integration_canvas.draw_idle()
            self.integration_preview_status.setText("Selected sample has no numeric signal columns.")
            return

        _, times, intensities = selected_channel
        if is_mz and self.integration_mz_axis_combo.currentData() == "mz":
            frame = pd.read_csv(path)
            rt_column = find_rt_column(frame)
            scan_index = self.integration_mz_scan_combo.currentData()
            scan_index = 0 if scan_index is None else int(scan_index)
            mass_columns = []
            for column in frame.columns:
                if column == rt_column:
                    continue
                try:
                    mass_columns.append((column, float(str(column).strip())))
                except ValueError:
                    continue
            masses = [mass for _, mass in mass_columns]
            values = [
                pd.to_numeric(pd.Series([frame.iloc[scan_index][column]]), errors="coerce").iloc[0]
                for column, _ in mass_columns
            ]
            spacing = min(
                (abs(right - left) for left, right in zip(masses, masses[1:]) if right != left),
                default=1.0,
            )
            self.integration_axes.bar(masses, values, width=spacing * 0.8, color="#207567")
            rt_value = frame.iloc[scan_index][rt_column]
            self.integration_axes.set_xlabel("m/z")
            self.integration_axes.set_title(f"{self.integration_preview_combo.currentText()} at RT {rt_value:g} min")
            self.integration_axes.grid(True, axis="y", alpha=0.3)
            self.integration_preview_status.setText(f"m/z | Mass spectrum at RT {rt_value:g} min")
            self.integration_canvas.draw_idle()
            return

        if is_mz:
            spacing = min(
                (abs(right - left) for left, right in zip(times, times[1:]) if right != left),
                default=1.0,
            )
            self.integration_axes.bar(times, intensities, width=min(spacing * 0.8, 0.8), color="#207567")
            self.integration_axes.grid(True, axis="y", alpha=0.3)
        else:
            self.integration_axes.plot(times, intensities, color="#207567", linewidth=1.5)
        for center, tol_minus, tol_plus in self.targets:
            self.integration_axes.axvspan(
                center - tol_minus,
                center + tol_plus,
                color="#dc8b36",
                alpha=0.16,
            )
        self.integration_axes.set_xlabel("Retention time (min)")
        ylabel = {
            "DAD": "Absorbance (AU)",
            "TIC": "Total ion current",
            "m/z": "Ion intensity",
        }.get(csv_type, "Signal")
        self.integration_axes.set_ylabel(ylabel)
        self.integration_axes.set_title(self.integration_preview_combo.currentText())
        self.integration_axes.grid(True, alpha=0.3)
        self.integration_canvas.draw_idle()
        self.integration_preview_status.setText(
            f"{csv_type} | {len(times)} points | shaded regions are target windows"
        )

    def _build_template_menu(self, parent_layout):
        self.template_button = QToolButton()
        self.template_button.setText("Templates")
        self.template_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.template_menu = QMenu(self.template_button)
        template_panel = QFrame()
        template_panel.setObjectName("templatePopup")
        template_panel.setMinimumWidth(360)
        template_layout = QVBoxLayout(template_panel)
        template_layout.setContentsMargins(12, 12, 12, 12)
        template_layout.setSpacing(8)
        template_heading = QLabel("Option templates")
        template_heading.setObjectName("templatePopupHeading")
        template_layout.addWidget(template_heading)

        self.template_name_edit = QLineEdit()
        self.template_name_edit.setPlaceholderText("Template name")
        self.template_combo = QComboBox()
        template_layout.addWidget(self.template_name_edit)
        template_layout.addWidget(self.template_combo)

        actions = QHBoxLayout()
        save_button = QPushButton("Save current options")
        save_button.clicked.connect(self.save_template)
        load_button = QPushButton("Load selected")
        load_button.setObjectName("secondaryButton")
        load_button.clicked.connect(self.load_template)
        delete_button = QPushButton("Delete selected")
        delete_button.setObjectName("secondaryButton")
        delete_button.clicked.connect(self.delete_template)
        actions.addWidget(save_button)
        actions.addWidget(load_button)
        actions.addWidget(delete_button)
        template_layout.addLayout(actions)
        template_action = QWidgetAction(self.template_menu)
        template_action.setDefaultWidget(template_panel)
        self.template_menu.addAction(template_action)
        self.template_button.setMenu(self.template_menu)
        parent_layout.addWidget(self.template_button)
        self.refresh_template_combo()

    def refresh_recent_targets(self):
        self.recent_target_combo.blockSignals(True)
        self.recent_target_combo.clear()
        self.recent_target_combo.addItem("Select a saved target", None)
        for target in self.recent_targets:
            if not isinstance(target, list) or len(target) != 3:
                continue
            center, tol_minus, tol_plus = target
            label = f"{center:g} min  (-{tol_minus:g}, +{tol_plus:g})"
            self.recent_target_combo.addItem(label, target)
        self.recent_target_combo.setCurrentIndex(0)
        self.recent_target_combo.blockSignals(False)

    def remember_recent_target(self, target):
        normalized = [float(value) for value in target]
        self.recent_targets = [
            previous for previous in self.recent_targets
            if [float(value) for value in previous] != normalized
        ]
        self.recent_targets.insert(0, normalized)
        self.recent_targets = self.recent_targets[:20]
        self.settings.setValue("recentTargets", json.dumps(self.recent_targets))
        self.refresh_recent_targets()

    def load_recent_target(self):
        target = self.recent_target_combo.currentData()
        if not target:
            return
        self.center_edit.setText(f"{float(target[0]):g}")
        self.tol_minus_edit.setText(f"{float(target[1]):g}")
        self.tol_plus_edit.setText(f"{float(target[2]):g}")

    def refresh_template_combo(self, selected_name=None):
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItems(sorted(self.templates, key=str.casefold))
        if selected_name:
            index = self.template_combo.findText(selected_name)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
        self.template_combo.blockSignals(False)

    def save_template(self):
        name = self.template_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Template name required", "Enter a name for these options.")
            return
        self.templates[name] = {
            "raw_root": self.raw_root_edit.text().strip(),
            "merge_wavelengths": self.merge_wavelengths_edit.text(),
            "folder_rules": self.group_rules_edit.toPlainText(),
            "match_mode": self.current_match_mode(),
            "csv_folder": self.csv_folder_edit.text().strip(),
            "targets": [list(target) for target in self.targets],
        }
        self.settings.setValue("templates", json.dumps(self.templates, ensure_ascii=False))
        self.refresh_template_combo(name)
        self.statusBar().showMessage(f"Saved template: {name}")

    def load_template(self):
        name = self.template_combo.currentText()
        template = self.templates.get(name)
        if not template:
            QMessageBox.warning(self, "No template selected", "Choose a saved template first.")
            return
        self.raw_root_edit.setText(template.get("raw_root", ""))
        self.merge_wavelengths_edit.setText(template.get("merge_wavelengths", ""))
        self.group_rules_edit.setPlainText(template.get("folder_rules", ""))
        self.regex_radio.setChecked(template.get("match_mode", "Regex") == "Regex")
        self.wildcard_radio.setChecked(template.get("match_mode", "Regex") != "Regex")
        self.csv_folder_edit.setText(template.get("csv_folder", ""))

        self.targets = []
        self.target_table.setRowCount(0)
        for target in template.get("targets", []):
            if not isinstance(target, (list, tuple)) or len(target) != 3:
                continue
            values = tuple(float(value) for value in target)
            self.targets.append(values)
            row = self.target_table.rowCount()
            self.target_table.insertRow(row)
            for column, value in enumerate(values):
                self.target_table.setItem(row, column, QTableWidgetItem(f"{value:.3f}"))
        self.plot_folder_edit.setText(self.csv_folder_edit.text())
        if os.path.isdir(self.raw_root_edit.text()):
            self.refresh_raw_preview()
        self.refresh_plot_files()
        self.refresh_integration_preview_files()
        self.statusBar().showMessage(f"Loaded template: {name}")

    def delete_template(self):
        name = self.template_combo.currentText()
        if name not in self.templates:
            return
        del self.templates[name]
        self.settings.setValue("templates", json.dumps(self.templates, ensure_ascii=False))
        self.refresh_template_combo()
        self.statusBar().showMessage(f"Deleted template: {name}")

    def _build_plot_section(self, parent_layout):
        section = CollapsibleSection("03  CSV data preview", expanded=False)
        parent_layout.addWidget(section)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.csv_preview_splitter = splitter
        controls = QWidget()
        self.csv_preview_controls_panel = controls
        controls.setMinimumWidth(220)
        controls.setMaximumWidth(380)
        controls_layout = QFormLayout(controls)
        controls_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        folder_row = QHBoxLayout()
        self.plot_folder_edit = QLineEdit()
        self.plot_folder_edit.setPlaceholderText("CSV folder")
        browse = QPushButton("Browse")
        browse.setObjectName("secondaryButton")
        browse.clicked.connect(self.browse_plot_folder)
        folder_row.addWidget(self.plot_folder_edit, 1)
        folder_row.addWidget(browse)
        controls_layout.addRow("Folder", folder_row)

        self.plot_file_table = QTableWidget(0, 2)
        self.plot_file_table.setColumnCount(3)
        self.plot_file_table.setHorizontalHeaderLabels(["CSV file", "Type", "Subfolder"])
        self.plot_file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.plot_file_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.plot_file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.plot_file_table.setAlternatingRowColors(True)
        self.plot_file_table.setMinimumHeight(180)
        self.plot_file_table.setMaximumHeight(320)
        self.plot_file_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.plot_file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.plot_file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.plot_file_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.plot_file_table.itemSelectionChanged.connect(self.load_plot_csv)
        controls_layout.addRow("CSV files", self.plot_file_table)
        self.plot_signal_combo = QComboBox()
        self.plot_signal_combo.currentIndexChanged.connect(self.update_plot)
        controls_layout.addRow("Signal", self.plot_signal_combo)
        self.plot_mz_axis_label = QLabel("m/z x-axis")
        self.plot_mz_axis_combo = QComboBox()
        self.plot_mz_axis_combo.addItem("Retention time", "rt")
        self.plot_mz_axis_combo.addItem("m/z values", "mz")
        self.plot_mz_axis_combo.currentIndexChanged.connect(self.update_plot)
        controls_layout.addRow(self.plot_mz_axis_label, self.plot_mz_axis_combo)
        self.plot_mz_scan_label = QLabel("RT scan")
        self.plot_mz_scan_combo = QComboBox()
        self.plot_mz_scan_combo.currentIndexChanged.connect(self.update_plot)
        controls_layout.addRow(self.plot_mz_scan_label, self.plot_mz_scan_combo)
        self.plot_mz_axis_label.setVisible(False)
        self.plot_mz_axis_combo.setVisible(False)
        self.plot_mz_scan_label.setVisible(False)
        self.plot_mz_scan_combo.setVisible(False)
        self.plot_all_checkbox = QCheckBox("Show all signals")
        self.plot_all_checkbox.toggled.connect(self.update_plot)
        controls_layout.addRow("Overlay", self.plot_all_checkbox)
        self.plot_controls_checkbox = QCheckBox("Plot controls")
        controls_layout.addRow(self.plot_controls_checkbox)
        reload_button = QPushButton("Reload CSV list")
        reload_button.setObjectName("secondaryButton")
        reload_button.clicked.connect(self.refresh_plot_files)
        controls_layout.addRow(reload_button)
        export_button = QPushButton("Export plot")
        export_button.clicked.connect(self.export_plot_clicked)
        controls_layout.addRow(export_button)
        self.plot_export_dpi = QSpinBox()
        self.plot_export_dpi.setRange(72, 2400)
        self.plot_export_dpi.setSingleStep(100)
        self.plot_export_dpi.setValue(600)
        self.plot_export_dpi.setSuffix(" dpi")
        controls_layout.addRow("Export resolution", self.plot_export_dpi)
        self.plot_status_label = QLabel("Choose a folder to preview chromatograms.")
        self.plot_status_label.setWordWrap(True)
        controls_layout.addRow(self.plot_status_label)

        self.preview_tabs = QTabWidget()
        plot_panel = QWidget()
        self.csv_preview_graph_panel = plot_panel
        plot_layout = QVBoxLayout(plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_canvas = FigureCanvasQTAgg(self.figure)
        self.plot_toolbar = NavigationToolbar2QT(self.plot_canvas, plot_panel)
        self.plot_toolbar.setVisible(False)
        self.plot_controls_checkbox.toggled.connect(self.plot_toolbar.setVisible)
        plot_layout.addWidget(self.plot_toolbar)
        plot_layout.addWidget(self.plot_canvas)
        self.preview_tabs.addTab(plot_panel, "Plot preview")

        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.csv_preview_table = QTableWidget(0, 0)
        self.csv_preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.csv_preview_table.setAlternatingRowColors(True)
        self.csv_preview_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.csv_preview_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.csv_preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.csv_preview_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table_layout.addWidget(self.csv_preview_table)
        self.csv_preview_status = QLabel("Select a CSV file to inspect its rows.")
        table_layout.addWidget(self.csv_preview_status)
        self.preview_tabs.addTab(table_panel, "Table preview")

        splitter.addWidget(controls)
        splitter.addWidget(self.preview_tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setHandleWidth(10)
        splitter.setChildrenCollapsible(False)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes([260, 420])
        splitter.handle(1).setCursor(Qt.CursorShape.SplitHCursor)
        splitter.handle(1).setStyleSheet(
            "QSplitter::handle { background: palette(mid); }"
            "QSplitter::handle:hover { background: palette(highlight); }"
        )
        section.body_layout.addWidget(splitter, 1)

    def browse_raw_root(self):
        folder = QFileDialog.getExistingDirectory(self, "Select root folder with .raw runs")
        if folder:
            self.raw_root_edit.setText(folder)
            self.refresh_raw_preview()

    def refresh_raw_preview(self):
        root = self.raw_root_edit.text().strip()
        if not os.path.isdir(root):
            QMessageBox.warning(self, "Invalid folder", "Select a valid root folder first.")
            return
        rules = self.current_group_rules()
        if rules is None:
            return
        raw_dirs = sorted(iter_raw_dirs(root))
        self.raw_preview_table.setRowCount(0)
        for raw_dir in raw_dirs[:20]:
            raw_name = os.path.basename(raw_dir)
            try:
                destination = match_group(raw_name, rules, self.current_match_mode()) or "(root)"
            except ValueError as exc:
                QMessageBox.warning(self, "Invalid folder rule", str(exc))
                return
            row = self.raw_preview_table.rowCount()
            self.raw_preview_table.insertRow(row)
            self.raw_preview_table.setItem(row, 0, QTableWidgetItem(raw_name))
            self.raw_preview_table.setItem(row, 1, QTableWidgetItem(destination))
        self.raw_preview_summary.setText(f"Found {len(raw_dirs)} samples; previewing {min(20, len(raw_dirs))}.")

    def current_match_mode(self):
        return "Regex" if self.regex_radio.isChecked() else "Wildcard"

    def current_group_rules(self):
        try:
            return parse_group_rules(self.group_rules_edit.toPlainText(), self.current_match_mode())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid folder rule", str(exc))
            return None

    def extract_clicked(self):
        root = self.raw_root_edit.text().strip()
        if not os.path.isdir(root):
            QMessageBox.warning(self, "Invalid folder", "Select a valid root folder first.")
            return
        try:
            merge_wavelengths = parse_merge_wavelengths(self.merge_wavelengths_edit.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid wavelength", str(exc))
            return
        rules = self.current_group_rules()
        if rules is None:
            return
        self.statusBar().showMessage("Extracting RAW files...")
        QApplication.processEvents()
        try:
            chrom_dir = extract_masslynx_folder(
                root,
                status_cb=lambda message: self.statusBar().showMessage(message),
                prec=0,
                merge_wavelengths=merge_wavelengths,
                group_rules=rules,
                match_mode=self.current_match_mode(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Extraction failed", str(exc))
            self.statusBar().showMessage("Extraction failed")
            return
        self.csv_folder_edit.setText(chrom_dir)
        self.plot_folder_edit.setText(chrom_dir)
        self.refresh_plot_files()
        self.refresh_integration_preview_files()
        QMessageBox.information(self, "Extraction complete", f"Chromatograms saved to:\n{chrom_dir}")
        self.statusBar().showMessage(f"Extraction complete: {chrom_dir}")

    def browse_csv_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select chromatogram CSV folder")
        if folder:
            self.csv_folder_edit.setText(folder)
            self.refresh_integration_preview_files()

    def add_target(self):
        try:
            center = float(self.center_edit.text().strip())
            tol_minus = float(self.tol_minus_edit.text().strip())
            tol_plus = float(self.tol_plus_edit.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Invalid target", "Enter numeric values for time and tolerances.")
            return
        if not all(math.isfinite(value) for value in (center, tol_minus, tol_plus)):
            QMessageBox.warning(self, "Invalid target", "Target values must be finite numbers.")
            return
        if tol_minus < 0 or tol_plus < 0 or (tol_minus == 0 and tol_plus == 0):
            QMessageBox.warning(self, "Invalid target", "Tolerances must be non-negative and at least one must be positive.")
            return
        self.targets.append((center, tol_minus, tol_plus))
        self.remember_recent_target((center, tol_minus, tol_plus))
        row = self.target_table.rowCount()
        self.target_table.insertRow(row)
        for column, value in enumerate((center, tol_minus, tol_plus)):
            self.target_table.setItem(row, column, QTableWidgetItem(f"{value:.3f}"))
        self.center_edit.clear()
        self.tol_minus_edit.clear()
        self.tol_plus_edit.clear()
        if self.integration_preview_toggle.isChecked():
            self.update_integration_preview()

    def delete_selected_targets(self):
        selected = sorted({index.row() for index in self.target_table.selectionModel().selectedRows()}, reverse=True)
        for row in selected:
            self.target_table.removeRow(row)
            del self.targets[row]
        if selected and self.integration_preview_toggle.isChecked():
            self.update_integration_preview()

    def integrate_clicked(self):
        csv_folder = self.csv_folder_edit.text().strip()
        if not os.path.isdir(csv_folder):
            QMessageBox.warning(self, "Invalid folder", "Select a valid chromatogram CSV folder.")
            return
        if not self.targets:
            QMessageBox.warning(self, "No targets", "Add at least one integration target.")
            return
        files = find_chrom_csv_files(csv_folder)
        if not files:
            QMessageBox.warning(self, "No CSVs", "No chromatogram CSVs were found.")
            return

        headers = ["Sample"] + [f"{center:.3f} min" for center, _, _ in self.targets]
        rows = []
        processed = 0
        for sample_name, path in files:
            try:
                channels = read_chrom_csv_channels(path)
                if not channels:
                    raise ValueError("Empty or invalid chromatogram")
                for channel_name, times, intensities in channels:
                    areas = [
                        integrate_peak_asym(times, intensities, center, tol_minus, tol_plus, smooth_win=5)
                        for center, tol_minus, tol_plus in self.targets
                    ]
                    name = sample_name
                    if len(channels) > 1:
                        name = f"{sample_name}_{channel_name.replace(' ', '')}"
                    rows.append({"Sample": name, **{header: f"{area:.6g}" for header, area in zip(headers[1:], areas)}})
                processed += 1
            except Exception:
                rows.append({"Sample": sample_name, **{header: "0" for header in headers[1:]}})

        output_path = os.path.join(csv_folder, "00_evaluated_data.csv")
        try:
            with open(output_path, "w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            QMessageBox.critical(self, "Write failed", str(exc))
            return
        QMessageBox.information(self, "Integration complete", f"Integrated {processed} file(s).\nSaved to:\n{output_path}")
        self.statusBar().showMessage(f"Integration complete: {processed}/{len(files)} files")

    def browse_plot_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder with chromatogram CSVs")
        if folder:
            self.plot_folder_edit.setText(folder)
            self.refresh_plot_files()

    def refresh_plot_files(self):
        folder = self.plot_folder_edit.text().strip()
        self.plot_file_table.setRowCount(0)
        if os.path.isdir(folder):
            for sample_name, path in find_chrom_csv_files(folder, include_evaluated=True):
                row = self.plot_file_table.rowCount()
                self.plot_file_table.insertRow(row)
                filename = os.path.basename(path)
                subfolder = os.path.dirname(sample_name) or "(root)"
                filename_item = QTableWidgetItem(filename)
                filename_item.setData(Qt.ItemDataRole.UserRole, path)
                filename_item.setToolTip(path)
                self.plot_file_table.setItem(row, 0, filename_item)
                self.plot_file_table.setItem(row, 1, QTableWidgetItem(classify_csv_type(path)))
                self.plot_file_table.setItem(row, 2, QTableWidgetItem(subfolder))
        if self.plot_file_table.rowCount():
            self.plot_file_table.selectRow(0)
            self.load_plot_csv()
        else:
            self.plot_dataframe = None
            self.plot_csv_type = "CSV"
            self.plot_signal_combo.clear()
            self.csv_preview_table.clear()
            self.csv_preview_table.setRowCount(0)
            self.csv_preview_table.setColumnCount(0)
            self.csv_preview_status.setText("Select a CSV file to inspect its rows.")
            self.plot_status_label.setText("No CSV data files found.")
            self.update_plot()

    def load_plot_csv(self, *_args):
        selected_rows = self.plot_file_table.selectionModel().selectedRows()
        path = None
        if selected_rows:
            item = self.plot_file_table.item(selected_rows[0].row(), 0)
            path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not path:
            self.plot_dataframe = None
            self.plot_csv_type = "CSV"
            self.csv_preview_table.clear()
            self.csv_preview_table.setRowCount(0)
            self.csv_preview_table.setColumnCount(0)
            self.csv_preview_status.setText("Select a CSV file to inspect its rows.")
            self.update_plot()
            return
        try:
            frame = pd.read_csv(path)
        except Exception as exc:
            self.plot_dataframe = None
            self.plot_csv_type = "CSV"
            self.csv_preview_table.clear()
            self.csv_preview_table.setRowCount(0)
            self.csv_preview_table.setColumnCount(0)
            self.csv_preview_status.setText("No table preview available for this file.")
            self.plot_status_label.setText(f"Could not read CSV: {exc}")
            self.update_plot()
            return
        self.plot_dataframe = frame
        type_item = self.plot_file_table.item(selected_rows[0].row(), 1)
        self.plot_csv_type = classify_csv_type(path, frame.columns)
        if type_item is not None:
            type_item.setText(self.plot_csv_type)
        if self.plot_csv_type == "Integrated areas":
            sample_column = next(
                (column for column in frame.columns if str(column).strip().casefold() == "sample"),
                frame.columns[0] if len(frame.columns) else None,
            )
            signal_columns = [column for column in frame.columns if column != sample_column]
        else:
            rt_column = find_rt_column(frame) if len(frame.columns) else None
            signal_columns = [column for column in frame.columns if column != rt_column]
        self.plot_signal_combo.blockSignals(True)
        self.plot_signal_combo.clear()
        self.plot_signal_combo.addItems([str(column) for column in signal_columns])
        self.plot_signal_combo.blockSignals(False)
        self.plot_signal_combo.setEnabled(True)
        self.plot_all_checkbox.setEnabled(True)

        is_mz = self.plot_csv_type == "m/z"
        self.plot_mz_axis_label.setVisible(is_mz)
        self.plot_mz_axis_combo.setVisible(is_mz)
        self.plot_mz_scan_label.setVisible(is_mz)
        self.plot_mz_scan_combo.setVisible(is_mz)
        self.plot_mz_axis_combo.blockSignals(True)
        self.plot_mz_scan_combo.blockSignals(True)
        self.plot_mz_scan_combo.clear()
        if is_mz and len(frame.columns):
            rt_column = find_rt_column(frame)
            for row_index, rt_value in enumerate(pd.to_numeric(frame[rt_column], errors="coerce")):
                if pd.notna(rt_value):
                    self.plot_mz_scan_combo.addItem(f"{rt_value:g} min", row_index)
            self.plot_mz_axis_combo.setCurrentIndex(self.plot_mz_axis_combo.findData("rt"))
        self.plot_mz_scan_combo.blockSignals(False)
        self.plot_mz_axis_combo.blockSignals(False)
        self.populate_csv_preview(frame)
        self.update_plot()

    def populate_csv_preview(self, frame: pd.DataFrame):
        max_preview_rows = 200
        max_preview_columns = 60
        preview = frame.iloc[:max_preview_rows, :max_preview_columns]
        self.csv_preview_table.clear()
        self.csv_preview_table.setRowCount(len(preview))
        self.csv_preview_table.setColumnCount(len(preview.columns))
        self.csv_preview_table.setHorizontalHeaderLabels([str(column) for column in preview.columns])
        self.csv_preview_table.setColumnWidth(0, 150)
        for row_index, row in enumerate(preview.itertuples(index=False, name=None)):
            for column_index, value in enumerate(row):
                text = "" if pd.isna(value) else str(value)
                self.csv_preview_table.setItem(row_index, column_index, QTableWidgetItem(text))
        truncated = len(frame) > max_preview_rows or len(frame.columns) > max_preview_columns
        if truncated:
            self.csv_preview_status.setText(
                f"Showing {len(preview)} of {len(frame)} rows and "
                f"{len(preview.columns)} of {len(frame.columns)} columns."
            )
        else:
            self.csv_preview_status.setText(f"{len(frame)} rows x {len(frame.columns)} columns")

    def update_plot(self, *_args):
        self.axes.clear()
        frame = self.plot_dataframe
        if frame is None or frame.empty or len(frame.columns) < 2:
            self.axes.set_title("CSV data preview")
            self.plot_canvas.draw_idle()
            return

        if self.plot_csv_type == "Integrated areas":
            sample_column = next(
                (column for column in frame.columns if str(column).strip().casefold() == "sample"),
                frame.columns[0],
            )
            area_columns = [column for column in frame.columns if column != sample_column]
            selected = area_columns if self.plot_all_checkbox.isChecked() else [self.plot_signal_combo.currentText()]
            selected = [column for column in selected if column in area_columns]
            labels = frame[sample_column].fillna("").astype(str).tolist()
            positions = list(range(len(labels)))
            width = 0.8 / max(len(selected), 1)
            for series_index, column in enumerate(selected):
                values = pd.to_numeric(frame[column], errors="coerce").fillna(0).tolist()
                offset = (series_index - (len(selected) - 1) / 2) * width
                self.axes.bar(
                    [position + offset for position in positions],
                    values,
                    width=width,
                    label=str(column),
                )
            tick_stride = max(1, len(labels) // 40)
            tick_positions = positions[::tick_stride]
            self.axes.set_xticks(tick_positions, [labels[index] for index in tick_positions], rotation=60, ha="right")
            self.axes.set_xlabel("Sample file")
            self.axes.set_ylabel("Integrated area")
            self.axes.set_title(self.current_plot_title())
            self.axes.grid(True, axis="y", alpha=0.3)
            if len(selected) > 1:
                self.axes.legend(loc="best")
            self.plot_status_label.setText(
                f"Showing {len(selected)} integrated area series across {len(labels)} samples."
            )
            self.plot_canvas.draw_idle()
            return

        if self.plot_csv_type == "m/z":
            rt_column = find_rt_column(frame)
            mass_columns = []
            for column in frame.columns:
                if column == rt_column:
                    continue
                try:
                    mass_columns.append((column, float(str(column).strip())))
                except ValueError:
                    continue
            if not mass_columns:
                self.plot_status_label.setText("No numeric m/z columns are available.")
                self.plot_canvas.draw_idle()
                return

            x_mode = self.plot_mz_axis_combo.currentData() or "rt"
            self.plot_signal_combo.setEnabled(x_mode == "rt")
            self.plot_all_checkbox.setEnabled(x_mode == "rt")
            self.axes.set_ylabel("Ion intensity")
            self.axes.set_title(self.current_plot_title())
            if x_mode == "mz":
                scan_index = self.plot_mz_scan_combo.currentData()
                if scan_index is None:
                    scan_index = 0
                scan_index = int(scan_index)
                masses = [mass for _, mass in mass_columns]
                values = [
                    pd.to_numeric(pd.Series([frame.iloc[scan_index][column]]), errors="coerce").iloc[0]
                    for column, _ in mass_columns
                ]
                spacing = min(
                    (abs(right - left) for left, right in zip(masses, masses[1:]) if right != left),
                    default=1.0,
                )
                self.axes.bar(masses, values, width=spacing * 0.8)
                rt_value = frame.iloc[scan_index][rt_column]
                self.axes.set_xlabel("m/z")
                self.axes.set_title(f"{self.current_plot_title()} at RT {rt_value:g} min")
                self.axes.grid(True, axis="y", alpha=0.3)
                self.plot_status_label.setText(
                    f"Mass spectrum at RT {rt_value:g} min | {len(masses)} m/z bins"
                )
            else:
                signal_columns = [column for column, _ in mass_columns]
                selected_columns = (
                    signal_columns
                    if self.plot_all_checkbox.isChecked()
                    else [self.plot_signal_combo.currentText()]
                )
                selected_columns = [column for column in selected_columns if column in signal_columns]
                x_values = pd.to_numeric(frame[rt_column], errors="coerce")
                step = min(
                    (abs(right - left) for left, right in zip(
                        sorted(x_values.dropna().unique()),
                        sorted(x_values.dropna().unique())[1:],
                    ) if right != left),
                    default=1.0,
                )
                group_width = min(step * 0.8, 0.8) / max(len(selected_columns), 1)
                plotted = 0
                for signal_index, column in enumerate(selected_columns):
                    y_values = pd.to_numeric(frame[column], errors="coerce")
                    valid = x_values.notna() & y_values.notna()
                    if not valid.any():
                        continue
                    offset = (signal_index - (len(selected_columns) - 1) / 2) * group_width
                    self.axes.bar(
                        x_values[valid] + offset,
                        y_values[valid],
                        width=group_width,
                        label=str(column),
                    )
                    plotted += 1
                self.axes.set_xlabel("Retention time (min)")
                self.axes.grid(True, axis="y", alpha=0.3)
                self.plot_status_label.setText(
                    f"Showing {plotted} m/z bar series; legend hidden for readability."
                    if self.plot_all_checkbox.isChecked()
                    else f"Showing m/z {self.plot_signal_combo.currentText()} over retention time."
                )
            self.plot_canvas.draw_idle()
            return

        rt_column = find_rt_column(frame)
        signal_columns = [column for column in frame.columns if column != rt_column]
        selected = signal_columns if self.plot_all_checkbox.isChecked() else [self.plot_signal_combo.currentText()]
        x_values = pd.to_numeric(frame[rt_column], errors="coerce")
        plotted = 0
        for column in selected:
            if column not in signal_columns:
                continue
            y_values = pd.to_numeric(frame[column], errors="coerce")
            valid = x_values.notna() & y_values.notna()
            if valid.any():
                self.axes.plot(x_values[valid], y_values[valid], label=str(column), linewidth=1.5)
                plotted += 1
        self.axes.set_xlabel(str(rt_column))
        if self.plot_csv_type == "DAD":
            self.axes.set_ylabel("Absorbance (AU)")
        elif self.plot_csv_type == "TIC":
            self.axes.set_ylabel("Total ion current")
        elif self.plot_csv_type == "m/z":
            self.axes.set_ylabel("Ion intensity")
        else:
            self.axes.set_ylabel("Signal")
        self.axes.set_title(self.current_plot_title())
        self.axes.grid(True, alpha=0.3)
        hide_mz_legend = self.plot_csv_type == "m/z" and self.plot_all_checkbox.isChecked() and plotted > 1
        if plotted > 1 and not hide_mz_legend:
            self.axes.legend(loc="best")
        if plotted == 0:
            status = "No numeric signal data available."
        elif hide_mz_legend:
            status = f"Showing {plotted} m/z traces; legend hidden for readability."
        else:
            status = f"Showing {plotted} {self.plot_csv_type} trace(s)."
        self.plot_status_label.setText(status)
        self.plot_canvas.draw_idle()

    def current_plot_title(self):
        selected_rows = self.plot_file_table.selectionModel().selectedRows()
        if not selected_rows:
            return "CSV data preview"
        row = selected_rows[0].row()
        filename = self.plot_file_table.item(row, 0).text()
        subfolder = self.plot_file_table.item(row, 2).text()
        return filename if subfolder == "(root)" else os.path.join(subfolder, filename)

    def export_plot_clicked(self):
        if not self.axes.has_data():
            QMessageBox.warning(self, "Nothing to export", "Load a CSV and select at least one signal first.")
            return
        filters = "PNG image (*.png);;SVG image (*.svg)"
        filename, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export chromatogram plot",
            "chromatogram.png",
            filters,
        )
        if not filename:
            return
        if not os.path.splitext(filename)[1]:
            extension = ".svg" if "SVG" in selected_filter else ".png"
            filename += extension
        try:
            self.export_plot_to(filename, self.plot_export_dpi.value())
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Plot exported: {filename}")

    def export_plot_to(self, filename: str, dpi: int = 600):
        extension = os.path.splitext(filename)[1].lower()
        if extension not in {".png", ".svg"}:
            raise ValueError("Choose PNG or SVG format.")
        if not 72 <= dpi <= 2400:
            raise ValueError("Export DPI must be between 72 and 2400.")
        self.figure.savefig(filename, format=extension[1:], bbox_inches="tight", dpi=dpi)


# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    qt_app = QApplication(sys.argv)
    window = MassLynxWindow()
    window.show()
    sys.exit(qt_app.exec())
