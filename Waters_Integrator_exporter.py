#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Waters Exporter
---------------------
Dedicated standalone tool and GUI for exporting MassLynx / chromatographic
CSV datasets into publication-ready plotting projects (Veusz, etc.).

Note on KDE LabPlot:
KDE LabPlot support has been deprecated in favor of the modernized,
robust Veusz format, but legacy LabPlot exports remain accessible
as fallback methods.

Features:
- Standalone PySide6 GUI matching the MassLynx Integrator workspace design
- Qt default and Windows 11 styling with System, Light, and Dark mode palettes
- Dataset scanner with automatic classification (DAD, TIC, m/z, Integrated areas)
- Selective dataset export via interactive checkboxes
- Dataset live data preview tab for instant inspection
- Modernized, publication-ready Veusz document generator (.vsz, v3.6+ standard)
- Self-contained embedded data (SetData / SetDataText) or linked CSV files (ImportFileCSV)
- Automatic axis scaling, formatting, subtle grids, and curated color palettes
- Optional Launch in Veusz action if Veusz executable is available
- Runtime debugging console drawer with stdout/stderr capture and diagnostics
- CLI arguments support for headless batch automation
"""

import os
import sys
import re
import csv
import json
import gzip
import shutil
import datetime
import argparse
import subprocess
import warnings
import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import List, Optional, Tuple, Dict, Any

import pandas as pd
from PySide6.QtCore import QObject, QSettings, Qt, Signal, QTime, QUrl, QSize
from PySide6.QtGui import QColor, QPalette, QDesktopServices, QTextCursor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStyleFactory,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


# Curated high-contrast scientific plotting palette for chromatographic traces
VEUSZ_PALETTE = [
    "#1f77b4",  # Blue
    "#d62728",  # Red
    "#2ca02c",  # Green
    "#ff7f0e",  # Orange
    "#9467bd",  # Purple
    "#8c564b",  # Brown
    "#e377c2",  # Pink
    "#17becf",  # Cyan
    "#bcbd22",  # Olive
    "#7f7f7f",  # Gray
]


# ---------------------------
# Utility helpers & logging
# ---------------------------

def console_log(message: str, category: str = "VEUSZ", level: str = "INFO"):
    """
    Emit a structured diagnostic log message to stdout.
    Automatically captured by QtConsoleStream into the runtime console drawer.
    """
    now_str = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    cat_tag = f"[{category}]" if category else ""
    print(f"[{now_str}] [{level.upper():<5}] {cat_tag} {message}", flush=True)


def open_in_explorer(path: str) -> bool:
    """Open a file or directory in the system's native file explorer."""
    if not path or not str(path).strip():
        console_log("open_in_explorer called with empty path", category="EXPLORER", level="WARN")
        return False
    clean_path = str(path).strip().strip('"').strip("'")
    target_path = os.path.abspath(clean_path)

    if not os.path.exists(target_path):
        parent = os.path.dirname(target_path)
        if os.path.exists(parent):
            target_path = parent
        else:
            console_log(f"Cannot open in explorer; path not found: {clean_path}", category="EXPLORER", level="WARN")
            return False

    console_log(f"Opening explorer view for: {target_path}", category="EXPLORER", level="INFO")
    if sys.platform == "win32":
        try:
            if os.path.isfile(target_path):
                subprocess.Popen(["explorer", f"/select,{os.path.normpath(target_path)}"])
            else:
                os.startfile(os.path.normpath(target_path))
            return True
        except Exception as exc:
            console_log(f"Win32 explorer open failed: {exc}", category="EXPLORER", level="WARN")

    try:
        folder = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)
        url = QUrl.fromLocalFile(folder)
        if QDesktopServices.openUrl(url):
            return True
    except Exception as exc:
        console_log(f"QDesktopServices openUrl failed: {exc}", category="EXPLORER", level="WARN")

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", target_path])
            return True
        elif sys.platform.startswith("linux"):
            folder = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)
            subprocess.Popen(["xdg-open", folder])
            return True
    except Exception as exc:
        console_log(f"Subprocess desktop opener failed: {exc}", category="EXPLORER", level="WARN")

    return False


