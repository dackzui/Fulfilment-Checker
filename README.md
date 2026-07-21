# Picking Barcode Scanner

A Python barcode scanning app for warehouse picking verification. Runs on **Windows desktop** and **tablets** (touch-friendly layout, large tap targets).

Built with [Flet](https://flet.dev/) — a Flutter-based Python UI framework.

## Features

- **New Scan** — capture picker/checker details, scan barcodes, verify picking
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

## Tablet / Browser Mode

To run on a tablet over the local network (e.g. Surface, iPad on same Wi‑Fi):

```bash
flet run main.py --web --port 8550
```

Open `http://<your-pc-ip>:8550` in the tablet browser. Barcode scanners that act as keyboard input work when the scan field is focused.

## Barcode Master List

The barcode list is stored inside the app at:

`data/BarcodeMasterList.xlsx`

On startup, the app loads this file into the local database.

**Only admins** can upload a new barcode list from the app. On **Home**, click **Update Barcode List** and sign in when prompted.

### Admin login

On first run, the app creates `data/admins.json` with default credentials:

- **Username:** `admin`
- **Password:** `admin`
- **Role:** Super Admin

Sign in from **Home → Admin Accounts**. After login:

- **Super Admin** — add users, set any user's password, upload barcode list, delete history.
- **Admin** — upload barcode list and delete history only.

Use **Add User** and the key icon (**Set Password**) on the user list. New users are created as Admin.

## Cloud Sync (History)

On **History**, click **Sync** to upload the filtered report PDF plus a full DB or filtered sessions backup.

### Simple setup (recommended — no API keys)

1. Open **Settings → Cloud Sync**
2. Click **Choose cloud folder**
3. Select your local **Google Drive** or **OneDrive** folder on this PC
4. Use **History → Sync** and choose **Cloud folder on this PC**

Files are copied to:
`YourFolder / Picking Barcode Scanner / History / YYYY-MM-DD_HHMM /`

### Optional browser sign-in

If IT enables Google / Microsoft app login in `app/cloud_oauth_defaults.py`, Settings also shows **Sign in with Google** / **Sign in with OneDrive** (browser login, like ShareFile — users never enter API keys).

When a barcode is scanned, the app looks up **Item Part No.** and **Description** from this file, then compares against the uploaded picking ticket **Qty Ordered**. If the barcode has a **Box Qty** value, that quantity is applied automatically (× the Qty field). Otherwise each scan counts as **1 item**. All scans for the same **Item Part No.** are summed together against the picking ticket. **PalletQty** is kept in the master list for reference only — pallets are not scanned via barcode yet.

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
