# ⚡ News-Hub

Ein leichtgewichtiger, selbst gehosteter RSS-News-Aggregator mit automatischer Deduplizierung, Gemini-gestützter Zusammenfassung/Übersetzung, AES-Verschlüsselung, **gerätedynamischem Lese-Sync** und einem **integrierten Offline-Lesemodus (Reader Mode)** für Live-Feed und Archiv.

Gehostet via **GitHub Pages**, vollautomatisiert über **GitHub Actions**.

---

## ✨ Features

- **Automatisierte Feed-Verarbeitung & Remote-Cache:** Liest Feeds per OPML ein, synchronisiert `cache_meta.json` (`ETag` / `Last-Modified`) automatisch mit GitHub Pages für schnelle `304 Not Modified`-Abfragen und nutzt adaptive Timeouts für langsame Server.
- **Smartes Deployment:** Schreibt das `deploy`-Flag nur bei echten Änderungen in den Action-Output (erzeugt keine unnötigen Commits, wenn 0 neue Artikel vorliegen).
- **KI-Zusammenfassung & Übersetzung:** Nutzt die Google Gemini API, um fremdsprachige Artikel ins Deutsche zu übersetzen und auf genau einen Satz mit prägnanten Fettungen einzudampfen.
- **Integrierter Offline-Lesemodus (Reader Mode):** Extrahiert bei neuen Artikeln der letzten 48 Stunden (Live-Feed & Archiv) vollautomatisch im Hintergrund den reinen Volltext (ohne Werbung, Skripte und Cookie-Banner) und speichert ihn in `offline.json`. Ein Klick auf das Buch-Symbol (`📖`) oder das Öffnen im Flugmodus öffnet den Volltext direkt in der App.
- **Live-Online/Offline-Indikator:** Erkennt in Echtzeit, ob eine Internetverbindung besteht, und schaltet die Anzeige in der Kopfleiste automatisch zwischen `📥 Offline` und `⚡ Offline-Modus` um.
- **Native Zurück-Geste (History API):** Eine Zurück-Wischgeste am Smartphone (oder die Zurück-Taste im Browser) schließt geöffnete Modals wie den Lesemodus (`📖`), Duplikate oder die Seitenleiste sauber, sodass du exakt an deiner vorherigen Scroll-Position im Feed bleibst.
- **Geräteübergreifender Echtzeit-Sync (PC ↔ Smartphone):** Synchronisiert gelesene/gesehene Artikel sowie Lesezeichen (`🔖`) im Hintergrund über eine GitHub-Actions-Variable (`NEWSHUB_SYNC`), ohne Git-Müll oder Commit-Historie.
- **Ergonomische Karten-Bedienung:**
  - **Quellname als Teilen-Button:** Ein Klick auf den grünen Feed-Namen (z. B. *Heise*) teilt den Artikel bzw. kopiert den Link (am Desktop mit `📤 Teilen`-Hover-Effekt).
  - **Zeitangabe als Bulk-Action („Ab hier abwärts gelesen“):** Ein Klick auf `vor Xh` zeigt den exakten Zeitstempel im Toast und markiert diesen sowie alle darunterliegenden (älteren) Artikel der aktuellen Ansicht in einem Rutsch als gelesen.
  - **Optimierte Touch-Targets:** Großzügige Klickflächen für Lesezeichen (`🔖` links) und Lesemodus (`📖` rechts hinter der Zeitangabe).
- **Smarte Bildextraktion (`srcset`) & Hotlink-Schutz:** Wählt aus `srcset`-Attributen automatisch die optimale Auflösung (500–1200px), filtert Tracker, Logos und „Related Posts“-Bilder aus und prüft Bild-URLs über Gemini.
- **Präzise Cross-Language Deduplizierung:** Verhindert Fehlverschmelzungen bei Versions-/Modellnummern (z. B. *iPhone 16* vs. *Galaxy 16GB*) durch mehrsprachigen Keyword-Overlap und `SequenceMatcher`.
- **Klickbare Zweitquellen:** Unter zusammengeführten Artikeln (`• Auch bei: ...`) sind alle weiteren Quellen direkt mit ihren jeweiligen Original-Artikeln verlinkt.
- **Sidebar mit Ungelesen-Zählern & Live-Filter:**
  - **Sidebar-Badges:** Zeigt pro Feed sowohl die Anzahl ungesehener Artikel (grünes Badge) als auch die Gesamtzahl an.
  - **Ungelesen-Filter (`👁️`):** Blendet bereits gesehene/gelesene Artikel komplett aus (inkl. Live-Zähler).
  - **Sortier-Umschalter (`⇅`):** Sortiert auf Wunsch ungesehene Artikel nach ganz oben und blendet eine visuelle Trennlinie ein.
  - **Volltextsuche mit Highlighting:** Hebt Suchtreffer farbig hervor (`<mark>`) und bietet einen Schnell-Löschen-Button (`×`).
- **Auto-Refresh & Zweistufiges Pull-to-Refresh (Mobile):**
  - **Auto-Reload:** Aktualisiert die Feeds automatisch im Hintergrund, wenn die App nach > 15 Minuten wieder geöffnet wird.
  - **Kurzer Zug (25px–179px):** Lädt lautlos die lokale `data.json` nach (`↓ Lokale Feeds laden`).
  - **Tiefer Zug (ab 180px):** Schaltet mit haptischem Feedback (Vibration) um und triggert den entfernten GitHub Actions Workflow (`🚀 Workflow starten`).
