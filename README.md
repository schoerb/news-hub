# ⚡ News-Hub

Ein leichtgewichtiger, selbst gehosteter RSS-News-Aggregator mit automatischer Deduplizierung, Gemini-gestützter Zusammenfassung/Übersetzung, Zlib-Komprimierung, AES-Verschlüsselung, **geräteübergreifendem Lese- & Filter-Sync** und einem **integrierten Offline-Lesemodus (Reader Mode)** für Live-Feed und Archiv.

Gehostet via **GitHub Pages**, vollautomatisiert über **GitHub Actions**.

---

## ✨ Features

- **Automatisierte Feed-Verarbeitung & Remote-Cache:** Liest Feeds per OPML ein, synchronisiert `cache_meta.json` (`ETag` / `Last-Modified`) automatisch mit GitHub Pages für schnelle `304 Not Modified`-Abfragen und nutzt adaptive Timeouts für langsame Server.
- **Smartes Deployment (`should_deploy`):** Führt das GitHub-Pages-Deployment nur aus, wenn tatsächlich neue Artikel, neue Zweitquellen/Duplikat-Treffer oder ein erzwungenes Refresh (`FORCE_REFRESH`) vorliegen.
- **Zlib/Deflate-Komprimierung + Ende-zu-Ende-Verschlüsselung:** Komprimiert `data.json` und `offline.json` vor der AES-CBC-Verschlüsselung via `zlib` (`GZ1:`-Header, ca. 75 % kleinere Dateien) und entpackt sie im Browser nativ per `DecompressionStream`.
- **KI-Zusammenfassung & Übersetzung:** Nutzt die Google Gemini API, um fremdsprachige Artikel ins Deutsche zu übersetzen und auf genau einen Satz mit prägnanten Fettungen einzudampfen.
- **Kombinierter `+/-` Mehrfach-Filter in der Kopfleiste:**
  - Unterstützt kommagetrennte Such- und Ausschlussbegriffe gleichzeitig in einem einzigen Feld (z. B. `linux, docker, -apple, -werbung`).
  - Begriffe ohne eigenes Vorzeichen übernehmen automatisch den Standard-Modus des Toggle-Buttons (`+` = Enthält / `-` = Enthält nicht) links neben dem Eingabefeld.
  - Hebt alle positiven Suchtreffer farbig hervor (`<mark>`).
- **Cloud-Sync für Lese-Status & Suchfilter (PC ↔ Smartphone):** Synchronisiert gelesene/gesehene Artikel, Lesezeichen (`🔖`) sowie deinen aktiven Suchfilter-String und `+`/`-`-Modus im Hintergrund über die GitHub-Actions-Variable `NEWSHUB_SYNC`.
- **Integrierter Offline-Lesemodus (Reader Mode):**
  - Extrahiert für alle Artikel der letzten 48 Stunden (Live-Feed & Archiv) automatisch den werbefreien Volltext und bereinigt geschützte Leerzeichen (`&nbsp;`) für sauberen Zeilenumbruch auf Smartphones.
  - **Grüne Lesefortschrittsanzeige (Progress-Bar)** am oberen Fensterrand und kompakte Toolbar für maximale Lesefläche.
  - **Wischgesten (Links/Rechts)**, Pfeil-Buttons (`‹` / `›`) und Tastatur-Pfeiltasten (`←` / `→`) zum direkten Blättern durch die aktuell gefilterten (oder gemerkten) Artikel.
  - **Direkter Lesezeichen-Button (`🔖`)**, Teilen-Button (`📤`) und 4-stufige Schriftgrößen-Steuerung (`Aa` bzw. `+` / `-`) in der Lesemodus-Toolbar.
- **Präzises Gesehen-Tracking:** Artikel werden erst dann automatisch als gesehen markiert, wenn die gesamte Kachel zu 100 % sichtbar ist und für mindestens 2 Sekunden im Blickfeld verbleibt.
- **Live-Online/Offline-Indikator:** Erkennt in Echtzeit den Verbindungsstatus und schaltet in der Kopfleiste zwischen `📥 Offline` und `⚡ Offline-Modus` um.
- **Native Zurück-Geste (History API):** Eine Zurück-Wischgeste am Smartphone schließt geöffnete Modals (Lesemodus, Duplikate, Feed-Status, Sidebar) sauber, ohne die Scroll-Position im Feed zu verlieren.
- **Ergonomische Karten-Bedienung:**
  - **Quellname als Teilen-Button:** Klick auf den grünen Feed-Namen teilt den Artikel bzw. kopiert den Link (am Desktop mit `📤 Teilen`-Hover-Effekt).
  - **Zeitangabe als Bulk-Action („Ab hier abwärts gelesen“):** Klick auf `vor Xh` zeigt den exakten Zeitstempel und markiert alle älteren Artikel darunter in einem Rutsch als gelesen.
