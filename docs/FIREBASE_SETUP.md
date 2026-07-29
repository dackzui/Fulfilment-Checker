# Firebase setup — Who's online (first time)

This app uses **Firebase Realtime Database** so tablets can report:

- which device is open
- which user is logged in
- shared users, barcode master, and daily `scanner.db` fleet backups

You only set this up **once**. Warehouse users do not create Firebase accounts.

---

## 1. Create a Firebase project

1. Open [https://console.firebase.google.com](https://console.firebase.google.com)
2. Click **Add project** (or Create a project)
3. Name it e.g. `deks-picker-check`
4. Google Analytics: **optional** (you can turn it off)
5. Click through until the project is ready

---

## 2. Register a Web app (for API keys)

We use the **Web** app config (works with our Python/Flet tablets via REST).

1. In the project overview, click the **Web** icon (`</>`)
2. App nickname: `Picking Barcode Scanner`
3. Do **not** need Firebase Hosting
4. Click **Register app**
5. Copy these values (you will paste them into the app later):
   - `apiKey`
   - `projectId`
   - `databaseURL` — may appear after you create the database in step 3

You can always find them later under **Project settings** (gear) → **Your apps** → Web app → SDK setup.

---

## 3. Create Realtime Database

1. Left menu → **Build** → **Realtime Database**
2. Click **Create Database**
3. Choose a location close to Australia if available (e.g. `australia-southeast1`), otherwise default is fine
4. Start in **locked mode** (recommended), then we set rules in the next step
5. After it is created, copy the **database URL**  
   Example: `https://deks-picker-check-default-rtdb.asia-southeast1.firebasedatabase.app`

---

## 4. Set security rules

1. Realtime Database → **Rules** tab
2. Replace everything with:

```json
{
  "rules": {
    "presence": {
      ".read": "auth != null",
      "$deviceId": {
        ".write": "auth != null && newData.child('firebase_uid').val() === auth.uid"
      }
    },
    "dashboard_settings": {
      ".read": "auth != null",
      ".write": "auth != null"
    },
    "picker_names": {
      ".read": "auth != null",
      ".write": "auth != null"
    },
    "app_users": {
      ".read": "auth != null",
      ".write": "auth != null"
    },
    "barcode_master": {
      ".read": "auth != null",
      ".write": "auth != null"
    },
    "fleet_sync_settings": {
      ".read": "auth != null",
      ".write": "auth != null"
    },
    "device_backups": {
      ".read": "auth != null",
      "$deviceId": {
        ".write": "auth != null"
      }
    }
  }
}
```

3. Click **Publish**

Meaning:
- only signed-in app devices can read the online list
- a device can only update its own presence row
- `dashboard_settings` stores the Home week-graph filter (This week / Last week) and optional prize message for the top picker. Only Super Admin should change these in the app / monitor UI.
- `picker_names` is the shared picker list for all tablets (Manage Picker Names)
- `app_users` stores shared app logins (usernames, roles, password hashes — never plaintext). Add/change users in Settings on any tablet; others pick them up on next sign-in.
- `barcode_master` stores the shared BarcodeMasterList.xlsx. Super Admin uploads it from **Top Pickers Monitor**; tablets download it automatically.

---

## 5. Enable Anonymous Auth

Tablets sign in anonymously in the background (users still use their normal app login).

1. Left menu → **Build** → **Authentication**
2. Click **Get started** if needed
3. **Sign-in method** → **Anonymous** → **Enable** → **Save**

---

## 6. Put the keys into the app

### Option A — Super Admin in the app (easiest on tablets)

1. Open **Settings**
2. Sign in as **Super Admin**
3. Under **Who's online** → **Firebase setup…**
4. Paste:
   - Web **API key**
   - **Database URL**
   - **Project ID** (optional but useful)
5. Save
6. Set a friendly **Tablet name** (e.g. `Warehouse Tablet 1`)

### Option B — Config file on PC

Copy:

`data/firebase_config.json.example` → `data/firebase_config.json`

Fill in the three fields, then restart the app.

**Do not commit** `firebase_config.json` to Git (it is excluded from the APK build list as a local secret-ish file).

---

## 7. Test

1. Open the app on **two** devices (or PC + tablet)
2. Log in as different users
3. Settings → **Who's online** → **Refresh**
4. You should see each device, user, and Online / Away

A device counts as **Online** if it sent a heartbeat within about **90 seconds** (heartbeat every 30 seconds while the app is open).

---

## Troubleshooting

| Problem | Fix |
|--------|-----|
| “Firebase not set up” | Add API key + database URL (step 6) |
| Anonymous sign-in failed | Enable Anonymous auth (step 5) |
| Permission denied | Publish the rules exactly as in step 4 |
| Empty list | Wait ~30s, tap Refresh, confirm internet on tablet |
| Wrong tablet name | Settings → change Tablet name → wait for next heartbeat |

---

## Privacy note

Presence data is only in **your** Firebase project. DEKS controls the Google account that owns the project. Warehouse staff never need Firebase Console access after setup.

---

## 8. Google Analytics 4 (optional)

Presence (“Who’s online”) stays in Firebase. **Daily active tablets** and **completed picks per picker** can also go to **Google Analytics 4** via the Measurement Protocol.

### What the app sends

| Event | When | Useful for |
|-------|------|------------|
| `tablet_active` (+ `session_start`) | Once per local day when a device heartbeats online | Daily active tablets |
| `pick_completed` | When a scan session is saved as completed (first time only) | Completed picks per picker (`picker_name`) |

### Setup in Google Analytics / Firebase

1. Open [Google Analytics](https://analytics.google.com/) (or Firebase Console → Analytics) for the same project.
2. Note the **Measurement ID** (`G-XXXXXXXXXX`) — Admin → Data streams → your stream.
3. Create a **Measurement Protocol API secret**: Admin → Data streams → your stream → Measurement Protocol API secrets → Create.
4. In the app: **Settings → Firebase setup** (Super Admin), paste:
   - Google Analytics Measurement ID
   - GA4 Measurement Protocol API secret  
   Or add the same keys to `data/firebase_config.json` on each tablet/PC (see `firebase_config.json.example`).
5. Complete one pick and leave a tablet online for a heartbeat. Events can take **up to 24–48 hours** to appear in standard reports; use **Admin → DebugView** (or Realtime) sooner if testing.

### Example Explorations / reports

- **Daily active tablets:** Event count for `tablet_active` (or Active users) by day.
- **Completed picks per picker:** Event count for `pick_completed`, breakdown by event parameter `picker_name`.

Events are sent over HTTPS from the device; they do not appear in the in-app “Who’s online” list.
