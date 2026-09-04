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
