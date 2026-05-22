# Nussmann MediaForge

Nussmann MediaForge is a Windows desktop app for ripping Blu-ray/DVD titles, naming movies and TV episodes with TMDb metadata, setting your preferred audio language as default, and transcoding videos to H.265 with NVIDIA or AMD hardware encoding.

![Nussmann MediaForge logo](assets/logo.svg)

## What It Does

- Rips movies and TV episodes from discs through MakeMKV
- Searches TMDb for movie and TV metadata
- Maps TV disc titles to episodes by runtime
- Names output files in a clean library-friendly format
- Transcodes single files or whole folders with HandBrakeCLI
- Supports NVIDIA NVENC H.265 and AMD VCE/VCN H.265
- Sets the first matching audio track as default based on the language selected in Settings
- Includes tools for TV/movie metadata search, auto-renaming, single-file transcoding, batch transcoding, and audio flag updates
- Saves settings locally in `%APPDATA%\NussmannMediaForge\settings.json`

## Download

Download the latest Windows ZIP from the GitHub Releases page, extract it, and run:

```text
NussmannMediaForge.exe
```

The app is portable. It does not include MakeMKV, MKVToolNix, HandBrakeCLI, or a TMDb API key. You install and configure those separately.

## Required Third-Party Tools

Install these tools before using the app:

- [MakeMKV](https://www.makemkv.com/download/)  
  Required executable: `makemkvcon64.exe`

- [MKVToolNix](https://mkvtoolnix.download/downloads.html)  
  Required executables: `mkvmerge.exe` and `mkvpropedit.exe`

- [HandBrake](https://handbrake.fr/downloads.php)  
  Required executable: `HandBrakeCLI.exe`

- [TMDb API Key](https://www.themoviedb.org/settings/api)  
  Required for movie and TV metadata search.

HandBrake hardware encoder availability depends on your GPU, drivers, and HandBrake build. You can check the encoders available on your machine with:

```powershell
HandBrakeCLI --help
```

This app uses:

- NVIDIA: `nvenc_h265`
- AMD: `vce_h265`

Default audio language matching currently supports German, English, French, Spanish, Italian, and Japanese.

## First-Time Setup

1. Start `NussmannMediaForge.exe`.
2. Open `Settings`.
3. Set the path to `makemkvcon64.exe`.
4. Set the MKVToolNix folder that contains `mkvmerge.exe` and `mkvpropedit.exe`.
5. Set the path to `HandBrakeCLI.exe`.
6. Enter your TMDb API key.
7. Select your hardware encoder: `nvidia` or `amd`.
8. Select the default audio language used for audio flag updates.
9. Optionally choose a default output folder.
10. Save the settings.

## Ripping a TV Show

1. Open the `Disc Ripper` tab.
2. Select `TV Show`.
3. Search for the show title through TMDb.
4. Select the correct result.
5. Enter the season number and the first episode number on the disc.
6. Choose `smart` mapping for runtime-based matching, or `auto` for simple order-based matching.
7. Choose an output folder or leave it empty for an automatic folder.
8. Enable or disable H.265 compression.
9. Click `Scan and rip disc`.

Progress and command output are shown in the Run Log panel on the right.

## Ripping a Movie

1. Open the `Disc Ripper` tab.
2. Select `Movie`.
3. Search for the movie through TMDb.
4. Select the correct result.
5. Choose an output folder or leave it empty for an automatic folder.
6. Enable or disable H.265 compression.
7. Click `Scan and rip disc`.

The app scans the disc and chooses the title whose runtime best matches the TMDb movie runtime.

## Tools

Open the `Tools` tab to work with existing video files.

Choose `TV Show` or `Movie` before searching TMDb.

`Auto-Rename` works in two modes. For TV shows, choose a folder, search TMDb, select the correct show match, enter the season, and rename files in alphabetical order using episode titles. For movies, choose a single video file, search TMDb as `Movie`, select the correct match, and rename the file to `Title (Year)`.

`Single Transcode` converts one selected video file into a `Transcoded` subfolder. If a movie match is selected, the output file uses the movie title and release year.

`Batch Transcode` converts all supported video files in the selected folder into a `Transcoded` subfolder using the hardware encoder selected in Settings.

`Set <Language> Default Audio` updates MKV/MP4 files so the first detected audio track matching the language selected in Settings becomes the default track.

Tool progress and output are also shown in the Run Log panel.

## Automated GitHub Releases

This repository includes a GitHub Actions workflow that builds the Windows app automatically.

- Every push to `main` or `master` builds `NussmannMediaForge-windows.zip` and uploads it to a rolling `latest` GitHub Release.
- Every pushed tag matching `v*` builds the app and creates or updates a versioned release for that tag.
- You can also run the workflow manually from the GitHub Actions tab.

To publish a versioned release:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

## Build From Source

Install Python, then run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python nussmann_mediaforge.py
```

To create a Windows release build locally:

```powershell
.\build_release.ps1
```

The portable app is created at:

```text
release-dist\NussmannMediaForge\NussmannMediaForge.exe
```

To package it manually for GitHub Releases:

```powershell
Compress-Archive -Path .\release-dist\NussmannMediaForge\* -DestinationPath .\NussmannMediaForge-windows.zip -Force
```

## Publishing Your Own Fork

```powershell
git init
git add .
git commit -m "Initial Nussmann MediaForge release"
git remote add origin https://github.com/YOURNAME/nussmann-mediaforge.git
git branch -M main
git push -u origin main
```

After pushing to `main` or `master`, GitHub Actions builds the Windows ZIP and attaches it to the `latest` release automatically.

## Legal Notice

Only use this app with discs and media you legally own and are allowed to back up in your jurisdiction. MakeMKV, MKVToolNix, HandBrake, and TMDb are separate projects with their own licenses and terms.

## Security

Do not commit API keys to Git. The TMDb API key is stored only in the local user settings file.
