# Dip Hunter — Android 15 Sideload Build Guide

End-to-end recipe for turning the existing `refresh_data.py` + `dip_hunter.html`
pair into a phone app that auto-refreshes every 10 minutes.

```
┌─────────────────────┐  every 10 min   ┌────────────────────┐
│  GitHub Actions cron│ ──────────────▶ │  GitHub Pages site │
│  runs refresh_data  │   commit HTML   │ dip_hunter.html    │
└─────────────────────┘                 └─────────┬──────────┘
                                                  │ HTTPS
                                                  ▼
                                        ┌────────────────────┐
                                        │  Android WebView   │
                                        │  app (this build)  │
                                        │  reloads every 10m │
                                        └────────────────────┘
```

## Part 1 — Publish the cloud refresher (one-time, ~10 min)

### 1.1 Push this folder to a new GitHub repo

```bash
cd C:\TempFolder\SForecast\Forecast
git init
git add .
git commit -m "initial dip hunter"
git branch -M main
# Create the repo on github.com first — then:
git remote add origin https://github.com/YOUR_GH_USERNAME/dip-hunter.git
git push -u origin main
```

A **public** repo is recommended — GitHub Actions minutes are free and
unlimited on public repos. Private works too but caps at 2,000 min/month.

### 1.2 Enable GitHub Pages

On github.com → repo → **Settings → Pages**

- Source: **Deploy from a branch**
- Branch: **main**, folder: **/ (root)**
- Save

After 1–2 minutes the dashboard is live at:

```
https://YOUR_GH_USERNAME.github.io/dip-hunter/dip_hunter.html
```

### 1.3 Verify the cron is running

`.github/workflows/refresh.yml` is already in place. The first run is
triggered automatically on push, then every 10 min during US market
hours (Mon–Fri, 13:00–21:50 UTC) plus a few off-hours backstops.

GitHub → repo → **Actions** tab. You should see "Refresh Dip Hunter"
runs succeeding. The bot commits `dip_hunter.html`, `top50.json`,
and `strong_buys.json` back to `main`.

> **Tip**: Click *Run workflow* in the Actions UI to refresh
> on demand without waiting for the cron.

## Part 2 — Build the Android app (one-time, ~15 min)

### 2.1 Install Android Studio

[https://developer.android.com/studio](https://developer.android.com/studio) — download "Koala" (Hedgehog or newer
also fine). On first launch let it install the Android 15 (API 35) SDK.

### 2.2 Open the project

In Android Studio: **File → Open** → select the `android-app/` folder
inside this repo. Wait for Gradle sync to finish (~3 min the first time
while it pulls dependencies).

If Studio prompts to upgrade plugins, **decline** — the versions
pinned in `build.gradle.kts` are tested.

### 2.3 Point the app at *your* dashboard URL

Edit `app/src/main/res/values/strings.xml`:

```xml
<string name="dashboard_url">
    https://YOUR_GH_USERNAME.github.io/dip-hunter/dip_hunter.html
</string>
```

(Optionally bump `reload_interval_ms` in `integers.xml` if you want
something other than 10 minutes — minimum useful is ~60_000 = 1 min.)

### 2.4 Build the APK

In Android Studio: **Build → Build Bundle(s) / APK(s) → Build APK(s)**

Or from a terminal:

```bash
cd C:\TempFolder\SForecast\Forecast\android-app
gradlew.bat assembleRelease            # Windows
./gradlew assembleRelease              # macOS / Linux
```

The signed (with the debug keystore) APK lands at:

```
android-app/app/build/outputs/apk/release/app-release.apk
```

### 2.5 Install on your phone

**Option A — USB debug**:

1. On the phone: **Settings → About phone → tap *Build number* 7 times** to
   unlock developer mode.
2. **Settings → System → Developer options → USB debugging**: ON.
3. Connect via USB. Android Studio's *Run* button will install the
   debug build directly.

**Option B — sideload the APK**:

1. Copy `app-release.apk` to the phone (Drive, email, USB).
2. Open it in the Files app. Allow "install unknown apps" when prompted.
3. Tap **Install**.

The launcher gets a "Dip Hunter" icon. Open it — the WebView pulls the
dashboard, auto-refreshes every 10 minutes while open, and pull-to-refresh
works any time.

## Part 3 — How the pieces fit

| Piece | Role | When it runs |
|---|---|---|
| `refresh_data.py` | Computes indicators, scores, embeds JSON in HTML | Cloud, every 10 min |
| `.github/workflows/refresh.yml` | Cron + commit | Cloud, every 10 min |
| `dip_hunter.html` | The dashboard (data baked in) | Served by GitHub Pages |
| `android-app/` | Native APK that loads the Pages URL | Phone, while open |

The phone never talks to Yahoo Finance directly — it only reads the
pre-rendered HTML — so there are no API keys, no rate-limit
headaches, and the phone barely uses any battery.

## Common issues

**Workflow run fails on `git push`** — GitHub now requires *workflow
permissions* to be set to **Read and write**. In the repo:
*Settings → Actions → General → Workflow permissions →
"Read and write permissions"* → Save.

**Pages 404** — Pages takes a minute to build the first time. Check the
*Actions* tab; once *pages-build-deployment* succeeds, refresh.

**WebView shows blank screen** — verify the `dashboard_url` in
`strings.xml` opens correctly in your phone's regular browser first.

**`yfinance` rate-limited in Actions** — rare but possible. Add
`pip install yfinance==0.2.50` (or pin to whatever the latest stable
is) in the workflow `Install deps` step and rerun.

**Cron drifts by 5–15 min** — known GitHub Actions behavior under
load. Acceptable for a 10-min interval; if you need tighter, move the
cron to a paid runner or a small Render/Fly.io worker.

## Next steps you might want later

- **Push notifications** when a new STRONG BUY enters the list — adds
  ~80 lines of Kotlin (a `WorkManager` job that diffs `strong_buys.json`
  against the previously seen copy).
- **Multiple watchlists** — add a query-string parameter to
  `dip_hunter.html` and a tab strip in the WebView.
- **Offline mode** — cache the last successful HTML in app storage so
  the dashboard renders even when the phone is offline.
