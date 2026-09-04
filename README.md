# ⚡ News-Hub

Ein serverloser, hochperformanter News-Aggregator und Dashboard-Generator für RSS/Atom-Feeds. 

Das System läuft vollständig automatisiert über **GitHub Actions**, fasst neue Artikel mithilfe der **Google Gemini API** prägnant auf Deutsch zusammen, bereinigt Duplikate quellenübergreifend mit einstellbarer Sensitivität und stellt ein statisches, optional **AES-verschlüsseltes Frontend** via **GitHub Pages** bereit.

---

## ✨ Features

- **Automatisierte Aggregation:** Ruft konfigurierte RSS/Atom-Feeds regelmäßig parallel und thread-safe ab (`HTTP 304 ETag/Last-Modified` Caching minimiert Bandbreite).
- **Kontrollierbare Deduplizierung:** Erkennt identische Berichte aus unterschiedlichen Medien via Wortstamm-, Zahlen- und Ähnlichkeitsabgleich. Schwellenwerte (`DEDUP_OVERLAP`, `DEDUP_RATIO`) sind flexibel anpassbar.
- **Dubletten-Inspektionsmodus:** Transparente Aufschlüsselung im UI-Modal. Ein Klick auf die Dubletten-Badges klappt alle verworfenen Originalartikel mit Direktlink und Zielzuordnung auf (inkl. Fallback-Anzeige für Alt-Bestände).
- **KI-Zusammenfassung & Titel-Optimierung (Gemini API):**
  - Sachliche, präzise deutsche Schlagzeilen (entfernt Clickbait, übersetzt fremdsprachige Titel sinngemäß).
  - Genau 1 prägnanter deutscher Satz mit **fett** hervorgehobenen Schlüsselbegriffen.
  - Bilderkennung filtert Tracker, Logos und Badges zuverlässig heraus.
- **2-Stufen-Zeitfenster:**
  - **Live-Feed (`index.html`):** Fokus auf topaktuelle Meldungen der letzten 24 Stunden (`jetzt >= Zeit >= vor 24h`).
  - **Archiv (`archive.html`):** Nahtloses Archiv für das vorangegangene Zeitfenster von vor 24 bis 48 Stunden.
- **End-to-End-Verschlüsselung (Zero-Knowledge):** `data.json` wird bei gesetztem Passwort serverseitig via AES-CBC verschlüsselt. Die Entschlüsselung erfolgt clientseitig im Browser mittels CryptoJS.
- **Modernes Dashboard-UI:**
  - Vollständig responsiv (Sidebar Drawer für Mobilgeräte).
  - Quellenspezifischer Filter mit Live-Dubletten-Zähler.
  - Diagnose-Modals für Feed-Status und zusammengeführte Artikel.
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
```

---

## 🛠️ Dateibeschreibungen

### `build_pages.py`
Das zentrale Python-Skript orchestriert den Aggregations- und Build-Prozess:
* **Feed-Aufbereitung:** Liest OPML-Strukturen dynamisch aus GitHub Secrets (`FEEDS_OPML`) oder einer lokalen `feeds.opml` ein.
* **Abruf & Delta-Erkennung:** Gleicht Feeds mit dem bestehenden Datenbestand ab (`cache_meta.json`). Nur echte Unikate werden an Gemini übergeben.
* **Parallele KI-Verarbeitung:** Chunking und parallele API-Aufrufe (`ThreadPoolExecutor`) beschleunigen die Generierung von Titeln und Zusammenfassungen.
* **Audit-Tracking & Vererbung:** Speichert bei Duplikaten die Details (`merged_details` mit Quelltitel, Link und Zuordnung) und vererbt sie auch über mehrstufige Konsolidierungsläufe hinweg.
* **Verschlüsselung & Export:** Serialisiert `data.json` und generiert statische Seiten (`index.html` und `archive.html`).
* **Change-Detection:** Meldet über `$GITHUB_OUTPUT`, ob inhaltliche Änderungen vorliegen, um redundante Deployments zu verhindern.

### `.github/workflows/deploy.yml`
Die CI/CD-Pipeline:
* **Trigger:** Läuft alle 30 Minuten per Cron, manuell via `workflow_dispatch` oder bei Code-Pushes auf den `main`-Branch.
* **Effizienz:** Nutzt `actions/setup-python` mit Pip-Caching für Installationszeiten unter 3 Sekunden.
* **Bereitstellung:** Schiebt generierte Artefakte aus `public/` isoliert auf den Branch `gh-pages`.

### `requirements.txt`
Minimale Abhängigkeiten für Feed-Parsing, Krypto, HTTP-Anfragen und das Gemini SDK:
* `feedparser`
* `google-genai`
* `pydantic`
* `requests`
* `cryptography`

---

## ⚙️ Einrichtung & Konfiguration

Die Konfiguration erfolgt über **Settings → Secrets and variables → Actions**:

### Secrets (Sensible Daten)
| Secret | Beschreibung | Erforderlich |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API-Key für Zusammenfassungen und Titel-Generierung. | Ja |
| `FEEDS_OPML` | OPML-XML-Inhalt mit den einzubindenden Feed-Definitionen (`xmlUrl`, `text`/`title`). | Ja |
| `PAGE_PASSWORD` | Beliebiges Passwort zur AES-Verschlüsselung von `data.json`. Bleibt es leer, wird unverschlüsseltes JSON ausgeliefert. | Nein |
| `FEED_PRIORITIES` | Optionales JSON-Mapping von Feed-Namen auf Prioritäten (z. B. `{"Heise": 5}`). | Nein |

### Variables (Optionales Deduplizierungs-Feintuning)
Steuerung der Ähnlichkeits-Grenzwerte ohne Code-Änderung:

| Variable | Standard | Beschreibung |
| :--- | :---: | :--- |
| `DEDUP_OVERLAP` | `0.50` | Benötigte Keyword-Überschneidung des kürzeren Titels (z. B. `0.60` für strengere Prüfung / weniger Zusammenfassungen). |
| `DEDUP_RATIO` | `0.72` | Mindestwert der Zeichenketten-Ähnlichkeit via SequenceMatcher (z. B. `0.78` für nahezu identische Wortlaute). |

---

## 🚀 GitHub Pages Setup

1. Navigiere zu **Settings → Pages** deines GitHub-Repositories.
2. Wähle unter **Build and deployment** als Quelle: `Deploy from a branch`.
3. Wähle den Branch `gh-pages` mit dem Ordner `/ (root)`.
4. Nach dem ersten erfolgreichen Durchlauf des Actions-Workflows ist das Dashboard live erreichbar.

---

## 🔒 Datenschutz & Sicherheit

* Das Repository enthält im Quellcode weder Feed-URLs noch persönliche Daten oder API-Keys.
* Die öffentliche `data.json` enthält bei gesetztem `PAGE_PASSWORD` ausschließlich verschlüsselte Chiffrate.
* Eine automatisch erzeugte `robots.txt` untersagt Suchmaschinen-Crawlern die Indexierung.
