# ⚡ News-Hub

Ein serverloser, hochperformanter News-Aggregator und Dashboard-Generator für RSS/Atom-Feeds. 

Das System läuft vollständig automatisiert über **GitHub Actions**, fasst neue Artikel mithilfe der **Google Gemini API** prägnant auf Deutsch zusammen, bereinigt Duplikate quellenübergreifend und stellt ein statisches, optional **AES-verschlüsseltes Frontend** via **GitHub Pages** bereit.

---

## ✨ Features

- **Automatisierte Aggregation:** Ruft konfigurierte RSS/Atom-Feeds regelmäßig parallel und thread-safe ab (`HTTP 304 ETag/Last-Modified` Caching minimiert Bandbreite).
- **Intelligente Bereinigung & Deduplizierung:** Erkennt identische Berichte aus unterschiedlichen Medien via Wortstamm- und Ähnlichkeitsabgleich. Quellen werden gebündelt angezeigt (*„Auch bei: …“*).
- **KI-Zusammenfassung (Gemini API):** Generiert genau einen prägnanten Satz pro Meldung, hebt Schlüsselbegriffe hervor, filtert dekorative Tracker/Badges aus und übersetzt fremdsprachige Titel sachlich ins Deutsche.
- **2-Stufen-Archivierung:**
  - **Live-Feed (`index.html`):** Fokus auf topaktuelle Nachrichten der letzten 24 Stunden.
  - **Archiv (`archive.html`):** Nahtlose Übergabe für Meldungen des Zeitfensters von vor 24 bis 48 Stunden.
- **End-to-End-Verschlüsselung (Zero-Knowledge):** Die generierte Datendatei `data.json` wird bei gesetztem Passwort serverseitig via AES-CBC (OpenSSL-kompatibles Key Derivation Format) verschlüsselt. Die Entschlüsselung erfolgt clientseitig im Browser mittels Web Crypto / CryptoJS.
- **Modernes Dashboard-UI:**
  - Vollständig responsiv (Sidebar Drawer für Smartphones/Tablets).
  - Quellenspezifischer Filter mit Live-Dubletten-Zähler.
  - Diagnose-Modals für Feed-Status und Dubletten-Statistiken.
  - Tastaturnavigation (`J`/`K` Navigation, `O`/`Enter` Öffnen, `M` Gelesen-Status, `/` Suche, `[` Sidebar).
  - Manuelle Sofort-Aktualisierung über GitHub Actions Workflow Dispatch direkt aus dem UI.

---

## 📁 Repository-Struktur

```text
.
├── .github/
│   └── workflows/
│       └── deploy.yml       # Automatisierungs-Workflow für CI/CD & GitHub Pages
├── build_pages.py           # Kernskript: Fetching, KI-Pipeline, Krypto & Rendering
├── requirements.txt         # Python-Abhängigkeiten mit Pip-Cache-Unterstützung
└── README.md

## 🛠️ Dateibeschreibungen

### `build_pages.py`
Das zentrale Python-Skript orchestriert den gesamten Aggregations- und Build-Prozess:
* **Feed-Aufbereitung:** Liest OPML-Strukturen dynamisch aus verschlüsselten GitHub Secrets (`FEEDS_OPML`) oder einer lokalen `feeds.opml` ein.
* **Abruf & Delta-Erkennung:** Gleicht eingehende Feeds mit dem bisherigen Datenbestand (`data.json` und `cache_meta.json`) ab. Nur echte Unikate werden an die Gemini API übergeben.
* **Parallele KI-Verarbeitung:** Batching und parallele Abfragen (`ThreadPoolExecutor`) sorgen für minimale Laufzeiten und schonen Token-Limits.
* **Verschlüsselung & Export:** Kompakte Serialisierung der Daten (`public/data.json`) sowie Kompilierung der statischen HTML-Views (`index.html` und `archive.html`).
* **Change-Detection:** Gibt über `$GITHUB_OUTPUT` zurück, ob sich der Datenbestand verändert hat, um unnötige Pages-Deployments zu vermeiden.

### `.github/workflows/deploy.yml`
Die CI/CD-Pipeline zur Automatisierung:
* **Trigger:** Läuft intervallbasiert via Cron (alle 30 Minuten), manuell via `workflow_dispatch` oder bei Code-Pushes auf den `main`-Branch.
* **Effizienz:** Nutzt Runner-Pip-Caching für sekundenschnelle Installation der Abhängigkeiten.
* **Bereitstellung:** Schiebt die generierten Artefakte aus dem Verzeichnis `public/` isoliert auf den Branch `gh-pages`.

### `requirements.txt`
Enthält die minimal notwendigen Python-Pakete für Feed-Parsing, Krypto-Funktionen, HTTP-Anfragen und das Gemini SDK:
* `feedparser`
* `google-genai`
* `pydantic`
* `requests`
* `cryptography`

---

## ⚙️ Einrichtung & Konfiguration

Alle Zugangsdaten und Konfigurationen werden sicher über **GitHub Actions Secrets** verwaltet:

| Secret | Beschreibung | Erforderlich |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API-Key für Zusammenfassungen und Titel-Generierung. | Ja |
| `FEEDS_OPML` | OPML-XML-Inhalt mit den einzubindenden Feed-Definitionen (`xmlUrl`, `text`/`title`). | Ja |
| `PAGE_PASSWORD` | Beliebiges Passwort zur AES-Verschlüsselung von `data.json`. Bleibt es leer, wird unverschlüsseltes JSON ausgeliefert. | Nein |
| `FEED_PRIORITIES` | Optionales JSON-Mapping von Feed-Namen auf Prioritäten (z. B. `{"Heise": 5}`). | Nein |

### GitHub Pages Setup
1. Navigiere zu **Settings → Pages** deines GitHub-Repositories.
2. Wähle als Quelle (**Build and deployment**): `Deploy from a branch`.
3. Wähle den Branch `gh-pages` mit dem Ordner `/ (root)`.
4. Nach dem ersten erfolgreichen Durchlauf des Actions-Workflows ist das Dashboard live erreichbar.

---

## 🔒 Datenschutz & Sicherheit
* Das Repository speichert im Code weder Feed-URLs noch persönliche Daten oder API-Keys.
* Die öffentliche `data.json` auf GitHub Pages enthält bei aktiviertem `PAGE_PASSWORD` ausschließlich verschlüsselte Datenblöcke.
* Eine automatische `robots.txt` untersagt Suchmaschinen-Crawlern die Indexierung.