- **Smarte Bildextraktion (`srcset`) & Hotlink-Schutz:** Wählt aus `srcset`-Attributen automatisch die optimale Auflösung (500–1200px) und filtert Tracker, Logos und Platzhalter aus.
- **Sidebar mit Quellen, Schlagwörtern & Live-Zählern:** Zeigt pro Feed und dynamisch erkanntem Tech-Schlagwort sowohl die ungesehenen Artikel (grünes Badge) als auch die Gesamtzahl an.
- **Progressive Web App (PWA):** Offline-Fallback dank Service Worker (`news-hub-v40`) und voller Feature-Parität zwischen Live-Feed (0–24h) und Archiv (24–48h).

---

## 🚀 Setup & Konfiguration

### 1. Repository Secrets & Variablen

Lege unter **Settings > Secrets and variables > Actions** folgende Einträge an:

| Secret / Variable | Erforderlich | Beschreibung |
|---|---|---|
| `GEMINI_API_KEY` | Ja | Google AI Studio API-Key für Zusammenfassungen und Übersetzungen (kostenlose Stufe ausreichend). |
| `FEEDS_OPML` | Nein | Rohinhalt deiner OPML-Datei als String (Fallback: `feeds.opml` im Repo-Root). |
| `PAGE_PASSWORD` | Nein | Passwort zur AES-Verschlüsselung der komprimierten `data.json` / `offline.json`. |
| `FEED_PRIORITIES` | Nein | JSON-Objekt zur Priorisierung bestimmter Feeds, z. B. `{"Heise": 2, "Golem": 2}`. |
| `DEDUP_RATIO` | Nein | Schwellenwert für String-Ähnlichkeit (Default: `0.82`). |
| `DEDUP_OVERLAP` | Nein | Schwellenwert für Keyword-Overlap (Default: `0.72`). |
| `FORCE_REFRESH` | Nein | Auf `true` setzen, um den Cache einmalig komplett neu aufzubauen. |

### 2. GitHub Personal Access Token (PAT) für Sync & Dispatches

1. Erstelle unter **GitHub > Settings > Developer Settings > Personal Access Tokens (classic)** ein Token mit Scope `repo`.
2. Klicke in der Web-App auf `🔄` oder `☁️ Sync` und füge das Token ein (Speicherung rein lokal im `localStorage`).

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

- **Kombi-Filter in der Kopfleiste:**
  - Button `+` (Grün): Begriffe ohne Vorzeichen müssen enthalten sein (`ki, linux` -> zeigt Artikel mit KI oder Linux).
  - Button `-` (Rot): Begriffe ohne Vorzeichen werden ausgeblendet (`apple, fussball` -> blendet beides aus).
  - Gemischte Eingabe jederzeit möglich: `linux, +docker, -ubuntu, -deal`.
- **Wischgesten auf News-Karten:**
  - *Nach rechts wischen:* Artikel direkt im Offline-Lesemodus (`📖`) öffnen (grüner Rahmen).
  - *Nach links wischen:* Lesezeichen setzen / merken (gelber Rahmen).
- **Navigation im geöffneten Lesemodus (`📖`):**
  - *Nach links/rechts wischen* (oder Tasten `←` / `→` / `j` / `k` / Buttons `‹` / `›`): Nächster / Vorheriger Artikel.
  - *Button `🔖`* (oder Taste `b`): Lesezeichen direkt im Lesemodus umschalten.
  - *Button `Aa`* (oder Tasten `+` / `-`): Schriftgröße in 4 Stufen (90% bis 130%) anpassen.
  - *Zurück-Geste am Smartphone* (oder Taste `Esc` / `v`): Lesemodus schließen.
- **Tastatur-Navigation (Desktop):**
  - `j` / `k`: Nächster / Vorheriger Artikel.
  - `o`: Artikel im neuen Tab öffnen und als gelesen markieren.
  - `v`: Offline-Lesemodus (`📖`) öffnen/schließen.
  - `m`: Gelesen-Status umschalten.
  - `b`: Lesezeichen umschalten.
  - `u`: Filter „Nur ungesehene Artikel“ (`👁️`) umschalten.
  - `s`: Sortierung „Ungesehene zuerst“ (`⇅`) umschalten.
  - `r`: GitHub Workflow neu anstoßen.
  - `[`: Seitenleiste ein-/ausklappen.
  - `/`: Filterfeld fokussieren.