- **Ende-zu-Ende-Verschlüsselung:** Verschlüsselt `data.json` und `offline.json` via AES-CBC (OpenSSL/CryptoJS-kompatibel), falls ein Seitenpasswort vergeben ist.
- **Progressive Web App (PWA):** Offline-Fallback dank Service Worker (`news-hub-v28`) mit Network-First-Strategie.
- **Stundengenaue Zeitangaben & Archiv:** Zeigt das Alter von Artikeln ab 60 Minuten durchgehend in Stunden an (`vor Xh`) und trennt automatisch zwischen Live-Feed (letzte 24h) und Archiv (24–48h) – bei voller Feature-Parität.

---

## 🚀 Setup & Konfiguration

### 1. Repository Secrets & Variablen

Lege unter **Settings > Secrets and variables > Actions** folgende Einträge an:

| Secret / Variable | Erforderlich | Beschreibung |
|---|---|---|
| `GEMINI_API_KEY` | Ja | Google AI Studio API-Key für Zusammenfassungen und Übersetzungen (kostenlose Stufe absolut ausreichend). |
| `FEEDS_OPML` | Nein | Rohinhalt deiner OPML-Datei als String (Fallback: `feeds.opml` im Repo-Root). |
| `PAGE_PASSWORD` | Nein | Passwort zur AES-Verschlüsselung der `data.json` / `offline.json` (leer lassen für Plaintext). |
| `FEED_PRIORITIES` | Nein | JSON-Objekt zur Priorisierung bestimmter Feeds, z. B. `{"Heise": 2, "Golem": 2}`. |
| `DEDUP_RATIO` | Nein | Schwellenwert für String-Ähnlichkeit (Default: `0.82`). |
| `DEDUP_OVERLAP` | Nein | Schwellenwert für Keyword-Overlap (Default: `0.72`). |
| `FORCE_REFRESH` | Nein | Auf `true` setzen, um den Cache einmalig komplett neu aufzubauen. |

### 2. GitHub Personal Access Token (PAT) für Sync & Dispatches

Um den Geräte-Sync zu aktivieren und Workflows aus der Web-App heraus zu starten:
1. Erstelle unter **GitHub > Settings > Developer Settings > Personal Access Tokens (classic)** ein Token mit Scope `repo` (beinhaltet Zugriff auf Variablen und Actions).
2. Klicke in der Web-App auf den `🔄`- oder `☁️ Sync`-Button und füge das Token ein. Es wird ausschließlich lokal im `localStorage` deines Browsers gespeichert.

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
- **Offline-Modus Vorwärmen (`📥 Offline`):** Ein Klick lädt alle Volltexte und Vorschaubilder der letzten 48h in den Browser-Cache. Im Flugmodus oder Offline-Zustand öffnen sich Artikel automatisch im Reader Mode (`📖`).
- **Aktionen in der Artikel-Kopfzeile:**
  - `🔖`: Lesezeichen setzen / entfernen.
  - **Grüner Quellname** (z. B. *Heise*): Artikel teilen / Link in die Zwischenablage kopieren.
  - **Status-Punkt** (`🟢` / `⚪`): Gelesen-Status umschalten.
  - **Zeitangabe** (`vor Xh`): Exakte Uhrzeit anzeigen und alle älteren Artikel ab dieser Position abwärts als gelesen markieren.
  - `📖`: Werbefreien Offline-Lesemodus (Reader Mode) öffnen (inkl. direktem Teilen-Button im Lesemodus).
- **Pull-to-Refresh (Mobile):**
  - *Leichtes Herunterziehen (25–179px):* Aktualisiert die Artikel lokal aus der `data.json`.
  - *Weites Herunterziehen (ab 180px):* Startet die GitHub Action neu.
- **Wischgesten auf News-Karten (mit visuellem Farbrahmen-Feedback):**
  - *Nach rechts wischen:* Artikel teilen / Link kopieren (blauer Rahmen).
  - *Nach links wischen:* Lesezeichen setzen / merken (gelber Rahmen).
- **Tastatur-Navigation (Desktop):**
  - `j` / `k`: Nächster / Vorheriger Artikel (mit grünem Fokus-Rahmen).
  - `o`: Ausgewählten Artikel im neuen Tab öffnen und als gelesen markieren.
  - `v`: Offline-Lesemodus (Reader Mode) für den ausgewählten Artikel öffnen.
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
- `build_pages.py`: Hauptskript (Fetching, Deduplizierung, Gemini, Reader-Extractor, HTML/SW-Generator)
- `feeds.opml`: Lokale Feedliste (Fallback zu `FEEDS_OPML`)
- `requirements.txt`: Python-Abhängigkeiten
- `public/`: Generierte Artefakte für GitHub Pages (`index.html`, `archive.html`, `data.json`, `offline.json`, `cache_meta.json`, `sw.js`, `manifest.json`)

---

## 🔒 Datenschutz & Sicherheit

Wird ein `PAGE_PASSWORD` vergeben, liegt auf GitHub Pages zu keinem Zeitpunkt lesbarer Klartext deiner News-Feeds. Die Entschlüsselung erfolgt rein clientseitig im Browser mittels `CryptoJS.AES`.
