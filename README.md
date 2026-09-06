# ⚡ News-Hub

Ein hochperformanter, KI-gestützter und clientseitig verschlüsselter RSS-Nachrichten-Aggregator. News-Hub konsolidiert Dutzende Tech-Feeds, filtert plattformübergreifend Duplikate heraus, übersetzt englische Meldungen vollautomatisch ins Deutsche und fasst Kernpunkte mittels Google Gemini prägnant zusammen.

Das Ergebnis wird als schlanke, statische Progressive Web App (PWA) via GitHub Pages bereitgestellt.

---

## ✨ Features & Highlights

### 🧠 KI-Redaktion (Google Gemini)
* **Automatische Titelübersetzung:** Englische Schlagzeilen werden ohne Sinnverlust vollständig ins Deutsche übertragen.
* **Anti-Clickbait:** Reißerische Überschriften werden durch konkrete Modellnamen, Versionsnummern oder Fehlerbeschreibungen ersetzt.
* **1-Satz-Zusammenfassung mit Fallback:** Jeder Artikel erhält genau einen kompakten Satz mit **fettgedruckten Schlüsselwörtern**. Sollte die API leer bleiben, greift automatisch ein Feed-Auszug.
* **Intelligente Bildfilterung:** Nur informative Fotos (Geräte, Benchmarks, UI-Screenshots) werden übernommen – Werbelogos, Tracking-Pixel und generische Icons werden automatisch verworfen.

### 🧹 Ausgefeilte Deduplizierung ($O(N^2)$ zeitfensteroptimiert)
* **20h-Zeitfenster Short-Circuit:** Liegen ähnliche Schlagzeilen mehr als 20 Stunden auseinander, werden sie nicht gemerged. Verhindert falsche Zusammenführungen bei wiederkehrenden Newsthemen.
* **Multi-Source Merge:** Berichten mehrere Magazine über denselben Sachverhalt, bleibt nur der primäre Artikel (nach konfigurierter Priorität) erhalten. Alle weiteren Quellen werden als Badge (`Auch bei: Heise, Golem`) verlinkt.
* **Interaktives Dubletten-Modal:** Aufschlüsselung aller zusammengeführten Meldungen samt Original-Links nach Quelle aufklappbar (`🧹 X Duplikate bereinigt ℹ️`).

### 📱 Responsive UI & Mobile First (PWA)
* **Desktop:**
  * **Auto-Hide Sticky Header:** Gleitet beim Runterscrollen aus dem Blickfeld und erscheint beim Hochscrollen sofort wieder.
  * Vollständige Tastatur-Navigation (`J`/`K` navigieren, `O` öffnen, `M` gelesen/ungelesen, `[` Sidebar toggeln, `/` Suche, `Esc` Modals schließen).
* **Smartphone:**
  * **Floating Bottom Pill:** Ergonomische Daumenleiste für Menü (`☰`), Suche (`🔍`), Workflow-Trigger (`🔄`) und Theme-Toggle (`🌓`).
  * **Layering & Überlappungsschutz:** Beim Öffnen der mobilen Sidebar blendet sich die Navigationsleiste automatisch nach unten aus und die Menüleiste legt sich mit erhöhtem Z-Index vollflächig über das Layout.
  * **Gestenleisten-Support:** Nutzt `env(safe-area-inset-bottom)` und 48×48px Touch-Targets für fehlerfreie Einhandbedienung.
* **DOM- & Performance-Tuning:**
  * **Debounced Search (120ms):** Verhindert Ruckler beim schnellen Tippen im Suchfeld.
  * **Scroll-Gedächtnis (`seen` vs. `read`):** Sichtbare Artikel (ab 1 Sekunde Viewport) werden gedimmt (`seen`), geklickte oder manuell markierte Meldungen ausgegraut (`read`).
  * **Live-Sync:** Beim Tab-Fokus oder Entsperren des Handys via `visibilitychange` prüft das Frontend im Hintergrund auf neu gebaute `data.json`-Dateien und aktualisiert relative Zeitangaben.

### 🔐 Ende-zu-Ende-Verschlüsselung & Workflow-Diagnose
* **AES-256-CBC:** Verschlüsselung der `data.json` direkt im GitHub Actions Runner. Entschlüsselung erfolgt clientseitig über Web Crypto / CryptoJS.
* **Direktabsprung zu GitHub Actions:** 
  * Der Reload-Button (`🔄`) bietet nach dem Start per Dialog an, direkt zur GitHub Actions Workflow-Übersicht zu springen.
  * Das Diagnose-Modal (`📡 Feed-Status Details`) enthält einen direkten Schnellzugriffslink auf den aktuellen Workflow-Status.

