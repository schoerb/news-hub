# ⚡ News-Hub

Ein leichtgewichtiger, selbst gehosteter RSS-News-Aggregator mit automatischer Deduplizierung, Gemini-gestützter Zusammenfassung/Übersetzung, AES-Verschlüsselung und einer für mobile Geräte optimierten Web-App (PWA).

Gehostet via **GitHub Pages**, vollautomatisiert über **GitHub Actions**.

---

## ✨ Features

- **Automatisierte Feed-Verarbeitung:** Liest Feeds per OPML ein, nutzt HTTP-Caching (ETag / Last-Modified) für schnelle Abfragen und minimale Bandbreite.
- **KI-Zusammenfassung & Übersetzung:** Nutzt die Google Gemini API, um fremdsprachige Artikel ins Deutsche zu übersetzen und auf genau einen Satz mit prägnanten Fettungen einzudampfen.
- **Smarte Bildextraktion:** Erkennt Bilder aus media:content, Enclosures sowie modernen HTML-Tags (data-src, data-original, WordPress/CDN-Bilder) und übernimmt Bilder bei Duplikaten automatisch von Zweitquellen.
- **Robuste Deduplizierung:** Verhindert Fehlverschmelzungen bei Versions-/Modellnummern (z. B. iPhone 16 vs. Galaxy 16GB) durch Keyword-Overlap und SequenceMatcher. Verhindert das Aufblähen von Duplikat-Listen über mehrere Runs hinweg.
- **Zweistufiges Pull-to-Refresh (Mobile):**
  - **Kurzer Zug (25px–179px):** Lädt lautlos die lokale data.json nach.
  - **Tiefer Zug (ab 180px):** Schaltet mit haptischem Feedback (Vibration) um und triggert den entfernten GitHub Actions Workflow.
  - Blockiert das native Chrome/Android-Pull-to-Refresh verlässlich via overscroll-behavior-y: none.
- **Workflow-Trigger & Live-Polling:** Button in der Navigation startet direkt den GitHub Actions Dispatch und pollt den Build-Status mit direkter Verlinkung zu den Action-Logs.
- **Ende-zu-Ende-Verschlüsselung:** Verschlüsselt die generierte data.json via AES-CBC (OpenSSL-kompatibel), falls ein Seitenpasswort vergeben ist.
- **Progressive Web App (PWA):** Offline-Fallback dank Service Worker mit Network-First-Strategie für Daten und HTML.
- **Archiv-Ansicht:** Automatische Trennung zwischen aktuellem Live-Feed (letzte 24h) und Archiv (24–48h).

---

## 🚀 Setup & Konfiguration

### 1. Repository Secrets & Variablen

Lege unter Settings > Secrets and variables > Actions folgende Einträge an:

| Secret / Variable | Erforderlich | Beschreibung |
|---|---|---|
| GEMINI_API_KEY | Ja | Google AI Studio API-Key für Zusammenfassungen und Übersetzungen. |
| FEEDS_OPML | Nein | Rohinhalt deiner OPML-Datei als String (Fallback: feeds.opml im Repo-Root). |
| PAGE_PASSWORD | Nein | Passwort zur AES-Verschlüsselung der data.json (leer lassen für Plaintext). |
| FEED_PRIORITIES | Nein | JSON-Objekt zur Priorisierung bestimmter Feeds, z. B. {"Heise": 2, "Golem": 2}. |
| DEDUP_RATIO | Nein | Schwellenwert für String-Ähnlichkeit (Default: 0.82). |
| DEDUP_OVERLAP | Nein | Schwellenwert für Keyword-Overlap (Default: 0.72). |

### 2. GitHub Personal Access Token (PAT) für Dispatches

Um den Workflow direkt aus der Web-App heraus neu zu starten:
1. Erstelle unter GitHub > Settings > Developer Settings > Personal Access Tokens (classic) ein Token mit Scope repo (oder workflow).
2. Beim ersten Klick auf den Aktualisieren-Button in der Web-App wirst du nach dem Token gefragt. Es wird ausschließlich lokal im localStorage deines Browsers hinterlegt.

---

## 🛠️ Lokale Ausführung

1. Abhaengigkeiten installieren: pip install -r requirements.txt
2. Umgebungsvariablen setzen:
   export GEMINI_API_KEY="dein-api-key"
   export PAGE_PASSWORD="optionales-passwort"
3. Build ausfuehren: python build_pages.py
4. Lokalen Webserver starten: cd public && python -m http.server 8000

Anschließend im Browser http://localhost:8000 aufrufen.

---

## 📱 Bedienung & Gesten

- Pull-to-Refresh:
  - Leichtes Herunterziehen: Aktualisiert die Artikel lokal aus der bereitgestellten data.json.
  - Weites Herunterziehen (Richtung Displaymitte): Startet die GitHub Action neu.
- Wischgesten auf News-Karten:
  - Nach rechts wischen: Artikel teilen / Link kopieren.
  - Nach links wischen: Lesezeichen setzen / merken.
- Tastatur-Navigation (Desktop):
  - j / k: Vorheriger / Nächster Artikel.
  - o: Artikel im neuen Tab öffnen.
  - m: Als gelesen / ungelesen markieren.
  - b: Lesezeichen umschalten.
  - r: GitHub Workflow neu anstoßen.
  - [: Seitenleiste ein-/ausklappen.
  - /: Suchfeld fokussieren.
  - Esc: Modals und Toasts schließen.

---

## 📂 Dateistruktur

- .github/workflows/deploy.yml: GitHub Actions Definition
- build_pages.py: Hauptskript (Fetching, Deduplizierung, Gemini, Render)
- feeds.opml: Lokale Feedliste
- requirements.txt: Python-Abhängigkeiten
- public/: Build-Artefakte für GitHub Pages (index.html, archive.html, data.json, sw.js, manifest.json)

---

## 🔒 Datenschutz & Sicherheit

Wird ein PAGE_PASSWORD vergeben, liegt auf GitHub Pages zu keinem Zeitpunkt lesbarer Klartext deiner News-Feeds. Die Entschlüsselung erfolgt rein clientseitig im Browser mittels CryptoJS.AES.