def find_veusz_executable() -> Optional[str]:
    """Look for Veusz binary on system PATH or standard installation directories."""
    which_path = shutil.which("veusz")
    if which_path:
        return which_path

    if sys.platform == "win32":
        candidates = [
            os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Veusz", "veusz.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Veusz", "veusz.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Veusz", "veusz.exe"),
            os.path.join(sys.prefix, "Scripts", "veusz.exe"),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
    return None


def sanitize_filename(name: str, default: str = "sample") -> str:
    """Remove problematic characters for filenames and dataset IDs."""
    name = re.sub(r'[\\/*?:"<>|]+', "_", name.strip())
    name = name.strip(" .")
    return name or default


def sanitize_veusz_identifier(name: str, default: str = "dataset") -> str:
    """Format string as a valid Veusz identifier (letters, digits, underscores)."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", str(name).strip()).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"ds_{cleaned}" if cleaned else default
    return cleaned


# Default expressive internal axis names adhering to the 'axistype_valuetypename' convention
DEFAULT_AXIS_NAMING = {
    "barchart_x": "x_sampleid",
    "barchart_y": "y_area",
    "dad_x": "x_rt",
    "dad_y": "y_absorbance",
    "tic_x": "x_rt",
    "tic_y": "y_relint",
    "mz_chrom_x": "x_rt",
    "mz_spectrum_x": "x_mz",
    "mz_y": "y_relint",
    "csv_x": "x_rt",
    "csv_y": "y_signal",
}


def get_expressive_axis_names(
    csv_type: str,
    x_col: str = "",
    is_bar_chart: bool = False,
    custom_config: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    Determine expressive axis names following the 'axistype_valuetypename' scheme.
    Examples:
      - Bar chart: ('x_sampleid', 'y_area')
      - DAD: ('x_rt', 'y_absorbance')
      - TIC: ('x_rt', 'y_relint')
      - m/z chromatogram: ('x_rt', 'y_relint')
      - m/z spectrum: ('x_mz', 'y_relint')
    """
    cfg = custom_config or {}

    def get_cfg(key: str, default_val: str) -> str:
        val = cfg.get(key)
        if isinstance(val, str) and val.strip():
            return sanitize_veusz_identifier(val.strip(), default=default_val)
        return default_val

    if is_bar_chart or csv_type == "Integrated areas":
        x_name = get_cfg("barchart_x", "x_sampleid")
        y_name = get_cfg("barchart_y", "y_area")
        return x_name, y_name

    x_lower = str(x_col).strip().casefold()
    is_mz_x = ("mz" in x_lower or "m/z" in x_lower or "mass" in x_lower)

    if csv_type == "DAD":
        x_name = get_cfg("dad_x", "x_rt")
        y_name = get_cfg("dad_y", "y_absorbance")
    elif csv_type == "TIC":
        x_name = get_cfg("tic_x", "x_rt")
        y_name = get_cfg("tic_y", "y_relint")
    elif csv_type == "m/z":
        if is_mz_x:
            x_name = get_cfg("mz_spectrum_x", "x_mz")
        else:
            x_name = get_cfg("mz_chrom_x", "x_rt")
        y_name = get_cfg("mz_y", "y_relint")
    else:  # Generic CSV
        if any(term in x_lower for term in ("rt", "time", "retention")):
            x_name = get_cfg("csv_x", "x_rt")
        elif is_mz_x:
            x_name = get_cfg("mz_spectrum_x", "x_mz")
        elif any(term in x_lower for term in ("sample", "id", "name")):
            x_name = get_cfg("barchart_x", "x_sampleid")
        else:
            clean_col = sanitize_veusz_identifier(str(x_col), default="val")
            x_name = f"x_{clean_col}"
        y_name = get_cfg("csv_y", "y_signal")

    return x_name, y_name


def get_axis_label(axis_name: str, csv_type: str = "", default_label: str = "") -> str:
    """Return expressive human-readable label for an axis based on its name and dataset context."""
    axis_name_lower = str(axis_name).strip().casefold()
    if axis_name_lower == "x_sampleid":
        return "Sample"
    if axis_name_lower == "x_rt":
        return "Retention time (min)"
    if axis_name_lower == "x_mz":
        return "m/z"
    if axis_name_lower == "y_area":
        return "Integrated Peak Area"
    if axis_name_lower == "y_absorbance":
        return "Absorbance (AU)"
    if axis_name_lower in ("y_relint", "y_relative_intensity"):
        return "Relative Intensity"
    if axis_name_lower in ("y_tic", "y_total_ion_current"):
        return "Total Ion Current"
    if axis_name_lower in ("y_intensity", "y_int"):
        return "Intensity"
    if default_label:
        return default_label
    if axis_name_lower.startswith("x_"):
        return axis_name_lower[2:].replace("_", " ").title()
    if axis_name_lower.startswith("y_"):
        return axis_name_lower[2:].replace("_", " ").title()
    return "Signal"


def find_rt_column(pdf: pd.DataFrame) -> str:
    """Find retention time column, prioritizing standard RT designations."""
    for col in pdf.columns:
        c_lower = str(col).strip().casefold()
        if c_lower.startswith("rt") or "retention" in c_lower:
            return col
    for col in pdf.columns:
        c_lower = str(col).strip().casefold()
        if c_lower.startswith("time") or c_lower == "t" or "min" in c_lower:
            return col
    return pdf.columns[0]


def find_chrom_csv_files(root: str, include_evaluated: bool = True) -> List[Tuple[str, str]]:
    """Find chromatogram CSVs recursively, retaining subfolders in sample names."""
    files = []
    if not os.path.isdir(root):
        return files
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
    """Identify MassLynx DAD, TIC, m/z, or integrated area CSVs from names, paths, and headers."""
    try:
        if not path:
            return "CSV"
        clean_path = str(path).replace("\\", "/")
        filename = os.path.basename(clean_path).casefold()
        path_parts = {part.casefold() for part in clean_path.split("/")}

        # 1. Integrated / Evaluated Area tables
        if (
            filename == "00_evaluated_data.csv"
            or "evaluated" in filename
            or "integrated" in filename
            or "quant" in filename
            or "peak_table" in filename
            or "area" in filename
        ):
            return "Integrated areas"

        # 2. Filename & Path-based detection for MS
        if filename.endswith("_mz.csv") or "mz" in path_parts or "m/z" in path_parts:
            return "m/z"
        if filename.endswith("_tic.csv") or "tic" in path_parts or "_tic_" in filename:
            return "TIC"

        # 3. Read headers if not provided
        if headers is None:
            if not os.path.isfile(path):
                return "CSV"
            try:
                with open(path, "r", newline="", encoding="utf-8-sig") as source:
                    first_line = source.readline()
                    for delim in (",", ";", "\t"):
                        if delim in first_line:
                            headers = [col.strip().strip('"').strip("'") for col in first_line.split(delim)]
                            break
                    if headers is None:
                        headers = [first_line.strip()] if first_line else []
            except (OSError, UnicodeError, csv.Error):
                try:
                    with open(path, "r", newline="", encoding="latin-1") as source:
                        first_line = source.readline()
                        for delim in (",", ";", "\t"):
                            if delim in first_line:
                                headers = [col.strip().strip('"').strip("'") for col in first_line.split(delim)]
                                break
                            if headers is None:
                                headers = [first_line.strip()] if first_line else []
                except Exception:
                    headers = []

        normalized_headers = [str(h).strip().casefold() for h in (headers or [])]

        # 4. Check for Integrated areas from column structure
        if normalized_headers and normalized_headers[0] in {
            "sample", "sample name", "sample_name", "samplename", "name", "sample id", "sample_id"
        }:
            has_rt = any(h.startswith("rt") or "retention" in h or h == "time" for h in normalized_headers)
            if not has_rt:
                return "Integrated areas"

        # 5. Check TIC from headers
        if any(h in {"tic", "total ion current"} or "tic" in h for h in normalized_headers):
            return "TIC"

        # 6. Check m/z from column names or numeric mass channels
        if any("m/z" in h or h == "mz" for h in normalized_headers):
            return "m/z"

        numeric_signals = sum(
            bool(re.fullmatch(r"\d+(?:\.\d+)?", h))
            for h in normalized_headers[1:]
        )
        if numeric_signals >= 2:
            return "m/z"

        # 7. Check DAD / UV from filename, paths, and headers
        if (
            filename.endswith("_nm.csv")
            or "nm" in filename
            or "dad" in path_parts
            or "uv" in path_parts
            or "pda" in path_parts
            or any(
                h == "absorbance"
                or "absorbance" in h
                or re.search(r"\d+\s*nm", h)
                for h in normalized_headers
            )
        ):
            return "DAD"

        # 8. Check if inside chromatographic subfolder
        if "chrom" in path_parts:
            return "DAD"

        return "CSV"
    except Exception:
        return "CSV"


# ---------------------------
# Modern Veusz Export Engine
# ---------------------------

def generate_veusz_vsz_project(
    csv_folder: str,
    output_path: str,
    add_plots: bool = True,
    embed_data: bool = True,
    page_width: str = "18cm",
    page_height: str = "11cm",
    csv_files: Optional[List[Tuple[str, str]]] = None,
    axis_naming_config: Optional[Dict[str, Any]] = None,
    status_cb=None,
) -> str:
    """
    Generate a modern, publication-quality Veusz project document (.vsz).

    Adheres to the latest Veusz 3.x+ standards:
    - Clean hierarchical widget tree (`page` -> `graph` -> `axis` -> `xy` -> `key`)
    - Expressive internal naming adhering to 'axistype_valuetypename' (e.g. x_sampleid, y_area, x_rt, y_absorbance, y_relint, x_mz)
    - Precise Cartesian formatting with non-distorting publication dimensions
    - Self-contained embedded data via `SetData` (numeric) and `SetDataText` (categorical)
    - Subdued dotted gridlines and curated scientific line colors
    - Automatic detector label assignments (Absorbance in AU, TIC, m/z, Area)
    """
    csv_folder = os.path.abspath(csv_folder)
    console_log(f"Generating modern Veusz project: '{output_path}' (source: '{csv_folder}', embed={embed_data})", category="VEUSZ")
    if csv_files is None:
        csv_files = find_chrom_csv_files(csv_folder, include_evaluated=True)
    if not csv_files:
        raise ValueError(f"No CSV files found in {csv_folder}")

    if not output_path.casefold().endswith(".vsz"):
        output_path = f"{output_path}.vsz"

    proj_name = os.path.splitext(os.path.basename(output_path))[0] or "Waters_Chromatography"

    lines = [
        "# Veusz saved document (version 3.6)",
        "# -*- coding: utf-8 -*-",
        f"# Document: {proj_name}",
        f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "# Engine: MassLynx Waters Python Exporter",
        "",
    ]

    datasets_added = 0
    pages_added = 0

    for idx, (sample_name, fpath) in enumerate(csv_files):
        if status_cb:
            status_cb(f"Exporting sample [{idx+1}/{len(csv_files)}]: {sample_name}")

        safe_name = sanitize_filename(sample_name.replace("/", "_").replace("\\", "_"))
        safe_prefix = sanitize_veusz_identifier(safe_name, default=f"sample_{idx}")

        try:
            df = pd.read_csv(fpath)
        except Exception as exc:
            console_log(f"Skipping sample '{sample_name}': cannot read CSV ({exc})", category="VEUSZ", level="WARN")
            continue
        if df.empty or len(df.columns) < 2:
            continue

        csv_type = classify_csv_type(fpath, df.columns)
        is_bar_chart = (csv_type == "Integrated areas")

        if is_bar_chart:
            sample_col = next(
                (c for c in df.columns if str(c).strip().casefold() in ("sample", "sample name", "sample_name", "samplename", "name")),
                df.columns[0]
            )
            x_col = sample_col
            y_cols = [c for c in df.columns if c != sample_col]
            x_type = "text"
        else:
            x_col = find_rt_column(df)
            y_cols = [c for c in df.columns if c != x_col]
            x_type = "double"

        clean_x = sanitize_veusz_identifier(str(x_col), default="x")
        ds_x_name = f"{safe_prefix}_{clean_x}"

        lines.append(f"# ========================================================")
        lines.append(f"# Sample {idx+1}: {sample_name} ({csv_type})")
        lines.append(f"# ========================================================")

        # Ingestion of datasets
        if embed_data:
            if x_type == "text":
                text_vals = [str(v).replace("\\", "\\\\").replace("'", "\\'") for v in df[x_col].fillna("").tolist()]
                text_vals_str = ", ".join(f"'{v}'" for v in text_vals)
                lines.append(f"SetDataText('{ds_x_name}', [{text_vals_str}])")
            else:
                x_vals = pd.to_numeric(df[x_col], errors="coerce").replace([float('inf'), float('-inf')], 0.0).fillna(0.0).tolist()
                x_vals_str = ", ".join(f"{v:.6g}" if isinstance(v, float) else str(v) for v in x_vals)
                lines.append(f"SetData('{ds_x_name}', [{x_vals_str}])")

            for y_col in y_cols:
                clean_y = sanitize_veusz_identifier(str(y_col), default="y")
                ds_y_name = f"{safe_prefix}_{clean_y}"
                y_vals = pd.to_numeric(df[y_col], errors="coerce").replace([float('inf'), float('-inf')], 0.0).fillna(0.0).tolist()
                y_vals_str = ", ".join(f"{v:.6g}" if isinstance(v, float) else str(v) for v in y_vals)
                lines.append(f"SetData('{ds_y_name}', [{y_vals_str}])")
        else:
            abs_csv = os.path.abspath(fpath).replace("\\", "/")
            lines.append(f"ImportFileCSV('{abs_csv}', linked=True, dsprefix='{safe_prefix}_')")

        datasets_added += 1

        # Determine expressive axis names according to the 'axistype_valuetypename' scheme
        x_axis_name, y_axis_name = get_expressive_axis_names(
            csv_type=csv_type,
            x_col=str(x_col),
            is_bar_chart=is_bar_chart,
            custom_config=axis_naming_config,
        )

        # Hierarchical Plot Construction
        if add_plots and y_cols:
            page_name = f"page_{safe_prefix}"
            graph_name = f"graph_{safe_prefix}"

            lines.append("")
            lines.append(f"# Page & Graph for {safe_name}")
            lines.append(f"Add('page', name='{page_name}', autoadd=False)")
            lines.append(f"To('{page_name}')")
            lines.append(f"Set('width', '{page_width}')")
            lines.append(f"Set('height', '{page_height}')")

            lines.append(f"Add('graph', name='{graph_name}', autoadd=False)")
            lines.append(f"To('{graph_name}')")
            lines.append("Set('leftMargin', '2.2cm')")
            if is_bar_chart:
                lines.append("Set('bottomMargin', '2.2cm')")
            else:
                lines.append("Set('bottomMargin', '1.6cm')")
            lines.append("Set('rightMargin', '1.0cm')")
            lines.append("Set('topMargin', '1.0cm')")

            # X Axis adhering to axistype_valuetypename scheme
            lines.append(f"Add('axis', name='{x_axis_name}')")
            lines.append(f"To('{x_axis_name}')")
            if is_bar_chart:
                x_lbl = get_axis_label(x_axis_name, csv_type=csv_type, default_label="Sample")
                lines.append(f"Set('label', '{x_lbl}')")
                lines.append("Set('mode', 'labels')")
                lines.append("Set('GridLines/hide', True)")
            else:
                default_x_lbl = "m/z" if x_axis_name == "x_mz" else "Retention time (min)"
                x_lbl = get_axis_label(x_axis_name, csv_type=csv_type, default_label=default_x_lbl)
                lines.append(f"Set('label', '{x_lbl}')")
                lines.append("Set('GridLines/hide', False)")
                lines.append("Set('GridLines/color', '#e6e6e6')")
                lines.append("Set('GridLines/style', 'dotted')")
            lines.append("Set('Label/size', '11pt')")
            lines.append("Set('Label/bold', False)")
            lines.append("Set('TickLabels/size', '9.5pt')")
            lines.append("Set('MajorTicks/length', '4pt')")
            lines.append("Set('MinorTicks/length', '2pt')")
            lines.append("To('..')")

            # Y Axis adhering to axistype_valuetypename scheme
            lines.append(f"Add('axis', name='{y_axis_name}')")
            lines.append(f"To('{y_axis_name}')")
            lines.append("Set('direction', 'vertical')")
            default_y_lbl = (
                "Integrated Peak Area"
                if is_bar_chart
                else "Absorbance (AU)"
                if csv_type == "DAD"
                else "Total Ion Current"
                if csv_type == "TIC"
                else "Relative Intensity"
                if csv_type == "m/z"
                else "Response / Signal"
            )
            y_lbl = get_axis_label(y_axis_name, csv_type=csv_type, default_label=default_y_lbl)
            lines.append(f"Set('label', '{y_lbl}')")
            lines.append("Set('Label/size', '11pt')")
            lines.append("Set('Label/bold', False)")
            lines.append("Set('TickLabels/size', '9.5pt')")
            lines.append("Set('MajorTicks/length', '4pt')")
            lines.append("Set('MinorTicks/length', '2pt')")
            lines.append("Set('GridLines/hide', False)")
            lines.append("Set('GridLines/color', '#e6e6e6')")
            lines.append("Set('GridLines/style', 'dotted')")
            lines.append("To('..')")

            if is_bar_chart:
                # Grouped Bar Plot for Integrated Areas
                bar_name = f"bar_{safe_prefix}"
                lengths_ds = [
                    f"{safe_prefix}_{sanitize_veusz_identifier(str(col), default='y')}" if embed_data else f"{safe_prefix}_{col}"
                    for col in y_cols
                ]
                lengths_arg = "[" + ", ".join(f"'{ds}'" for ds in lengths_ds) + "]"
                keys_arg = "[" + ", ".join(f"'{str(col).replace(chr(92), chr(92)+chr(92)).replace(chr(39), chr(92)+chr(39))}'" for col in y_cols) + "]"
                ds_x_val = ds_x_name if embed_data else f"{safe_prefix}_{x_col}"

                lines.append(f"Add('bar', name='{bar_name}', autoadd=False)")
                lines.append(f"To('{bar_name}')")
                lines.append(f"Set('xAxis', '{x_axis_name}')")
                lines.append(f"Set('yAxis', '{y_axis_name}')")
                lines.append(f"Set('lengths', {lengths_arg})")
                lines.append(f"Set('keys', {keys_arg})")
                lines.append(f"Set('labels', '{ds_x_val}')")
                lines.append("Set('mode', 'grouped')")
                lines.append("Set('barfill', 0.8)")
                lines.append("Set('groupfill', 0.85)")
                lines.append("To('..')")
            else:
                # XY Plot Curves for Chromatograms
                for y_idx, y_col in enumerate(y_cols):
                    clean_y = sanitize_veusz_identifier(str(y_col), default="y")
                    ds_y_name = f"{safe_prefix}_{clean_y}" if embed_data else f"{safe_prefix}_{y_col}"
                    ds_x_val = ds_x_name if embed_data else f"{safe_prefix}_{x_col}"
                    xy_name = f"xy_{clean_y}"
                    color_hex = VEUSZ_PALETTE[y_idx % len(VEUSZ_PALETTE)]

                    lines.append(f"Add('xy', name='{xy_name}', autoadd=False)")
                    lines.append(f"To('{xy_name}')")
                    lines.append(f"Set('xAxis', '{x_axis_name}')")
                    lines.append(f"Set('yAxis', '{y_axis_name}')")
                    lines.append(f"Set('xData', '{ds_x_val}')")
                    lines.append(f"Set('yData', '{ds_y_name}')")
                    lines.append(f"Set('key', '{y_col}')")
                    lines.append("Set('PlotLine/width', '1.25pt')")
                    lines.append(f"Set('PlotLine/color', '{color_hex}')")
                    lines.append("Set('marker', 'none')")
                    lines.append("To('..')")

            # Publication Key (Legend)
            if len(y_cols) >= 1:
                lines.append("Add('key', name='legend', autoadd=False)")
                lines.append("To('legend')")
                lines.append("Set('horzPosn', 'right')")
                lines.append("Set('vertPosn', 'top')")
                lines.append("Set('Border/color', '#cccccc')")
                lines.append("Set('Background/color', '#ffffff')")
                lines.append("Set('Background/transparency', 15)")
                lines.append("Set('Text/size', '9pt')")
                lines.append("To('..')")

            lines.append("To('..')")  # Exit graph
            lines.append("To('..')")  # Exit page
            pages_added += 1

        lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    console_log(f"Veusz document created successfully: '{output_path}' ({datasets_added} datasets, {pages_added} graph pages)", category="VEUSZ")
    return output_path


def create_veusz_project(
    csv_folder: str,
    output_path: str,
    add_plots: bool = True,
    embed_data: bool = True,
    page_width: str = "18cm",
    page_height: str = "11cm",
    csv_files: Optional[List[Tuple[str, str]]] = None,
    axis_naming_config: Optional[Dict[str, Any]] = None,
    status_cb=None,
) -> str:
    """Convenience wrapper for generate_veusz_vsz_project."""
    return generate_veusz_vsz_project(
        csv_folder=csv_folder,
        output_path=output_path,
        add_plots=add_plots,
        embed_data=embed_data,
        page_width=page_width,
        page_height=page_height,
        csv_files=csv_files,
        axis_naming_config=axis_naming_config,
        status_cb=status_cb,
    )


# ---------------------------
# Legacy / Deprecated LabPlot
# ---------------------------

def create_labplot_project(
    csv_folder: str,
    output_path: str,
    add_plots: bool = True,
    csv_files: Optional[List[Tuple[str, str]]] = None,
    status_cb=None,
) -> str:
    """
    Deprecated: KDE LabPlot support has been replaced by Veusz.
    Maintained for legacy project compatibility.
    """
    warnings.warn("create_labplot_project is deprecated. Use create_veusz_project instead.", DeprecationWarning, stacklevel=2)
    console_log("KDE LabPlot export is deprecated; Veusz (.vsz) is now the primary standard.", category="LABPLOT", level="WARN")

    csv_folder = os.path.abspath(csv_folder)
    if csv_files is None:
        csv_files = find_chrom_csv_files(csv_folder, include_evaluated=True)
    if not csv_files:
        raise ValueError(f"No CSV files found in {csv_folder}")

    proj_name = os.path.basename(output_path)
    for ext in [".lml.gz", ".lml", ".gz", ".py"]:
        if proj_name.casefold().endswith(ext):
            proj_name = proj_name[:-len(ext)]
            break

    root_elem = ET.Element("project", {
        "version": "2.11.0",
        "name": proj_name or "Waters_LabPlot_Project",
    })

    for sample_name, fpath in csv_files:
        safe_name = sanitize_filename(sample_name.replace("/", "_").replace("\\", "_"))
        try:
            df = pd.read_csv(fpath)
        except Exception:
            continue
        if df.empty or len(df.columns) < 2:
            continue

        sheet_elem = ET.SubElement(root_elem, "spreadsheet", {"name": safe_name})
        csv_type = classify_csv_type(fpath, df.columns)
        if csv_type == "Integrated areas":
            sample_col = next((c for c in df.columns if str(c).strip().casefold() in ("sample", "sample name", "sample_name", "samplename", "name")), df.columns[0])
            x_col, y_cols, x_type = sample_col, [c for c in df.columns if c != sample_col], "text"
        else:
            x_col = find_rt_column(df)
            y_cols, x_type = [c for c in df.columns if c != x_col], "double"

        x_col_elem = ET.SubElement(sheet_elem, "column", {"name": str(x_col), "type": x_type, "plotDesignation": "x"})
        x_col_elem.text = " ".join(df[x_col].fillna("").astype(str).tolist())

        for y_col in y_cols:
            y_col_elem = ET.SubElement(sheet_elem, "column", {"name": str(y_col), "type": "double", "plotDesignation": "y"})
            y_col_elem.text = " ".join(pd.to_numeric(df[y_col], errors="coerce").fillna(0.0).astype(str).tolist())

    xml_raw = ET.tostring(root_elem, encoding="utf-8")
    try:
        xml_str = minidom.parseString(xml_raw).toprettyxml(indent="  ")
    except Exception:
        xml_str = xml_raw.decode("utf-8")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml_str)
    return output_path