---

## 🏗️ Architektur & Performance

```text
[ RSS / Atom Feeds ] 
      │ (Thread-Pool + ETag / 304 Cache + Zeitstempel-Toleranz)
      ▼
[ build_pages.py ] 
      │
      ├── 1. Lokaler Cross-Check & 20h-Zeitfenster (Duplikate abfangen)
      ├── 2. Delta-Batching (nur echte Neuheiten verarbeiten)
      ├── 3. Gemini Flash (2 parallele Worker, strukturierter JSON-Output)
      ├── 4. Globaler Bereinigungslauf & Payload-Verschlankung
      └── 5. AES-Verschlüsselung & statische HTML-Generierung
      │
      ▼
[ GitHub Pages / Browser ]
      └── Entschlüsselung im Client, PWA-Caching & Indexed/Local Storage
```

---

## ⚙️ Einrichtung & Konfiguration

### 1. Repository Secrets & Variablen

Unter **Settings → Secrets and variables → Actions** einrichten:

| Typ | Name | Beschreibung |
| :--- | :--- | :--- |
| **Secret** | `GEMINI_API_KEY` | *(Erforderlich)* API-Key für Google Gemini. |
| **Secret** | `PAGE_PASSWORD` | *(Optional)* Passwort für die AES-256-Verschlüsselung von `data.json`. Bleibt unverschlüsselt, wenn leer. |
| **Secret** | `FEEDS_OPML` | *(Optional)* Rohinhalt deiner `feeds.opml`. Falls nicht gesetzt, wird eine lokale `feeds.opml` im Repo verwendet. |
| **Secret** | `FEED_PRIORITIES`| *(Optional)* JSON-Map mit Quell-Prioritäten, z. B. `{"Heise Online": 2, "Golem": 1}`. |
| **Variable**| `DEDUP_RATIO` | *(Optional)* Schwellenwert für String-Ähnlichkeit (Default: `0.78`). |
| **Variable**| `DEDUP_OVERLAP`| *(Optional)* Schwellenwert für Keyword-Überdeckung (Default: `0.65`). |

---

### 2. GitHub Actions Workflow (`.github/workflows/deploy.yml`)

```yaml
name: Deploy News Hub

on:
  schedule:
    # Läuft halbstündlich zwischen ca. 06:00 und 23:59 Uhr deutscher Zeit
    - cron: '*/30 4-22 * * *'
  workflow_dispatch:
  push:
    branches:
      - main

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pages: write
      id-token: write

    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install Dependencies
        run: |
          pip install feedparser google-genai pydantic requests cryptography urllib3

      - name: Build Pages & Process Feeds
        id: build
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          PAGE_PASSWORD: ${{ secrets.PAGE_PASSWORD }}
          FEEDS_OPML: ${{ secrets.FEEDS_OPML }}
          FEED_PRIORITIES: ${{ secrets.FEED_PRIORITIES }}
        run: |
          python build_pages.py

      - name: Cache Metadata Commit
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add cache_meta.json
          git diff --quiet && git diff --staged --quiet || (git commit -m "chore: update feed cache metadata [skip ci]" && git push)

      - name: Upload Pages Artifact
        if: steps.build.outputs.deploy == 'true' || github.event_name == 'workflow_dispatch'
        uses: actions/upload-pages-artifact@v3
        with:
          path: public

      - name: Deploy to GitHub Pages
        if: steps.build.outputs.deploy == 'true' || github.event_name == 'workflow_dispatch'
        uses: actions/deploy-pages@v4
```

---

## ⌨️ Tastaturkürzel (Desktop)

| Taste | Aktion |
| :---: | :--- |
| <kbd>J</kbd> / <kbd>↓</kbd> | Nächsten Artikel auswählen |
| <kbd>K</kbd> / <kbd>↑</kbd> | Vorherigen Artikel auswählen |
| <kbd>O</kbd> / <kbd>Enter</kbd> | Ausgewählten Artikel im neuen Tab öffnen & als gelesen markieren |
| <kbd>M</kbd> | Ausgewählten Artikel als gelesen / ungelesen umschalten |
| <kbd>[</kbd> | Sidebar ein- oder ausklappen |
| <kbd>/</kbd> | Direkt in das Suchfeld springen |
| <kbd>Esc</kbd> | Suche verlassen / geöffnete Modals schließen |
