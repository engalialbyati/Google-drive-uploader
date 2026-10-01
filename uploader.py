#!/usr/bin/env python3
"""
Drive Uploader - resumable bulk uploads to Google Drive.

Picks a local folder, then either uploads into an existing Drive folder
(paste its link) or creates a new folder with the same name in My Drive.
Files already on Drive with the same size are skipped, so an interrupted
upload is resumed by simply pressing Start Upload again.
The heavy lifting is done by tools/rclone.exe next to this script.
"""
import json
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_DIR = os.path.dirname(os.path.abspath(__file__))
RCLONE = os.path.join(APP_DIR, "tools", "rclone.exe")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
REPORT_FILE = os.path.join(APP_DIR, "upload_report.txt")
MISSING_FILE = os.path.join(APP_DIR, "missing_on_drive.txt")
SETUP_BAT = os.path.join(APP_DIR, "Connect Google Account.bat")


def _local_default_link():
    """Optional private file (default_link.txt, kept out of git) holding your
    own Drive folder link, pre-filled into the destination field."""
    try:
        with open(os.path.join(APP_DIR, "default_link.txt"), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


DEFAULT_LINK = _local_default_link()
CREATE_NO_WINDOW = 0x08000000
LOG_MAX_LINES = 500


def config_path():
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "rclone", "rclone.conf")


def remote_configured():
    try:
        with open(config_path(), "r", encoding="utf-8", errors="replace") as fh:
            return "[gdrive]" in fh.read()
    except OSError:
        return False


def folder_id_from_link(link):
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", link.strip())
    return m.group(1) if m else None


def fmt_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024.0


