#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import csv
import math
import fnmatch
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Optional

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
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


def find_chrom_csv_files(root: str) -> List[tuple]:
    """Find chromatogram CSVs recursively, retaining subfolders in sample names."""
    files = []
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.lower().endswith(".csv") and filename.lower() != "00_evaluated_data.csv":
                path = os.path.join(dirpath, filename)
                relative = os.path.relpath(path, root)
                sample_name = os.path.splitext(relative)[0]
                files.append((sample_name, path))
    return sorted(files, key=lambda item: item[0].casefold())


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


# ---------------------------
# GUI
# ---------------------------

class GCApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MassLynx .raw — Extract & Integrate")
        self.geometry("980x760")
        self.minsize(960, 670)

        # data models
        self.targets: List[tuple] = []  # (center_min, tol_minus_min, tol_plus_min)
        self.raw_root_var = tk.StringVar()
        self.merge_wavelengths_var = tk.StringVar()
        self.group_match_mode_var = tk.StringVar(value="Regex")
        self.preview_summary_var = tk.StringVar(value="Select a root folder, then preview samples.")
        self.csv_folder_var = tk.StringVar()
        self.center_var = tk.StringVar()
        self.tol_minus_var = tk.StringVar()
        self.tol_plus_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")

        # plotting tool vars
        self.plot_folder_var = tk.StringVar()
        self.plot_file_var = tk.StringVar()
        self.plot_dataset_var = tk.StringVar()
        self.plot_all_datasets_var = tk.BooleanVar(value=False)
        self.plot_status_var = tk.StringVar(value="Choose a folder to preview chromatograms.")
        self.plot_paths = {}
        self.plot_dataframe = None

        self._build_ui()

    def _build_ui(self):
        pad = 8

        # ===== Extraction section (top) =====
        frm_extract = ttk.LabelFrame(self, text="1) Data extraction (MassLynx .raw → csvs)")
        frm_extract.grid(row=0, column=0, columnspan=3, sticky="ew", padx=pad, pady=(pad, 0))
        frm_extract.columnconfigure(1, weight=1)

        ttk.Label(frm_extract, text="Root folder (containing .raw runs):").grid(
            row=0, column=0, sticky="w", padx=(6, 6), pady=6
        )
        ent_raw = ttk.Entry(frm_extract, textvariable=self.raw_root_var)
        ent_raw.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=6)
        ttk.Button(frm_extract, text="Browse…", command=self.browse_raw_root).grid(
            row=0, column=2, padx=(0, 6), pady=6
        )
        ttk.Button(frm_extract, text="Extract", command=self.extract_clicked).grid(
            row=0, column=3, padx=(0, 6), pady=6
        )
        ttk.Label(frm_extract, text="Merge wavelengths (nm, comma-separated):").grid(
            row=1, column=0, sticky="w", padx=(6, 6), pady=(0, 6)
        )
        ttk.Entry(frm_extract, textvariable=self.merge_wavelengths_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(0, 6), pady=(0, 6)
        )
        ttk.Label(frm_extract, text="Folder rules (one per line: pattern => folder):").grid(
            row=2, column=0, sticky="nw", padx=(6, 6), pady=(0, 6)
        )
        self.group_rules_text = tk.Text(frm_extract, height=3, width=36, wrap="none")
        self.group_rules_text.grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(0, 6), pady=(0, 6)
        )
        frm_match_mode = ttk.Frame(frm_extract)
        frm_match_mode.grid(row=2, column=3, sticky="nw", padx=(0, 6), pady=(0, 6))
        ttk.Radiobutton(
            frm_match_mode,
            text="Wildcard",
            variable=self.group_match_mode_var,
            value="Wildcard",
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            frm_match_mode,
            text="Regex",
            variable=self.group_match_mode_var,
            value="Regex",
        ).grid(row=1, column=0, sticky="w")
        ttk.Label(frm_match_mode, text=r"Folders: () | (?P<name>) => \1 | \g<name>").grid(
            row=2, column=0, sticky="w"
        )
        ttk.Label(frm_match_mode, text=r"Nested folders: / or \\").grid(
            row=3, column=0, sticky="w"
        )
        self.preview_tree = ttk.Treeview(
            frm_extract,
            columns=("sample", "folder"),
            show="headings",
            height=5,
        )
        self.preview_tree.heading("sample", text="RAW sample")
        self.preview_tree.heading("folder", text="Output subfolder")
        self.preview_tree.column("sample", width=300, anchor="w")
        self.preview_tree.column("folder", width=180, anchor="w")
        self.preview_tree.grid(
            row=3, column=1, columnspan=2, sticky="ew", padx=(0, 6), pady=(0, 6)
        )
        preview_scrollbar = ttk.Scrollbar(
            frm_extract,
            orient="vertical",
            command=self.preview_tree.yview,
        )
        preview_scrollbar.grid(row=3, column=3, sticky="ns", padx=(0, 6), pady=(0, 6))
        self.preview_tree.configure(yscrollcommand=preview_scrollbar.set)
        ttk.Button(frm_extract, text="Preview", command=self.refresh_raw_preview).grid(
            row=3, column=0, sticky="nw", padx=6, pady=(0, 6)
        )
        ttk.Label(frm_extract, textvariable=self.preview_summary_var).grid(
            row=4, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 6)
        )

        # ===== Integration: folder for CSVs =====
        frm_csv = ttk.LabelFrame(self, text="2) Integration target folder (chromatographic CSVs)")
        frm_csv.grid(row=1, column=0, columnspan=3, sticky="ew", padx=pad, pady=(pad, 0))
        frm_csv.columnconfigure(1, weight=1)

        ttk.Label(frm_csv, text="CSV folder:").grid(row=0, column=0, sticky="w", padx=(6, 6), pady=6)
        ent_csv = ttk.Entry(frm_csv, textvariable=self.csv_folder_var)
        ent_csv.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=6)
        ttk.Button(frm_csv, text="Browse…", command=self.browse_csv_folder).grid(
            row=0, column=2, padx=(0, 6), pady=6
        )

        # ===== Peak targets & list =====
        frm_inputs = ttk.LabelFrame(self, text="3) Add peak targets")
        frm_inputs.grid(row=2, column=0, sticky="nsew", padx=pad, pady=pad)
        for c in range(3):
            frm_inputs.columnconfigure(c, weight=1)

        ttk.Label(frm_inputs, text="Retention time (min):").grid(
            row=0, column=0, sticky="w", padx=6, pady=(6, 2)
        )
        ttk.Label(frm_inputs, text="Tol − (min):").grid(
            row=0, column=1, sticky="w", padx=6, pady=(6, 2)
        )
        ttk.Label(frm_inputs, text="Tol + (min):").grid(
            row=0, column=2, sticky="w", padx=6, pady=(6, 2)
        )

        ent_center = ttk.Entry(frm_inputs, textvariable=self.center_var, width=16)
        ent_tol_minus = ttk.Entry(frm_inputs, textvariable=self.tol_minus_var, width=16)
        ent_tol_plus = ttk.Entry(frm_inputs, textvariable=self.tol_plus_var, width=16)
        ent_center.grid(row=1, column=0, sticky="ew", padx=6)
        ent_tol_minus.grid(row=1, column=1, sticky="ew", padx=6)
        ent_tol_plus.grid(row=1, column=2, sticky="ew", padx=6)

        ttk.Button(frm_inputs, text="Add", command=self.add_target).grid(
            row=2, column=0, columnspan=3, pady=(8, 8)
        )

        frm_list = ttk.LabelFrame(self, text="Targets (select + Delete)")
        frm_list.grid(row=2, column=1, columnspan=2, sticky="nsew", padx=pad, pady=pad)
        frm_list.rowconfigure(0, weight=1)
        frm_list.columnconfigure(0, weight=1)
        self.listbox = tk.Listbox(frm_list, height=12, selectmode=tk.EXTENDED)
        self.listbox.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        ttk.Button(frm_list, text="Delete", command=self.delete_selected).grid(
            row=1, column=0, sticky="ew", padx=6, pady=(0, 6)
        )

        # ===== Bottom: Integrate + status =====
        frm_run = ttk.Frame(self)
        frm_run.grid(row=3, column=0, columnspan=3, sticky="ew", padx=pad, pady=(0, pad))
        frm_run.columnconfigure(0, weight=1)
        ttk.Label(frm_run, textvariable=self.status_var, foreground="#555").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(frm_run, text="Integrate", command=self.integrate_clicked).grid(
            row=0, column=1, sticky="e"
        )

        # ===== Plotting tool (BOTTOM) =====
        frm_plot = ttk.LabelFrame(self, text="4) CSV chromatogram preview")
        frm_plot.grid(row=4, column=0, columnspan=3, sticky="nsew", padx=pad, pady=(0, pad))
        frm_plot.columnconfigure(1, weight=1)
        frm_plot.rowconfigure(3, weight=1)

        ttk.Label(frm_plot, text="Folder:").grid(row=0, column=0, sticky="w", padx=(6, 6), pady=6)
        ent_plot_folder = ttk.Entry(frm_plot, textvariable=self.plot_folder_var)
        ent_plot_folder.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=6)
        ttk.Button(frm_plot, text="Browse…", command=self.browse_plot_folder).grid(
            row=0, column=2, padx=(0, 6), pady=6
        )

        ttk.Label(frm_plot, text="CSV:").grid(row=1, column=0, sticky="w", padx=(6, 6), pady=6)
        self.plot_combo = ttk.Combobox(frm_plot, textvariable=self.plot_file_var, state="readonly")
        self.plot_combo.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=6)
        ttk.Button(frm_plot, text="Reload", command=self.load_plot_csv).grid(
            row=1, column=2, padx=(0, 6), pady=6
        )
        ttk.Label(frm_plot, text="Signal:").grid(row=2, column=0, sticky="w", padx=(6, 6), pady=6)
        self.plot_dataset_combo = ttk.Combobox(
            frm_plot,
            textvariable=self.plot_dataset_var,
            state="readonly",
        )
        self.plot_dataset_combo.grid(row=2, column=1, sticky="ew", padx=(0, 6), pady=6)
        ttk.Checkbutton(
            frm_plot,
            text="Show all signals",
            variable=self.plot_all_datasets_var,
            command=self.update_plot,
        ).grid(row=2, column=2, sticky="w", padx=(0, 6), pady=6)

        self.plot_figure = Figure(figsize=(8, 3.2), dpi=100, constrained_layout=True)
        self.plot_axes = self.plot_figure.add_subplot(111)
        self.plot_canvas = FigureCanvasTkAgg(self.plot_figure, master=frm_plot)
        self.plot_canvas.get_tk_widget().grid(
            row=3, column=0, columnspan=3, sticky="nsew", padx=6, pady=(0, 4)
        )
        ttk.Label(frm_plot, textvariable=self.plot_status_var).grid(
            row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 6)
        )
        self.plot_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_plot_csv())
        self.plot_dataset_combo.bind("<<ComboboxSelected>>", lambda _event: self.update_plot())

        # layout weights
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(4, weight=1)

        # convenience
        self.listbox.bind("<Delete>", lambda e: self.delete_selected())

    # --------- UI actions ---------

    def browse_raw_root(self):
        folder = filedialog.askdirectory(title="Select root folder with .raw runs")
        if folder:
            self.raw_root_var.set(folder)
            self.refresh_raw_preview()

    def refresh_raw_preview(self):
        root = self.raw_root_var.get().strip()
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        if not root or not os.path.isdir(root):
            self.preview_summary_var.set("Select a valid root folder to preview samples.")
            return
        try:
            rules = parse_group_rules(
                self.group_rules_text.get("1.0", tk.END),
                self.group_match_mode_var.get(),
            )
        except ValueError as exc:
            self.preview_summary_var.set(str(exc))
            return

        raw_dirs = sorted(iter_raw_dirs(root))
        preview_rows = []
        try:
            for raw_dir in raw_dirs[:20]:
                raw_name = os.path.basename(raw_dir)
                group = match_group(raw_name, rules, self.group_match_mode_var.get())
                preview_rows.append((raw_name, group or "(root)"))
        except ValueError as exc:
            self.preview_summary_var.set(str(exc))
            return
        for raw_name, group in preview_rows:
            self.preview_tree.insert("", tk.END, values=(raw_name, group))
        shown = min(len(raw_dirs), 20)
        self.preview_summary_var.set(f"Found {len(raw_dirs)} RAW samples; showing {shown}.")

    def browse_csv_folder(self):
        folder = filedialog.askdirectory(title="Select folder containing chromatogram CSVs")
        if folder:
            self.csv_folder_var.set(folder)

    def add_target(self):
        try:
            center = float(self.center_var.get().strip())
            tol_minus = float(self.tol_minus_var.get().strip())
            tol_plus = float(self.tol_plus_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid input", "Please enter numeric values for time and tolerances.")
            return
        if tol_minus < 0 or tol_plus < 0:
            messagebox.showerror("Invalid input", "Tolerances must be non-negative.")
            return
        if tol_minus == 0 and tol_plus == 0:
            messagebox.showerror("Invalid input", "At least one of the tolerances must be > 0.")
            return
        self.targets.append((center, tol_minus, tol_plus))
        self.listbox.insert(
            tk.END,
            f"{center:.3f} min  - {tol_minus:.3f} min  + {tol_plus:.3f} min"
        )
        self.center_var.set("")
        self.tol_minus_var.set("")
        self.tol_plus_var.set("")

    def delete_selected(self):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        for idx in reversed(sel):
            del self.targets[idx]
            self.listbox.delete(idx)

    # --------- Core actions ---------

    def extract_clicked(self):
        root = self.raw_root_var.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showerror(
                "Invalid folder",
                "Please select a valid root folder containing MassLynx .raw runs."
            )
            return
        try:
            merge_wavelengths = parse_merge_wavelengths(self.merge_wavelengths_var.get())
        except ValueError as e:
            messagebox.showerror("Invalid wavelength", str(e))
            return
        try:
            group_rules = parse_group_rules(
                self.group_rules_text.get("1.0", tk.END),
                self.group_match_mode_var.get(),
            )
        except ValueError as e:
            messagebox.showerror("Invalid folder rule", str(e))
            return
        self.status_var.set("Extracting…")
        self.update_idletasks()
        try:
            chrom_dir = extract_masslynx_folder(
                root,
                status_cb=lambda s: self.status_var.set(s),
                prec=0,
                merge_wavelengths=merge_wavelengths,
                group_rules=group_rules,
                match_mode=self.group_match_mode_var.get(),
            )
            self.csv_folder_var.set(chrom_dir)
            self.status_var.set(f"Extraction finished → {chrom_dir}")
            messagebox.showinfo(
                "Extraction finished",
                f"Chromatographic CSV files were written to:\n{chrom_dir}\n\n"
                f"m/z and TIC CSVs are in sibling folders:\n"
                f"{os.path.dirname(chrom_dir)}"
            )
        except Exception as e:
            self.status_var.set("Extraction failed.")
            messagebox.showerror("Extraction failed", str(e))

    def integrate_clicked(self):
        csv_folder = self.csv_folder_var.get().strip()
        if not csv_folder or not os.path.isdir(csv_folder):
            messagebox.showerror("Invalid CSV folder", "Please select a valid CSV folder.")
            return
        if not self.targets:
            messagebox.showerror("No targets", "Add at least one target with time and tolerances.")
            return

        files = find_chrom_csv_files(csv_folder)
        if not files:
            messagebox.showwarning("No CSVs found", "No CSV files found in the selected folder.")
            return

        headers = ["Sample"] + [f"{c:.3f} min" for (c, _m, _p) in self.targets]
        rows = []
        processed = 0
        self.status_var.set("Integrating…")
        self.update_idletasks()

        for sample_name, fpath in files:
            try:
                channels = read_chrom_csv_channels(fpath)
                if not channels:
                    raise RuntimeError("Empty/invalid chromatogram")
                for channel_name, times, intens in channels:
                    areas = []
                    for center, tol_minus, tol_plus in self.targets:
                        area = integrate_peak_asym(
                            times, intens,
                            center, tol_minus, tol_plus,
                            smooth_win=5
                        )
                        areas.append(area)
                    row_name = sample_name
                    if len(channels) > 1:
                        row_name = f"{sample_name}_{channel_name.replace(' ', '')}"
                    row = {"Sample": row_name}
                    for hdr, val in zip(headers[1:], areas):
                        row[hdr] = f"{val:.6g}"
                    rows.append(row)
                processed += 1
                self.status_var.set(f"Integrating… {processed}/{len(files)}")
                self.update_idletasks()
            except Exception:
                row = {"Sample": sample_name}
                for hdr in headers[1:]:
                    row[hdr] = "0"
                rows.append(row)

        out_path = os.path.join(csv_folder, "00_evaluated_data.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for r in rows:
                w.writerow(r)

        self.status_var.set(f"Done. Wrote: {out_path}")
        messagebox.showinfo(
            "Finished",
            f"Integrated {processed} file(s).\nSaved:\n{out_path}"
        )

    # --------- Plotting tool actions ---------

    def browse_plot_folder(self):
        folder = filedialog.askdirectory(title="Select folder with CSV files to plot")
        if not folder:
            return
        self.plot_folder_var.set(folder)
        self.refresh_plot_files()

    def refresh_plot_files(self):
        folder = self.plot_folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            self.plot_paths = {}
            self.plot_combo["values"] = []
            self.plot_file_var.set("")
            self.plot_status_var.set("Select a valid folder to preview chromatograms.")
            return

        files = find_chrom_csv_files(folder)
        self.plot_paths = {f"{sample_name}.csv": path for sample_name, path in files}
        choices = list(self.plot_paths)
        self.plot_combo["values"] = choices
        if not choices:
            self.plot_file_var.set("")
            self.plot_dataset_combo["values"] = []
            self.plot_status_var.set("No chromatogram CSV files found in this folder.")
            self.plot_dataframe = None
            self.update_plot()
            return

        self.plot_file_var.set(choices[0])
        self.load_plot_csv()

    def load_plot_csv(self):
        selected_file = self.plot_file_var.get().strip()
        path = self.plot_paths.get(selected_file)
        if not path:
            self.plot_status_var.set("Choose a CSV file to preview.")
            return
        try:
            self.plot_dataframe = pd.read_csv(path)
        except Exception as e:
            self.plot_dataframe = None
            self.plot_status_var.set(f"Could not read CSV: {e}")
            self.update_plot()
            return

        if self.plot_dataframe.empty or len(self.plot_dataframe.columns) < 2:
            self.plot_status_var.set("CSV must contain a time column and at least one signal column.")
            self.update_plot()
            return

        rt_col = find_rt_column(self.plot_dataframe)
        signal_columns = [column for column in self.plot_dataframe.columns if column != rt_col]
        self.plot_dataset_combo["values"] = signal_columns
        if self.plot_dataset_var.get() not in signal_columns:
            self.plot_dataset_var.set(signal_columns[0] if signal_columns else "")
        self.update_plot()

    def update_plot(self):
        self.plot_axes.clear()
        df = self.plot_dataframe
        if df is None or df.empty or len(df.columns) < 2:
            self.plot_axes.set_title("CSV chromatogram preview")
            self.plot_canvas.draw_idle()
            return

        rt_col = find_rt_column(df)
        signal_columns = [column for column in df.columns if column != rt_col]
        if self.plot_all_datasets_var.get():
            selected_columns = signal_columns
        else:
            selected = self.plot_dataset_var.get()
            selected_columns = [selected] if selected in signal_columns else []

        x_values = pd.to_numeric(df[rt_col], errors="coerce")
        plotted = 0
        for column in selected_columns:
            y_values = pd.to_numeric(df[column], errors="coerce")
            valid = x_values.notna() & y_values.notna()
            if not valid.any():
                continue
            self.plot_axes.plot(x_values[valid], y_values[valid], label=str(column), linewidth=1.4)
            plotted += 1

        self.plot_axes.set_xlabel(str(rt_col))
        self.plot_axes.set_ylabel("Signal")
        self.plot_axes.set_title(self.plot_file_var.get())
        self.plot_axes.grid(True, alpha=0.3)
        if plotted > 1:
            self.plot_axes.legend(loc="best")
        if plotted == 0:
            self.plot_status_var.set("No numeric signal data available to plot.")
        else:
            mode = "all signals" if self.plot_all_datasets_var.get() else str(selected_columns[0])
            self.plot_status_var.set(f"Showing {mode} ({plotted} trace{'s' if plotted != 1 else ''}).")
        self.plot_canvas.draw_idle()


# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    app = GCApp()
    app.mainloop()
