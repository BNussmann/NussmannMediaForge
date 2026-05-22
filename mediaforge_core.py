import os
import subprocess
import sys
import json
import re
import requests
from rich.console import Console
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel
import time

console = Console()

# Configuration
MAKEMKVCON_PATH = r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe" # Default for Windows, can be adjusted
MIN_DURATION_SECONDS = 1800 # 15 minutes default to skip bonus content
HAND_BRAKE_ENCODERS = {
    "nvidia": {
        "label": "NVIDIA NVENC H.265",
        "handbrake": "nvenc_h265",
        "preset": "slow",
    },
    "amd": {
        "label": "AMD VCE/VCN H.265",
        "handbrake": "vce_h265",
        "preset": None,
    },
}
LANGUAGE_OPTIONS = {
    "ger": {"label": "German", "codes": ["ger", "deu", "de"]},
    "eng": {"label": "English", "codes": ["eng", "en"]},
    "fre": {"label": "French", "codes": ["fre", "fra", "fr"]},
    "spa": {"label": "Spanish", "codes": ["spa", "es"]},
    "ita": {"label": "Italian", "codes": ["ita", "it"]},
    "jpn": {"label": "Japanese", "codes": ["jpn", "ja"]},
}

class TMDbClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key):
        self.api_key = api_key

    def search_tv_show(self, query):
        url = f"{self.BASE_URL}/search/tv"
        params = {"api_key": self.api_key, "query": query, "language": "de-DE"}
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json().get('results', [])

    def get_season_episodes(self, tv_id, season_number):
        url = f"{self.BASE_URL}/tv/{tv_id}/season/{season_number}"
        params = {"api_key": self.api_key, "language": "de-DE"}
        response = requests.get(url, params=params)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        episodes = data.get('episodes', [])
        
        # TMDb usually provides 'runtime' in minutes per episode
        return episodes

    def search_movie(self, query):
        url = f"{self.BASE_URL}/search/movie"
        params = {"api_key": self.api_key, "query": query, "language": "de-DE"}
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json().get('results', [])

