import csv
import json
import mimetypes
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import yaml

from config import SETTINGS

STATUS_PATH = Path('/data/logs/status.json')
LAST_RUN_PATH = Path('/data/logs/last_run.json')
OVERRIDES_PATH = Path('/data/logs/review_overrides.json')
TAXONOMY_PATH = Path('/config/taxonomy.yml')
PROVIDER_SOURCES_PATH = Path('/config/provider_sources.yml')
REVIEW_EXPORT_PATH = Path('/data/exports/steuer_2025_export_reviewed.csv')
FILE_ROOTS = {
    'manual-drop': Path('/manual-drop'),
    'portal-screenshots': Path('/data/portal/screenshots'),
    'portal-downloads': Path('/data/portal/downloads'),
    'processed': Path('/data/processed'),
    'exports': Path('/data/exports'),
}


def load_status() -> dict:
    if not STATUS_PATH.exists():
        return {
            'generated_at_utc': None,
            'mode': 'unknown',
            'counts': {},
            'last_10_documents': [],
            'open_inbox_files': [],
            'notes': {'info': 'No run yet. Execute pipeline first.'},
        }
    try:
        return json.loads(STATUS_PATH.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'error': str(exc)}


def load_last_run() -> list[dict]:
    if not LAST_RUN_PATH.exists():
        return []
    try:
        return json.loads(LAST_RUN_PATH.read_text(encoding='utf-8'))
    except Exception:
        return []


def load_overrides() -> dict:
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(OVERRIDES_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_overrides(overrides: dict) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_PATH.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding='utf-8')


def taxonomy_categories() -> list[str]:
    if not TAXONOMY_PATH.exists():
        return []
    try:
        data = yaml.safe_load(TAXONOMY_PATH.read_text(encoding='utf-8')) or {}
    except Exception:
        return []
    categories = data.get('categories', {}) if isinstance(data, dict) else {}
    if not isinstance(categories, dict):
        return []
    return sorted(categories.keys())


