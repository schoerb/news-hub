# ⚡ News-Hub

Ein hochperformanter, KI-gestützter und clientseitig verschlüsselter RSS-Nachrichten-Aggregator. News-Hub konsolidiert Dutzende Tech-Feeds, filtert plattformübergreifend Duplikate heraus, übersetzt englische Meldungen vollautomatisch ins Deutsche und fasst Kernpunkte mittels Google Gemini prägnant zusammen.

Das Ergebnis wird als schlanke, statische Progressive Web App (PWA) via GitHub Pages bereitgestellt.

---

## ✨ Features & Highlights

### 🧠 KI-Redaktion (Google Gemini)
* **Automatische Titelübersetzung:** Englische Schlagzeilen werden ohne Sinnverlust vollständig ins Deutsche übertragen.
* **Anti-Clickbait:** Reißerische Überschriften werden durch konkrete Modellnamen, Versionsnummern oder Fehlerbeschreibungen ersetzt.
* **1-Satz-Zusammenfassung:** Jeder Artikel erhält genau einen kompakten Satz mit **fettgedruckten Schlüsselwörtern**.
* **Intelligente Bildfilterung:** Nur informative Fotos (Geräte, Benchmarks, UI-Screenshots) werden übernommen – Werbelogos, Tracking-Pixel und generische Icons werden automatisch verworfen.

### 🧹 Ausgefeilte Deduplizierung ($O(N^2)$ optimiert)
* **Multi-Source Merge:** Berichten mehrere Magazine über denselben Sachverhalt, bleibt nur der primäre Artikel (nach konfigurierter Priorität) erhalten. Alle weiteren Quellen werden als Badge (`Auch bei: Heise, Golem`) verlinkt.
* **Audit-Modal:** Ein Klick auf `🧹 X Duplikate bereinigt ℹ️` öffnet eine Aufstellung aller zusammengeführten Artikel samt Original-Links.
* **High-Speed Vorfilterung:** Schnelle Keyword- und String-Längen-Short-Circuits reduzieren teure `SequenceMatcher`-Prüfungen auf ein Minimum und verhindern Runner-Timeouts.

### 📱 Hybrides Responsive-Design & Mobile First
* **Desktop:**
  * **Auto-Hide Sticky Header:** Bleibt beim Lesen unauffällig stehen, gleitet beim Runterscrollen sanft aus dem Sichtfeld und erscheint beim Hochscrollen sofort wieder.
  * Vollständige Tastatur-Navigation (`J`/`K` durchkämmen, `O` öffnen, `M` gelesen markieren, `[` Sidebar toggeln, `/` Suche).
* **Smartphone:**
  * **Floating Bottom Pill:** Schwebende Daumen-Leiste für Menü, Suche, Aktualisierung (`🔄`) und Theme-Toggle (`🌓`).
  * **Auto-Hide:** Verschwindet beim Wischen nach unten (Scrollen nach oben) und taucht beim Weiterlesen/Ganz-oben-Sein wieder auf.
  * **Gestenleisten-Support:** Nutzt `env(safe-area-inset-bottom)` und 48×48px Touch-Targets für fehlerfreie Einhandbedienung.
* **Scroll-Gedächtnis (`seen` vs. `read`):** 
  * Über die *Intersection Observer API* werden Artikel, die für mindestens 1 Sekunde im Viewport sichtbar waren, dezent gedimmt (`seen`). Neuer Content sticht sofort ins Auge.
  * Geklickte oder mit `M` markierte Artikel werden stark ausgegraut (`read`).
* **Live-Sync:** Kehrst du nach längerer Zeit in den Tab zurück oder entsperrst das Smartphone, prüft die App im Hintergrund via `visibilitychange` auf neue Feeds und aktualisiert relative Zeitangaben.

### 🔐 Ende-zu-Ende Verschlüsselung & Datenschutz
* Die Daten in `public/data.json` werden im GitHub Actions Runner mittels AES-256-CBC (OpenSSL-kompatibles KDF mit Salt) verschlüsselt.
* Das Entschlüsseln geschieht rein clientseitig im Browser via Web Crypto / CryptoJS. Auf GitHub Pages liegen zu keinem Zeitpunkt unverschlüsselte Lesedaten öffentlich zugänglich.

---

## 🏗️ Architektur & Performance

```text
[ RSS Feeds ] 
      │ (Thread-Pool + ETag / 304 Cache + 8s Timeout)
      ▼
[ build_pages.py ] 
      │
      ├── 1. Lokaler Cross-Check (bereits bekannte URLs/Titel abfangen)
      ├── 2. Delta-Batching (nur echte Unikate)
      ├── 3. Gemini 3.5 Flash Lite (2 parallele Worker, strukturierter JSON-Output)
      ├── 4. Globaler Bereinigungslauf & Payload-Verschlankung
      └── 5. AES-Verschlüsselung & statische HTML-Generierung
      │
      ▼
[ GitHub Pages / Browser ]
      └── Entschlüsselung im Client, PWA-Caching & Indexed/Local Storage
```

* **Runner-Laufzeit:** Typischerweise unter 90 Sekunden dank Connection Pooling (`requests.Session` + `HTTPAdapter`), Concurrency und Delta-Caching.
* **Payload-Diät:** Leere Felder (`null`, `[]`) werden vor der Serialisierung aus `data.json` entfernt, was Dateigröße und Entschlüsselungszeit auf Mobilgeräten um ca. 35 % senkt.

---

## ⚙️ Einrichtung & Konfiguration

### 1. Repository Secrets & Variablen

Lege unter **Settings → Secrets and variables → Actions** folgende Einträge an:

| Typ | Name | Beschreibung |
| :--- | :--- | :--- |
| **Secret** | `GEMINI_API_KEY` | *(Erforderlich)* API-Key für Google Gemini. |
| **Secret** | `PAGE_PASSWORD` | *(Optional)* Passwort für die AES-256-Verschlüsselung von `data.json`. Wenn leer, bleiben die Daten öffentlich lesbar. |
| **Secret** | `FEEDS_OPML` | *(Optional)* Rohinhalt deiner `feeds.opml`. Falls nicht gesetzt, wird eine lokale `feeds.opml` im Repo verwendet. |
| **Secret** | `FEED_PRIORITIES`| *(Optional)* JSON-Map mit Quell-Prioritäten, z. B. `{"Heise Online": 2, "Golem": 1}`. |
| **Variable**| `DEDUP_RATIO` | *(Optional)* Schwellenwert für String-Ähnlichkeit (Default: `0.78`). |
| **Variable**| `DEDUP_OVERLAP`| *(Optional)* Schwellenwert für Keyword-Überdeckung (Default: `0.65`). |

---

### 2. GitHub Actions Workflow (`.github/workflows/deploy.yml`)

Der Workflow aktualisiert die Feeds tagsüber alle 30 Minuten und pausiert nachts nach deutscher Zeit (spart Kontingent):

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
