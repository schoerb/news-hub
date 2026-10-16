# ⚡ News-Hub

Ein leichtgewichtiger, selbst gehosteter RSS-News-Aggregator mit automatischer Deduplizierung, Gemini-gestützter Zusammenfassung/Übersetzung, AES-Verschlüsselung und einer für mobile Geräte optimierten Web-App (PWA).

Gehostet via **GitHub Pages**, vollautomatisiert über **GitHub Actions**.

---

## ✨ Features

- **Automatisierte Feed-Verarbeitung & Remote-Cache:** Liest Feeds per OPML ein und synchronisiert `cache_meta.json` (`ETag` / `Last-Modified`) automatisch mit GitHub Pages für extrem schnelle `304 Not Modified`-Abfragen.
- **KI-Zusammenfassung & Übersetzung:** Nutzt die Google Gemini API, um fremdsprachige Artikel ins Deutsche zu übersetzen und auf genau einen Satz mit prägnanten Fettungen einzudampfen.
- **Smarte Bildextraktion & Hotlink-Schutz:** Erkennt Bilder aus `media:content`, Enclosures sowie modernen HTML-Tags (`data-src`, `data-original`, CDN-URLs), filtert Tracker, Logos und Avatare aus, werten Bild-URLs direkt über Gemini aus und blenden fehlerhafte oder winzige Vorschaubilder (< 80px) im Browser automatisch aus.
- **Präzise Cross-Language Deduplizierung:** Verhindert Fehlverschmelzungen bei Versions-/Modellnummern (z. B. *iPhone 16* vs. *Galaxy 16GB*) durch mehrsprachigen Keyword-Overlap und `SequenceMatcher`.
- **Klickbare Zweitquellen:** Unter zusammengeführten Artikeln (`• Auch bei: ...`) sind alle weiteren Quellen direkt mit ihren jeweiligen Original-Artikeln verlinkt.
- **Ungelesen- & Lesezeichen-Filter (mit Live-Zähler):** Per Klick auf `👁️` (oder Taste `u`) lassen sich gelesene/gesehene Artikel komplett ausblenden. Der Button zeigt dynamisch die Anzahl der verbleibenden neuen Artikel mit grünem Punkt an.
- **Zweistufiges Pull-to-Refresh (Mobile):**
  - **Kurzer Zug (25px–179px):** Lädt lautlos die lokale `data.json` nach (`↓ Lokale Feeds laden`).
  - **Tiefer Zug (ab 180px):** Schaltet mit haptischem Feedback (Vibration) um und triggert den entfernten GitHub Actions Workflow (`🚀 Workflow starten`).
- **Workflow-Trigger & Live-Polling:** Button `🔄` in der Navigation startet direkt den GitHub Actions Dispatch und pollt live den Build-Status.
- **Ende-zu-Ende-Verschlüsselung:** Verschlüsselt die generierte `data.json` via AES-CBC (OpenSSL-kompatibel), falls ein Seitenpasswort vergeben ist.
- **Progressive Web App (PWA):** Offline-Fallback dank Service Worker (`news-hub-v18`) mit Network-First-Strategie für Daten und HTML.
- **Stundengenaue Zeitangaben & Archiv:** Zeigt das Alter von Artikeln ab 60 Minuten durchgehend in Stunden an (`vor Xh`) und trennt automatisch zwischen Live-Feed (letzte 24h) und Archiv (24–48h).

---

## 🚀 Setup & Konfiguration

### 1. Repository Secrets & Variablen

Lege unter **Settings > Secrets and variables > Actions** folgende Einträge an:

