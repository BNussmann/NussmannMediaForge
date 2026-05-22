import contextlib
import os
import queue
import re
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import requests
from rich.console import Console

import mediaforge_core as core
from config_manager import get_config_path, load_settings, save_settings


VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".ts", ".m2ts")
PROGRESS_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%")
LANGUAGE_LABELS = {code: data["label"] for code, data in core.LANGUAGE_OPTIONS.items()}
LANGUAGE_CODES_BY_LABEL = {label: code for code, label in LANGUAGE_LABELS.items()}


def safe_name(value):
    cleaned = re.sub(r"[^\w\s().-]", "", value, flags=re.UNICODE).strip()
    return cleaned or "Untitled"


def resource_path(relative_path):
    base_path = getattr(__import__("sys"), "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


class QueueWriter:
    def __init__(self, log_queue):
        self.log_queue = log_queue

    def write(self, text):
        if text:
            self.log_queue.put(text)
            match = PROGRESS_RE.search(text)
            if match:
                value = float(match.group(1))
                if 0 <= value <= 100:
                    self.log_queue.put(("progress", value / 100))

    def flush(self):
        pass


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, settings, on_save):
        super().__init__(parent)
        self.title("Settings")
        self.geometry("760x520")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.on_save = on_save
        self.vars = {
            "makemkv_path": ctk.StringVar(value=settings.get("makemkv_path", "")),
            "mkvtoolnix_path": ctk.StringVar(value=settings.get("mkvtoolnix_path", "")),
            "handbrake_path": ctk.StringVar(value=settings.get("handbrake_path", "")),
            "tmdb_api_key": ctk.StringVar(value=settings.get("tmdb_api_key", "")),
            "encoder": ctk.StringVar(value=settings.get("encoder", "nvidia")),
            "output_dir": ctk.StringVar(value=settings.get("output_dir", "")),
            "default_language": ctk.StringVar(value=LANGUAGE_LABELS.get(settings.get("default_language", "ger"), "German")),
        }
        self.grid_columnconfigure(1, weight=1)
        self._build()

    def _build(self):
        title = ctk.CTkLabel(self, text="Settings", font=ctk.CTkFont(size=24, weight="bold"))
        title.grid(row=0, column=0, columnspan=3, sticky="w", padx=24, pady=(22, 16))

        self._path_row(1, "MakeMKV", "makemkv_path", "makemkvcon64.exe", filetypes=[("MakeMKV", "makemkvcon64.exe"), ("EXE", "*.exe")])
        self._path_row(2, "MKVToolNix Folder", "mkvtoolnix_path", "Folder containing mkvmerge.exe", folder=True)
        self._path_row(3, "HandBrakeCLI", "handbrake_path", "HandBrakeCLI.exe", filetypes=[("HandBrakeCLI", "HandBrakeCLI.exe"), ("EXE", "*.exe")])
        self._path_row(4, "Default Output", "output_dir", "Output folder", folder=True)

        ctk.CTkLabel(self, text="TMDb API Key").grid(row=5, column=0, sticky="w", padx=24, pady=10)
        ctk.CTkEntry(self, textvariable=self.vars["tmdb_api_key"], show="*", placeholder_text="API Key").grid(row=5, column=1, sticky="ew", padx=8, pady=10)

        ctk.CTkLabel(self, text="Hardware Encoder").grid(row=6, column=0, sticky="w", padx=24, pady=10)
        ctk.CTkSegmentedButton(
            self,
            values=["nvidia", "amd"],
            variable=self.vars["encoder"],
        ).grid(row=6, column=1, sticky="w", padx=8, pady=10)

        ctk.CTkLabel(self, text="Default Audio Language").grid(row=7, column=0, sticky="w", padx=24, pady=10)
        ctk.CTkOptionMenu(
            self,
            variable=self.vars["default_language"],
            values=list(LANGUAGE_CODES_BY_LABEL.keys()),
        ).grid(row=7, column=1, sticky="w", padx=8, pady=10)

        config_hint = ctk.CTkLabel(self, text=f"Saved at: {get_config_path()}", text_color=("gray35", "gray70"))
        config_hint.grid(row=8, column=0, columnspan=3, sticky="w", padx=24, pady=(10, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=9, column=0, columnspan=3, sticky="e", padx=24, pady=24)
        ctk.CTkButton(buttons, text="Cancel", fg_color="gray35", command=self.destroy).pack(side="left", padx=8)
        ctk.CTkButton(buttons, text="Save", command=self._save).pack(side="left")

    def _path_row(self, row, label, key, placeholder, folder=False, filetypes=None):
        ctk.CTkLabel(self, text=label).grid(row=row, column=0, sticky="w", padx=24, pady=10)
        ctk.CTkEntry(self, textvariable=self.vars[key], placeholder_text=placeholder).grid(row=row, column=1, sticky="ew", padx=8, pady=10)
        command = lambda: self._browse(key, folder=folder, filetypes=filetypes)
        ctk.CTkButton(self, text="Browse", width=96, command=command).grid(row=row, column=2, padx=(8, 24), pady=10)

    def _browse(self, key, folder=False, filetypes=None):
        if folder:
            selected = filedialog.askdirectory(parent=self)
        else:
            selected = filedialog.askopenfilename(parent=self, filetypes=filetypes or [("EXE", "*.exe"), ("All files", "*.*")])
        if selected:
            self.vars[key].set(selected)

    def _save(self):
        values = {key: var.get().strip() for key, var in self.vars.items()}
        values["default_language"] = LANGUAGE_CODES_BY_LABEL.get(values["default_language"], "ger")
        self.on_save(save_settings(values))
        self.destroy()


class NussmannMediaForgeApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Nussmann MediaForge")
        with contextlib.suppress(Exception):
            self.iconbitmap(resource_path(os.path.join("assets", "app.ico")))
        self.geometry("1180x780")
        self.minsize(1040, 680)
        self.settings = load_settings()
        self.log_queue = queue.Queue()
        self.worker_running = False
        self.search_results = []
        self.result_labels = []
        self.tool_search_results = []
        self.tool_result_labels = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_tabs()
        self.after(120, self._drain_log_queue)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Nussmann MediaForge", font=ctk.CTkFont(size=30, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(header, text="Settings", width=150, command=self._open_settings).grid(row=0, column=1, sticky="e")
        self.progress_bar = ctk.CTkProgressBar(header, mode="determinate")
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.progress_bar.set(0)
        self.progress_status = ctk.CTkLabel(header, text="Idle", text_color=("gray35", "gray70"))
        self.progress_status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=24, pady=(4, 20))
        self.disc_tab = self.tabs.add("Disc Ripper")
        self.tools_tab = self.tabs.add("Tools")
        self._build_disc_tab()
        self._build_tools_tab()

    def _build_disc_tab(self):
        self.disc_tab.grid_columnconfigure(0, weight=1)
        self.disc_tab.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(self.disc_tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=16)
        left.grid_columnconfigure(1, weight=1)

        right = ctk.CTkFrame(self.disc_tab)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)
        right.grid_columnconfigure(0, weight=1)

        self.media_type = ctk.StringVar(value="TV Show")
        ctk.CTkLabel(left, text="Source", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(18, 10))
        ctk.CTkSegmentedButton(left, values=["TV Show", "Movie"], variable=self.media_type, command=lambda _: self._sync_disc_mode()).grid(row=1, column=0, columnspan=3, sticky="ew", padx=18, pady=8)

        ctk.CTkLabel(left, text="TMDb Search").grid(row=2, column=0, sticky="w", padx=18, pady=10)
        self.query_var = ctk.StringVar()
        ctk.CTkEntry(left, textvariable=self.query_var, placeholder_text="Enter title").grid(row=2, column=1, sticky="ew", padx=8, pady=10)
        ctk.CTkButton(left, text="Search", width=100, command=self._start_search).grid(row=2, column=2, padx=(8, 18), pady=10)

        ctk.CTkLabel(left, text="Match").grid(row=3, column=0, sticky="w", padx=18, pady=10)
        self.result_var = ctk.StringVar(value="No search yet")
        self.result_menu = ctk.CTkOptionMenu(left, variable=self.result_var, values=["No search yet"])
        self.result_menu.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(8, 18), pady=10)

        self.series_frame = ctk.CTkFrame(left, fg_color="transparent")
        self.series_frame.grid(row=4, column=0, columnspan=3, sticky="ew", padx=18, pady=(6, 0))
        for i in range(6):
            self.series_frame.grid_columnconfigure(i, weight=1)
        self.season_var = ctk.StringVar(value="1")
        self.start_episode_var = ctk.StringVar(value="1")
        self.mapping_var = ctk.StringVar(value="smart")
        ctk.CTkLabel(self.series_frame, text="Season").grid(row=0, column=0, sticky="w")
        ctk.CTkEntry(self.series_frame, textvariable=self.season_var, width=72).grid(row=0, column=1, sticky="w", padx=(8, 18))
        ctk.CTkLabel(self.series_frame, text="Start episode").grid(row=0, column=2, sticky="w")
        ctk.CTkEntry(self.series_frame, textvariable=self.start_episode_var, width=72).grid(row=0, column=3, sticky="w", padx=(8, 18))
        ctk.CTkLabel(self.series_frame, text="Mapping").grid(row=0, column=4, sticky="w")
        ctk.CTkOptionMenu(self.series_frame, variable=self.mapping_var, values=["smart", "auto"]).grid(row=0, column=5, sticky="ew", padx=(8, 0))

        ctk.CTkLabel(left, text="Output folder").grid(row=5, column=0, sticky="w", padx=18, pady=10)
        self.output_var = ctk.StringVar(value=self.settings.get("output_dir", ""))
        ctk.CTkEntry(left, textvariable=self.output_var, placeholder_text="Empty = automatic project folder").grid(row=5, column=1, sticky="ew", padx=8, pady=10)
        ctk.CTkButton(left, text="Browse", width=100, command=lambda: self._browse_folder(self.output_var)).grid(row=5, column=2, padx=(8, 18), pady=10)

        self.compress_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(left, text="Compress with hardware H.265 after ripping", variable=self.compress_var).grid(row=6, column=0, columnspan=3, sticky="w", padx=18, pady=14)

        self.rip_button = ctk.CTkButton(left, text="Scan and rip disc", height=44, command=self._start_disc_rip)
        self.rip_button.grid(row=7, column=0, columnspan=3, sticky="ew", padx=18, pady=(18, 8))
        self.disc_status = ctk.CTkLabel(left, text="Ready", text_color=("gray30", "gray75"))
        self.disc_status.grid(row=8, column=0, columnspan=3, sticky="w", padx=18, pady=(6, 18))

        ctk.CTkLabel(right, text="Run Log", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 10))
        info = (
            "Progress and command output appear here while jobs are running.\n"
            "Use Settings to configure MakeMKV, MKVToolNix, HandBrakeCLI, TMDb, and the hardware encoder."
        )
        ctk.CTkLabel(right, text=info, justify="left", anchor="w").grid(row=1, column=0, sticky="ew", padx=18, pady=8)

        self.log_box = ctk.CTkTextbox(right, height=360)
        self.log_box.grid(row=2, column=0, sticky="nsew", padx=18, pady=(12, 18))
        right.grid_rowconfigure(2, weight=1)
        self.log_box.insert("end", "Ready.\n")
        self.log_box.configure(state="disabled")

    def _build_tools_tab(self):
        self.tools_tab.grid_columnconfigure(0, weight=1)
        self.tools_tab.grid_rowconfigure(0, weight=1)
        panel = ctk.CTkFrame(self.tools_tab)
        panel.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        panel.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(panel, text="Folder Tools", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(18, 12))
        ctk.CTkLabel(panel, text="Folder").grid(row=1, column=0, sticky="w", padx=18, pady=10)
        self.folder_var = ctk.StringVar()
        ctk.CTkEntry(panel, textvariable=self.folder_var, placeholder_text="Folder containing video files").grid(row=1, column=1, sticky="ew", padx=8, pady=10)
        ctk.CTkButton(panel, text="Browse", width=100, command=lambda: self._browse_folder(self.folder_var)).grid(row=1, column=2, padx=(8, 18), pady=10)

        ctk.CTkLabel(panel, text="Single file").grid(row=2, column=0, sticky="w", padx=18, pady=10)
        self.single_file_var = ctk.StringVar()
        ctk.CTkEntry(panel, textvariable=self.single_file_var, placeholder_text="Single video file for one-off transcode or movie rename").grid(row=2, column=1, sticky="ew", padx=8, pady=10)
        ctk.CTkButton(panel, text="Browse", width=100, command=lambda: self._browse_file(self.single_file_var)).grid(row=2, column=2, padx=(8, 18), pady=10)

        ctk.CTkLabel(panel, text="Search type").grid(row=3, column=0, sticky="w", padx=18, pady=10)
        self.tool_media_type = ctk.StringVar(value="TV Show")
        ctk.CTkSegmentedButton(panel, values=["TV Show", "Movie"], variable=self.tool_media_type, command=lambda _: self._sync_tool_mode()).grid(row=3, column=1, sticky="ew", padx=8, pady=10)

        ctk.CTkLabel(panel, text="Title").grid(row=4, column=0, sticky="w", padx=18, pady=10)
        self.rename_show_var = ctk.StringVar()
        ctk.CTkEntry(panel, textvariable=self.rename_show_var, placeholder_text="Search title for Auto Rename or movie naming").grid(row=4, column=1, sticky="ew", padx=8, pady=10)
        ctk.CTkButton(panel, text="Search TMDb", width=120, command=self._start_tool_media_search).grid(row=4, column=2, padx=(8, 18), pady=10)

        ctk.CTkLabel(panel, text="TMDb match").grid(row=5, column=0, sticky="w", padx=18, pady=10)
        self.tool_result_var = ctk.StringVar(value="No search yet")
        self.tool_result_menu = ctk.CTkOptionMenu(panel, variable=self.tool_result_var, values=["No search yet"])
        self.tool_result_menu.grid(row=5, column=1, columnspan=2, sticky="ew", padx=(8, 18), pady=10)

        self.tool_season_label = ctk.CTkLabel(panel, text="Season")
        self.tool_season_label.grid(row=6, column=0, sticky="w", padx=18, pady=10)
        self.rename_season_var = ctk.StringVar(value="1")
        self.tool_season_entry = ctk.CTkEntry(panel, textvariable=self.rename_season_var, width=86)
        self.tool_season_entry.grid(row=6, column=1, sticky="w", padx=8, pady=10)

        language_label = self._default_language_label()
        guide = (
            "Auto Rename: for TV shows, choose a folder, search TMDb, select the show, enter the season, then rename files in episode order. For movies, choose one file, search TMDb as Movie, then rename it to Title (Year).\n\n"
            "Single Transcode: choose one file, optionally select a movie match for a clean output name, then transcode it into a Transcoded subfolder.\n\n"
            "Batch Transcode: choose a folder, then convert supported video files into the Transcoded subfolder.\n\n"
            f"Set {language_label} Default Audio: choose a folder, then update MKV/MP4 files so the first {language_label} audio track becomes the default track when one is found."
        )
        self.tools_guide_var = ctk.StringVar(value=guide)
        ctk.CTkLabel(panel, text="How to use these tools", font=ctk.CTkFont(size=16, weight="bold")).grid(row=7, column=0, columnspan=3, sticky="w", padx=18, pady=(18, 6))
        ctk.CTkLabel(panel, textvariable=self.tools_guide_var, justify="left", anchor="w").grid(row=8, column=0, columnspan=3, sticky="ew", padx=18, pady=(0, 12))

        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.grid(row=9, column=0, columnspan=3, sticky="ew", padx=18, pady=20)
        actions.grid_columnconfigure((0, 1, 2, 3), weight=1)
        ctk.CTkButton(actions, text="Auto-Rename", command=self._start_auto_rename).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(actions, text="Single Transcode", command=self._start_single_transcode).grid(row=0, column=1, sticky="ew", padx=8)
        ctk.CTkButton(actions, text="Batch Transcode", command=self._start_batch_transcode).grid(row=0, column=2, sticky="ew", padx=8)
        self.default_audio_button = ctk.CTkButton(actions, text=f"Set {language_label} Default Audio", command=self._start_default_audio)
        self.default_audio_button.grid(row=0, column=3, sticky="ew", padx=(8, 0))

    def _open_settings(self):
        SettingsDialog(self, self.settings, self._settings_saved)

    def _settings_saved(self, settings):
        self.settings = settings
        self.output_var.set(settings.get("output_dir", ""))
        self._refresh_language_text()
        self._log("Settings saved.\n")

    def _default_language_label(self):
        return LANGUAGE_LABELS.get(self.settings.get("default_language", "ger"), "German")

    def _refresh_language_text(self):
        if not hasattr(self, "tools_guide_var"):
            return
        language_label = self._default_language_label()
        self.tools_guide_var.set(
            "Auto Rename: for TV shows, choose a folder, search TMDb, select the show, enter the season, then rename files in episode order. For movies, choose one file, search TMDb as Movie, then rename it to Title (Year).\n\n"
            "Single Transcode: choose one file, optionally select a movie match for a clean output name, then transcode it into a Transcoded subfolder.\n\n"
            "Batch Transcode: choose a folder, then convert supported video files into the Transcoded subfolder.\n\n"
            f"Set {language_label} Default Audio: choose a folder, then update MKV/MP4 files so the first {language_label} audio track becomes the default track when one is found."
        )
        self.default_audio_button.configure(text=f"Set {language_label} Default Audio")

    def _sync_disc_mode(self):
        if self.media_type.get() == "TV Show":
            self.series_frame.grid()
        else:
            self.series_frame.grid_remove()

    def _browse_folder(self, variable):
        selected = filedialog.askdirectory(parent=self)
        if selected:
            variable.set(selected)

    def _browse_file(self, variable):
        filetypes = [
            ("Video files", "*.mkv *.mp4 *.avi *.ts *.m2ts"),
            ("All files", "*.*"),
        ]
        selected = filedialog.askopenfilename(parent=self, filetypes=filetypes)
        if selected:
            variable.set(selected)

    def _sync_tool_mode(self):
        if self.tool_media_type.get() == "TV Show":
            self.tool_season_label.grid()
            self.tool_season_entry.grid()
        else:
            self.tool_season_label.grid_remove()
            self.tool_season_entry.grid_remove()

    def _selected_result(self):
        label = self.result_var.get()
        if label in self.result_labels:
            index = self.result_labels.index(label)
            if index < len(self.search_results):
                return self.search_results[index]
        return None

    def _start_search(self):
        query = self.query_var.get().strip()
        media_type = self.media_type.get()
        if not query:
            messagebox.showwarning("TMDb Search", "Please enter a title first.")
            return
        api_key = self._api_key()
        if not api_key:
            self._open_settings()
            messagebox.showwarning("TMDb API Key", "Please add your TMDb API key in Settings.")
            return
        self._run_worker("TMDb Search", self._search_worker, query, media_type, api_key)

    def _search_worker(self, query, media_type, api_key):
        tmdb = core.TMDbClient(api_key)
        results = tmdb.search_tv_show(query) if media_type == "TV Show" else tmdb.search_movie(query)
        labels = []
        for item in results[:8]:
            title = item.get("name") or item.get("title") or "Unknown"
            date = item.get("first_air_date") or item.get("release_date") or ""
            year = date[:4] if date else "?"
            labels.append(f"{title} ({year})")
        self.after(0, self._set_search_results, results[:8], labels)

    def _set_search_results(self, results, labels):
        self.search_results = results
        self.result_labels = labels or ["No results"]
        self.result_menu.configure(values=self.result_labels)
        self.result_var.set(self.result_labels[0])
        self._log(f"{len(results)} TMDb results loaded.\n")

    def _selected_tool_result(self):
        label = self.tool_result_var.get()
        if label in self.tool_result_labels:
            index = self.tool_result_labels.index(label)
            if index < len(self.tool_search_results):
                return self.tool_search_results[index]
        return None

    def _start_tool_media_search(self):
        query = self.rename_show_var.get().strip()
        media_type = self.tool_media_type.get()
        if not query:
            messagebox.showwarning("TMDb Search", "Please enter a title first.")
            return
        api_key = self._api_key()
        if not api_key:
            self._open_settings()
            messagebox.showwarning("TMDb API Key", "Please add your TMDb API key in Settings.")
            return
        self._run_worker("Tool TMDb Search", self._tool_media_search_worker, query, media_type, api_key, focus_log=False)

    def _tool_media_search_worker(self, query, media_type, api_key):
        tmdb = core.TMDbClient(api_key)
        results = tmdb.search_tv_show(query) if media_type == "TV Show" else tmdb.search_movie(query)
        labels = []
        for item in results[:8]:
            title = item.get("name") or item.get("title") or "Unknown"
            date = item.get("first_air_date") or item.get("release_date") or ""
            year = date[:4] if date else "?"
            labels.append(f"{title} ({year})")
        self.after(0, self._set_tool_search_results, results[:8], labels)

    def _set_tool_search_results(self, results, labels):
        self.tool_search_results = results
        self.tool_result_labels = labels or ["No results"]
        self.tool_result_menu.configure(values=self.tool_result_labels)
        self.tool_result_var.set(self.tool_result_labels[0])
        self._log(f"{len(results)} tool TMDb results loaded.\n")

    def _start_disc_rip(self):
        selected = self._selected_result()
        if not selected:
            messagebox.showwarning("Disc Ripper", "Please search TMDb and select a result first.")
            return
        settings = self.settings.copy()
        media_type = self.media_type.get()
        output_dir = self.output_var.get().strip()
        compress = self.compress_var.get()
        try:
            season = int(self.season_var.get())
            start_episode = int(self.start_episode_var.get())
        except ValueError:
            messagebox.showwarning("Disc Ripper", "Season and start episode must be numbers.")
            return
        self._run_worker("Disc Rip", self._disc_rip_worker, media_type, selected, season, start_episode, self.mapping_var.get(), output_dir, compress, settings)

    def _disc_rip_worker(self, media_type, selected, season, start_episode, mapping, output_dir, compress, settings):
        ripper = core.DiscRipper(settings=settings, interactive=False, progress_callback=self._progress_callback)
        self._validate_ripper(ripper, need_makemkv=True, need_handbrake=compress)
        api_key = settings.get("tmdb_api_key") or os.environ.get("TMDB_API_KEY")
        tmdb = core.TMDbClient(api_key)

        if media_type == "TV Show":
            episodes = tmdb.get_season_episodes(selected["id"], season)
            if not episodes:
                raise RuntimeError("No episodes were found for this season.")
            runtimes = [e.get("runtime", 0) for e in episodes if e.get("runtime", 0) > 0]
            if runtimes:
                scan_min_seconds = max(600, int(min(runtimes) * 60 * 0.75))
                scan_max_seconds = int(max(runtimes) * 60 * 1.5)
            else:
                scan_min_seconds = core.MIN_DURATION_SECONDS
                scan_max_seconds = 7200

            titles = ripper.scan_disc(min_length_seconds=scan_min_seconds)
            if not titles:
                raise RuntimeError("No matching titles were found on the disc.")
            titles = [title for title in titles if title["seconds"] <= scan_max_seconds]
            match_mode = "linear" if mapping == "auto" else "smart"
            matched = ripper.match_titles_to_episodes(titles, episodes, start_episode, mode=match_mode)
            if not matched:
                raise RuntimeError("No episodes could be mapped to disc titles.")

            show_name = selected.get("name", "TV Show")
            for title, episode, status in matched:
                episode["show_name"] = show_name
                episode["season"] = season
                core.console.print(f"Mapping: Title {title['id']} ({title['duration']}) -> E{episode['episode_number']:02} {episode['name']} [{status}]")

            target_dir = output_dir or os.path.join(os.getcwd(), safe_name(show_name), f"Season {season}")
            ripper.rip_all_matched(matched, target_dir, scan_min_seconds, compress=compress)
            return

        movie_title = selected.get("title", "Movie")
        release_year = (selected.get("release_date") or "Unknown")[:4]
        runtime = selected.get("runtime", 0)
        if not runtime:
            response = requests.get(
                f"{tmdb.BASE_URL}/movie/{selected['id']}",
                params={"api_key": api_key, "language": "de-DE"},
                timeout=30,
            )
            if response.status_code == 200:
                runtime = response.json().get("runtime", 0)

        scan_min_seconds = max(1800, int(runtime * 60 * 0.5)) if runtime else 3600
        titles = ripper.scan_disc(min_length_seconds=scan_min_seconds)
        if not titles:
            raise RuntimeError("No matching titles were found on the disc.")

        if runtime:
            selected_title = min(titles, key=lambda title: abs(title["seconds"] - runtime * 60))
        else:
            selected_title = max(titles, key=lambda title: title.get("size_bytes", 0))
        core.console.print(f"Selected movie title: Disc ID {selected_title['id']} ({selected_title.get('duration', '?')})")

        target_dir = output_dir or os.path.join(os.getcwd(), f"{safe_name(movie_title)} ({release_year})")
        filename = f"{safe_name(movie_title)} ({release_year}).mkv"
        ripper.rip_title(selected_title["id"], target_dir, filename, expected_size_bytes=selected_title.get("size_bytes", 0), compress=compress)

    def _start_batch_transcode(self):
        folder = self.folder_var.get().strip()
        if not self._require_folder(folder):
            return
        self._run_worker("Batch Transcode", self._batch_transcode_worker, folder, self.settings.copy())

    def _start_single_transcode(self):
        source = self.single_file_var.get().strip()
        if not self._require_video_file(source):
            return
        selected = self._selected_tool_result() if self.tool_media_type.get() == "Movie" else None
        self._run_worker("Single Transcode", self._single_transcode_worker, source, selected, self.settings.copy())

    def _single_transcode_worker(self, source, selected, settings):
        ripper = core.DiscRipper(settings=settings, interactive=False, progress_callback=self._progress_callback)
        self._validate_ripper(ripper, need_handbrake=True)
        source_path = Path(source)
        out_folder = source_path.parent / "Transcoded"
        out_folder.mkdir(exist_ok=True)

        if selected:
            title = selected.get("title") or source_path.stem
            year = (selected.get("release_date") or "Unknown")[:4]
            target_name = f"{safe_name(title)} ({year}).mp4"
        else:
            target_name = f"transcoded_{source_path.stem}.mp4"

        target = out_folder / target_name
        core.console.print(f"Transcoding single file: {source_path.name}")
        if target.exists():
            raise RuntimeError(f"Target already exists: {target}")
        ripper.transcode_video(str(source_path), str(target))

    def _batch_transcode_worker(self, folder, settings):
        ripper = core.DiscRipper(settings=settings, interactive=False, progress_callback=self._progress_callback)
        self._validate_ripper(ripper, need_handbrake=True)
        out_folder = os.path.join(folder, "Transcoded")
        os.makedirs(out_folder, exist_ok=True)
        files = [name for name in sorted(os.listdir(folder)) if name.lower().endswith(VIDEO_EXTENSIONS)]
        if not files:
            raise RuntimeError("No video files were found in the selected folder.")
        for name in files:
            source = os.path.join(folder, name)
            target = os.path.join(out_folder, f"transcoded_{Path(name).stem}.mp4")
            if os.path.exists(target):
                core.console.print(f"Skipping existing file: {target}")
                continue
            core.console.print(f"Transcoding: {name}")
            ripper.transcode_video(source, target)

    def _start_default_audio(self):
        folder = self.folder_var.get().strip()
        if not self._require_folder(folder):
            return
        self._run_worker(f"Set {self._default_language_label()} Default Audio", self._default_audio_worker, folder, self.settings.copy())

    def _default_audio_worker(self, folder, settings):
        ripper = core.DiscRipper(settings=settings, interactive=False, progress_callback=self._progress_callback)
        self._validate_ripper(ripper, need_mkvtoolnix=True)
        files = [name for name in sorted(os.listdir(folder)) if name.lower().endswith((".mkv", ".mp4"))]
        if not files:
            raise RuntimeError("No MKV/MP4 files were found in the selected folder.")
        language = settings.get("default_language", "ger")
        language_label = LANGUAGE_LABELS.get(language, "German")
        for name in files:
            core.console.print(f"Checking {language_label} default audio: {name}")
            ripper.set_default_audio(os.path.join(folder, name), language=language)

    def _start_auto_rename(self):
        media_type = self.tool_media_type.get()
        show_name = self.rename_show_var.get().strip()
        if not show_name:
            messagebox.showwarning("Auto Rename", "Please enter a title.")
            return
        selected = self._selected_tool_result()
        if not selected:
            messagebox.showwarning("Auto Rename", "Please search TMDb and select a match first.")
            return
        if media_type == "Movie":
            source = self.single_file_var.get().strip()
            if not self._require_video_file(source):
                return
            self._run_worker("Movie Rename", self._movie_rename_worker, source, selected)
            return

        folder = self.folder_var.get().strip()
        if not self._require_folder(folder):
            return
        try:
            season = int(self.rename_season_var.get())
        except ValueError:
            messagebox.showwarning("Auto Rename", "Season must be a number.")
            return
        api_key = self._api_key()
        if not api_key:
            self._open_settings()
            messagebox.showwarning("TMDb API Key", "Please add your TMDb API key in Settings.")
            return
        self._run_worker("Auto-Rename", self._auto_rename_worker, folder, show_name, selected, season, api_key)

    def _movie_rename_worker(self, source, selected):
        source_path = Path(source)
        title = selected.get("title") or source_path.stem
        year = (selected.get("release_date") or "Unknown")[:4]
        target_path = source_path.with_name(f"{safe_name(title)} ({year}){source_path.suffix}")
        core.console.print(f"Movie rename: {source_path.name} -> {target_path.name}")
        if target_path.exists() and target_path != source_path:
            raise RuntimeError(f"Target already exists: {target_path}")
        if target_path != source_path:
            os.rename(source_path, target_path)

    def _auto_rename_worker(self, folder, show_name, selected, season, api_key):
        tmdb = core.TMDbClient(api_key)
        episodes = tmdb.get_season_episodes(selected["id"], season)
        if not episodes:
            raise RuntimeError("No episodes were found for this season.")
        files = [name for name in sorted(os.listdir(folder)) if name.lower().endswith(VIDEO_EXTENSIONS)]
        if not files:
            raise RuntimeError("No video files were found in the selected folder.")
        count = min(len(files), len(episodes))
        core.console.print(f"Using TMDb match: {selected.get('name')} - Season {season}")
        for index in range(count):
            old_name = files[index]
            episode = episodes[index]
            new_name = f"{safe_name(selected.get('name', show_name))} - S{season:02}E{episode['episode_number']:02} - {safe_name(episode['name'])}{Path(old_name).suffix}"
            old_path = os.path.join(folder, old_name)
            new_path = os.path.join(folder, new_name)
            core.console.print(f"{old_name} -> {new_name}")
            if old_path != new_path and not os.path.exists(new_path):
                os.rename(old_path, new_path)

    def _api_key(self):
        return self.settings.get("tmdb_api_key") or os.environ.get("TMDB_API_KEY")

    def _require_folder(self, folder):
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Folder", "Please select a valid folder.")
            return False
        return True

    def _require_video_file(self, filepath):
        if not filepath or not os.path.isfile(filepath):
            messagebox.showwarning("Video file", "Please select a valid video file.")
            return False
        if not filepath.lower().endswith(VIDEO_EXTENSIONS):
            messagebox.showwarning("Video file", "Please select a supported video file.")
            return False
        return True

    def _validate_ripper(self, ripper, need_makemkv=False, need_mkvtoolnix=False, need_handbrake=False):
        if need_makemkv and not ripper.makemkv_path:
            raise RuntimeError("MakeMKV was not found. Please set the path in Settings.")
        if need_mkvtoolnix and (not ripper.mkvmerge_path or not ripper.mkvpropedit_path):
            raise RuntimeError("MKVToolNix was not found. Please set the folder in Settings.")
        if need_handbrake and not ripper.handbrake_path:
            raise RuntimeError("HandBrakeCLI was not found. Please set the path in Settings.")

    def _run_worker(self, label, func, *args, focus_log=True):
        if self.worker_running:
            messagebox.showinfo("Already running", "Another job is already running.")
            return
        self.worker_running = True
        self.disc_status.configure(text=f"{label} running...")
        self._start_progress()
        if focus_log:
            self.tabs.set("Disc Ripper")
        self._log(f"\n--- {label} started ---\n")
        thread = threading.Thread(target=self._worker_wrapper, args=(label, func, args), daemon=True)
        thread.start()

    def _worker_wrapper(self, label, func, args):
        writer = QueueWriter(self.log_queue)
        core.console = Console(file=writer, force_terminal=False, width=120)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                func(*args)
            self.log_queue.put(f"\n--- {label} complete ---\n")
            self.after(0, lambda: self.disc_status.configure(text=f"{label} complete"))
        except Exception as exc:
            self.log_queue.put(f"\nERROR: {exc}\n")
            self.log_queue.put(traceback.format_exc())
            self.after(0, lambda: self.disc_status.configure(text=f"{label} failed"))
        finally:
            self.after(0, self._worker_done)

    def _worker_done(self):
        self.worker_running = False
        self._finish_progress()

    def _progress_callback(self, phase, fraction, detail=""):
        self.log_queue.put(("progress", fraction, phase, detail))

    def _log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _start_progress(self):
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.set(0)
        self.progress_status.configure(text="Starting...")
        self.progress_bar.start()

    def _set_progress(self, value, phase=None, detail=""):
        label = phase or "Working"
        if value is None:
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
            self.progress_status.configure(text=f"{label}: {detail}" if detail else label)
            return

        clamped = max(0, min(1, value))
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(clamped)
        percent = clamped * 100
        suffix = f" - {detail}" if detail else ""
        self.progress_status.configure(text=f"{label}: {percent:.1f}%{suffix}")

    def _finish_progress(self):
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(1)
        self.progress_status.configure(text="Complete")
        self.after(1200, self._reset_progress_if_idle)

    def _reset_progress_if_idle(self):
        if not self.worker_running:
            self.progress_bar.set(0)
            self.progress_status.configure(text="Idle")

    def _drain_log_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "progress":
                    value = item[1]
                    phase = item[2] if len(item) > 2 else None
                    detail = item[3] if len(item) > 3 else ""
                    self._set_progress(value, phase, detail)
                else:
                    self._log(item)
        except queue.Empty:
            pass
        self.after(120, self._drain_log_queue)


if __name__ == "__main__":
    app = NussmannMediaForgeApp()
    app.mainloop()