def provider_sources() -> list[dict]:
    if not PROVIDER_SOURCES_PATH.exists():
        return []
    try:
        data = yaml.safe_load(PROVIDER_SOURCES_PATH.read_text(encoding='utf-8')) or {}
    except Exception:
        return []
    rows = data.get('providers', []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
    return out


def apply_overrides(rows: list[dict], overrides: dict) -> list[dict]:
    out = []
    for row in rows:
        merged = dict(row)
        patch = overrides.get(str(row.get('datei', '')), {})
        if isinstance(patch, dict):
            for key, value in patch.items():
                merged[key] = value
        out.append(merged)
    return out


def write_reviewed_export(rows: list[dict]) -> None:
    REVIEW_EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        REVIEW_EXPORT_PATH.write_text('', encoding='utf-8')
        return
    fieldnames = list(rows[0].keys())
    with REVIEW_EXPORT_PATH.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def latest_files(root: Path, limit: int = 30):
    if not root.exists():
        return []
    files = [p for p in root.rglob('*') if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def rel_path(root: Path, file_path: Path) -> str:
    return str(file_path.resolve().relative_to(root.resolve())).replace('\\', '/')


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, code: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html: str, code: int = 200) -> None:
        data = html.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_html('<h1>404</h1>', 404)
            return
        mime, _ = mimetypes.guess_type(path.name)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime or 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get('Content-Length', '0') or '0')
        raw = self.rfile.read(length) if length > 0 else b'{}'
        try:
            payload = json.loads(raw.decode('utf-8'))
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _build_file_list(self, root_key: str, title: str, limit: int = 20) -> str:
        root = FILE_ROOTS[root_key]
        items = latest_files(root, limit=limit)
        if not items:
            return f"<h3>{escape(title)}</h3><p>Keine Dateien</p>"

        rows = []
        for file_path in items:
            rel = rel_path(root, file_path)
            href = f"/files/{root_key}/{quote(rel)}"
            rows.append(
                f"<tr><td><a href='{href}' target='_blank'>{escape(rel)}</a></td>"
                f"<td>{file_path.stat().st_size}</td></tr>"
            )

        return (
            f"<h3>{escape(title)}</h3>"
            "<table><thead><tr><th>Datei</th><th>Bytes</th></tr></thead><tbody>"
            + ''.join(rows)
            + "</tbody></table>"
        )

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {'/review', '/review/'}:
            rows = load_last_run()
            overrides = load_overrides()
            merged = apply_overrides(rows, overrides)
            categories = taxonomy_categories()
            body_rows = []
            for row in merged:
                filename = str(row.get('datei', ''))
                file_href = f"/files/processed/{quote(filename)}"
                options = []
                current_category = str(row.get('steuer_kategorie', ''))
                all_options = categories if categories else [current_category]
                if current_category and current_category not in all_options:
                    all_options = [current_category] + all_options
                for cat in all_options:
                    selected = " selected" if cat == current_category else ""
                    options.append(f"<option value='{escape(cat)}'{selected}>{escape(cat)}</option>")
                review_required = str(row.get('review_required', 'true')).lower() in {'true', '1', 'yes'}
                review_true_sel = " selected" if review_required else ""
                review_false_sel = "" if review_required else " selected"
                body_rows.append(
                    "<tr>"
                    f"<td><a href='{file_href}' target='_blank'>{escape(filename)}</a></td>"
                    f"<td>{escape(str(row.get('haendler', '')))}</td>"
                    f"<td><select data-kind='category' data-file='{escape(filename)}'>{''.join(options)}</select></td>"
                    f"<td><select data-kind='review' data-file='{escape(filename)}'>"
                    f"<option value='true'{review_true_sel}>Pruefung noetig</option>"
                    f"<option value='false'{review_false_sel}>OK</option></select></td>"
                    f"<td><button data-action='save' data-file='{escape(filename)}'>Speichern</button></td>"
                    "</tr>"
                )
            table = ''.join(body_rows) or "<tr><td colspan='5'>Keine Daten in last_run.json</td></tr>"
            html = f"""
<!doctype html>
<html lang='de'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Tax AI Review</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; background:#f7f9fb; color:#112; }}
    table {{ width:100%; border-collapse: collapse; background:white; margin-bottom:18px; }}
    th,td {{ border:1px solid #d8e0ea; padding:8px; font-size:14px; text-align:left; }}
    th {{ background:#eef3f8; }}
    button {{ padding:6px 10px; }}
    .toolbar {{ display:flex; gap:10px; margin:12px 0; }}
  </style>
</head>
<body>
  <h1>Klassifizierung Review</h1>
  <p>Nur klicken/auswaehlen. Keine Texteingabe noetig.</p>
  <div class='toolbar'>
    <button id='export-btn'>Reviewed CSV erzeugen</button>
    <a href='/'>Status</a>
  </div>
  <table>
    <thead><tr><th>Datei</th><th>Haendler</th><th>Kategorie</th><th>Review</th><th>Aktion</th></tr></thead>
    <tbody>{table}</tbody>
  </table>
  <script>
    async function saveRow(file) {{
      const category = document.querySelector(`select[data-kind="category"][data-file="${{CSS.escape(file)}}"]`).value;
      const review = document.querySelector(`select[data-kind="review"][data-file="${{CSS.escape(file)}}"]`).value === 'true';
      const res = await fetch('/api/review/save', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{file: file, steuer_kategorie: category, review_required: review}})
      }});
      if (!res.ok) alert('Speichern fehlgeschlagen');
    }}
    document.querySelectorAll('button[data-action="save"]').forEach(btn => {{
      btn.addEventListener('click', async () => {{
        await saveRow(btn.dataset.file);
      }});
    }});
    document.getElementById('export-btn').addEventListener('click', async () => {{
      const res = await fetch('/api/review/export', {{method: 'POST'}});
      if (res.ok) {{
        window.location.href = '/files/exports/steuer_2025_export_reviewed.csv';
      }} else {{
        alert('Export fehlgeschlagen');
      }}
    }});
  </script>
</body>
</html>
"""
            self._send_html(html)
            return

        if self.path in {'/api/status', '/api/status/'}:
            self._send_json(load_status())
            return

        if self.path.startswith('/files/'):
            raw = unquote(self.path[len('/files/'):])
            parts = raw.split('/', 1)
            if len(parts) != 2:
                self._send_html('<h1>400</h1>', 400)
                return
            root_key, rel = parts
            root = FILE_ROOTS.get(root_key)
            if not root:
                self._send_html('<h1>404</h1>', 404)
                return
            candidate = (root / rel).resolve()
            if root.resolve() not in candidate.parents and candidate != root.resolve():
                self._send_html('<h1>403</h1>', 403)
                return
            self._send_file(candidate)
            return

        if self.path not in {'/', '/index.html'}:
            self._send_html('<h1>404</h1>', 404)
            return

        s = load_status()
        counts = s.get('counts', {})
        docs = s.get('last_10_documents', [])

        rows = []
        for d in docs:
            rows.append(
                f"<tr><td>{escape(str(d.get('datei','')))}</td><td>{escape(str(d.get('haendler','')))}</td>"
                f"<td>{escape(str(d.get('steuer_kategorie','')))}</td><td>{escape(str(d.get('ecodms_upload_status','')))}</td>"
                f"<td>{escape(str(d.get('steuerbox_upload_status','')))}</td></tr>"
            )
        table = ''.join(rows) or '<tr><td colspan="5">Keine Daten</td></tr>'
        provider_rows = []
        for row in provider_sources():
            provider_rows.append(
                "<tr>"
                f"<td>{escape(str(row.get('name', '')))}</td>"
                f"<td>{escape(str(row.get('mode', '')))}</td>"
                f"<td>{escape(str(row.get('status', '')))}</td>"
                f"<td>{escape(str(row.get('details', '')))}</td>"
                "</tr>"
            )
        providers_table = ''.join(provider_rows) or '<tr><td colspan="4">Keine Provider-Definitionen</td></tr>'

        manual_html = self._build_file_list('manual-drop', 'Manuelle Ablage (taxdrop)', limit=30)
        portal_dl_html = self._build_file_list('portal-downloads', 'Portal-Downloads (PDF/Original)', limit=40)
        portal_html = self._build_file_list('portal-screenshots', 'Portal-Screenshots', limit=40)
        processed_html = self._build_file_list('processed', 'Verarbeitete Dateien', limit=20)
        exports_html = self._build_file_list('exports', 'Exporte', limit=20)

        html = f"""
<!doctype html>
<html lang='de'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Tax AI Status</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; background:#f7f9fb; color:#112; }}
    .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:12px; margin-bottom:18px; }}
    .card {{ background:white; padding:12px; border-radius:8px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
    table {{ width:100%; border-collapse: collapse; background:white; margin-bottom:18px; }}
    th,td {{ border:1px solid #d8e0ea; padding:8px; font-size:14px; text-align:left; }}
    th {{ background:#eef3f8; }}
    code {{ background:#eef3f8; padding:2px 5px; border-radius:4px; }}
    a {{ color:#0b57d0; text-decoration:none; }}
  </style>
</head>
<body>
  <h1>Tax AI Verarbeitung</h1>
  <p>Stand: <code>{escape(str(s.get('generated_at_utc')))}</code> | Modus: <code>{escape(str(s.get('mode')))}</code></p>
  <div class='grid'>
    <div class='card'><b>Verarbeitet</b><br>{counts.get('documents_processed_this_run',0)}</div>
    <div class='card'><b>Prüfung nötig</b><br>{counts.get('documents_review_required',0)}</div>
    <div class='card'><b>ecoDMS ok/fehler</b><br>{counts.get('ecodms_success',0)} / {counts.get('ecodms_errors',0)}</div>
    <div class='card'><b>Steuerbox gesendet/fehler</b><br>{counts.get('steuerbox_sent',0)} / {counts.get('steuerbox_errors',0)}</div>
    <div class='card'><b>IMAP gesammelt</b><br>{counts.get('imap_collected_attachments',0)}</div>
    <div class='card'><b>Manuell gesammelt</b><br>{counts.get('manual_drop_collected_files',0)}</div>
    <div class='card'><b>Duplikate Hash</b><br>{counts.get('duplicates_skipped_hash',0)}</div>
    <div class='card'><b>Duplikate Semantik</b><br>{counts.get('duplicates_skipped_semantic',0)}</div>
  </div>
  <h2>Letzte 10 Dokumente</h2>
  <table>
    <thead><tr><th>Datei</th><th>Händler</th><th>Kategorie</th><th>ecoDMS</th><th>Steuerbox</th></tr></thead>
    <tbody>{table}</tbody>
  </table>
  <h2>Pflichtanbieter</h2>
  <table>
    <thead><tr><th>Anbieter</th><th>Modus</th><th>Status</th><th>Details</th></tr></thead>
    <tbody>{providers_table}</tbody>
  </table>
  {manual_html}
  {portal_dl_html}
  {portal_html}
  {processed_html}
  {exports_html}
  <p>JSON API: <a href='/api/status'>/api/status</a></p>
</body>
</html>
"""
        self._send_html(html)

    def do_POST(self) -> None:  # noqa: N802
        if self.path in {'/api/review/save', '/api/review/save/'}:
            payload = self._read_json_body()
            file_name = str(payload.get('file', '')).strip()
            if not file_name:
                self._send_json({'ok': False, 'error': 'missing file'}, 400)
                return
            overrides = load_overrides()
            item = overrides.get(file_name, {})
            if not isinstance(item, dict):
                item = {}
            if 'steuer_kategorie' in payload:
                item['steuer_kategorie'] = str(payload.get('steuer_kategorie', '')).strip()
            if 'review_required' in payload:
                value = payload.get('review_required')
                item['review_required'] = bool(value) if isinstance(value, bool) else str(value).lower() in {'true', '1', 'yes'}
            overrides[file_name] = item
            save_overrides(overrides)
            self._send_json({'ok': True})
            return

        if self.path in {'/api/review/export', '/api/review/export/'}:
            rows = load_last_run()
            overrides = load_overrides()
            merged = apply_overrides(rows, overrides)
            write_reviewed_export(merged)
            self._send_json({'ok': True, 'path': str(REVIEW_EXPORT_PATH)})
            return

        self._send_json({'ok': False, 'error': 'not found'}, 404)


def main() -> None:
    server = HTTPServer((SETTINGS.status_web_host, SETTINGS.status_web_port), Handler)
    print(f"Status server running on http://{SETTINGS.status_web_host}:{SETTINGS.status_web_port}")
    server.serve_forever()


if __name__ == '__main__':
    main()
