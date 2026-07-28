# Firebase setup — Who's online (first time)

This app uses **Firebase Realtime Database** so tablets can report:

- which device is open
- which user is logged in

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
    }
  }
}
```

3. Click **Publish**

Meaning:
- only signed-in app devices can read the online list
- a device can only update its own presence row
- `dashboard_settings` stores the Home week-graph filter (This week / Last week) and optional prize message for the top picker. Only Super Admin should change these in the app / monitor UI.

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
