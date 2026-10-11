#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List

import pandas as pd
import matplotlib.pyplot as plt
from rainbow.waters import masslynx

# ---------------------------
# Utility helpers
# ---------------------------

def sanitize_filename(name: str, default: str = "sample") -> str:
    """Remove problematic characters for filenames."""
    name = re.sub(r'[\\/*?:"<>|]+', "_", name.strip())
    name = name.strip(" .")
    return name or default


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


def process_func002(dat_path: str, base_name: str, chrom_dir: str, prec: int = 0):
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

    for col in spec_cols:
        wl_suffix = parse_wavelength_label(col)
        out_csv = os.path.join(chrom_dir, f"{base_name}_{wl_suffix}_nm.csv")
        out_df = pd.DataFrame({
            rt_col: pdf[rt_col],
            "absorbance": pdf[col]
        })
        out_df.to_csv(out_csv, index=False)


def extract_masslynx_folder(root: str, status_cb=None, prec: int = 0) -> str:
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
            if func001:
                process_func001(func001, base_name, mz_dir, tic_dir, prec=prec)
            if func002:
                process_func002(func002, base_name, chrom_dir, prec=prec)
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
        self.geometry("980x620")
        self.minsize(960, 560)

        # data models
        self.targets: List[tuple] = []  # (center_min, tol_minus_min, tol_plus_min)
        self.raw_root_var = tk.StringVar()
        self.csv_folder_var = tk.StringVar()
        self.center_var = tk.StringVar()
        self.tol_minus_var = tk.StringVar()
        self.tol_plus_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")

        # plotting tool vars
        self.plot_folder_var = tk.StringVar()
        self.plot_file_var = tk.StringVar()

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
        frm_plot = ttk.LabelFrame(self, text="4) Quick CSV plot")
        frm_plot.grid(row=4, column=0, columnspan=3, sticky="ew", padx=pad, pady=(0, pad))
        frm_plot.columnconfigure(1, weight=1)

        ttk.Label(frm_plot, text="Folder:").grid(row=0, column=0, sticky="w", padx=(6, 6), pady=6)
        ent_plot_folder = ttk.Entry(frm_plot, textvariable=self.plot_folder_var)
        ent_plot_folder.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=6)
        ttk.Button(frm_plot, text="Browse…", command=self.browse_plot_folder).grid(
            row=0, column=2, padx=(0, 6), pady=6
        )

        ttk.Label(frm_plot, text="CSV:").grid(row=1, column=0, sticky="w", padx=(6, 6), pady=6)
        self.plot_combo = ttk.Combobox(frm_plot, textvariable=self.plot_file_var, state="readonly")
        self.plot_combo.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=6)
        ttk.Button(frm_plot, text="Plot", command=self.plot_csv_clicked).grid(
            row=1, column=2, padx=(0, 6), pady=6
        )

        # layout weights
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)
        self.rowconfigure(2, weight=1)

        # convenience
        self.listbox.bind("<Delete>", lambda e: self.delete_selected())

    # --------- UI actions ---------

    def browse_raw_root(self):
        folder = filedialog.askdirectory(title="Select root folder with .raw runs")
        if folder:
            self.raw_root_var.set(folder)

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
        self.status_var.set("Extracting…")
        self.update_idletasks()
        try:
            chrom_dir = extract_masslynx_folder(
                root,
                status_cb=lambda s: self.status_var.set(s),
                prec=0
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

        files = [fn for fn in os.listdir(csv_folder) if fn.lower().endswith(".csv")]
        files = [fn for fn in files if fn.lower() != "00_evaluated_data.csv"]
        if not files:
            messagebox.showwarning("No CSVs found", "No CSV files found in the selected folder.")
            return

        headers = ["Sample"] + [f"{c:.3f} min" for (c, _m, _p) in self.targets]
        rows = []
        processed = 0
        self.status_var.set("Integrating…")
        self.update_idletasks()

        for i, fn in enumerate(sorted(files)):
            fpath = os.path.join(csv_folder, fn)
            try:
                times, intens = read_chrom_csv(fpath)
                if not times:
                    raise RuntimeError("Empty/invalid chromatogram")
                areas = []
                for center, tol_minus, tol_plus in self.targets:
                    area = integrate_peak_asym(
                        times, intens,
                        center, tol_minus, tol_plus,
                        smooth_win=5
                    )
                    areas.append(area)
                row = {"Sample": os.path.splitext(fn)[0]}
                for hdr, val in zip(headers[1:], areas):
                    row[hdr] = f"{val:.6g}"
                rows.append(row)
                processed += 1
                self.status_var.set(f"Integrating… {processed}/{len(files)}")
                self.update_idletasks()
            except Exception:
                row = {"Sample": os.path.splitext(fn)[0]}
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

        files = sorted(
            fn for fn in os.listdir(folder)
            if fn.lower().endswith(".csv")
        )
        self.plot_combo["values"] = files
        self.plot_file_var.set(files[0] if files else "")

    def plot_csv_clicked(self):
        folder = self.plot_folder_var.get().strip()
        fn = self.plot_file_var.get().strip()

        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Invalid folder", "Please select a valid folder to plot from.")
            return
        if not fn:
            messagebox.showerror("No file selected", "Please select a CSV file to plot.")
            return

        path = os.path.join(folder, fn)
        try:
            df = pd.read_csv(path)
        except Exception as e:
            messagebox.showerror("Read error", str(e))
            return

        if df.shape[1] < 2:
            messagebox.showerror("Invalid CSV", "CSV must contain at least two columns to plot.")
            return

        # Plot first two columns (basis code behavior)
        plt.figure()
        plt.plot(df.iloc[:, 0], df.iloc[:, 1], linestyle='-', marker='')
        plt.xlabel(str(df.columns[0]))
        plt.ylabel(str(df.columns[1]))
        plt.title(fn)
        plt.grid(True)
        plt.tight_layout()
        plt.show()


# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    app = GCApp()
    app.mainloop()
