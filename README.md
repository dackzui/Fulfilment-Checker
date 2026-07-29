# Picking Barcode Scanner

A Python barcode scanning app for warehouse picking verification. Runs on **Windows desktop** and **tablets** (touch-friendly layout, large tap targets).

Built with [Flet](https://flet.dev/) — a Flutter-based Python UI framework.

## Features

- **New Scan** — capture picker details, scan barcodes, verify picking
- **Hardware scanner support** — USB/handheld scanners work as keyboard input (scan into the barcode field, press Enter)
- **Barcode Master List** — loads `BarcodeMasterList.xlsx` to resolve scanned barcodes to Item Part No.
- **Scan verification** — green checkmarks when scanned part and qty match the uploaded picking ticket
- **History** — SQLite-backed session storage with scanned item details
- **Responsive layout** — sidebar navigation, touch-sized controls for tablet use

## Quick Start

```bash
# Create virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
python main.py
```

## Android APK

The project is set up for **repeatable APK builds** — keep editing Python source as usual and rebuild when you need a new APK.

### Build on Windows (x64)

```powershell
cd c:\picker-check
.\scripts\build_apk.ps1
```

APK output: `build\apk\`. The first build downloads Flutter/Android tooling (10–20 minutes).

**Note:** `flet build apk` is not supported on **Windows ARM64** (Surface Pro X, etc.). Use an x64 PC, WSL on x64, or GitHub Actions below.

### Build with GitHub Actions (any dev machine)

1. Push this repo to GitHub.
2. Open **Actions** → **Build Android APK** → **Run workflow**.
3. Download the `picker-check-apk` artifact when the job finishes.

### After you change the app

1. Edit code under `app/` (same workflow as desktop).
2. Test locally: `python main.py`
3. Rebuild the APK (script or GitHub Actions).
4. Install the new APK on your tablet.

`build/` is git-ignored — only the APK output is regenerated; your source stays the project you edit.

Configuration: `pyproject.toml` (app name, bundle id, icons, packaged files).

## Top Pickers Monitor (desktop)

Super Admin / Monitor Viewer board for live rankings. Launch:

```bat
Run-Monitor.bat
```

Uses the packaged EXE under `dist\monitor\` when you have built it (`build-monitor.bat`); otherwise starts from the Python virtualenv. Both modes use this project's `data\` folder (Firebase, users, barcode list).

### Build / install (repeatable)

On this PC (x64 or ARM64):

```bat
build-monitor.bat
```

Or:

```powershell
.\scripts\build_monitor.ps1              # build + install shortcuts
.\scripts\build_monitor.ps1 -BuildOnly   # EXE only under dist\monitor\
.\scripts\build_monitor.ps1 -InstallOnly # reinstall last build
```

- Build output: `dist\monitor\DEKSTopPickersMonitor\`
- Installs to: `%LOCALAPPDATA%\Programs\DEKSTopPickersMonitor\`
- Creates Desktop + Start Menu shortcuts
- Uninstall: run `Uninstall Monitor.bat` in the install folder

First pack downloads/builds PyInstaller tooling (a few minutes). After that, re-run `build-monitor.bat` whenever you change the Monitor.

Copy `data\firebase_config.json` into the install `data` folder if the monitor should use Who's online / cloud settings.

## Tablet / Browser Mode

To run on a tablet over the local network (e.g. Surface, iPad on same Wi‑Fi):

```bash
flet run main.py --web --port 8550
```

Open `http://<your-pc-ip>:8550` in the tablet browser. Barcode scanners that act as keyboard input work when the scan field is focused.

## Barcode Master List

The barcode list is stored on each device as:

`data/BarcodeMasterList.xlsx`

**Super Admin** uploads and publishes the list from **Top Pickers Monitor → Settings → Barcode Master List**. Tablets download the shared file from Firebase automatically when the app opens.

When a barcode is scanned, the app looks up **Item Part No.** and **Description** from this file, then compares against the uploaded picking ticket **Qty Ordered**. If the barcode has a **Box Qty** value, that quantity is applied automatically (× the Qty field). Otherwise each scan counts as **1 item**. All scans for the same **Item Part No.** are summed together against the picking ticket. **PalletQty** is kept in the master list for reference only — pallets are not scanned via barcode yet.

### Admin login

On first run, the app creates `data/admins.json` with default credentials:

- **Username:** `admin`
- **Password:** `admin`
- **Role:** Super Admin

Sign in from **Settings**. After login:

- **Super Admin** — add users, set passwords, manage Monitor settings / barcode publish, delete history.
- **Admin** — delete history and cloud sync actions as allowed.
- **Picker** — appears in the New Scan picker dropdown.

Use **Add User / Picker** and the key icon (**Set Password**) on the user list.

## Fleet data sync (daily scanner.db)

Controlled from **Top Pickers Monitor → Settings → Fleet data sync** (Super Admin):

1. Enable daily backup and set the time (e.g. 17:00)
2. Leave each tablet app open near that time
3. Tablets upload their `scanner.db` into Firebase (`device_backups`)
4. On the Monitor PC, use **Refresh list** / **Download all to this PC**

Publish the updated Firebase rules in `docs/FIREBASE_SETUP.md` (includes `fleet_sync_settings` and `device_backups`).

## History export

On **History**, use **Export Report PDF** for a filtered PDF. Daily tablet backups are controlled from **Top Pickers Monitor → Fleet data sync**.

## Sample barcodes (from picking ticket SO5570391)

| Barcode         | Part No  | Description                        |
|-----------------|----------|------------------------------------|
| 9328204000055   | 2027     | EW Roll-in Rubber NR 100mm         |
| 9316867005784   | DNL101B  | Dektite Nulead #1 Blk              |
| 9316867001076   | 2023     | Jenco Multi Vent cap Poly 100mm    |

## Project Structure

```
picker-check/
├── main.py              # Entry point
├── app/
│   ├── main_app.py      # Shell + navigation
│   ├── barcode_catalog.py  # Excel barcode master loader
│   ├── pdf_parser.py    # Picking ticket PDF parser
│   ├── database.py      # SQLite persistence
│   └── pages/
│       ├── home.py
│       ├── new_scan.py
│       └── history.py
└── data/
    ├── BarcodeMasterList.xlsx  # Barcode master list (editable)
    ├── config.json
    └── scanner.db              # Created on first run
```

## Developer

**Marie Apellanes**
