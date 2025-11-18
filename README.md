# Qrabackup – Simple Rsync Backup GUI for Linux

Qrabackup is a small, no-nonsense backup helper for Linux built with **PyQt5** and **rsync**.

It lets you define multiple backup *profiles* (called “locations”) with their own:

- Source folders
- Destination folder
- Exclude patterns
- Per-job rsync options

Profiles are saved under `~/.config/qrabackup/settings.json` (XDG-aware) and are loaded automatically on start.

---

## Features

-  **Multiple profiles** (“Backup Locations”) in a list on the left  
-  Each profile has:
  - One or more **Sources**
  - One **Destination**
  - **Exclude patterns** (one per line)
  - Per-profile **rsync options** (archive, verbose, compress, delete, dry-run, etc.)
-  **Run Selected** – run the currently selected profile  
-  **Run All** – run all profiles sequentially  
-  **Auto-save settings** on any change and on exit  
-  **Command preview** – shows the exact `rsync` command being executed  
-  **Live log output** and **progress bar** based on rsync output (`--info=progress2`)  
-  Settings stored in a single JSON file: `~/.config/qrabackup/settings.json`  
-  Honors `$XDG_CONFIG_HOME` if set  

---

## Requirements

- **Linux** (tested/targeted)
- **Python** 3.8+
- **PyQt5**
- **rsync** available in `PATH`

Install PyQt5 (for example):

```bash
pip install PyQt5

