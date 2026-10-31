# ⚡ News-Hub

Ein leichtgewichtiger, selbst gehosteter RSS-News-Aggregator mit automatischer Deduplizierung, Gemini-gestützter Zusammenfassung/Übersetzung, AES-Verschlüsselung, **gerätedynamischem Lese- & Filter-Sync**, **Lesefortschritt** und einem **integrierten Offline-Lesemodus (Reader Mode)** für Live-Feed und Archiv.

Gehostet via **GitHub Pages**, vollautomatisiert über **GitHub Actions**.

---

## ✨ Features

- **Automatisierte Feed-Verarbeitung & Remote-Cache:** Liest Feeds per OPML ein, synchronisiert `cache_meta.json` (`ETag` / `Last-Modified`) automatisch mit GitHub Pages für schnelle `304 Not Modified`-Abfragen und nutzt adaptive Timeouts für langsame Server.
- **KI-Zusammenfassung & Übersetzung:** Nutzt die Google Gemini API, um fremdsprachige Artikel ins Deutsche zu übersetzen und auf genau einen Satz mit prägnanten Fettungen einzudampfen.
- **Integrierter Offline-Lesemodus (Reader Mode) mit Lesefortschritt & Schriftgröße:** Extrahiert bei neuen Artikeln der letzten 48 Stunden (Live-Feed & Archiv) vollautomatisch im Hintergrund den reinen Volltext und speichert ihn in `offline.json`. Im Lesemodus (`📖`) sorgt eine dezente grüne Progress-Bar für den Überblick, per **Wischgeste (Links/Rechts)**, Pfeil-Buttons (`‹` / `›`) oder Pfeiltasten lässt sich nahtlos blättern, und die Schriftgröße lässt sich in 4 Stufen (`Aa` / `+` / `-`) anpassen.
- **Präzises Gesehen-Tracking:** Artikel werden erst dann automatisch als gesehen markiert, wenn die gesamte Kachel zu 100 % sichtbar ist und für mindestens 2 Sekunden im Blickfeld verbleibt.
- **Live-Online/Offline-Indikator:** Erkennt in Echtzeit, ob eine Internetverbindung besteht, und schaltet die Anzeige in der Kopfleiste automatisch zwischen `📥 Offline` und `⚡ Offline-Modus` um.
- **Cloud-Sync für Lese-Status & Filter (PC ↔ Smartphone):** Synchronisiert gelesene/gesehene Artikel, Lesezeichen (`🔖`) sowie aktive **Ausschluss-Filter (`🚫 Ausblenden`)** im Hintergrund über eine GitHub-Actions-Variable (`NEWSHUB_SYNC`).
- **Ergonomische Karten-Bedienung:**
  - **Quellname als Teilen-Button:** Ein Klick auf den grünen Feed-Namen (z. B. *Heise*) teilt den Artikel bzw. kopiert den Link (am Desktop mit `📤 Teilen`-Hover-Effekt).
  - **Zeitangabe als Bulk-Action („Ab hier abwärts gelesen“):** Ein Klick auf `vor Xh` zeigt den exakten Zeitstempel im Toast und markiert diesen sowie alle darunterliegenden (älteren) Artikel in einem Rutsch als gelesen.
  - **Optimierte Touch-Targets:** Großzügige Klickflächen für Lesezeichen (`🔖` links) und Lesemodus (`📖` rechts hinter der Zeitangabe).
- **Smarte Bildextraktion (`srcset`) & Hotlink-Schutz:** Wählt aus `srcset`-Attributen automatisch die optimale Auflösung (500–1200px), filtert Tracker, Logos und „Related Posts“-Bilder aus und prüft Bild-URLs über Gemini.
- **Präzise Cross-Language Deduplizierung:** Verhindert Fehlverschmelzungen bei Versions-/Modellnummern durch mehrsprachigen Keyword-Overlap und `SequenceMatcher`.
- **Klickbare Zweitquellen:** Unter zusammengeführten Artikeln (`• Auch bei: ...`) sind alle weiteren Quellen direkt mit ihren jeweiligen Original-Artikeln verlinkt.
- **Sidebar mit Kategorien, Filtern & Blocklist:**
  - **Schlagwörter & Feeds:** Filtriert Feeds nach dynamisch erkannten Tech-Themen oder einzelnen Quellen.
  - **Ausblenden (Negativ-Filter):** Blendet Themen per Klick in der Sidebar dauerhaft aus (synchronisiert über alle Geräte).
  - **Ungelesen-Filter (`👁️`) & Sortier-Umschalter (`⇅`).**
- **Auto-Refresh & Zweistufiges Pull-to-Refresh (Mobile):**
  - **Auto-Reload:** Aktualisiert die Feeds automatisch im Hintergrund, wenn die App nach > 15 Minuten wieder geöffnet wird.
  - **Kurzer Zug (25px–179px):** Lädt lautlos die lokale `data.json` nach (`↓ Lokale Feeds laden`).
  - **Tiefer Zug (ab 180px):** Triggert den entfernten GitHub Actions Workflow (`🚀 Workflow starten`).
- **Ende-zu-Ende-Verschlüsselung:** Verschlüsselt `data.json` und `offline.json` via AES-CBC, falls ein Seitenpasswort vergeben ist.
- **Progressive Web App (PWA):** Offline-Fallback dank Service Worker (`news-hub-v36`) mit Network-First-Strategie.
- **Stundengenaue Zeitangaben & Archiv:** Trennt automatisch zwischen Live-Feed (letzte 24h) und Archiv (24–48h) – bei voller Feature-Parität.

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
- **Wischgesten auf News-Karten (mit visuellem Farbrahmen-Feedback):**
  - *Nach rechts wischen:* Artikel direkt im Offline-Lesemodus (`📖`) öffnen (grüner Rahmen).
  - *Nach links wischen:* Lesezeichen setzen / merken (gelber Rahmen).
- **Navigation im geöffneten Lesemodus (`📖`):**
  - *Nach links wischen* (oder Taste `→` / `j` / Button `›`): Nächster Artikel (im Lesezeichen-Modus automatisch nur durch gemerkte Artikel).
  - *Nach rechts wischen* (or Taste `←` / `k` / Button `‹`): Vorheriger Artikel.
  - *Button `Aa`* (oder Tasten `+` / `-`): Schriftgröße in 4 Stufen (90% bis 130%) umschalten.
  - *Zurück-Geste am Smartphone* (oder Taste `Esc` / `v`): Lesemodus schließen und an derselben Position im Feed weiterlesen.