| Secret / Variable | Erforderlich | Beschreibung |
|---|---|---|
| `GEMINI_API_KEY` | Ja | Google AI Studio API-Key für Zusammenfassungen und Übersetzungen. |
| `FEEDS_OPML` | Nein | Rohinhalt deiner OPML-Datei als String (Fallback: `feeds.opml` im Repo-Root). |
| `PAGE_PASSWORD` | Nein | Passwort zur AES-Verschlüsselung der `data.json` (leer lassen für Plaintext). |
| `FEED_PRIORITIES` | Nein | JSON-Objekt zur Priorisierung bestimmter Feeds, z. B. `{"Heise": 2, "Golem": 2}`. |
| `DEDUP_RATIO` | Nein | Schwellenwert für String-Ähnlichkeit (Default: `0.82`). |
| `DEDUP_OVERLAP` | Nein | Schwellenwert für Keyword-Overlap (Default: `0.72`). |

### 2. GitHub Personal Access Token (PAT) für Dispatches

Um den Workflow direkt aus der Web-App heraus neu zu starten (über `🔄` oder Deep-Pull):
1. Erstelle unter **GitHub > Settings > Developer Settings > Personal Access Tokens (classic)** ein Token mit Scope `repo` (oder `workflow`).
2. Beim ersten Auslösen in der Web-App wirst du nach dem Token gefragt. Es wird ausschließlich lokal im `localStorage` deines Browsers gespeichert.

---

## 🛠️ Lokale Ausführung

1. Abhängigkeiten installieren: `pip install -r requirements.txt`
2. Umgebungsvariablen setzen:
   `export GEMINI_API_KEY="dein-api-key"`
   `export PAGE_PASSWORD="optionales-passwort"`
3. Build ausführen: `python3 build_pages.py`
4. Lokalen Webserver starten: `public` Ordner aufrufen via `python3 -m http.server 8000`

---

## 📱 Bedienung & Gesten

- **Kopfleiste (Tap-to-Top):** Ein Tipp auf den Header-Titel scrollt den Feed sanft ganz nach oben.
- **Pull-to-Refresh (Mobile):**
  - *Leichtes Herunterziehen (25–179px):* Aktualisiert die Artikel lokal aus der `data.json`.
  - *Weites Herunterziehen (ab 180px):* Startet die GitHub Action neu.
- **Wischgesten auf News-Karten (mit visuellem Farbrahmen-Feedback):**
  - *Nach rechts wischen:* Artikel teilen / Link kopieren (blauer Rahmen).
  - *Nach links wischen:* Lesezeichen setzen / merken (gelber Rahmen).
- **Tastatur-Navigation (Desktop):**
  - `j` / `k`: Nächster / Vorheriger Artikel (mit grünem Fokus-Rahmen).
  - `o`: Ausgewählten Artikel im neuen Tab öffnen und als gelesen markieren.
  - `m`: Ausgewählten Artikel als gelesen / ungelesen umschalten.
  - `b`: Lesezeichen für ausgewählten Artikel umschalten.
  - `u`: Filter „Nur ungesehene Artikel“ (`👁️` mit Live-Zähler) ein-/ausschalten.
  - `r`: GitHub Workflow neu anstoßen.
  - `[`: Seitenleiste ein-/ausklappen.
  - `/`: Suchfeld fokussieren.
  - `Esc`: Fokus lösen, Modals und Toasts schließen.

---

## 📂 Dateistruktur

- `.github/workflows/deploy.yml`: GitHub Actions Workflow (Cron & Dispatch)
- `build_pages.py`: Hauptskript (Fetching, Deduplizierung, Gemini, HTML/SW-Generator)
- `feeds.opml`: Lokale Feedliste (Fallback zu `FEEDS_OPML`)
- `requirements.txt`: Python-Abhängigkeiten
- `public/`: Generierte Artefakte für GitHub Pages (`index.html`, `archive.html`, `data.json`, `cache_meta.json`, `sw.js`, `manifest.json`)

---

## 🔒 Datenschutz & Sicherheit

Wird ein `PAGE_PASSWORD` vergeben, liegt auf GitHub Pages zu keinem Zeitpunkt lesbarer Klartext deiner News-Feeds. Die Entschlüsselung erfolgt rein clientseitig im Browser mittels `CryptoJS.AES`.