class DiscRipper:
    def __init__(self, settings=None, interactive=True, progress_callback=None):
        self.settings = settings or {}
        self.interactive = interactive
        self.progress_callback = progress_callback
        self.drive_index = 0
        self.encoder = self.settings.get("encoder", "nvidia")
        self.default_language = self.settings.get("default_language", "ger")
        self.makemkv_path = self._find_makemkv()
        self.mkvmerge_path = self._find_tool("mkvmerge")
        self.mkvpropedit_path = self._find_tool("mkvpropedit")
        self.handbrake_path = self._find_tool("HandBrakeCLI")

    def _emit_progress(self, phase, fraction, detail=""):
        if not self.progress_callback:
            return
        try:
            if fraction is not None:
                fraction = max(0, min(1, fraction))
            self.progress_callback(phase, fraction, detail)
        except Exception:
            pass

    def _resolve_configured_tool(self, name):
        configured = None
        if name == "makemkvcon64":
            configured = self.settings.get("makemkv_path")
        elif name in ("mkvmerge", "mkvpropedit"):
            configured = self.settings.get("mkvtoolnix_path")
        elif name == "HandBrakeCLI":
            configured = self.settings.get("handbrake_path")

        if not configured:
            return None

        configured = configured.strip().strip('"\'')
        if not configured:
            return None

        if os.path.isdir(configured):
            exe_path = os.path.join(configured, f"{name}.exe")
            if os.path.exists(exe_path):
                return exe_path

        if os.path.exists(configured):
            return configured

        return None

    def _find_tool(self, name):
        configured = self._resolve_configured_tool(name)
        if configured:
            return configured

        # Check script directory first
        local_path = os.path.join(os.getcwd(), f"{name}.exe")
        if os.path.exists(local_path):
            return local_path
            
        # Check PATH
        try:
            subprocess.run([name, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return name
        except FileNotFoundError:
            pass
        
        # Check common Windows paths
        common_paths = [
            fr"C:\Program Files\MKVToolNix\{name}.exe",
            fr"C:\Program Files (x86)\MKVToolNix\{name}.exe",
            fr"C:\Program Files\HandBrake\{name}.exe",
            fr"C:\Program Files\HandBrakeCLI\{name}.exe",
            fr"Y:\Nussmann MediaForge\{name}.exe" 
        ]
        for path in common_paths:
            if os.path.exists(path):
                return path
        return None

    def _find_makemkv(self):
        configured = self._resolve_configured_tool("makemkvcon64")
        if configured:
            return configured

        # Check default paths or PATH
        paths = [
            MAKEMKVCON_PATH,
            r"C:\Program Files\MakeMKV\makemkvcon64.exe",
            "makemkvcon"
        ]
        for path in paths:
            try:
                subprocess.run([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return path
            except FileNotFoundError:
                continue
        
        # If not found, ask user
        if not self.interactive:
            return None

        console.print("[yellow]MakeMKV (makemkvcon) not found in standard paths.[/yellow]")
        while True:
            user_path = Prompt.ask("Please enter the full path to 'makemkvcon64.exe' (or 'q' to quit)")
            if user_path.lower() == 'q':
                return None
            
            # Remove quotes if user added them
            user_path = user_path.strip('"\'')
            
            if os.path.exists(user_path):
                return user_path
            else:
                console.print("[red]File not found. Try again.[/red]")

    def register_beta_key(self):
        console.print("[yellow]MakeMKV license issue detected.[/yellow]")
        console.print("[yellow]Please register MakeMKV manually or install a current beta key from the official MakeMKV forum.[/yellow]")
        return False

    def scan_disc(self, min_length_seconds=MIN_DURATION_SECONDS):
        if not self.makemkv_path:
            console.print("[bold red]MakeMKV (makemkvcon) not found![/bold red] Please install MakeMKV or update the path in the script.")
            return None

        console.print(f"[bold blue]Scanning disc (Min Length: {min_length_seconds//60} min)... this may take a minute...[/bold blue]")
        
        # Get disc info using -r (robot) mode
        # --minlength filters titles DURING scan, which is MUCH faster on DVDs
        cmd = [self.makemkv_path, "-r", "info", f"disc:{self.drive_index}", f"--minlength={min_length_seconds}"]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        except Exception as e:
            console.print(f"[red]Error running MakeMKV: {e}[/red]")
            return None

        # Check for trial/license errors
        if "MSG:5053" in result.stdout or "MSG:5010" in result.stdout:
            # 5053 = Shareware trial prompt
            # 5010 = Open failed (often consequence of expired trial)
            console.print("[yellow]MakeMKV license issue detected (Trial expired or prompt).[/yellow]")
            if self.register_beta_key():
                console.print("[blue]Retrying scan with new key...[/blue]")
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
                except Exception as e:
                    console.print(f"[red]Error running MakeMKV on retry: {e}[/red]")
                    return None

        # Parse output for titles
        titles = []
        
        lines = result.stdout.splitlines()
        for line in lines:
            if line.startswith("TINFO:"):
                # format: TINFO:title_id,code,value,quotes_value
                parts = line.split(',', 3)
                if len(parts) >= 4:
                    title_id = int(parts[0].split(':')[1])
                    code = int(parts[1])
                    value = parts[3].strip('"')
                    
                    while len(titles) <= title_id:
                        titles.append({'id': len(titles)})
                    
                    # Code 9 is duration (format HH:MM:SS)
                    if code == 9:
                        try:
                            h, m, s = map(int, value.split(':'))
                            seconds = h * 3600 + m * 60 + s
                            titles[title_id]['duration'] = value
                            titles[title_id]['seconds'] = seconds
                        except ValueError:
                            pass
                    # Code 10 is size (formatted string like "5.2 GB")
                    elif code == 10:
                        titles[title_id]['size_str'] = value
                        # Parse approximate bytes
                        parts_size = value.split(' ')
                        if len(parts_size) == 2:
                            try:
                                num = float(parts_size[0].replace(',', '.')) # Handle German comma
                                unit = parts_size[1].upper()
                                multiplier = 1
                                if "GB" in unit: multiplier = 1024**3
                                elif "MB" in unit: multiplier = 1024**2
                                elif "KB" in unit: multiplier = 1024
                                titles[title_id]['size_bytes'] = int(num * multiplier)
                            except ValueError:
                                titles[title_id]['size_bytes'] = 0
                    # Code 2 is name (sometimes)
                    elif code == 2:
                        titles[title_id]['name'] = value
                    # Code 27 is file name
                    elif code == 27:
                         titles[title_id]['filename'] = value

        # Filter valid titles (already filtered by --minlength, but just in case)
        valid_titles = [t for t in titles if 'seconds' in t and t['seconds'] >= min_length_seconds]
        
        if not valid_titles:
            console.print(f"[yellow]No titles found matching the duration filter (> {min_length_seconds/60} min).[/yellow]")
            if titles:
                console.print(f"Found {len(titles)} titles that were skipped:")
                for t in titles:
                    console.print(f" - ID {t['id']}: {t.get('duration', 'Unknown')} ({t.get('size_str', '?')}) (skipped)")
            else:
                console.print("[red]MakeMKV found NO titles at all.[/red]")
                
        return valid_titles

    def match_titles_to_episodes(self, titles, episodes, start_ep_num, mode="linear", tolerance_seconds=300):
        """
        Matches titles on disc to TMDb episodes.
        Modes: 
        - 'linear': 1-to-1 mapping in order
        - 'smart': Finds the best duration match globally
        """
        matched = []

        if mode == "linear":
            start_idx = start_ep_num - 1
            for i, title in enumerate(titles):
                ep_idx = start_idx + i
                if ep_idx >= len(episodes): break
                ep = episodes[ep_idx]
                tmdb_runtime = ep.get('runtime', 0) * 60
                diff = abs(title['seconds'] - tmdb_runtime) if tmdb_runtime > 0 else 0
                status = "Match" if diff < 60 else f"Diff: {diff}s"
                matched.append((title, ep, status))

        elif mode == "smart":
            # Select the block of episodes we want to match
            available_episodes = episodes[start_ep_num-1 : start_ep_num-1 + len(titles)]
            remaining_titles = list(titles)

            # For each episode in the selection, find the title that fits BEST
            for ep in available_episodes:
                tmdb_runtime = ep.get('runtime', 0) * 60

                if not remaining_titles: break

                if tmdb_runtime == 0:
                    # If no runtime, just take the first remaining title
                    t = remaining_titles.pop(0)
                    matched.append((t, ep, "Order Match (No runtime data)"))
                    continue

                # Find title with closest duration
                best_t_idx = -1
                min_diff = 999999

                for i, t in enumerate(remaining_titles):
                    diff = abs(t['seconds'] - tmdb_runtime)
                    if diff < min_diff:
                        min_diff = diff
                        best_t_idx = i

                if best_t_idx != -1:
                    t = remaining_titles.pop(best_t_idx)
                    status = f"Smart Match (Diff: {min_diff}s)"
                    matched.append((t, ep, status))

        return matched
    def transcode_video(self, source_path, dest_path, encoder=None):
        if not self.handbrake_path:
            console.print("[yellow]HandBrakeCLI not found. Skipping compression.[/yellow]")
            return False

        encoder_key = (encoder or self.encoder or "nvidia").lower()
        encoder_config = HAND_BRAKE_ENCODERS.get(encoder_key, HAND_BRAKE_ENCODERS["nvidia"])
        console.print(f"[bold purple]Compressing with {encoder_config['label']}...[/bold purple]")
        
        # HandBrakeCLI command for H.265 hardware encoding
        # -e selects the configured hardware encoder
        # -q 20 : Constant Quality (lower is better quality, 20 is good balance for H.265)
        # --all-audio : Keep all audio tracks
        # --all-subtitles : Keep all subtitles
        # --map-chapters : Keep chapters
        
        cmd = [
            self.handbrake_path,
            "-i", source_path,
            "-o", dest_path,
            "-e", encoder_config["handbrake"],
            "-q", "20",
            "--all-audio",
            # Re-encode audio instead of copying it.
            "--aencoder", "aac", 
            "--mixdown", "stereo", # Optional: improves TV compatibility.
            # Skip subtitles for now.
            # "--all-subtitles"
        ]
        if encoder_config.get("preset"):
            cmd.extend(["--encoder-preset", encoder_config["preset"]])
        
        # We need to parse progress from HandBrake too
        # Output format: Encoding: task 1 of 1, 12.34 %
        
        start_time = time.time()
        full_log = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold purple]Compressing[/bold purple]"),
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            TextColumn("{task.fields[eta]}"),
            console=console
        ) as progress:
            task_id = progress.add_task("Encoding", total=100, eta="Estimating...")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, 
                text=True,
                encoding='utf-8',
                errors='replace', # Fix for UnicodeDecodeError on non-UTF8 output
                bufsize=1
            )
            
            # Regex for progress: Encoding: task 1 of 1, 1.23 %
            pattern = re.compile(r"Encoding:.*?(\d+(?:\.\d+)?)\s*%")
            
            for line in process.stdout:
                line = line.strip()
                full_log.append(line)
                if not line: continue
                
                match = pattern.search(line)
                if match:
                    percent = float(match.group(1))
                    progress.update(task_id, completed=percent, eta="") 
                    self._emit_progress("Encoding", percent / 100, f"{percent:.1f}%")
                
                if "ETA" in line:
                    parts = line.split("ETA")
                    if len(parts) > 1:
                        eta_str = parts[1].strip().strip(')')
                        progress.update(task_id, eta=f"ETA: {eta_str}")

            rc = process.wait()
            
        duration = time.time() - start_time
        
        # Verify output file
        file_created = os.path.exists(dest_path) and os.path.getsize(dest_path) > 1024 * 1024 # at least 1MB
        
        if rc != 0 or not file_created or duration < 5:
            console.print(f"[bold red]HandBrakeCLI Error (RC={rc}, Duration={duration:.1f}s)[/bold red]")
            if not file_created:
                console.print("[red]Output file was not created or is too small.[/red]")
            
            console.print("[dim]Last 20 lines of log:[/dim]")
            for log_line in full_log[-20:]:
                console.print(f"[dim]{log_line}[/dim]")
            return False
            
        return True

    def rip_all_matched(self, matched_titles, output_path, min_length_seconds, compress=False):
        """
        Rips all matched titles in one single MakeMKV session to save time on DVDs.
        """
        os.makedirs(output_path, exist_ok=True)
        temp_batch_dir = os.path.join(output_path, "temp_batch_rip")
        os.makedirs(temp_batch_dir, exist_ok=True)

        # Clean temp dir
        for f in os.listdir(temp_batch_dir):
            try: os.remove(os.path.join(temp_batch_dir, f))
            except: pass

        # Command to rip ALL titles that match min_length
        # This is much faster as it opens the disc only once
        cmd = [self.makemkv_path, "-r", "mkv", f"disc:{self.drive_index}", "all", temp_batch_dir, f"--minlength={min_length_seconds}"]
        
        console.print(f"[bold green]Starting Batch Rip of all matching titles...[/bold green]")
        console.print(f"[dim]This avoids re-opening the disc for every episode.[/dim]")

        # For progress, we'll monitor the temp directory
        total_expected_size = sum(t.get('size_bytes', 0) for t, ep, status in matched_titles)
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]Batch Ripping"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            TextColumn("[bold yellow]{task.fields[status]}"),
            console=console
        ) as progress:
            task_id = progress.add_task("Ripping", total=total_expected_size if total_expected_size > 0 else 100, status="Starting...")
            
            process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            while process.poll() is None:
                try:
                    current_files = [os.path.join(temp_batch_dir, f) for f in os.listdir(temp_batch_dir) if f.endswith('.mkv')]
                    current_size = sum(os.path.getsize(f) for f in current_files)
                    
                    status_text = f"{current_size/(1024**2):.0f} MB"
                    if total_expected_size > 0:
                        progress.update(task_id, completed=current_size, status=status_text)
                        self._emit_progress("Ripping", current_size / total_expected_size, status_text)
                    else:
                        progress.update(task_id, completed=0, status=status_text)
                        self._emit_progress("Ripping", None, status_text)
                except:
                    pass
                time.sleep(2)
            
            rc = process.wait()

        if rc != 0:
            console.print("[bold red]Batch Rip failed![/bold red]")
            return False
        self._emit_progress("Ripping", 1, "Rip complete")

        # Now identify and rename files
        generated_files = sorted([f for f in os.listdir(temp_batch_dir) if f.endswith('.mkv')])
        
        # MakeMKV names files title_t00.mkv, title_t01.mkv etc. 
        # We need to match them back to our 'matched_titles' list.
        # Note: MakeMKV IDs in 'all' mode correspond to the order of titles on disc.
        
        # Re-scan the generated files to get their actual durations
        actual_files = []
        for f in generated_files:
            fpath = os.path.join(temp_batch_dir, f)
            # Use mkvmerge to get duration if possible, or just assume order if IDs match
            actual_files.append({
                'path': fpath,
                'name': f,
                'size': os.path.getsize(fpath)
            })

        console.print(f"\n[bold]Processing {len(actual_files)} ripped files...[/bold]")
        
        # Strategy: Map matched titles (by ID) to the generated files
        # MakeMKV titles end with _tXX.mkv or tXX.mkv
        for t, ep, status in matched_titles:
            # Look for a file that contains 'tXX.mkv' (where XX is title ID)
            target_suffix = f"t{t['id']:02d}.mkv"
            source_match = next((f for f in actual_files if f['name'].endswith(target_suffix)), None)
            
            if source_match:
                safe_title = "".join([c for c in ep['name'] if c.isalpha() or c.isdigit() or c==' ']).strip()
                final_filename = f"{ep['show_name']} - S{ep['season']:02}E{ep['episode_number']:02} - {safe_title}.mkv"
                
                console.print(f"Mapping [cyan]{source_match['name']}[/cyan] -> [green]{final_filename}[/green]")
                
                # Rip title logic (compression etc)
                final_path = os.path.join(output_path, final_filename)
                if compress and self.handbrake_path:
                    success = self.transcode_video(source_match['path'], final_path)
                    if success:
                        self.set_default_audio(final_path, language=self.default_language)
                        os.remove(source_match['path'])
                else:
                    if os.path.exists(final_path): os.remove(final_path)
                    os.rename(source_match['path'], final_path)
                    self.set_default_audio(final_path, language=self.default_language)
            else:
                console.print(f"[red]Could not find ripped file for Episode {ep['episode_number']} (Searching for suffix {target_suffix})[/red]")
                console.print(f"[dim]Available files: {', '.join([f['name'] for f in actual_files])}[/dim]")

        # Cleanup
        try:
            for f in os.listdir(temp_batch_dir):
                os.remove(os.path.join(temp_batch_dir, f))
            os.rmdir(temp_batch_dir)
        except:
            pass
            
        return True

    def rip_title(self, title_id, output_path, filename, expected_size_bytes=0, compress=False):
        os.makedirs(output_path, exist_ok=True)
        final_path = os.path.join(output_path, filename)
        
        # If compressing, we rip to a raw file first
        if compress:
            raw_filename = f"raw_{filename}"
            raw_path = os.path.join(output_path, raw_filename)
            target_path_for_rip = raw_path
            # Cleanup previous raw file if exists
            if os.path.exists(raw_path):
                os.remove(raw_path)
        else:
            target_path_for_rip = final_path

        # Cleanup final path if exists (overwrite)
        if os.path.exists(final_path):
            os.remove(final_path)

        temp_rip_dir = os.path.join(output_path, f"temp_rip_{title_id}")
        os.makedirs(temp_rip_dir, exist_ok=True)
        
        # Clean temp dir
        for f in os.listdir(temp_rip_dir):
            os.remove(os.path.join(temp_rip_dir, f))

        cmd = [self.makemkv_path, "mkv", f"disc:{self.drive_index}", str(title_id), temp_rip_dir]
        
        console.print(f"Ripping Title [bold cyan]{title_id}[/bold cyan]...")
        if compress:
            console.print(f"Target (Raw): [dim]{raw_filename}[/dim]")
            console.print(f"Final Target: [green]{filename}[/green] (Compressed)")
        else:
            console.print(f"Target: [green]{filename}[/green]")

        if expected_size_bytes > 0:
            console.print(f"Expected Size (Raw): ~{expected_size_bytes / (1024**3):.2f} GB")
        
        # Progress Bar for Ripping
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.fields[action]}", justify="right"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            TextColumn("[bold yellow]{task.fields[status]}"),
            console=console
        ) as progress:
            task_id = progress.add_task("Ripping", total=expected_size_bytes if expected_size_bytes > 0 else 100, action="Ripping", status="Waiting...")
            
            # Start process
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            
            while process.poll() is None:
                try:
                    files = [f for f in os.listdir(temp_rip_dir) if f.endswith('.mkv')]
                    if files:
                        current_file = os.path.join(temp_rip_dir, files[0])
                        current_size = os.path.getsize(current_file)
                        
                        if expected_size_bytes > 0:
                            status_text = f"{current_size/(1024**2):.0f} MB"
                            progress.update(task_id, completed=current_size, total=expected_size_bytes, status=status_text)
                            self._emit_progress("Ripping", current_size / expected_size_bytes, status_text)
                        else:
                            status_text = f"{current_size/(1024**2):.0f} MB"
                            progress.update(task_id, completed=0, total=100, status=status_text)
                            self._emit_progress("Ripping", None, status_text)
                except Exception:
                    pass
                time.sleep(1)
            
            rc = process.wait()

        # Check result of Rip
        if rc == 0:
            files = [f for f in os.listdir(temp_rip_dir) if f.endswith('.mkv')]
            if len(files) == 1:
                src_file = os.path.join(temp_rip_dir, files[0])
                try:
                    os.rename(src_file, target_path_for_rip)
                    os.rmdir(temp_rip_dir)
                    console.print("[green]Rip completed successfully.[/green]")
                    self._emit_progress("Ripping", 1, "Rip complete")
                except OSError as e:
                    console.print(f"[red]Error moving file: {e}[/red]")
                    return
            else:
                console.print(f"[red]Error: Expected 1 MKV file, found {len(files)}[/red]")
                return
        else:
            console.print(f"[bold red]Rip Failed with exit code {rc}![/bold red]")
            return

        # 2. Compression (Optional)
        if compress and self.handbrake_path:
            success = self.transcode_video(target_path_for_rip, final_path)
            if success:
                console.print("[bold green]Compression Complete![/bold green]")
                # Remove raw file
                try:
                    os.remove(target_path_for_rip)
                except OSError:
                    console.print("[yellow]Warning: Could not delete raw file.[/yellow]")
                
                # Post-process Audio on FINAL file
                self.set_default_audio(final_path, language=self.default_language)
            else:
                console.print("[red]Compression failed! Keeping raw file.[/red]")
                # Maybe rename raw to final name if final doesn't exist?
                # For safety, let user handle it.
        else:
            # Post-process Audio on Raw/Final file (since they are same if no compress)
            self.set_default_audio(target_path_for_rip, language=self.default_language)

    def set_default_audio(self, filepath, language='ger'):
        if not self.mkvmerge_path or not self.mkvpropedit_path:
            console.print("[yellow]MKVToolNix not found. Skipping audio track configuration.[/yellow]")
            return

        language_config = LANGUAGE_OPTIONS.get(language, LANGUAGE_OPTIONS["ger"])
        language_label = language_config["label"]
        target_langs = language_config["codes"]

        try:
            # 1. Identify tracks
            cmd_identify = [self.mkvmerge_path, "-J", filepath]
            result = subprocess.run(cmd_identify, capture_output=True, text=True, encoding='utf-8')
            data = json.loads(result.stdout)
            
            tracks = data.get("tracks", [])
            audio_tracks = [t for t in tracks if t.get("type") == "audio"]
            
            if not audio_tracks:
                return

            console.print(f"Found {len(audio_tracks)} audio tracks:")
            for t in audio_tracks:
                props = t.get("properties", {})
                lang = props.get("language", "und")
                lang_ietf = props.get("language_ietf", "und")
                name = props.get("track_name", "")
                uid = props.get("uid", "N/A")
                console.print(f" - ID {t['id']} (UID: {uid}): {lang}/{lang_ietf} ({name})")

            def language_matches(value):
                if not value:
                    return False
                normalized = value.lower()
                primary = normalized.split("-", 1)[0]
                return normalized in target_langs or primary in target_langs

            target_tracks = [
                t for t in audio_tracks
                if language_matches(t.get("properties", {}).get("language"))
                or language_matches(t.get("properties", {}).get("language_ietf"))
            ]
            
            if not target_tracks:
                console.print(f"[yellow]No {language_label} audio track found. Leaving defaults.[/yellow]")
                return
            
            # Use the first matching language track.
            target_track = target_tracks[0]
            
            is_mp4 = filepath.lower().endswith('.mp4')
            
            if is_mp4:
                target_id = target_track.get("id")
                new_filepath = os.path.splitext(filepath)[0] + ".mkv"
                
                cmd_remux = [self.mkvmerge_path, "-o", new_filepath]
                
                for track in audio_tracks:
                    tid = track.get("id")
                    if tid is None: continue
                    
                    is_target = (tid == target_id)
                    flag_default = "1" if is_target else "0"
                    
                    cmd_remux.extend(["--default-track", f"{tid}:{flag_default}", "--forced-display-flag", f"{tid}:0"])
                
                cmd_remux.append(filepath)
                
                console.print(f"Remuxing MP4 to MKV and setting default audio to [bold]{language_label}[/bold] (ID: {target_id})...")
                subprocess.run(cmd_remux, check=True, stdout=subprocess.DEVNULL)
                
                try:
                    os.remove(filepath)
                    console.print(f"[green]Converted to MKV and audio tracks updated: {os.path.basename(new_filepath)}[/green]")
                except OSError:
                    console.print(f"[yellow]Converted to MKV, but could not delete original MP4 file.[/yellow]")
            else:
                target_uid = target_track.get("properties", {}).get("uid")
                cmd_edit = [self.mkvpropedit_path, filepath]
                
                for track in audio_tracks:
                    uid = track.get("properties", {}).get("uid")
                    if not uid: continue
                    
                    is_target = (uid == target_uid)
                    flag_default = "1" if is_target else "0"
                    
                    cmd_edit.extend(["--edit", f"track:={uid}", "--set", f"flag-default={flag_default}", "--set", "flag-forced=0"])
                    
                console.print(f"Setting default audio to [bold]{language_label}[/bold] (UID: {target_uid})...")
                subprocess.run(cmd_edit, check=True, stdout=subprocess.DEVNULL)
                console.print("[green]Audio tracks updated.[/green]")
            
        except Exception as e:
            console.print(f"[red]Error setting default audio: {e}[/red]")