def export_labplot_script(*args, **kwargs):
    """Deprecated legacy stub."""
    warnings.warn("export_labplot_script is deprecated.", DeprecationWarning, stacklevel=2)
    return ""


def generate_labplot_lml_project(*args, **kwargs):
    """Deprecated legacy stub."""
    warnings.warn("generate_labplot_lml_project is deprecated. Use generate_veusz_vsz_project.", DeprecationWarning, stacklevel=2)
    return create_labplot_project(*args, **kwargs)


# ---------------------------
# GUI Components
# ---------------------------

class QtConsoleStream(QObject):
    text_written = Signal(str, str)

    def __init__(self, original_stream=None, stream_type="stdout"):
        super().__init__()
        self.original_stream = original_stream
        self.stream_type = stream_type

    @property
    def encoding(self):
        return getattr(self.original_stream, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self.original_stream, "errors", "strict")

    def isatty(self):
        return getattr(self.original_stream, "isatty", lambda: False)()

    def fileno(self):
        if hasattr(self.original_stream, "fileno"):
            return self.original_stream.fileno()
        raise OSError("fileno not supported")

    def write(self, text):
        if self.original_stream:
            try:
                self.original_stream.write(text)
            except Exception:
                pass
        if text:
            self.text_written.emit(str(text), self.stream_type)

    def flush(self):
        if self.original_stream:
            try:
                self.original_stream.flush()
            except Exception:
                pass


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