def fmt_eta(sec):
    if sec is None:
        return "—"
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        return "—"
    if sec < 0:
        return "—"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def to_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.ui_q = queue.Queue()
        self.running_kind = None
        self.cancelled = False
        self.log_lines = 0
        root.title("Drive Uploader")
        root.minsize(760, 540)
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass
        self._build_ui()
        self._load_settings()
        self._refresh_status()
        root.bind("<FocusIn>", lambda e: self._refresh_status())
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._pump_queue)

    # ---------- UI ----------

    def _build_ui(self):
        pad = {"padx": 12, "pady": 4}
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Drive Uploader", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        self.status_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.status_var, foreground="#0a6").pack(anchor="w", pady=(0, 8))

        srcf = ttk.LabelFrame(outer, text="1 · Folder to upload", padding=10)
        srcf.pack(fill="x", **pad)
        self.src_var = tk.StringVar()
        ttk.Entry(srcf, textvariable=self.src_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(srcf, text="Browse…", command=self._browse).pack(side="right")

        dstf = ttk.LabelFrame(outer, text="2 · Google Drive destination", padding=10)
        dstf.pack(fill="x", **pad)
        self.mode = tk.StringVar(value="existing")
        rb1 = ttk.Radiobutton(dstf, text="Upload into my existing Drive folder (paste its link):",
                              variable=self.mode, value="existing", command=self._sync_dest)
        rb1.pack(anchor="w")
        self.link_var = tk.StringVar(value=DEFAULT_LINK)
        self.link_entry = ttk.Entry(dstf, textvariable=self.link_var)
        self.link_entry.pack(fill="x", padx=(24, 0), pady=(2, 6))
        rb2 = ttk.Radiobutton(dstf, text="Create a folder in My Drive named:",
                              variable=self.mode, value="new", command=self._sync_dest)
        rb2.pack(anchor="w")
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(dstf, textvariable=self.name_var, width=44)
        self.name_entry.pack(padx=(24, 0))
        self._sync_dest()

        actf = ttk.LabelFrame(outer, text="3 · Run", padding=10)
        actf.pack(fill="x", **pad)
        self.btn_connect = ttk.Button(actf, text="Connect Google Account", command=self._connect)
        self.btn_connect.pack(side="left")
        self.btn_check = ttk.Button(actf, text="Check what's uploaded", command=self._start_check)
        self.btn_check.pack(side="left", padx=(12, 0))
        self.btn_start = ttk.Button(actf, text="Start Upload", command=self._start_upload)
        self.btn_start.pack(side="left", padx=(12, 0))
        self.btn_cancel = ttk.Button(actf, text="Cancel", command=self._cancel, state="disabled")
        self.btn_cancel.pack(side="right")

        pf = ttk.LabelFrame(outer, text="Progress", padding=10)
        pf.pack(fill="x", **pad)
        self.prog_var = tk.StringVar(value="Idle.")
        ttk.Label(pf, textvariable=self.prog_var).pack(anchor="w")
        self.bar = ttk.Progressbar(pf, maximum=100.0)
        self.bar.pack(fill="x", pady=(4, 0))

        lf = ttk.LabelFrame(outer, text="Log", padding=6)
        lf.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(lf, height=10, wrap="none", state="disabled",
                                font=("Consolas", 9), background="#1e1e1e",
                                foreground="#d4d4d4", insertbackground="#d4d4d4")
        scroll = ttk.Scrollbar(lf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _sync_dest(self):
        if self.mode.get() == "existing":
            self.link_entry.configure(state="normal")
            self.name_entry.configure(state="disabled")
        else:
            self.link_entry.configure(state="disabled")
            self.name_entry.configure(state="normal")

    # ---------- settings ----------

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                s = json.load(fh)
            self.src_var.set(s.get("src", ""))
            self.link_var.set(s.get("link", DEFAULT_LINK))
            self.name_var.set(s.get("name", ""))
            self.mode.set(s.get("mode", "existing"))
            self._sync_dest()
        except (OSError, ValueError):
            pass

    def _save_settings(self):
        s = {"src": self.src_var.get(), "link": self.link_var.get(),
             "name": self.name_var.get(), "mode": self.mode.get()}
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
                json.dump(s, fh)
        except OSError:
            pass

    # ---------- helpers ----------

    def log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_lines += 1
        if self.log_lines > LOG_MAX_LINES:
            self.log_text.delete("1.0", "200.0")
            self.log_lines -= 200
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_status(self):
        if remote_configured():
            self.status_var.set("Google account: connected ✓")
        else:
            self.status_var.set("Google account: NOT connected — click “Connect Google Account” first")

    def _set_running(self, kind):
        self.running_kind = kind
        running = kind is not None
        for btn in (self.btn_check, self.btn_start, self.btn_connect):
            btn.configure(state="disabled" if running else "normal")
        self.btn_cancel.configure(state="normal" if running else "disabled")
        if kind is None:
            self._refresh_status()

    def _dest_args(self):
        """Returns (remote_path, extra_flags) and raises ValueError on bad input."""
        if self.mode.get() == "existing":
            fid = folder_id_from_link(self.link_var.get())
            if not fid:
                raise ValueError("Paste a valid Drive folder link (it looks like\n"
                                 "https://drive.google.com/drive/folders/…)")
            return "gdrive:", ["--drive-root-folder-id", fid]
        name = self.name_var.get().strip().strip("/")
        if not name:
            raise ValueError("Enter a name for the new Drive folder.")
        return f"gdrive:{name}", []

    def _validate(self):
        src = self.src_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("Pick a folder", "Choose the local folder you want to upload first.")
            return None, None
        if not remote_configured():
            messagebox.showerror("Not connected",
                                 "Your Google account is not connected yet.\n"
                                 "Click “Connect Google Account” and finish the browser login first.")
            return None, None
        try:
            dest, extra = self._dest_args()
        except ValueError as e:
            messagebox.showerror("Destination", str(e))
            return None, None
        return src, dest + " " + " ".join(extra)

    def _spawn(self, args):
        self.cancelled = False
        self._save_settings()
        self.proc = subprocess.Popen(
            [RCLONE] + args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            cwd=APP_DIR, creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            self.ui_q.put(("line", line.rstrip("\r\n")))
        self.ui_q.put(("done", self.proc.wait()))

    # ---------- actions ----------

    def _browse(self):
        path = filedialog.askdirectory(initialdir=r"D:\ALi Saad\Books")
        if path:
            self.src_var.set(path)
            if not self.name_var.get().strip():
                self.name_var.set(os.path.basename(path))
            self._save_settings()

    def _connect(self):
        if remote_configured() and not messagebox.askyesno(
                "Reconnect?", "Your account already appears connected.\nReconnect anyway?"):
            return
        if not os.path.exists(SETUP_BAT):
            messagebox.showerror("Missing file", f"Not found:\n{SETUP_BAT}")
            return
        os.startfile(SETUP_BAT)  # noqa: opens its own console + browser login
        self.log("Waiting for you to finish the browser login… then come back here.")

    def _start_check(self):
        src, full = self._validate()
        if not src:
            return
        remote, extra = self._dest_args()
        args = ["check", src, remote] + extra + [
            "--size-only", "--combined", REPORT_FILE, "--fast-list", "-v",
            "--use-json-log", "--stats", "2s", "--stats-log-level", "NOTICE",
        ]
        self._set_running("check")
        self.bar.configure(value=0)
        self.prog_var.set("Scanning your Drive folder (first time can take a few minutes)…")
        self.log(f"$ rclone check {src} → {remote}")
        self._spawn(args)

    def _start_upload(self):
        src, full = self._validate()
        if not src:
            return
        remote, extra = self._dest_args()
        args = ["copy", src, remote] + extra + [
            "--size-only", "--transfers", "4", "--checkers", "8",
            "--drive-chunk-size", "64M", "--fast-list", "-v",
            "--use-json-log", "--stats", "1s", "--stats-log-level", "NOTICE",
        ]
        self._set_running("upload")
        self.bar.configure(value=0)
        self.prog_var.set("Starting… (files already on Drive with the same size are skipped)")
        self.log(f"$ rclone copy {src} → {remote}")
        self._spawn(args)

    def _cancel(self):
        if self.proc and self.proc.poll() is None:
            self.cancelled = True
            self.proc.terminate()
            self.log("Cancelling… (nothing is deleted; press Start Upload again to resume)")

    # ---------- process output ----------

    def _pump_queue(self):
        try:
            while True:
                kind, payload = self.ui_q.get_nowait()
                if kind == "line":
                    self._handle_line(payload)
                elif kind == "done":
                    self._handle_done(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._pump_queue)

    def _handle_line(self, line):
        try:
            obj = json.loads(line)
        except ValueError:
            return
        if "stats" in obj and isinstance(obj["stats"], dict):
            self._update_stats(obj["stats"])
            return
        level = obj.get("level", "")
        msg = (obj.get("msg") or "").strip()
        if not msg or "Config file" in msg:
            return
        if self.running_kind == "check" and level == "error" and (
                msg.startswith("file not in") or msg.startswith("sizes differ")):
            return  # expected differences, reported in the report file
        if level in ("error", "warning"):
            name = obj.get("object") or obj.get("dir") or ""
            first = msg.splitlines()[0]
            self.log(f"[{level.upper()}] {name}: {first}" if name else f"[{level.upper()}] {first}")
        elif level == "info" and msg.startswith("Copied"):
            self.log(f"✓ uploaded {obj.get('object', '')}")

    def _update_stats(self, st):
        tr, tot = to_num(st.get("transfers")), to_num(st.get("totalTransfers"))
        ck, tck = to_num(st.get("checks")), to_num(st.get("totalChecks"))
        b, tb = to_num(st.get("bytes")), to_num(st.get("totalBytes"))
        speed = to_num(st.get("speed")) or 0
        eta = st.get("eta")
        listed = to_num(st.get("listed")) or 0

        if tb and tb > 0:  # an upload is in flight
            pct = min(100.0, (b or 0.0) / tb * 100.0)
            self.bar.configure(value=pct)
            self.prog_var.set(
                f"Uploading: {int(tr or 0):,} / {int(tot):,} files  •  "
                f"{fmt_bytes(b)} / {fmt_bytes(tb)} ({pct:.1f}%)  •  "
                f"{fmt_bytes(speed)}/s  •  ETA {fmt_eta(eta)}")
        elif tck and tck > 0:  # checking phase
            self.prog_var.set(f"Comparing files: {int(ck or 0):,} / {int(tck):,}")
        elif listed:
            self.prog_var.set(f"Scanning… {int(listed):,} files found so far")

    def _handle_done(self, code):
        kind = self.running_kind
        self._set_running(None)
        if kind == "check":
            self._finish_check()
        elif kind == "upload":
            if self.cancelled:
                self.prog_var.set("Cancelled. Press Start Upload any time to resume.")
            elif code == 0:
                self.bar.configure(value=100)
                self.prog_var.set("✓ Upload finished — everything is on Google Drive.")
                messagebox.showinfo("Done", "Upload finished successfully!\n\n"
                                    "All files from the folder are now on Google Drive.")
            else:
                self.prog_var.set(f"Finished with errors (see log). Press Start Upload to retry the rest.")
                messagebox.showwarning("Finished with errors",
                                       "Some files did not upload (details in the log).\n"
                                       "Press Start Upload again — completed files are skipped\n"
                                       "and only the failures are retried.")

    def _finish_check(self):
        counts = {"=": 0, "+": 0, "*": 0, "-": 0, "!": 0}
        missing, on_drive_only, differ = [], [], []
        try:
            with open(REPORT_FILE, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            lines = []
        for ln in lines:
            if len(ln) < 3 or ln[1] != " ":
                continue
            tag, path = ln[0], ln[2:]
            counts[tag] = counts.get(tag, 0) + 1
            if tag == "+":
                missing.append(path)
            elif tag == "*":
                differ.append(path)
            elif tag == "-":
                on_drive_only.append(path)

        with open(MISSING_FILE, "w", encoding="utf-8") as fh:
            fh.write("Files NOT on Google Drive yet (need uploading):\n")
            fh.writelines(p + "\n" for p in missing)
            fh.write("\nFiles with a different size (will be re-uploaded):\n")
            fh.writelines(p + "\n" for p in differ)
            fh.write("\nFiles on Drive but not in the local folder:\n")
            fh.writelines(p + "\n" for p in on_drive_only)

        summary = (f"Already on Drive ✓: {counts['=']:,}\n"
                   f"Missing (need upload): {counts['+']:,}\n"
                   f"Different size (re-upload): {counts['*']:,}\n"
                   f"On Drive only: {counts['-']:,}")
        self.prog_var.set(f"Check done — {counts['=']:,} uploaded ✓, {counts['+']:,} missing.")
        self.log(summary.replace("\n", "  |  "))
        self.log(f"Full report: {REPORT_FILE}")
        self.log(f"Missing list: {MISSING_FILE}")
        messagebox.showinfo(
            "Check complete",
            summary + f"\n\nFull report:\n{REPORT_FILE}\n\nMissing-only list:\n{MISSING_FILE}\n\n"
                      "Press “Start Upload” to upload what's missing.")

    def _on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("Still running",
                                       "An upload/check is still running.\n"
                                       "Stop it and close the app?\n"
                                       "(Nothing is lost — you can resume later.)"):
                return
            self.proc.terminate()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
