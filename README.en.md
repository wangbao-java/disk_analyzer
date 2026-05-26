English | [简体中文](README.md)

# Disk Analyzer — Disk Space Visualization Tool

A cross-platform disk space analysis and cleanup tool built with Python + tkinter.

## Features

- **Directory Scanning** — Multi-threaded recursive scanning with real-time progress
- **Treemap Visualization** — Color-coded rectangles proportional to disk usage; double-click to drill down
- **Directory Tree** — Sortable tree view, right-click to open in Explorer or delete
- **Top 100 Largest Files** — Find the biggest space hogs instantly
- **Directory Bloat Analysis** — Spot directories with many small files (e.g. `node_modules`, `.git`, log dirs)
- **File Type Breakdown** — Aggregate by extension with percentage bars
- **Junk Cleanup Scanner** — Auto-detect 13 categories of system junk (temp files, caches, logs)
- **Safe Delete** — Confirmation dialog with size preview before permanent deletion

## Requirements

- Python 3.7+
- tkinter (bundled with Python on Windows/macOS; on Linux install `python3-tk`)

```bash
# Linux only
sudo apt install python3-tk        # Debian/Ubuntu
sudo dnf install python3-tkinter   # Fedora
```

## Quick Start

### Run directly

```bash
python disk_analyzer.py
```

### Windows executable (no Python required)

Download the latest `DiskAnalyzer.exe` from [GitHub Releases](https://github.com/wangbao-java/disk_analyzer/releases).

Or build it yourself:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name DiskAnalyzer --icon=app.ico disk_analyzer.py
```

On Windows, just double-click `build_exe.bat`.

## Interface

```
┌──────────────────────────────────────────────────────────┐
│ Scan: [C:\__________] [Browse] [▶ Start] [⏹ Stop]       │
├─────────────────────┬────────────────────────────────────┤
│ Directory Tree      │  📊 Treemap │ 📄 Big Files │       │
│ ├─ Windows   12 GB  │  ┌──┬──────┐                       │
│ ├─ Users     30 GB  │  │  │      │                       │
│ ├─ ...              │  └──┴──────┘                       │
│                     │                                     │
│                     │  📁 File Types │ 📂 Bloat │ 🧹 Junk│
├─────────────────────┴────────────────────────────────────┤
│ Scan complete | 123,456 items | 120.5 GB | 3.2 s         │
└──────────────────────────────────────────────────────────┘
```

## Tabs

| Tab | Description |
|-----|-------------|
| 📊 Treemap | Visual size distribution; click a tree node to zoom in |
| 📄 Big Files | Top 100 files by size; double-click to open location |
| 📁 File Types | Aggregated by extension with share percentage |
| 📂 Bloat | Subdirectories sorted by size/file count; 🟠 = many small files |
| 🧹 Junk | Scan and list temporary files, caches, logs for cleanup |

## Notes

- Scanning a large drive may take tens of seconds depending on file count
- Click **Stop** to cancel a scan at any time
- Deletion is **permanent** (bypasses Recycle Bin); a confirmation dialog will appear
- Directories without read permission are shown as `🔒 (no access)` in the tree

## License

MIT