class GripSplitterHandle(QSplitterHandle):
    """Prominent, tactile splitter handle with clear border divider and centered grip."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self._hovered = False

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        # Detect light vs dark theme
        is_dark = self.palette().window().color().lightness() < 128

        # Background gutter tint
        if self._hovered:
            bg_color = QColor(56, 139, 253, 30) if is_dark else QColor(9, 105, 218, 25)
        else:
            bg_color = QColor(255, 255, 255, 12) if is_dark else QColor(0, 0, 0, 10)
        painter.fillRect(rect, bg_color)

        # Border separator lines
        border_pen = QColor(110, 125, 145, 140) if is_dark else QColor(175, 185, 200, 180)
        painter.setPen(QPen(border_pen, 1))

        if self.orientation() == Qt.Orientation.Horizontal:
            painter.drawLine(rect.left(), rect.top(), rect.left(), rect.bottom())
            painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())

            # Tactile center grip pill
            mid_x = rect.center().x()
            pill_w = 4
            pill_h = min(42, max(24, rect.height() // 4))
            pill_x = mid_x - pill_w // 2
            pill_y = max(rect.top() + 6, rect.center().y() - pill_h // 2)

            grip_color = (
                QColor("#388bfd")
                if (self._hovered and is_dark)
                else QColor("#0969da")
                if self._hovered
                else QColor("#8b949e")
                if is_dark
                else QColor("#57606a")
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grip_color)
            painter.drawRoundedRect(pill_x, pill_y, pill_w, pill_h, 2, 2)

            # Embossed grip dots inside pill
            dot_color = QColor(255, 255, 255, 220) if is_dark else QColor(255, 255, 255, 240)
            painter.setBrush(dot_color)
            cy = rect.center().y()
            for dy in (-10, 0, 10):
                painter.drawEllipse(mid_x - 1, cy + dy - 1, 2, 2)
        else:
            painter.drawLine(rect.left(), rect.top(), rect.right(), rect.top())
            painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

            mid_y = rect.center().y()
            pill_w = min(42, max(24, rect.width() // 4))
            pill_h = 4
            pill_x = max(rect.left() + 6, rect.center().x() - pill_w // 2)
            pill_y = mid_y - pill_h // 2

            grip_color = (
                QColor("#388bfd")
                if (self._hovered and is_dark)
                else QColor("#0969da")
                if self._hovered
                else QColor("#8b949e")
                if is_dark
                else QColor("#57606a")
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grip_color)
            painter.drawRoundedRect(pill_x, pill_y, pill_w, pill_h, 2, 2)

            dot_color = QColor(255, 255, 255, 220) if is_dark else QColor(255, 255, 255, 240)
            painter.setBrush(dot_color)
            cx = rect.center().x()
            for dx in (-10, 0, 10):
                painter.drawEllipse(cx + dx - 1, mid_y - 1, 2, 2)


class GripSplitter(QSplitter):
    """QSplitter configured with prominent, high-contrast GripSplitterHandle."""

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setHandleWidth(12)

    def createHandle(self):
        return GripSplitterHandle(self.orientation(), self)


class WatersExporterWindow(QMainWindow):
    """
    Main application window for Waters dataset export and project generation.
    """

    def __init__(self, initial_folder: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("Waters Exporter | Chromatographic workspace")
        self.resize(960, 720)
        self.setMinimumSize(640, 480)

        self.settings = QSettings("danield4n13l", "WatersExporter")
        # Migrate legacy settings from WatersVeuszExporter if empty
        if not self.settings.allKeys():
            legacy_settings = QSettings("danield4n13l", "WatersVeuszExporter")
            for key in legacy_settings.allKeys():
                self.settings.setValue(key, legacy_settings.value(key))

        self.native_style_name = QApplication.style().objectName()
        self.system_palette = QPalette(QApplication.palette())
        self.discovered_csv_files: List[Tuple[str, str]] = []
        self._selected_preview_df: Optional[pd.DataFrame] = None
        self._last_exported_path: Optional[str] = None
        self.veusz_bin = find_veusz_executable()

        self._build_ui()

        initial = initial_folder or self.settings.value("last_folder", "")
        if initial and os.path.isdir(str(initial)):
            self.csv_folder_edit.setText(str(initial))
            self.scan_datasets()

        console_log("Waters Exporter initialized", category="INIT")
        if self.veusz_bin:
            console_log(f"Detected Veusz binary on system: {self.veusz_bin}", category="INIT")

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
        content_layout.setSpacing(14)

        # Header: Title & Subtitle + Theme Selector
        title = QLabel("Waters Exporter")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Package MassLynx and chromatographic CSV datasets into publication-ready export documents and projects.")
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

        header = QHBoxLayout()
        header.addLayout(heading, 1)
        header.addLayout(appearance)
        content_layout.addLayout(header)

        # Section 01: Source datasets
        self._build_source_section(content_layout)

        # Section 02: Veusz Export configuration
        self._build_export_section(content_layout)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        self.setCentralWidget(scroll)

        # Debug console drawer & status bar
        self._build_console_drawer()
        self.statusBar().showMessage("Ready")

        # Themes setup
        self.theme_style_combo.currentIndexChanged.connect(self.apply_theme)
        self.theme_mode_combo.currentIndexChanged.connect(self.apply_theme)
        QApplication.styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.apply_theme()

    def _build_source_section(self, parent_layout):
        self.source_section = CollapsibleSection("01  Source CSV datasets", expanded=True)
        parent_layout.addWidget(self.source_section)
        form = QGridLayout()
        self.source_section.body_layout.addLayout(form)

        self.csv_folder_edit = QLineEdit()
        self.csv_folder_edit.setPlaceholderText("Select folder containing chromatographic CSV files")
        self.csv_folder_edit.textChanged.connect(self._on_csv_folder_changed)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("secondaryButton")
        browse_btn.clicked.connect(self.browse_csv_folder)
        open_btn = QPushButton("Open")
        open_btn.setObjectName("secondaryButton")
        open_btn.setToolTip("Open source folder in file explorer")
        open_btn.clicked.connect(lambda: self.open_folder_in_explorer(self.csv_folder_edit.text()))
        btn_layout.addWidget(browse_btn)
        btn_layout.addWidget(open_btn)

        form.addWidget(QLabel("CSV source folder"), 0, 0)
        form.addWidget(self.csv_folder_edit, 0, 1)
        form.addLayout(btn_layout, 0, 2)

        opts_layout = QHBoxLayout()
        self.include_evaluated_checkbox = QCheckBox("Include evaluated data (00_evaluated_data.csv)")
        self.include_evaluated_checkbox.setChecked(True)
        self.include_evaluated_checkbox.toggled.connect(self.scan_datasets)
        opts_layout.addWidget(self.include_evaluated_checkbox)

        scan_btn = QPushButton("Scan datasets")
        scan_btn.setObjectName("secondaryButton")
        scan_btn.clicked.connect(self.scan_datasets)
        opts_layout.addWidget(scan_btn)

        opts_layout.addStretch(1)

        select_all_btn = QPushButton("Select all")
        select_all_btn.setObjectName("secondaryButton")
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        deselect_all_btn = QPushButton("Deselect all")
        deselect_all_btn.setObjectName("secondaryButton")
        deselect_all_btn.clicked.connect(lambda: self._set_all_checked(False))
        opts_layout.addWidget(select_all_btn)
        opts_layout.addWidget(deselect_all_btn)

        self.source_section.body_layout.addLayout(opts_layout)

        # Splitter: Table on left, Table preview on right
        splitter = GripSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.dataset_summary_label = QLabel("No datasets loaded. Select a folder to scan.")
        self.dataset_summary_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        left_layout.addWidget(self.dataset_summary_label)

        self.dataset_table = QTableWidget(0, 5)
        self.dataset_table.setHorizontalHeaderLabels(["Export", "Sample / Dataset", "Type", "Rows", "Columns"])
        self.dataset_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.dataset_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.dataset_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.dataset_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.dataset_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.dataset_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.dataset_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.dataset_table.setMinimumHeight(240)
        left_layout.addWidget(self.dataset_table, 1)

        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self.preview_header_label = QLabel("Dataset Data Preview")
        self.preview_header_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        right_layout.addWidget(self.preview_header_label)

        self.data_preview_table = QTableWidget(0, 0)
        self.data_preview_table.setMinimumHeight(240)
        right_layout.addWidget(self.data_preview_table, 1)

        splitter.addWidget(right_panel)
        splitter.setSizes([550, 400])

        self.source_section.body_layout.addWidget(splitter)

    def _build_export_section(self, parent_layout):
        self.export_section = CollapsibleSection("02  Veusz export configuration & generation", expanded=True)
        parent_layout.addWidget(self.export_section)
        form = QGridLayout()
        self.export_section.body_layout.addLayout(form)

        # Output Veusz Document Path
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Target Veusz project document (.vsz)")

        out_btn_layout = QHBoxLayout()
        out_btn_layout.setContentsMargins(0, 0, 0, 0)
        out_btn_layout.setSpacing(6)
        browse_out_btn = QPushButton("Browse…")
        browse_out_btn.setObjectName("secondaryButton")
        browse_out_btn.clicked.connect(self.browse_output_file)
        open_out_btn = QPushButton("Open")
        open_out_btn.setObjectName("secondaryButton")
        open_out_btn.setToolTip("Open output directory in file explorer")
        open_out_btn.clicked.connect(lambda: self.open_folder_in_explorer(self.output_edit.text()))
        out_btn_layout.addWidget(browse_out_btn)
        out_btn_layout.addWidget(open_out_btn)

        form.addWidget(QLabel("Output Veusz project"), 0, 0)
        form.addWidget(self.output_edit, 0, 1)
        form.addLayout(out_btn_layout, 0, 2)

        # Dimension options
        dim_layout = QHBoxLayout()
        dim_layout.addWidget(QLabel("Page size:"))
        self.page_size_combo = QComboBox()
        self.page_size_combo.addItem("Standard publication (18 × 11 cm)", ("18cm", "11cm"))
        self.page_size_combo.addItem("Wide widescreen (22 × 11 cm)", ("22cm", "11cm"))
        self.page_size_combo.addItem("Single-column compact (10 × 8 cm)", ("10cm", "8cm"))
        self.page_size_combo.addItem("Full-page A4 landscape (28 × 18 cm)", ("28cm", "18cm"))
        dim_layout.addWidget(self.page_size_combo)
        dim_layout.addStretch(1)
        form.addLayout(dim_layout, 1, 1, 1, 2)

        # Format-specific options
        options_box = QVBoxLayout()
        self.add_plots_checkbox = QCheckBox("Generate plot pages (XY chromatograms / Categorical bar charts)")
        self.add_plots_checkbox.setChecked(True)
        options_box.addWidget(self.add_plots_checkbox)

        self.veusz_embed_checkbox = QCheckBox("Embed data values directly into Veusz document (self-contained, 100% portable)")
        self.veusz_embed_checkbox.setChecked(True)
        options_box.addWidget(self.veusz_embed_checkbox)

        self.export_section.body_layout.addLayout(options_box)

        # Export Actions
        action_layout = QHBoxLayout()
        self.export_button = QPushButton("Create Veusz project (.vsz)")
        self.export_button.setStyleSheet("font-size: 13px; font-weight: 650; padding: 10px 22px;")
        self.export_button.clicked.connect(self.export_veusz_clicked)
        action_layout.addWidget(self.export_button)

        self.open_exported_button = QPushButton("Open in file explorer")
        self.open_exported_button.setObjectName("secondaryButton")
        self.open_exported_button.setVisible(False)
        self.open_exported_button.clicked.connect(self._open_last_exported_in_explorer)
        action_layout.addWidget(self.open_exported_button)

        if self.veusz_bin:
            self.launch_veusz_button = QPushButton("Launch in Veusz")
            self.launch_veusz_button.setObjectName("secondaryButton")
            self.launch_veusz_button.setVisible(False)
            self.launch_veusz_button.clicked.connect(self._launch_in_veusz)
            action_layout.addWidget(self.launch_veusz_button)

        action_layout.addStretch(1)
        self.export_section.body_layout.addLayout(action_layout)

        self.status_banner = QLabel("Ready. Select datasets and click 'Create Veusz project (.vsz)'.")
        self.status_banner.setWordWrap(True)
        self.status_banner.setStyleSheet("padding: 4px; color: palette(text);")
        self.export_section.body_layout.addWidget(self.status_banner)

        # Collapsible Internal naming configuration (axistype_valuetypename)
        self.naming_section = CollapsibleSection("03  Internal axis naming conventions (axistype_valuetypename)", expanded=False)
        parent_layout.addWidget(self.naming_section)
        naming_layout = QGridLayout()
        naming_layout.setContentsMargins(6, 6, 6, 6)
        naming_layout.setSpacing(8)

        naming_hint = QLabel("Internal Veusz axis names adhering to the 'axistype_valuetypename' convention (e.g. x_sampleid, y_area, x_rt, y_absorbance, y_relint, x_mz).")
        naming_hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        naming_hint.setWordWrap(True)
        naming_layout.addWidget(naming_hint, 0, 0, 1, 4)

        # Bar chart row
        naming_layout.addWidget(QLabel("Bar chart X:"), 1, 0)
        self.axis_barchart_x_edit = QLineEdit(self.settings.value("axis_barchart_x", "x_sampleid"))
        self.axis_barchart_x_edit.setPlaceholderText("x_sampleid")
        naming_layout.addWidget(self.axis_barchart_x_edit, 1, 1)

        naming_layout.addWidget(QLabel("Bar chart Y:"), 1, 2)
        self.axis_barchart_y_edit = QLineEdit(self.settings.value("axis_barchart_y", "y_area"))
        self.axis_barchart_y_edit.setPlaceholderText("y_area")
        naming_layout.addWidget(self.axis_barchart_y_edit, 1, 3)

        # Chromatogram RT & DAD row
        naming_layout.addWidget(QLabel("RT trace X:"), 2, 0)
        self.axis_rt_x_edit = QLineEdit(self.settings.value("axis_rt_x", "x_rt"))
        self.axis_rt_x_edit.setPlaceholderText("x_rt")
        naming_layout.addWidget(self.axis_rt_x_edit, 2, 1)

        naming_layout.addWidget(QLabel("DAD Absorbance Y:"), 2, 2)
        self.axis_dad_y_edit = QLineEdit(self.settings.value("axis_dad_y", "y_absorbance"))
        self.axis_dad_y_edit.setPlaceholderText("y_absorbance")
        naming_layout.addWidget(self.axis_dad_y_edit, 2, 3)

        # TIC & m/z row
        naming_layout.addWidget(QLabel("TIC Y:"), 3, 0)
        self.axis_tic_y_edit = QLineEdit(self.settings.value("axis_tic_y", "y_relint"))
        self.axis_tic_y_edit.setPlaceholderText("y_relint")
        naming_layout.addWidget(self.axis_tic_y_edit, 3, 1)

        naming_layout.addWidget(QLabel("m/z Intensity Y:"), 3, 2)
        self.axis_mz_y_edit = QLineEdit(self.settings.value("axis_mz_y", "y_relint"))
        self.axis_mz_y_edit.setPlaceholderText("y_relint")
        naming_layout.addWidget(self.axis_mz_y_edit, 3, 3)

        # m/z Spectrum X & Reset button row
        naming_layout.addWidget(QLabel("m/z Spectrum X:"), 4, 0)
        self.axis_mz_x_edit = QLineEdit(self.settings.value("axis_mz_x", "x_mz"))
        self.axis_mz_x_edit.setPlaceholderText("x_mz")
        naming_layout.addWidget(self.axis_mz_x_edit, 4, 1)

        reset_naming_btn = QPushButton("Reset naming defaults")
        reset_naming_btn.setObjectName("secondaryButton")
        reset_naming_btn.clicked.connect(self._reset_axis_naming_defaults)
        naming_layout.addWidget(reset_naming_btn, 4, 2, 1, 2)

        self.naming_section.body_layout.addLayout(naming_layout)

        # Collapsible Deprecated LabPlot section
        self.deprecated_section = CollapsibleSection("04  Legacy export engines (KDE LabPlot — Deprecated)", expanded=False)
        parent_layout.addWidget(self.deprecated_section)
        dep_layout = QVBoxLayout()
        dep_label = QLabel(
            "Notice: KDE LabPlot support is currently deprecated in favor of native Veusz (.vsz) export.\n"
            "You can still generate legacy LabPlot .lml files here if required for archival purposes."
        )
        dep_label.setStyleSheet("color: palette(mid); font-style: italic;")
        dep_layout.addWidget(dep_label)
        dep_btn = QPushButton("Export legacy LabPlot XML (.lml)")
        dep_btn.setObjectName("secondaryButton")
        dep_btn.clicked.connect(self._export_deprecated_labplot)
        dep_layout.addWidget(dep_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.deprecated_section.body_layout.addLayout(dep_layout)

    # ---------------------------
    # Theme & Styling
    # ---------------------------

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

        if hasattr(self, "console_text"):
            console_bg = "#161719" if system_dark else "#fdfdfd"
            console_border = "#35393f" if system_dark else "#d4d9df"
            self.console_text.setStyleSheet(
                f"QPlainTextEdit {{ background: {console_bg}; color: {colors['text']}; "
                f"border: 1px solid {console_border}; border-radius: 6px; padding: 6px; "
                f"font-family: Consolas, 'Cascadia Code', 'Courier New', monospace; font-size: 12px; }}"
            )
        if hasattr(self, "console_status_button"):
            self.console_status_button.setStyleSheet(
                f"QToolButton {{ padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 600; "
                f"background: {colors['button']}; color: {colors['text']}; border: 1px solid {colors['border']}; }}"
                f"QToolButton:hover {{ background: {colors['alternate']}; }}"
                f"QToolButton:checked {{ background: {colors['accent']}; color: {colors['accent_text']}; }}"
            )

    # ---------------------------
    # Console Drawer
    # ---------------------------

    def _build_console_drawer(self):
        self.console_dock = QDockWidget("Debug Console", self)
        self.console_dock.setObjectName("runtimeConsoleDock")
        self.console_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        self.console_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

        console_container = QWidget()
        console_layout = QVBoxLayout(console_container)
        console_layout.setContentsMargins(10, 6, 10, 8)
        console_layout.setSpacing(6)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)

        console_title = QLabel("Runtime Console & Diagnostics")
        console_title.setStyleSheet("font-weight: 650; font-size: 13px;")
        toolbar_layout.addWidget(console_title)

        self.console_count_label = QLabel("0 lines")
        self.console_count_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        toolbar_layout.addWidget(self.console_count_label)

        toolbar_layout.addStretch(1)

        self.console_autoscroll_checkbox = QCheckBox("Auto-scroll")
        self.console_autoscroll_checkbox.setChecked(True)
        toolbar_layout.addWidget(self.console_autoscroll_checkbox)

        copy_btn = QPushButton("Copy all")
        copy_btn.setObjectName("secondaryButton")
        copy_btn.clicked.connect(self._copy_console_text)
        toolbar_layout.addWidget(copy_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("secondaryButton")
        clear_btn.clicked.connect(self._clear_console)
        toolbar_layout.addWidget(clear_btn)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setToolTip("Close console drawer")
        close_btn.clicked.connect(self.console_dock.hide)
        toolbar_layout.addWidget(close_btn)

        console_layout.addLayout(toolbar_layout)

        self.console_text = QPlainTextEdit()
        self.console_text.setReadOnly(True)
        self.console_text.setMaximumBlockCount(5000)
        self.console_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        console_layout.addWidget(self.console_text, 1)

        console_container.setMinimumHeight(150)
        self.console_dock.setWidget(console_container)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.console_dock)
        self.console_dock.setVisible(False)

        self.console_status_button = QToolButton()
        self.console_status_button.setText("Console")
        self.console_status_button.setCheckable(True)
        self.console_status_button.setToolTip("Toggle runtime debugging console drawer")
        self.console_status_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.console_status_button.toggled.connect(self.console_dock.setVisible)
        self.console_dock.visibilityChanged.connect(self.console_status_button.setChecked)
        self.statusBar().addPermanentWidget(self.console_status_button)

        self._msg_count = 0
        self._stdout_stream = QtConsoleStream(sys.stdout, "stdout")
        self._stderr_stream = QtConsoleStream(sys.stderr, "stderr")
        self._stdout_stream.text_written.connect(self._handle_stream_output)
        self._stderr_stream.text_written.connect(self._handle_stream_output)
        sys.stdout = self._stdout_stream
        sys.stderr = self._stderr_stream

        self.log_console("Waters Exporter runtime debug console initialized.", "SYSTEM")
        self.statusBar().messageChanged.connect(self._handle_status_message_changed)

    def _handle_status_message_changed(self, msg: str):
        if msg:
            self.log_console(f"{msg}", "STATUS")

    def _handle_stream_output(self, text: str, stream_type: str):
        if not text:
            return
        self._append_console_output(text, stream_type)

    def _append_console_output(self, text: str, stream_type: str = "stdout"):
        self.console_text.moveCursor(QTextCursor.MoveOperation.End)
        self.console_text.insertPlainText(text)
        self._msg_count += text.count("\n") or 1
        self.console_count_label.setText(f"{self._msg_count} lines")
        if self.console_autoscroll_checkbox.isChecked():
            self.console_text.moveCursor(QTextCursor.MoveOperation.End)

    def log_console(self, text: str, level: str = "INFO"):
        time_str = QTime.currentTime().toString("HH:mm:ss.zzz")
        formatted = f"[{time_str}] [{level}] {text}\n"
        self._append_console_output(formatted, level.lower())

    def _clear_console(self):
        self.console_text.clear()
        self._msg_count = 0
        self.console_count_label.setText("0 lines")

    def _copy_console_text(self):
        text = self.console_text.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage("Console output copied to clipboard", 3000)

    def closeEvent(self, event):
        if hasattr(self, "_stdout_stream") and self._stdout_stream.original_stream:
            sys.stdout = self._stdout_stream.original_stream
        if hasattr(self, "_stderr_stream") and self._stderr_stream.original_stream:
            sys.stderr = self._stderr_stream.original_stream
        super().closeEvent(event)

    # ---------------------------
    # UI Actions & Event Handlers
    # ---------------------------

    def open_folder_in_explorer(self, folder_path: str):
        if not folder_path or not str(folder_path).strip():
            QMessageBox.warning(self, "No folder specified", "Please select or enter a path first.")
            return
        console_log(f"User requested opening folder in explorer: {folder_path}", category="EXPLORER")
        if not open_in_explorer(folder_path):
            QMessageBox.warning(self, "Folder not found", f"The chosen path does not exist:\n{folder_path}")
        else:
            self.statusBar().showMessage(f"Opened path in file explorer: {folder_path}")

    def _open_last_exported_in_explorer(self):
        if self._last_exported_path:
            open_in_explorer(self._last_exported_path)
        elif self.output_edit.text().strip():
            open_in_explorer(self.output_edit.text().strip())

    def _launch_in_veusz(self):
        target = self._last_exported_path or self.output_edit.text().strip()
        if not target or not os.path.isfile(target):
            QMessageBox.warning(self, "File not found", "Please export the Veusz project file first.")
            return
        if self.veusz_bin and os.path.isfile(self.veusz_bin):
            console_log(f"Launching Veusz: '{self.veusz_bin}' '{target}'", category="VEUSZ")
            subprocess.Popen([self.veusz_bin, target])
            self.statusBar().showMessage(f"Launched in Veusz: {os.path.basename(target)}")
        else:
            QMessageBox.warning(self, "Veusz not found", "Could not locate the Veusz executable on your system.")

    def browse_csv_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder with CSV datasets")
        if folder:
            self.csv_folder_edit.setText(folder)
            self.scan_datasets()

    def _on_csv_folder_changed(self, text: str):
        folder = text.strip()
        if folder and os.path.isdir(folder):
            self.settings.setValue("last_folder", folder)
            if not self.output_edit.text().strip():
                default_out = os.path.join(folder, "Waters_Veusz_Project.vsz")
                self.output_edit.setText(default_out)

    def browse_output_file(self):
        current_out = self.output_edit.text().strip() or "Waters_Veusz_Project.vsz"
        filters = "Veusz Project (*.vsz);;All Files (*.*)"
        filename, _ = QFileDialog.getSaveFileName(self, "Select Veusz project output path", current_out, filters)
        if filename:
            self.output_edit.setText(filename)

    def scan_datasets(self):
        folder = self.csv_folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            return

        include_eval = self.include_evaluated_checkbox.isChecked()
        console_log(f"Scanning for CSV datasets in: {folder} (include_evaluated={include_eval})", category="SCAN")
        self.statusBar().showMessage(f"Scanning datasets in {folder}…")
        QApplication.processEvents()

        self.discovered_csv_files = find_chrom_csv_files(folder, include_evaluated=include_eval)
        console_log(f"Discovered {len(self.discovered_csv_files)} CSV files", category="SCAN")

        self.dataset_table.setRowCount(0)
        self.data_preview_table.setRowCount(0)
        self.data_preview_table.setColumnCount(0)

        for sample_name, fpath in self.discovered_csv_files:
            row = self.dataset_table.rowCount()
            self.dataset_table.insertRow(row)

            # Checkbox item
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk_item.setCheckState(Qt.CheckState.Checked)
            self.dataset_table.setItem(row, 0, chk_item)

            # Name item
            name_item = QTableWidgetItem(sample_name)
            name_item.setData(Qt.ItemDataRole.UserRole, fpath)
            self.dataset_table.setItem(row, 1, name_item)

            # Type item
            csv_type = classify_csv_type(fpath)
            type_item = QTableWidgetItem(csv_type)
            self.dataset_table.setItem(row, 2, type_item)

            # Quick inspect row/col count
            try:
                with open(fpath, "r", encoding="utf-8-sig") as f:
                    first_line = f.readline()
                    col_count = len(first_line.split(",")) if first_line else 0
                    row_count = sum(1 for _ in f) + 1
            except Exception:
                row_count, col_count = 0, 0

            self.dataset_table.setItem(row, 3, QTableWidgetItem(str(row_count)))
            self.dataset_table.setItem(row, 4, QTableWidgetItem(str(col_count)))

        self.dataset_summary_label.setText(
            f"Found {len(self.discovered_csv_files)} datasets. All currently selected for export."
        )
        self.statusBar().showMessage(f"Loaded {len(self.discovered_csv_files)} CSV datasets.")

        if self.discovered_csv_files:
            self.dataset_table.selectRow(0)

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.dataset_table.rowCount()):
            item = self.dataset_table.item(row, 0)
            if item:
                item.setCheckState(state)
        selected_count = sum(
            1 for r in range(self.dataset_table.rowCount())
            if self.dataset_table.item(r, 0).checkState() == Qt.CheckState.Checked
        )
        self.dataset_summary_label.setText(
            f"Found {len(self.discovered_csv_files)} datasets. {selected_count} selected for export."
        )

    def _on_table_selection_changed(self):
        selected_rows = self.dataset_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        name_item = self.dataset_table.item(row, 1)
        if not name_item:
            return
        fpath = name_item.data(Qt.ItemDataRole.UserRole)
        sample_name = name_item.text()
        self._load_dataset_preview(sample_name, fpath)

    def _load_dataset_preview(self, sample_name: str, fpath: str):
        if not fpath or not os.path.isfile(fpath):
            return
        try:
            df = pd.read_csv(fpath, nrows=100)
            self._selected_preview_df = df
            self.preview_header_label.setText(f"Preview: {sample_name} ({len(df.columns)} cols, top 100 rows)")
            self.data_preview_table.setRowCount(0)
            self.data_preview_table.setColumnCount(len(df.columns))
            self.data_preview_table.setHorizontalHeaderLabels([str(c) for c in df.columns])

            for row_idx, row_data in df.iterrows():
                r = self.data_preview_table.rowCount()
                self.data_preview_table.insertRow(r)
                for col_idx, val in enumerate(row_data):
                    self.data_preview_table.setItem(r, col_idx, QTableWidgetItem(str(val)))
        except Exception as exc:
            console_log(f"Failed to preview CSV '{fpath}': {exc}", category="PREVIEW", level="WARN")
            self.preview_header_label.setText(f"Preview: {sample_name} (Error reading file)")

    def _reset_axis_naming_defaults(self):
        self.axis_barchart_x_edit.setText("x_sampleid")
        self.axis_barchart_y_edit.setText("y_area")
        self.axis_rt_x_edit.setText("x_rt")
        self.axis_dad_y_edit.setText("y_absorbance")
        self.axis_tic_y_edit.setText("y_relint")
        self.axis_mz_y_edit.setText("y_relint")
        self.axis_mz_x_edit.setText("x_mz")

    def export_veusz_clicked(self):
        folder = self.csv_folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "Invalid folder", "Select a valid CSV folder to export.")
            return

        selected_files: List[Tuple[str, str]] = []
        for row in range(self.dataset_table.rowCount()):
            chk_item = self.dataset_table.item(row, 0)
            if chk_item and chk_item.checkState() == Qt.CheckState.Checked:
                name_item = self.dataset_table.item(row, 1)
                if name_item:
                    fpath = name_item.data(Qt.ItemDataRole.UserRole)
                    selected_files.append((name_item.text(), fpath))

        if not selected_files:
            QMessageBox.warning(self, "No datasets selected", "Please check at least one dataset to export.")
            return

        output_path = self.output_edit.text().strip()
        if not output_path:
            output_path = os.path.join(folder, "Waters_Veusz_Project.vsz")
            self.output_edit.setText(output_path)

        add_plots = self.add_plots_checkbox.isChecked()
        embed_veusz = self.veusz_embed_checkbox.isChecked()
        page_w, page_h = self.page_size_combo.currentData() or ("18cm", "11cm")

        # Collect configured axis naming scheme
        axis_config = {
            "barchart_x": self.axis_barchart_x_edit.text().strip() or "x_sampleid",
            "barchart_y": self.axis_barchart_y_edit.text().strip() or "y_area",
            "dad_x": self.axis_rt_x_edit.text().strip() or "x_rt",
            "dad_y": self.axis_dad_y_edit.text().strip() or "y_absorbance",
            "tic_x": self.axis_rt_x_edit.text().strip() or "x_rt",
            "tic_y": self.axis_tic_y_edit.text().strip() or "y_relint",
            "mz_chrom_x": self.axis_rt_x_edit.text().strip() or "x_rt",
            "mz_spectrum_x": self.axis_mz_x_edit.text().strip() or "x_mz",
            "mz_y": self.axis_mz_y_edit.text().strip() or "y_relint",
        }
        for k, v in axis_config.items():
            self.settings.setValue(f"axis_{k}", v)

        self.status_banner.setText("Exporting Veusz project…")
        self.statusBar().showMessage("Exporting Veusz project…")
        QApplication.processEvents()

        try:
            final_path = create_veusz_project(
                csv_folder=folder,
                output_path=output_path,
                add_plots=add_plots,
                embed_data=embed_veusz,
                page_width=page_w,
                page_height=page_h,
                csv_files=selected_files,
                axis_naming_config=axis_config,
                status_cb=lambda msg: self.statusBar().showMessage(msg),
            )
            self._last_exported_path = final_path
            self.status_banner.setText(f"✓ Veusz export complete: {final_path}")
            self.statusBar().showMessage(f"Veusz project created: {final_path}")
            self.open_exported_button.setVisible(True)
            if hasattr(self, "launch_veusz_button"):
                self.launch_veusz_button.setVisible(True)

            msg = (
                f"Veusz project created successfully!\n\n"
                f"Project document: {final_path}\n"
                f"Datasets imported: {len(selected_files)}\n"
                f"Publication graphs added: {'Yes' if add_plots else 'No'}\n"
                f"Page aspect ratio: {page_w} × {page_h}\n"
                f"Data storage: {'Embedded (self-contained)' if embed_veusz else 'Linked CSV files'}"
            )
            console_log(f"Veusz project saved: {final_path}", category="UI-VEUSZ")
            QMessageBox.information(self, "Veusz export complete", msg)

        except Exception as exc:
            console_log(f"Veusz export failed: {exc}", category="UI-VEUSZ", level="ERROR")
            self.status_banner.setText(f"✕ Export failed: {exc}")
            self.statusBar().showMessage("Veusz export failed")
            QMessageBox.critical(self, "Veusz export failed", str(exc))

    def _export_deprecated_labplot(self):
        folder = self.csv_folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "Invalid folder", "Select a valid CSV folder first.")
            return

        out_path = os.path.join(folder, "Waters_LabPlot_Project.lml")
        filename, _ = QFileDialog.getSaveFileName(self, "Save Legacy LabPlot Project", out_path, "LabPlot Project (*.lml)")
        if not filename:
            return

        try:
            final_path = create_labplot_project(
                csv_folder=folder,
                output_path=filename,
                add_plots=self.add_plots_checkbox.isChecked(),
            )
            QMessageBox.information(self, "LabPlot Export Complete", f"Legacy LabPlot project saved to:\n{final_path}")
        except Exception as exc:
            QMessageBox.critical(self, "LabPlot Export Failed", str(exc))


# Backward compatibility alias
LabPlotExporterWindow = WatersExporterWindow


# ---------------------------
# CLI & Standalone Runner
# ---------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Waters Exporter: Export MassLynx / chromatographic CSV datasets to publication-ready export projects (Veusz, etc.)."
    )
    parser.add_argument("folder", nargs="?", default=None, help="Path to folder containing CSV files")
    parser.add_argument("-o", "--output", default=None, help="Output project path (.vsz)")
    parser.add_argument("--no-plots", action="store_true", help="Do not generate Cartesian plot pages")
    parser.add_argument("--no-eval", action="store_true", help="Exclude 00_evaluated_data.csv")
    parser.add_argument("--link-csv", action="store_true", help="Link CSV files in Veusz rather than embedding data values")
    parser.add_argument("--page-width", default="18cm", help="Veusz page width (default: 18cm)")
    parser.add_argument("--page-height", default="11cm", help="Veusz page height (default: 11cm)")
    parser.add_argument("--headless", action="store_true", help="Run in headless non-GUI batch mode")
    return parser.parse_args()


def main():
    args = parse_args()

    # Headless mode
    if args.headless:
        if not args.folder or not os.path.isdir(args.folder):
            print(f"Error: Valid folder must be provided in headless mode. Got: {args.folder}", file=sys.stderr)
            sys.exit(1)

        add_plots = not args.no_plots
        include_eval = not args.no_eval
        embed_veusz = not args.link_csv
        csv_files = find_chrom_csv_files(args.folder, include_evaluated=include_eval)
        print(f"Discovered {len(csv_files)} datasets in {args.folder}")

        base_out = args.output or os.path.join(args.folder, "Waters_Veusz_Project.vsz")
        res = create_veusz_project(
            csv_folder=args.folder,
            output_path=base_out,
            add_plots=add_plots,
            embed_data=embed_veusz,
            page_width=args.page_width,
            page_height=args.page_height,
            csv_files=csv_files,
        )
        print(f"Successfully generated Veusz project: {res}")
        sys.exit(0)

    # GUI Mode
    qt_app = QApplication(sys.argv)
    window = WatersExporterWindow(initial_folder=args.folder)
    if args.output:
        window.output_edit.setText(args.output)
    if args.no_plots:
        window.add_plots_checkbox.setChecked(False)
    if args.no_eval:
        window.include_evaluated_checkbox.setChecked(False)
    if args.link_csv:
        window.veusz_embed_checkbox.setChecked(False)
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