def main():
    console.print(Panel.fit("[bold yellow]Nussmann MediaForge[/bold yellow]"))

    ripper = DiscRipper()

    # Mode Selection
    console.print("\n[bold cyan]Select Operation Mode:[/bold cyan]")
    console.print("1. [bold]Rip Disc (TV Show)[/bold] (Standard)")
    console.print("2. [bold]Set Default Audio to German[/bold] (Process existing MKV folder)")
    console.print("3. [bold]Batch Transcode Folder to H.265[/bold] (Fix playback issues on FireTV)")
    console.print("4. [bold]Auto-Rename Episodes[/bold] (Fetch titles from TMDb)")
    console.print("5. [bold]Rip Disc (Movie)[/bold]")
    
    mode = IntPrompt.ask("Choose mode", choices=["1", "2", "3", "4", "5"], default=1)
    
    if mode == 4:
        folder_path = Prompt.ask("Enter folder path containing files to rename").strip('"\'')
        if not os.path.exists(folder_path):
            console.print(f"[red]Folder not found: {folder_path}[/red]")
            return

        api_key = os.environ.get("TMDB_API_KEY")
        if not api_key:
            console.print("Please enter your TMDb API Key (or set TMDB_API_KEY env var).")
            console.print("Get one here: https://www.themoviedb.org/settings/api")
            api_key = Prompt.ask("API Key")
        
        tmdb = TMDbClient(api_key)

        # 2. Search for Show
        show_name = Prompt.ask("Enter TV Show Name")
        results = tmdb.search_tv_show(show_name)
        
        if not results:
            console.print("[red]No results found![/red]")
            return
        
        # Simple selection
        console.print("\n[bold]Found Shows:[/bold]")
        for idx, show in enumerate(results[:5]):
            console.print(f"{idx + 1}. {show['name']} ({show.get('first_air_date', 'Unknown')[:4]})")
        
        choice = IntPrompt.ask("Select Show", choices=[str(i+1) for i in range(len(results[:5]))])
        selected_show = results[choice-1]
        
        season_number = IntPrompt.ask("Enter Season Number")
        
        # 3. Get Episodes
        episodes = tmdb.get_season_episodes(selected_show['id'], season_number)
        if not episodes:
            console.print("[red]Could not load episodes for this season![/red]")
            return

        video_exts = ('.mkv', '.mp4', '.avi', '.ts', '.m2ts')
        files = [f for f in os.listdir(folder_path) if f.lower().endswith(video_exts)]
        # Sort files alphabetically to ensure they match episode order
        files.sort()
        
        if not files:
            console.print("[yellow]No video files found in the specified folder.[/yellow]")
            return

        console.print(f"\nFound {len(files)} video files in folder.")
        console.print(f"Found {len(episodes)} episodes in Season {season_number} of {selected_show['name']}.")

        if len(files) != len(episodes):
            console.print(f"[bold yellow]Warning: Number of files ({len(files)}) does not match number of episodes ({len(episodes)})![/bold yellow]")
            if not Confirm.ask("Do you want to proceed anyway? (Will map 1:1 in alphabetical order)", default=False):
                return
        
        count = min(len(files), len(episodes))
        
        # Preview
        console.print("\n[bold cyan]Preview of changes:[/bold cyan]")
        renames = []
        for i in range(count):
            f = files[i]
            ep = episodes[i]
            
            safe_title = "".join([c for c in ep['name'] if c.isalpha() or c.isdigit() or c==' ']).strip()
            ext = os.path.splitext(f)[1]
            new_filename = f"{selected_show['name']} - S{season_number:02d}E{ep['episode_number']:02d} - {safe_title}{ext}"
            
            renames.append((f, new_filename))
            console.print(f"[dim]{f}[/dim] -> [green]{new_filename}[/green]")

        if count < len(files):
            console.print(f"[yellow]...and {len(files) - count} files will be ignored.[/yellow]")
            
        if not Confirm.ask("\nProceed with renaming?"):
            return

        for old_name, new_name in renames:
            old_path = os.path.join(folder_path, old_name)
            new_path = os.path.join(folder_path, new_name)
            
            if old_path != new_path:
                try:
                    os.rename(old_path, new_path)
                except OSError as e:
                    console.print(f"[red]Error renaming {old_name}: {e}[/red]")
        
        console.print("\n[bold green]Auto-Renaming Complete![/bold green]")
        return

    if mode == 3:
        folder_path = Prompt.ask("Enter folder path containing video files to transcode").strip('"\'')
        if not os.path.exists(folder_path):
            console.print(f"[red]Folder not found: {folder_path}[/red]")
            return
            
        # Support common video formats
        video_exts = ('.mkv', '.mp4', '.avi', '.ts', '.m2ts')
        video_files = [f for f in os.listdir(folder_path) if f.lower().endswith(video_exts) and not f.startswith("transcoded_")]
        
        if not video_files:
            console.print("[yellow]No video files found (excluding already 'transcoded_' ones).[/yellow]")
            return
            
        console.print(f"Found {len(video_files)} video files. Processing...")
        
        if not ripper.handbrake_path:
            console.print("[red]HandBrakeCLI not found! Cannot transcode files.[/red]")
            return

        out_folder = os.path.join(folder_path, "Transcoded")
        os.makedirs(out_folder, exist_ok=True)

        for f in video_files:
            source_path = os.path.join(folder_path, f)
            # Use MP4 output for transcoded files.
            dest_filename = f"transcoded_{os.path.splitext(f)[0]}.mp4" 
            dest_path = os.path.join(out_folder, dest_filename)
            
            console.print(f"\nProcessing: [cyan]{f}[/cyan]")
            if os.path.exists(dest_path):
                console.print(f"[yellow]Skipping, destination already exists: {dest_filename}[/yellow]")
                continue
                
            success = ripper.transcode_video(source_path, dest_path)
            if success:
                console.print(f"[green]Successfully transcoded to: {dest_filename}[/green]")
                # Optional: try to set default audio on the new file too
                # ripper.set_default_audio(dest_path, language='ger')
            else:
                console.print(f"[red]Failed to transcode: {f}[/red]")
            
        console.print("\n[bold green]Batch Transcoding Complete![/bold green]")
        return

    if mode == 2:
        folder_path = Prompt.ask("Enter folder path containing MKV/MP4 files").strip('"\'')
        if not os.path.exists(folder_path):
            console.print(f"[red]Folder not found: {folder_path}[/red]")
            return
            
        media_files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.mkv', '.mp4'))]
        if not media_files:
            console.print("[yellow]No MKV or MP4 files found.[/yellow]")
            return
            
        console.print(f"Found {len(media_files)} files. Processing...")
        
        # Check tools for Mode 2
        if not ripper.mkvmerge_path or not ripper.mkvpropedit_path:
            console.print("[red]MKVToolNix (mkvmerge/mkvpropedit) not found! Cannot process files.[/red]")
            return

        for f in media_files:
            full_path = os.path.join(folder_path, f)
            console.print(f"Processing: [cyan]{f}[/cyan]")
            ripper.set_default_audio(full_path, language='ger')
            
        console.print("[bold green]All files processed![/bold green]")
        return

    if mode in [1, 5]:
        if not ripper.makemkv_path:
            return

        # 1. API Setup
        api_key = os.environ.get("TMDB_API_KEY")
        if not api_key:
            console.print("Please enter your TMDb API Key (or set TMDB_API_KEY env var).")
            console.print("Get one here: https://www.themoviedb.org/settings/api")
            api_key = Prompt.ask("API Key")
        
        tmdb = TMDbClient(api_key)

        if mode == 1:
            # 2. Search for Show
            show_name = Prompt.ask("Enter TV Show Name")
            results = tmdb.search_tv_show(show_name)
            
            if not results:
                console.print("[red]No results found![/red]")
                return
            
            # Simple selection
            console.print("\n[bold]Found Shows:[/bold]")
            for idx, show in enumerate(results[:5]):
                console.print(f"{idx + 1}. {show['name']} ({show.get('first_air_date', 'Unknown')[:4]})")
            
            choice = IntPrompt.ask("Select Show", choices=[str(i+1) for i in range(len(results[:5]))])
            selected_show = results[choice-1]
            
            season_number = IntPrompt.ask("Enter Season Number")
            
            # 3. Get Episodes
            episodes = tmdb.get_season_episodes(selected_show['id'], season_number)
            if not episodes:
                console.print("[red]Could not load episodes![/red]")
                return
                
            console.print(f"Found {len(episodes)} episodes for {selected_show['name']} Season {season_number}")

            # Calculate reasonable min/max length for scan to speed up DVD scanning and skip "Play All" tracks
            runtimes = [e.get('runtime', 0) for e in episodes if e.get('runtime', 0) > 0]
            if runtimes:
                min_ep_runtime = min(runtimes)
                max_ep_runtime = max(runtimes)
                # Set scan limit to 75% of shortest episode or at least 10 minutes
                scan_min_seconds = max(600, int(min_ep_runtime * 60 * 0.75))
                # Set a max limit to 150% of longest episode to skip "Play All" tracks
                scan_max_seconds = int(max_ep_runtime * 60 * 1.5)
            else:
                scan_min_seconds = MIN_DURATION_SECONDS
                scan_max_seconds = 7200 # Default 2 hours max for TV episodes if no TMDb info

            # 4. Scan Disc
            titles = ripper.scan_disc(min_length_seconds=scan_min_seconds)
            if not titles:
                console.print("[red]No valid titles found on disc.[/red]")
                return

            # Filter out titles that are too long (Play All tracks)
            original_count = len(titles)
            titles = [t for t in titles if t['seconds'] <= scan_max_seconds]
            
            if len(titles) < original_count:
                console.print(f"[yellow]Filtered out {original_count - len(titles)} titles that were too long (likely 'Play All' tracks).[/yellow]")

            # 5. Mapping Logic
            console.print(f"\n[bold cyan]Step 5: Title-to-Episode Mapping[/bold cyan]")
            console.print(f"Found {len(titles)} titles on disc. Season {season_number} has {len(episodes)} episodes.")
            
            mapping_mode = Prompt.ask(
                "Select Mapping Mode", 
                choices=["auto", "smart", "manual", "skip"], 
                default="smart"
            )
            
            final_matched = []
            
            if mapping_mode in ["auto", "smart"]:
                start_ep_num = IntPrompt.ask("Which episode number does this disc start with?", default=1)
                match_type = "linear" if mapping_mode == "auto" else "smart"
                final_matched = ripper.match_titles_to_episodes(titles, episodes, start_ep_num, mode=match_type)
            
            elif mapping_mode == "manual":
                console.print("\n[bold]Available Titles on Disc:[/bold]")
                for idx, t in enumerate(titles):
                    console.print(f" {idx+1}. Title ID {t['id']} - Duration: {t['duration']} (~{t.get('size_str', '?')})")
                
                console.print("\n[bold]Available Episodes in Season:[/bold]")
                for ep in episodes:
                    ov = ep.get('overview', 'No description available.')
                    if len(ov) > 100: ov = ov[:97] + "..."
                    console.print(f" E{ep['episode_number']}: [bold]{ep['name']}[/bold] ({ep.get('runtime', '??')} min)\n    [dim]{ov}[/dim]")
                
                mapping_input = Prompt.ask(
                    "\nEnter episode numbers for the titles above in order (e.g. '2,3,4,5,6,7,1' or 's,1,2,3' to skip first title)"
                )
                
                ep_nums = [s.strip().lower() for s in mapping_input.split(",")]
                
                for idx, val in enumerate(ep_nums):
                    if idx >= len(titles): break
                    if val == 's': continue # Skip this title
                    
                    try:
                        ep_num = int(val)
                        # Find episode in TMDb list
                        ep = next((e for e in episodes if e['episode_number'] == ep_num), None)
                        if ep:
                            final_matched.append((titles[idx], ep, "Manual Match"))
                        else:
                            console.print(f"[yellow]Warning: Episode {ep_num} not found in TMDb data. Skipping title {idx+1}.[/yellow]")
                    except ValueError:
                        console.print(f"[red]Invalid input '{val}'. Skipping.[/red]")

            if not final_matched:
                console.print("[red]No episodes matched. Aborting.[/red]")
                return

            # Display Preview
            console.print("\n[bold]Final Mapping Preview:[/bold]")
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Disc ID")
            table.add_column("Disc Duration")
            table.add_column("TMDb Episode")
            table.add_column("TMDb Duration")
            table.add_column("Status")

            for t, ep, status in final_matched:
                tmdb_dur = f"{ep.get('runtime', '??')} min"
                table.add_row(str(t['id']), t['duration'], f"E{ep['episode_number']}: {ep['name']}", tmdb_dur, status)
            console.print(table)

            # Prepare episode data for the ripper
            for t, ep, status in final_matched:
                ep['show_name'] = selected_show['name']
                ep['season'] = season_number

            if not Confirm.ask(f"Proceed with ripping these {len(final_matched)} episodes?"):
                return

            output_dir = Prompt.ask("Output Directory", default="./" + "".join(c for c in selected_show['name'] if c.isalnum() or c==' ') + "/Season " + str(season_number))
            
            # Ask for Compression
            compress = False
            if ripper.handbrake_path:
                compress = Confirm.ask("Compress video with configured hardware encoder (H.265)? (Saves space, takes time)", default=True)
            
            # Start Batch Ripping
            ripper.rip_all_matched(final_matched, output_dir, scan_min_seconds, compress=compress)

        elif mode == 5:
            # 2. Search for Movie
            movie_name = Prompt.ask("Enter Movie Name")
            results = tmdb.search_movie(movie_name)
            
            if not results:
                console.print("[red]No results found![/red]")
                return
            
            # Simple selection
            console.print("\n[bold]Found Movies:[/bold]")
            for idx, movie in enumerate(results[:5]):
                console.print(f"{idx + 1}. {movie['title']} ({movie.get('release_date', 'Unknown')[:4]})")
            
            choice = IntPrompt.ask("Select Movie", choices=[str(i+1) for i in range(len(results[:5]))])
            selected_movie = results[choice-1]
            release_year = selected_movie.get('release_date', 'Unknown')[:4]
            tmdb_runtime = selected_movie.get('runtime', 0)
            
            # If runtime is not in the search results, we might need to fetch full movie details
            if not tmdb_runtime:
                url = f"{tmdb.BASE_URL}/movie/{selected_movie['id']}"
                params = {"api_key": tmdb.api_key, "language": "de-DE"}
                r = requests.get(url, params=params)
                if r.status_code == 200:
                    tmdb_runtime = r.json().get('runtime', 0)

            # 3. Scan Disc
            # For movies, we can set a very high min_length (e.g. 50% of runtime or 30 mins)
            scan_min_seconds = max(1800, int(tmdb_runtime * 60 * 0.5)) if tmdb_runtime else 3600
            
            titles = ripper.scan_disc(min_length_seconds=scan_min_seconds)
            if not titles:
                console.print("[red]No valid titles found on disc.[/red]")
                return

            console.print(f"\n[bold]Found {len(titles)} potential titles on disc (> {scan_min_seconds/60} min):[/bold]")
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("#", style="dim")
            table.add_column("ID")
            table.add_column("Duration")
            table.add_column("Approx Size")
            table.add_column("TMDb Match")

            # Try to find the best match based on duration
            best_match_idx = 0
            min_diff = 999999
            
            for idx, t in enumerate(titles):
                match_str = ""
                if tmdb_runtime:
                    diff = abs(t['seconds'] - (tmdb_runtime * 60))
                    if diff < min_diff:
                        min_diff = diff
                        best_match_idx = idx
                    
                    if diff < 120: # Within 2 minutes
                        match_str = "[green]Perfect Match[/green]"
                    elif diff < 300: # Within 5 minutes
                        match_str = "[yellow]Likely Match[/yellow]"
                
                table.add_row(str(idx+1), str(t['id']), t['duration'], t.get('size_str', 'N/A'), match_str)
            
            console.print(table)
            
            if tmdb_runtime:
                console.print(f"[cyan]TMDb Runtime: {tmdb_runtime} min ({tmdb_runtime*60}s)[/cyan]")
                console.print(f"[green]Recommended Title: #{best_match_idx+1}[/green]")
            
            title_choice = IntPrompt.ask("Select Title to rip", choices=[str(i+1) for i in range(len(titles))], default=best_match_idx+1)
            selected_title = titles[title_choice-1]
            
            safe_folder_name = selected_movie['title'].replace(':', ' -').replace('/', '-')
            output_dir = Prompt.ask("Output Directory", default=f"./{safe_folder_name} ({release_year})")
            
            compress = False
            if ripper.handbrake_path:
                compress = Confirm.ask("Compress video with configured hardware encoder (H.265)? (Saves space, takes time)", default=True)
            
            safe_title = "".join([c for c in selected_movie['title'] if c.isalpha() or c.isdigit() or c==' ' or c=='-']).strip()
            filename = f"{safe_title} ({release_year}).mkv"
            
            console.print(f"\n[bold]Processing Movie: {selected_movie['title']}[/bold]")
            ripper.rip_title(selected_title['id'], output_dir, filename, expected_size_bytes=selected_title.get('size_bytes', 0), compress=compress)

if __name__ == "__main__":
    main()
