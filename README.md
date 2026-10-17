# ⚡ News-Hub

Ein leichtgewichtiger, selbst gehosteter RSS-News-Aggregator mit automatischer Deduplizierung, Gemini-gestützter Zusammenfassung/Übersetzung, nativer AES-256-GCM-Verschlüsselung und einer für mobile Geräte optimierten Web-App (PWA).

Gehostet via **GitHub Pages**, vollautomatisiert über **GitHub Actions**.

---

## ✨ Features

- **Automatisierte Feed-Verarbeitung & Remote-Cache:** Liest Feeds per OPML ein, synchronisiert `cache_meta.json` (`ETag` / `Last-Modified`) automatisch mit GitHub Pages für schnelle `304 Not Modified`-Abfragen und nutzt adaptive Timeouts für langsame Server.
- **Smartes Deployment:** Setzt das `deploy`-Flag in GitHub Actions nur dann, wenn tatsächlich neue Artikel oder Duplikat-Zuordnungen vorliegen (spart Action-Minuten).
- **KI-Zusammenfassung & Übersetzung:** Nutzt die Google Gemini API, um fremdsprachige Artikel ins Deutsche zu übersetzen und auf genau einen Satz mit prägnanten Fettungen einzudampfen.
- **Smarte Bildextraktion (`srcset`) & Hotlink-Schutz:** Wählt aus `srcset`-Attributen automatisch die optimale Auflösung (500–1200px), filtert Tracker, Logos und „Related Posts“-Bilder aus, prüft Bild-URLs über Gemini und blendet Mini-Grafiken (< 80px) im Browser per `onload` aus.
- **Präzise Cross-Language Deduplizierung:** Verhindert Fehlverschmelzungen bei Versions-/Modellnummern (z. B. *iPhone 16* vs. *Galaxy 16GB*) durch mehrsprachigen Keyword-Overlap und `SequenceMatcher`.
- **Klickbare Zweitquellen:** Unter zusammengeführten Artikeln (`• Auch bei: ...`) sind alle weiteren Quellen direkt mit ihren jeweiligen Original-Artikeln verlinkt.
- **Sidebar mit Ungelesen-Zählern & Live-Filter:**
  - **Sidebar-Badges:** Zeigt pro Feed sowohl die Anzahl ungesehener Artikel (grünes Badge) als auch die Gesamtzahl an.
  - **Ungelesen-Filter (`👁️`):** Blendet bereits gesehene/gelesene Artikel komplett aus (inkl. Live-Zähler).
  - **Sortier-Umschalter (`⇅`):** Sortiert auf Wunsch ungesehene Artikel nach ganz oben und blendet eine visuelle Trennlinie (`✓ Bis hierhin bereits gesehen`) ein.
  - **Volltextsuche mit Highlighting:** Hebt Suchtreffer farbig hervor (`<mark>`) und bietet einen Schnell-Löschen-Button (`×`).
- **Auto-Refresh & Zweistufiges Pull-to-Refresh (Mobile):**
  - **Auto-Reload:** Aktualisiert die Feeds automatisch im Hintergrund, wenn die App nach > 15 Minuten wieder geöffnet wird.
  - **Kurzer Zug (25px–179px):** Lädt lautlos die lokale `data.json` nach (`↓ Lokale Feeds laden`).
  - **Tiefer Zug (ab 180px):** Schaltet mit haptischem Feedback (Vibration) um und triggert den entfernten GitHub Actions Workflow (`🚀 Workflow starten`).
- **100 % Native Ende-zu-Ende-Verschlüsselung (Zero Dependencies):** Verschlüsselt `data.json` mit `AES-256-GCM` und `PBKDF2-SHA256` (100.000 Iterationen). Die Entschlüsselung erfolgt nativ über die `WebCrypto API` des Browsers – ganz ohne externe Script-Abhängigkeiten wie CryptoJS.
- **Progressive Web App (PWA):** Offline-Fallback dank Service Worker (`news-hub-v20`) mit Network-First-Strategie.
- **Stundengenaue Zeitangaben & Archiv:** Zeigt das Alter von Artikeln ab 60 Minuten durchgehend in Stunden an (`vor Xh`) und trennt automatisch zwischen Live-Feed (letzte 24h) und Archiv (24–48h).

---

## 🚀 Setup & Konfiguration

### 1. Repository Secrets & Variablen

Lege unter **Settings > Secrets and variables > Actions** folgende Einträge an:

| Secret / Variable | Erforderlich | Beschreibung |
|---|---|---|
| `GEMINI_API_KEY` | Ja | Google AI Studio API-Key für Zusammenfassungen und Übersetzungen. |
| `FEEDS_OPML` | Nein | Rohinhalt deiner OPML-Datei als String (Fallback: `feeds.opml` im Repo-Root). |
| `PAGE_PASSWORD` | Nein | Passwort zur AES-256-GCM-Verschlüsselung der `data.json` (leer lassen für Plaintext). |
| `FEED_PRIORITIES` | Nein | JSON-Objekt zur Priorisierung bestimmter Feeds, z. B. `{"Heise": 2, "Golem": 2}`. |
| `DEDUP_RATIO` | Nein | Schwellenwert für String-Ähnlichkeit (Default: `0.82`). |
| `DEDUP_OVERLAP` | Nein | Schwellenwert für Keyword-Overlap (Default: `0.72`). |
| `FORCE_REFRESH` | Nein | Auf `true` setzen, um den Cache einmalig komplett neu aufzubauen. |

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
4. Lokalen Webserver starten: `cd public && python3 -m http.server 8000`

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
  - `u`: Filter „Nur ungesehene Artikel“ (`👁️`) ein-/ausschalten.
  - `s`: Sortierung „Ungesehene zuerst“ (`⇅`) umschalten.
  - `r`: GitHub Workflow neu anstoßen.
  - `[`: Seitenleiste ein-/ausklappen.
  - `/`: Suchfeld fokussieren.
  - `Esc`: Suchfeld leeren, Fokus lösen, Modals und Toasts schließen.

---

## 📂 Dateistruktur

- `.github/workflows/deploy.yml`: GitHub Actions Workflow (Cron & Dispatch)
- `build_pages.py`: Hauptskript (Fetching, Deduplizierung, Gemini, AES-GCM, HTML/SW-Generator)
- `feeds.opml`: Lokale Feedliste (Fallback zu `FEEDS_OPML`)
- `requirements.txt`: Python-Abhängigkeiten
- `public/`: Generierte Artefakte für GitHub Pages (`index.html`, `archive.html`, `data.json`, `cache_meta.json`, `sw.js`, `manifest.json`)

---

## 🔒 Datenschutz & Sicherheit

Wird ein `PAGE_PASSWORD` vergeben, liegt auf GitHub Pages zu keinem Zeitpunkt lesbarer Klartext deiner News-Feeds. Die Entschlüsselung erfolgt rein clientseitig im Browser über `window.crypto.subtle` (`AES-256-GCM`).
