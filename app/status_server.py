import csv
import json
import mimetypes
from datetime import datetime, timezone
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
EXPORT_DATA_PATH = Path('/data/logs/last_nonempty_export.json')
WORKFLOW_STATE_PATH = Path('/data/logs/review_workflow_state.json')
FILE_ROOTS = {
    'manual-drop': Path('/manual-drop'),
    'portal-screenshots': Path('/data/portal/screenshots'),
    'portal-downloads': Path('/data/portal/downloads'),
    'processed': Path('/data/processed'),
    'exports': Path('/data/exports'),
    'inbox-imap': Path('/data/inbox_imap_test'),
    'pipeline-inbox': Path('/data/pipeline_inbox_imap'),
    'pipeline-inbox-005': Path('/data/pipeline_inbox_imap_005'),
    'pipeline-inbox-006': Path('/data/pipeline_inbox_imap_006'),
    'pipeline-inbox-gmail': Path('/data/pipeline_inbox_imap_gmail_001'),
    'pipeline-inbox-proton': Path('/data/pipeline_inbox_imap_proton_001'),
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


# --- Review Workflow Helpers ---

def load_export_data() -> list[dict]:
    if not EXPORT_DATA_PATH.exists():
        return []
    try:
        data = json.loads(EXPORT_DATA_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_workflow_state() -> dict:
    if not WORKFLOW_STATE_PATH.exists():
        return {'version': 1, 'current_step': 'cleanup', 'auto_cleanup_done': False, 'decisions': {}}
    try:
        data = json.loads(WORKFLOW_STATE_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict) and 'decisions' in data:
            return data
    except Exception:
        pass
    return {'version': 1, 'current_step': 'cleanup', 'auto_cleanup_done': False, 'decisions': {}}


def save_workflow_state(state: dict) -> None:
    WORKFLOW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WORKFLOW_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


_PREVIEW_ROOTS = [k for k in (
    'processed', 'portal-downloads', 'portal-screenshots', 'manual-drop',
    'inbox-imap', 'pipeline-inbox', 'pipeline-inbox-005', 'pipeline-inbox-006',
    'pipeline-inbox-gmail', 'pipeline-inbox-proton',
)]


def resolve_file_url(filename: str) -> str | None:
    # If the file is a .txt, prefer a generated .pdf with the same base name
    candidates = [filename]
    if filename.endswith('.txt'):
        candidates.insert(0, filename[:-4] + '.pdf')
    for check_name in candidates:
        for root_key in _PREVIEW_ROOTS:
            root = FILE_ROOTS.get(root_key)
            if not root:
                continue
            candidate = root / check_name
            if candidate.exists() and candidate.is_file():
                return f"/files/{root_key}/{quote(check_name)}"
    return None


def build_review_spa() -> str:
    return """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tax AI Review Workflow</title>
<style>
:root { --bg:#f5f7fa; --card:#fff; --border:#d8e0ea; --accent:#0b57d0; --green:#16a34a; --red:#dc2626; --orange:#ea580c; --gray:#6b7280; --text:#112; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); min-height:100vh; }
.header { background:white; border-bottom:1px solid var(--border); padding:12px 24px; display:flex; align-items:center; gap:16px; position:sticky; top:0; z-index:100; }
.header h1 { font-size:18px; white-space:nowrap; }
.steps { display:flex; gap:4px; flex:1; }
.step-pill { padding:6px 14px; border-radius:20px; font-size:13px; cursor:pointer; border:1px solid var(--border); background:white; color:var(--gray); transition:all .2s; }
.step-pill.active { background:var(--accent); color:white; border-color:var(--accent); }
.step-pill.done { background:#dcfce7; color:var(--green); border-color:#bbf7d0; }
.step-pill:hover { opacity:0.85; }
.nav-link { font-size:13px; color:var(--accent); text-decoration:none; }
.main { max-width:1400px; margin:0 auto; padding:20px 24px; }
.progress-bar { background:#e5e7eb; border-radius:8px; height:8px; margin-bottom:16px; overflow:hidden; }
.progress-fill { height:100%; border-radius:8px; background:linear-gradient(90deg,var(--accent),var(--green)); transition:width .3s; }
.stats { display:flex; gap:12px; margin-bottom:16px; flex-wrap:wrap; }
.stat { background:white; padding:8px 14px; border-radius:8px; border:1px solid var(--border); font-size:13px; }
.stat b { font-size:16px; }

/* Step 1: Cleanup */
.cleanup-list { background:white; border-radius:10px; border:1px solid var(--border); overflow:hidden; }
.cleanup-item { display:flex; align-items:center; padding:10px 16px; border-bottom:1px solid var(--border); gap:12px; }
.cleanup-item:last-child { border-bottom:none; }
.cleanup-item input[type=checkbox] { width:18px; height:18px; }
.cleanup-actions { margin-top:16px; display:flex; gap:12px; }

/* Step 2+3: Card view */
.card-view { display:flex; gap:20px; min-height:500px; }
.card-meta { flex:0 0 420px; background:white; border-radius:10px; border:1px solid var(--border); padding:20px; overflow-y:auto; max-height:75vh; }
.card-preview { flex:1; background:white; border-radius:10px; border:1px solid var(--border); overflow:hidden; display:flex; align-items:center; justify-content:center; min-height:400px; }
.card-preview img { max-width:100%; max-height:72vh; object-fit:contain; }
.card-preview iframe { width:100%; height:72vh; border:none; }
.card-preview pre { padding:20px; font-size:13px; white-space:pre-wrap; word-break:break-word; overflow-y:auto; max-height:72vh; width:100%; text-align:left; }
.card-preview .no-preview { color:var(--gray); font-size:14px; }
.meta-row { margin-bottom:8px; font-size:14px; }
.meta-label { font-weight:600; color:var(--gray); font-size:12px; text-transform:uppercase; }
.meta-value { margin-top:2px; }
.decision-buttons { display:flex; gap:12px; margin-top:20px; }
.btn { padding:12px 28px; border:none; border-radius:8px; font-size:15px; font-weight:600; cursor:pointer; transition:all .15s; display:inline-flex; align-items:center; gap:6px; }
.btn:active { transform:scale(0.97); }
.btn-green { background:var(--green); color:white; }
.btn-green:hover { background:#15803d; }
.btn-red { background:var(--red); color:white; }
.btn-red:hover { background:#b91c1c; }
.btn-skip { background:#e5e7eb; color:var(--text); }
.btn-skip:hover { background:#d1d5db; }
.btn-accent { background:var(--accent); color:white; }
.btn-accent:hover { background:#0945a5; }
.btn-outline { background:white; color:var(--text); border:1px solid var(--border); }
.btn-outline:hover { background:var(--bg); }
.kbd { display:inline-block; background:#f3f4f6; border:1px solid #d1d5db; border-radius:4px; padding:1px 6px; font-size:11px; font-family:monospace; margin-left:4px; vertical-align:middle; }

/* Step 3: Category buttons */
.cat-buttons { display:flex; flex-direction:column; gap:8px; margin-top:16px; }
.cat-btn { padding:12px 16px; border:2px solid var(--border); border-radius:8px; background:white; cursor:pointer; font-size:14px; text-align:left; transition:all .15s; display:flex; align-items:center; gap:10px; }
.cat-btn:hover { border-color:var(--accent); background:#f0f7ff; }
.cat-btn.suggested { border-color:var(--orange); background:#fff7ed; }
.cat-btn .cat-key { background:var(--accent); color:white; border-radius:6px; width:28px; height:28px; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:14px; flex-shrink:0; }

/* Step 4: Summary */
.summary-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:20px; }
.summary-card { background:white; padding:16px; border-radius:10px; border:1px solid var(--border); text-align:center; }
.summary-card .num { font-size:32px; font-weight:700; }
.summary-card .label { font-size:13px; color:var(--gray); margin-top:4px; }
.filter-bar { display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; }
.filter-btn { padding:5px 12px; border:1px solid var(--border); border-radius:16px; background:white; cursor:pointer; font-size:13px; }
.filter-btn.active { background:var(--accent); color:white; border-color:var(--accent); }
table.summary-table { width:100%; border-collapse:collapse; background:white; border-radius:10px; overflow:hidden; border:1px solid var(--border); }
table.summary-table th, table.summary-table td { padding:8px 12px; border-bottom:1px solid var(--border); font-size:13px; text-align:left; }
table.summary-table th { background:#f8fafc; font-weight:600; position:sticky; top:0; }
table.summary-table tr:hover td { background:#f8fafc; }
.tag { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }
.tag-green { background:#dcfce7; color:var(--green); }
.tag-red { background:#fee2e2; color:var(--red); }
.tag-blue { background:#dbeafe; color:var(--accent); }
.tag-gray { background:#f3f4f6; color:var(--gray); }

.hidden { display:none !important; }
.loading { text-align:center; padding:60px; color:var(--gray); }
</style>
</head>
<body>
<div class="header">
  <h1>Review Workflow</h1>
  <div class="steps">
    <button class="step-pill" data-step="cleanup" onclick="goStep('cleanup')">1. Bereinigung</button>
    <button class="step-pill" data-step="triage" onclick="goStep('triage')">2. Triage</button>
    <button class="step-pill" data-step="categorize" onclick="goStep('categorize')">3. Kategorien</button>
    <button class="step-pill" data-step="summary" onclick="goStep('summary')">4. Zusammenfassung</button>
  </div>
  <a class="nav-link" href="/">Status-Dashboard</a>
</div>
<div class="main" id="app">
  <div class="loading" id="loading">Lade Daten...</div>
</div>

<script>
// ---- State ----
let allDocs = [];
let state = { version:1, current_step:'cleanup', auto_cleanup_done:false, decisions:{} };
let triageList = [];   // docs needing triage (review_required, not auto-cleaned)
let catList = [];       // docs marked relevant, needing category
let triageIdx = 0;
let catIdx = 0;
let currentStep = 'cleanup';
let summaryFilter = 'all';
const VODAFONE_PATTERN = /vodafone.*fallback/i;

const CATEGORIES = [
  { key:'werbungskosten_arbeitsmittel', label:'Werbungskosten > Arbeitsmittel', short:'Arbeitsmittel' },
  { key:'werbungskosten_fahrtkosten', label:'Werbungskosten > Fahrtkosten', short:'Fahrtkosten' },
  { key:'sonderausgaben_spenden', label:'Sonderausgaben > Spenden', short:'Spenden' },
  { key:'haushaltsnahe_dienstleistungen', label:'Haushaltsnahe Leistungen', short:'Haushaltsnah' },
  { key:'sonstiges_pruefung', label:'Sonstiges (Pruefung)', short:'Sonstiges' },
];

// ---- API ----
async function api(method, path, body) {
  const opts = { method };
  if (body) {
    opts.headers = {'Content-Type':'application/json'};
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

async function loadData() {
  const d = await api('GET', '/api/review/data');
  allDocs = d.documents || [];
  state = d.state || state;
  rebuildLists();
}

function rebuildLists() {
  // Triage: all review_required docs, excluding already-decided ones
  triageList = allDocs.filter(d => {
    const rr = String(d.review_required||'').toLowerCase();
    if (rr !== 'true' && rr !== '1') return false;
    return !state.decisions[d.datei];
  });
  // Sort: PDFs with amount first, then PNGs, then TXTs
  triageList.sort((a,b) => {
    const sa = triageSortKey(a), sb = triageSortKey(b);
    return sa - sb;
  });
  // CatList: docs marked relevant but no category yet
  catList = allDocs.filter(d => {
    const dec = state.decisions[d.datei];
    if (!dec) return false;
    return dec.relevant === true && !dec.steuer_kategorie;
  });
}

function triageSortKey(d) {
  const ext = (d.datei||'').split('.').pop().toLowerCase();
  const amt = parseFloat(d.brutto_betrag||'0');
  if (ext === 'pdf' && amt > 0) return 0;
  if (ext === 'pdf') return 1;
  if (ext === 'png' || ext === 'jpg') return 2;
  return 3;
}

// ---- Rendering ----
const $app = () => document.getElementById('app');

function goStep(step) {
  currentStep = step;
  rebuildLists();
  document.querySelectorAll('.step-pill').forEach(p => {
    p.classList.remove('active','done');
    const s = p.dataset.step;
    if (s === step) p.classList.add('active');
    else if (isStepDone(s)) p.classList.add('done');
  });
  render();
}

function isStepDone(step) {
  if (step === 'cleanup') return state.auto_cleanup_done;
  if (step === 'triage') return triageList.length === 0 && Object.keys(state.decisions).length > 0;
  if (step === 'categorize') return catList.length === 0 && Object.values(state.decisions).some(d => d.steuer_kategorie);
  return false;
}

function render() {
  if (currentStep === 'cleanup') renderCleanup();
  else if (currentStep === 'triage') renderTriage();
  else if (currentStep === 'categorize') renderCategorize();
  else renderSummary();
}

function getDecisionCounts() {
  const decs = Object.values(state.decisions);
  const relevant = decs.filter(d => d.relevant === true).length;
  const irrelevant = decs.filter(d => d.relevant === false).length;
  const categorized = decs.filter(d => d.steuer_kategorie).length;
  const total = allDocs.filter(d => String(d.review_required||'').toLowerCase() === 'true' || d.review_required === '1').length;
  return { relevant, irrelevant, categorized, total, decided: relevant + irrelevant };
}

function progressHTML(decided, total) {
  const pct = total ? Math.round(decided/total*100) : 0;
  return '<div class="progress-bar"><div class="progress-fill" style="width:'+pct+'%"></div></div>';
}

function statsHTML(c) {
  return '<div class="stats">'
    +'<div class="stat"><b>'+c.total+'</b> gesamt</div>'
    +'<div class="stat"><b>'+c.decided+'</b> entschieden</div>'
    +'<div class="stat" style="color:var(--green)"><b>'+c.relevant+'</b> relevant</div>'
    +'<div class="stat" style="color:var(--red)"><b>'+c.irrelevant+'</b> irrelevant</div>'
    +'<div class="stat" style="color:var(--accent)"><b>'+c.categorized+'</b> kategorisiert</div>'
    +'</div>';
}

// ---- Step 1: Cleanup ----
function renderCleanup() {
  const vodafone = allDocs.filter(d => VODAFONE_PATTERN.test(d.datei));
  const c = getDecisionCounts();
  let html = '<h2>Schritt 1: Auto-Bereinigung</h2>'
    +'<p style="margin:8px 0 16px;color:var(--gray)">'+vodafone.length+' Vodafone-Fallback-Screenshots gefunden. Diese sind keine Steuerbelege.</p>'
    + progressHTML(c.decided, c.total) + statsHTML(c);

  if (state.auto_cleanup_done) {
    html += '<div style="padding:24px;background:#dcfce7;border-radius:10px;text-align:center;margin:20px 0">'
      +'<b style="color:var(--green)">Bereinigung abgeschlossen</b><br>'
      +'<span style="color:var(--gray);font-size:14px">'+vodafone.length+' Eintraege als nicht relevant markiert</span></div>'
      +'<div class="cleanup-actions"><button class="btn btn-accent" onclick="goStep(\\x27triage\\x27)">Weiter zu Triage</button>'
      +'<button class="btn btn-outline" onclick="undoCleanup()">Bereinigung rueckgaengig</button></div>';
  } else {
    html += '<div class="cleanup-list">';
    vodafone.forEach((d,i) => {
      const decided = state.decisions[d.datei];
      const checked = !decided ? 'checked' : (decided.relevant === false ? 'checked' : '');
      html += '<div class="cleanup-item">'
        +'<input type="checkbox" id="vf'+i+'" data-file="'+esc(d.datei)+'" '+checked+'>'
        +'<label for="vf'+i+'" style="flex:1;font-size:14px">'+esc(d.datei)+'</label>'
        +'<span style="color:var(--gray);font-size:12px">'+esc(d.haendler||'')+'</span>'
        +'</div>';
    });
    html += '</div>';
    html += '<div class="cleanup-actions">'
      +'<button class="btn btn-red" onclick="doCleanup()">Markierte als nicht relevant entfernen ('+vodafone.length+')</button>'
      +'<button class="btn btn-skip" onclick="goStep(\\x27triage\\x27)">Ueberspringen</button></div>';
  }
  $app().innerHTML = html;
}

async function doCleanup() {
  const checks = document.querySelectorAll('.cleanup-item input[type=checkbox]');
  const batch = [];
  checks.forEach(cb => {
    if (cb.checked) batch.push({ datei: cb.dataset.file, relevant: false });
  });
  if (batch.length === 0) return;
  await api('POST', '/api/review/decide-batch', { decisions: batch });
  batch.forEach(b => {
    state.decisions[b.datei] = { relevant:false, decided_at:new Date().toISOString() };
  });
  state.auto_cleanup_done = true;
  rebuildLists();
  renderCleanup();
}

async function undoCleanup() {
  const vodafone = allDocs.filter(d => VODAFONE_PATTERN.test(d.datei));
  const batch = vodafone.map(d => ({ datei:d.datei, undo:true }));
  await api('POST', '/api/review/decide-batch', { decisions: batch });
  vodafone.forEach(d => { delete state.decisions[d.datei]; });
  state.auto_cleanup_done = false;
  rebuildLists();
  renderCleanup();
}

// ---- Step 2: Triage ----
function renderTriage() {
  const c = getDecisionCounts();
  if (triageList.length === 0) {
    $app().innerHTML = '<h2>Schritt 2: Schnell-Triage</h2>'
      + progressHTML(c.decided, c.total) + statsHTML(c)
      +'<div style="padding:40px;background:#dcfce7;border-radius:10px;text-align:center;margin:20px 0">'
      +'<b style="color:var(--green)">Alle Belege durchgearbeitet!</b></div>'
      +'<button class="btn btn-accent" onclick="goStep(\\x27categorize\\x27)">Weiter zu Kategorien</button>';
    return;
  }
  if (triageIdx >= triageList.length) triageIdx = triageList.length - 1;
  if (triageIdx < 0) triageIdx = 0;
  const doc = triageList[triageIdx];
  const ext = (doc.datei||'').split('.').pop().toLowerCase();
  const fileUrl = doc._file_url || '';
  const amt = parseFloat(doc.brutto_betrag||'0');

  let preview = '<div class="no-preview">Keine Vorschau verfuegbar</div>';
  if (fileUrl) {
    if (['png','jpg','jpeg','gif','webp'].includes(ext)) {
      preview = '<img src="'+esc(fileUrl)+'" alt="Vorschau">';
    } else if (ext === 'pdf') {
      preview = '<iframe src="'+esc(fileUrl)+'"></iframe>';
    } else if (ext === 'txt') {
      preview = '<pre id="txt-preview">Lade...</pre>';
    }
  }

  let html = '<h2>Schritt 2: Schnell-Triage</h2>'
    + progressHTML(c.decided, c.total) + statsHTML(c)
    +'<p style="margin-bottom:12px;color:var(--gray);font-size:14px">Beleg '+(triageIdx+1)+' von '+triageList.length
    +' &mdash; Tastatur: <span class="kbd">1</span> / <span class="kbd">&rarr;</span> Relevant, '
    +'<span class="kbd">2</span> / <span class="kbd">&larr;</span> Nicht relevant, '
    +'<span class="kbd">Leertaste</span> Skip</p>'
    +'<div class="card-view">'
    +'<div class="card-meta">';

  const fields = [
    ['Datei', doc.datei],
    ['Haendler', doc.haendler],
    ['Betrag', amt > 0 ? amt.toFixed(2)+' '+doc.waehrung : '(kein Betrag)'],
    ['Kategorie (KI)', doc.steuer_kategorie],
    ['Confidence', doc.confidence],
    ['Dokumenttyp', doc.dokumenttyp],
    ['Belegdatum', doc.belegdatum || '(unbekannt)'],
    ['Hinweis', doc.tax_relevance_hint],
  ];
  fields.forEach(([label,val]) => {
    html += '<div class="meta-row"><div class="meta-label">'+esc(label)+'</div><div class="meta-value">'+esc(String(val||''))+'</div></div>';
  });

  html += '<div class="decision-buttons">'
    +'<button class="btn btn-green" onclick="triageDecide(true)">Relevant <span class="kbd">1</span></button>'
    +'<button class="btn btn-red" onclick="triageDecide(false)">Nicht relevant <span class="kbd">2</span></button>'
    +'<button class="btn btn-skip" onclick="triageSkip()">Skip <span class="kbd">Space</span></button>'
    +'</div>';

  if (fileUrl) {
    html += '<div style="margin-top:12px"><a href="'+esc(fileUrl)+'" target="_blank" style="font-size:13px;color:var(--accent)">Datei in neuem Tab oeffnen</a></div>';
  }

  html += '</div><div class="card-preview">'+preview+'</div></div>';
  $app().innerHTML = html;

  if (ext === 'txt' && fileUrl) loadTxtPreview(doc.datei);
}

async function loadTxtPreview(datei) {
  try {
    const r = await fetch('/api/review/file-content/' + encodeURIComponent(datei));
    const d = await r.json();
    const el = document.getElementById('txt-preview');
    if (el) el.textContent = d.content || '(leer)';
  } catch(e) {
    const el = document.getElementById('txt-preview');
    if (el) el.textContent = '(Fehler beim Laden)';
  }
}

async function triageDecide(relevant) {
  if (triageList.length === 0) return;
  const doc = triageList[triageIdx];
  await api('POST', '/api/review/decide', { datei:doc.datei, relevant:relevant });
  state.decisions[doc.datei] = { relevant, decided_at:new Date().toISOString() };
  rebuildLists();
  if (triageIdx >= triageList.length) triageIdx = Math.max(0, triageList.length - 1);
  renderTriage();
}

function triageSkip() {
  if (triageList.length === 0) return;
  triageIdx = (triageIdx + 1) % triageList.length;
  renderTriage();
}

// ---- Step 3: Categorize ----
function renderCategorize() {
  const c = getDecisionCounts();
  if (catList.length === 0) {
    $app().innerHTML = '<h2>Schritt 3: Kategorisierung</h2>'
      + progressHTML(c.categorized, c.relevant) + statsHTML(c)
      +'<div style="padding:40px;background:#dcfce7;border-radius:10px;text-align:center;margin:20px 0">'
      +'<b style="color:var(--green)">Alle relevanten Belege kategorisiert!</b></div>'
      +'<button class="btn btn-accent" onclick="goStep(\\x27summary\\x27)">Weiter zur Zusammenfassung</button>';
    return;
  }
  if (catIdx >= catList.length) catIdx = catList.length - 1;
  if (catIdx < 0) catIdx = 0;
  const doc = catList[catIdx];
  const ext = (doc.datei||'').split('.').pop().toLowerCase();
  const fileUrl = doc._file_url || '';
  const amt = parseFloat(doc.brutto_betrag||'0');
  const aiCat = doc.steuer_kategorie || '';
  const conf = parseFloat(doc.confidence||'0');

  let preview = '<div class="no-preview">Keine Vorschau verfuegbar</div>';
  if (fileUrl) {
    if (['png','jpg','jpeg','gif','webp'].includes(ext)) {
      preview = '<img src="'+esc(fileUrl)+'" alt="Vorschau">';
    } else if (ext === 'pdf') {
      preview = '<iframe src="'+esc(fileUrl)+'"></iframe>';
    } else if (ext === 'txt') {
      preview = '<pre id="txt-preview-cat">Lade...</pre>';
    }
  }

  let html = '<h2>Schritt 3: Kategorisierung</h2>'
    +'<p style="margin:4px 0 12px;color:var(--gray);font-size:13px">'+c.categorized+' von '+c.relevant+' kategorisiert</p>'
    + progressHTML(c.categorized, c.relevant)
    +'<p style="margin-bottom:12px;color:var(--gray);font-size:14px">Beleg '+(catIdx+1)+' von '+catList.length
    +' &mdash; Taste <span class="kbd">1</span>-<span class="kbd">5</span> fuer Kategorie, <span class="kbd">Leertaste</span> Skip</p>'
    +'<div class="card-view"><div class="card-meta">';

  const fields = [
    ['Datei', doc.datei],
    ['Haendler', doc.haendler],
    ['Betrag', amt > 0 ? amt.toFixed(2)+' '+doc.waehrung : '(kein Betrag)'],
    ['Dokumenttyp', doc.dokumenttyp],
    ['Belegdatum', doc.belegdatum || '(unbekannt)'],
    ['Hinweis', doc.tax_relevance_hint],
  ];
  fields.forEach(([label,val]) => {
    html += '<div class="meta-row"><div class="meta-label">'+esc(label)+'</div><div class="meta-value">'+esc(String(val||''))+'</div></div>';
  });

  html += '<div class="cat-buttons">';
  CATEGORIES.forEach((cat, i) => {
    const isSuggested = conf > 0 && aiCat === cat.key;
    html += '<button class="cat-btn'+(isSuggested?' suggested':'')+'" onclick="catDecide(\\x27'+cat.key+'\\x27)">'
      +'<span class="cat-key">'+(i+1)+'</span>'
      +'<span>'+esc(cat.label)+(isSuggested?' (KI-Vorschlag)':'')+'</span></button>';
  });
  html += '</div>';

  if (fileUrl) {
    html += '<div style="margin-top:12px"><a href="'+esc(fileUrl)+'" target="_blank" style="font-size:13px;color:var(--accent)">Datei in neuem Tab oeffnen</a></div>';
  }

  html += '</div><div class="card-preview">'+preview+'</div></div>';
  $app().innerHTML = html;

  if (ext === 'txt' && fileUrl) loadTxtPreviewCat(doc.datei);
}

async function loadTxtPreviewCat(datei) {
  try {
    const r = await fetch('/api/review/file-content/' + encodeURIComponent(datei));
    const d = await r.json();
    const el = document.getElementById('txt-preview-cat');
    if (el) el.textContent = d.content || '(leer)';
  } catch(e) {
    const el = document.getElementById('txt-preview-cat');
    if (el) el.textContent = '(Fehler beim Laden)';
  }
}

async function catDecide(category) {
  if (catList.length === 0) return;
  const doc = catList[catIdx];
  await api('POST', '/api/review/decide', { datei:doc.datei, steuer_kategorie:category });
  state.decisions[doc.datei].steuer_kategorie = category;
  rebuildLists();
  if (catIdx >= catList.length) catIdx = Math.max(0, catList.length - 1);
  renderCategorize();
}

function catSkip() {
  if (catList.length === 0) return;
  catIdx = (catIdx + 1) % catList.length;
  renderCategorize();
}

// ---- Step 4: Summary ----
function renderSummary() {
  const c = getDecisionCounts();
  const decs = state.decisions;

  // Count categories
  const catCounts = {};
  CATEGORIES.forEach(cat => catCounts[cat.key] = 0);
  Object.values(decs).forEach(d => { if (d.steuer_kategorie) catCounts[d.steuer_kategorie] = (catCounts[d.steuer_kategorie]||0)+1; });

  let html = '<h2>Schritt 4: Zusammenfassung</h2>'
    +'<div class="summary-grid">'
    +'<div class="summary-card"><div class="num" style="color:var(--accent)">'+c.total+'</div><div class="label">Gesamt (Review)</div></div>'
    +'<div class="summary-card"><div class="num" style="color:var(--green)">'+c.relevant+'</div><div class="label">Relevant</div></div>'
    +'<div class="summary-card"><div class="num" style="color:var(--red)">'+c.irrelevant+'</div><div class="label">Nicht relevant</div></div>'
    +'<div class="summary-card"><div class="num" style="color:var(--accent)">'+c.categorized+'</div><div class="label">Kategorisiert</div></div>'
    +'<div class="summary-card"><div class="num" style="color:var(--gray)">'+(c.total-c.decided)+'</div><div class="label">Offen</div></div>'
    +'</div>';

  // Category breakdown
  html += '<h3 style="margin:12px 0 8px">Kategorien</h3><div class="stats">';
  CATEGORIES.forEach(cat => {
    html += '<div class="stat">'+esc(cat.short)+': <b>'+catCounts[cat.key]+'</b></div>';
  });
  html += '</div>';

  // Actions
  html += '<div style="display:flex;gap:12px;margin:16px 0">'
    +'<button class="btn btn-accent" onclick="doFinalize()">Export CSV erzeugen</button>'
    +'<button class="btn btn-outline" onclick="doReset()">Workflow zuruecksetzen</button>'
    +'</div>';

  // Filter bar
  html += '<h3 style="margin:16px 0 8px">Alle Entscheidungen</h3>'
    +'<div class="filter-bar">'
    +'<button class="filter-btn'+(summaryFilter==='all'?' active':'')+'" onclick="setFilter(\\x27all\\x27)">Alle</button>'
    +'<button class="filter-btn'+(summaryFilter==='relevant'?' active':'')+'" onclick="setFilter(\\x27relevant\\x27)">Relevant</button>'
    +'<button class="filter-btn'+(summaryFilter==='irrelevant'?' active':'')+'" onclick="setFilter(\\x27irrelevant\\x27)">Nicht relevant</button>'
    +'<button class="filter-btn'+(summaryFilter==='open'?' active':'')+'" onclick="setFilter(\\x27open\\x27)">Offen</button>'
    +'</div>';

  // Table
  html += '<div style="max-height:50vh;overflow-y:auto"><table class="summary-table"><thead><tr>'
    +'<th>Datei</th><th>Haendler</th><th>Betrag</th><th>Status</th><th>Kategorie</th></tr></thead><tbody>';

  const reviewDocs = allDocs.filter(d => {
    const rr = String(d.review_required||'').toLowerCase();
    return rr === 'true' || rr === '1';
  });

  reviewDocs.forEach(doc => {
    const dec = decs[doc.datei];
    let statusTag, catTag;
    if (!dec) {
      statusTag = '<span class="tag tag-gray">Offen</span>';
      catTag = '-';
    } else if (dec.relevant === false) {
      statusTag = '<span class="tag tag-red">Nicht relevant</span>';
      catTag = '-';
    } else {
      statusTag = '<span class="tag tag-green">Relevant</span>';
      const catInfo = CATEGORIES.find(c => c.key === dec.steuer_kategorie);
      catTag = catInfo ? '<span class="tag tag-blue">'+esc(catInfo.short)+'</span>' : '<span class="tag tag-gray">Offen</span>';
    }

    // Filter
    if (summaryFilter === 'relevant' && (!dec || dec.relevant !== true)) return;
    if (summaryFilter === 'irrelevant' && (!dec || dec.relevant !== false)) return;
    if (summaryFilter === 'open' && dec) return;

    const amt = parseFloat(doc.brutto_betrag||'0');
    const fileUrl = doc._file_url;
    const nameCell = fileUrl
      ? '<a href="'+esc(fileUrl)+'" target="_blank" style="color:var(--accent)">'+esc(doc.datei)+'</a>'
      : esc(doc.datei);

    html += '<tr><td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+nameCell+'</td>'
      +'<td>'+esc(doc.haendler||'')+'</td>'
      +'<td>'+(amt>0?amt.toFixed(2):'-')+'</td>'
      +'<td>'+statusTag+'</td>'
      +'<td>'+catTag+'</td></tr>';
  });

  html += '</tbody></table></div>';
  $app().innerHTML = html;
}

function setFilter(f) {
  summaryFilter = f;
  renderSummary();
}

async function doFinalize() {
  const r = await api('POST', '/api/review/finalize');
  if (r.ok) {
    alert('Export erzeugt: ' + (r.path||'steuer_2025_export_reviewed.csv') + '\\n' + (r.count||0) + ' Eintraege');
    window.open('/files/exports/steuer_2025_export_reviewed.csv', '_blank');
  } else {
    alert('Fehler: '+(r.error||'unbekannt'));
  }
}

async function doReset() {
  if (!confirm('Workflow wirklich zuruecksetzen? Alle Entscheidungen gehen verloren.')) return;
  await api('POST', '/api/review/reset');
  state = { version:1, current_step:'cleanup', auto_cleanup_done:false, decisions:{} };
  triageIdx = 0;
  catIdx = 0;
  rebuildLists();
  goStep('cleanup');
}

// ---- Keyboard ----
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

  if (currentStep === 'triage') {
    if (e.key === '1' || e.key === 'ArrowRight') { e.preventDefault(); triageDecide(true); }
    else if (e.key === '2' || e.key === 'ArrowLeft') { e.preventDefault(); triageDecide(false); }
    else if (e.key === ' ') { e.preventDefault(); triageSkip(); }
  } else if (currentStep === 'categorize') {
    const num = parseInt(e.key);
    if (num >= 1 && num <= CATEGORIES.length) { e.preventDefault(); catDecide(CATEGORIES[num-1].key); }
    else if (e.key === ' ') { e.preventDefault(); catSkip(); }
  }
});

// ---- Helpers ----
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ---- Init ----
(async () => {
  try {
    await loadData();
    document.getElementById('loading').remove();
    goStep(state.auto_cleanup_done ? (triageList.length > 0 ? 'triage' : 'categorize') : 'cleanup');
  } catch(e) {
    document.getElementById('loading').innerHTML =
      '<div style="color:red;padding:20px"><b>Fehler beim Laden:</b><br>'+esc(String(e))
      +'<br><br>API-URL: <a href="/api/review/data">/api/review/data</a>'
      +'<br><br><button onclick="location.reload()">Neu laden</button></div>';
    console.error('loadData failed:', e);
  }
})();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict | list, code: int = 200) -> None:
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
        # --- Review Workflow SPA ---
        if self.path in {'/review', '/review/'}:
            self._send_html(build_review_spa())
            return

        # --- Review Workflow API ---
        if self.path in {'/api/review/data', '/api/review/data/'}:
            docs = load_export_data()
            wf_state = load_workflow_state()
            for doc in docs:
                doc['_file_url'] = resolve_file_url(str(doc.get('datei', '')))
            self._send_json({'documents': docs, 'state': wf_state})
            return

        if self.path.startswith('/api/review/file-content/'):
            raw_name = unquote(self.path[len('/api/review/file-content/'):])
            if not raw_name or '/' in raw_name or '..' in raw_name:
                self._send_json({'error': 'invalid filename'}, 400)
                return
            file_path = None
            for root_key in _PREVIEW_ROOTS:
                root = FILE_ROOTS.get(root_key)
                if not root:
                    continue
                candidate = root / raw_name
                if candidate.exists() and candidate.is_file():
                    file_path = candidate
                    break
            if not file_path:
                self._send_json({'content': '(Datei nicht gefunden)'})
                return
            try:
                content = file_path.read_text(encoding='utf-8', errors='replace')[:10240]
            except Exception:
                content = '(Fehler beim Lesen)'
            self._send_json({'content': content})
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
  <p>Stand: <code>{escape(str(s.get('generated_at_utc')))}</code> | Modus: <code>{escape(str(s.get('mode')))}</code>
     | <a href='/review'>Review Workflow</a></p>
  <div class='grid'>
    <div class='card'><b>Verarbeitet</b><br>{counts.get('documents_processed_this_run',0)}</div>
    <div class='card'><b>Pr&uuml;fung n&ouml;tig</b><br>{counts.get('documents_review_required',0)}</div>
    <div class='card'><b>ecoDMS ok/fehler</b><br>{counts.get('ecodms_success',0)} / {counts.get('ecodms_errors',0)}</div>
    <div class='card'><b>Steuerbox gesendet/fehler</b><br>{counts.get('steuerbox_sent',0)} / {counts.get('steuerbox_errors',0)}</div>
    <div class='card'><b>IMAP gesammelt</b><br>{counts.get('imap_collected_attachments',0)}</div>
    <div class='card'><b>Manuell gesammelt</b><br>{counts.get('manual_drop_collected_files',0)}</div>
    <div class='card'><b>Duplikate Hash</b><br>{counts.get('duplicates_skipped_hash',0)}</div>
    <div class='card'><b>Duplikate Semantik</b><br>{counts.get('duplicates_skipped_semantic',0)}</div>
  </div>
  <h2>Letzte 10 Dokumente</h2>
  <table>
    <thead><tr><th>Datei</th><th>H&auml;ndler</th><th>Kategorie</th><th>ecoDMS</th><th>Steuerbox</th></tr></thead>
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
        # --- Legacy save endpoint (backward compat) ---
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

        # --- Legacy export endpoint (backward compat) ---
        if self.path in {'/api/review/export', '/api/review/export/'}:
            rows = load_last_run()
            overrides = load_overrides()
            merged = apply_overrides(rows, overrides)
            write_reviewed_export(merged)
            self._send_json({'ok': True, 'path': str(REVIEW_EXPORT_PATH)})
            return

        # --- Workflow: Single decision ---
        if self.path in {'/api/review/decide', '/api/review/decide/'}:
            payload = self._read_json_body()
            datei = str(payload.get('datei', '')).strip()
            if not datei:
                self._send_json({'ok': False, 'error': 'missing datei'}, 400)
                return
            wf = load_workflow_state()
            entry = wf['decisions'].get(datei, {})
            if 'relevant' in payload:
                entry['relevant'] = bool(payload['relevant'])
                entry['decided_at'] = datetime.now(timezone.utc).isoformat()
            if 'steuer_kategorie' in payload:
                entry['steuer_kategorie'] = str(payload['steuer_kategorie']).strip()
                entry['decided_at'] = datetime.now(timezone.utc).isoformat()
            wf['decisions'][datei] = entry
            save_workflow_state(wf)
            self._send_json({'ok': True})
            return

        # --- Workflow: Batch decisions ---
        if self.path in {'/api/review/decide-batch', '/api/review/decide-batch/'}:
            payload = self._read_json_body()
            decisions = payload.get('decisions', [])
            if not isinstance(decisions, list):
                self._send_json({'ok': False, 'error': 'decisions must be array'}, 400)
                return
            wf = load_workflow_state()
            now = datetime.now(timezone.utc).isoformat()
            for dec in decisions:
                if not isinstance(dec, dict):
                    continue
                datei = str(dec.get('datei', '')).strip()
                if not datei:
                    continue
                if dec.get('undo'):
                    wf['decisions'].pop(datei, None)
                    continue
                entry = wf['decisions'].get(datei, {})
                if 'relevant' in dec:
                    entry['relevant'] = bool(dec['relevant'])
                    entry['decided_at'] = now
                if 'steuer_kategorie' in dec:
                    entry['steuer_kategorie'] = str(dec['steuer_kategorie']).strip()
                    entry['decided_at'] = now
                wf['decisions'][datei] = entry
            # Track cleanup
            vodafone_count = sum(1 for d in decisions if isinstance(d, dict) and not d.get('undo'))
            if vodafone_count > 0 and not wf.get('auto_cleanup_done'):
                wf['auto_cleanup_done'] = True
            elif all(isinstance(d, dict) and d.get('undo') for d in decisions):
                wf['auto_cleanup_done'] = False
            save_workflow_state(wf)
            self._send_json({'ok': True, 'count': len(decisions)})
            return

        # --- Workflow: Reset ---
        if self.path in {'/api/review/reset', '/api/review/reset/'}:
            save_workflow_state({'version': 1, 'current_step': 'cleanup', 'auto_cleanup_done': False, 'decisions': {}})
            self._send_json({'ok': True})
            return

        # --- Workflow: Finalize (export reviewed CSV) ---
        if self.path in {'/api/review/finalize', '/api/review/finalize/'}:
            docs = load_export_data()
            wf = load_workflow_state()
            decisions = wf.get('decisions', {})
            out_rows = []
            for doc in docs:
                row = dict(doc)
                dec = decisions.get(doc.get('datei', ''), {})
                if dec.get('relevant') is False:
                    row['review_required'] = 'False'
                    row['steuer_kategorie'] = 'nicht_relevant'
                    row['steuer_web_hinweis'] = 'Nicht relevant (Review)'
                elif dec.get('relevant') is True:
                    row['review_required'] = 'False'
                    if dec.get('steuer_kategorie'):
                        row['steuer_kategorie'] = dec['steuer_kategorie']
                        # Look up hint from taxonomy
                        cat_hints = {
                            'werbungskosten_arbeitsmittel': 'Werbungskosten > Arbeitsmittel',
                            'werbungskosten_fahrtkosten': 'Werbungskosten > Fahrtkosten',
                            'sonderausgaben_spenden': 'Sonderausgaben > Spenden',
                            'haushaltsnahe_dienstleistungen': 'Haushaltsnahe Leistungen',
                            'sonstiges_pruefung': 'Manuelle Pruefung',
                        }
                        row['steuer_web_hinweis'] = cat_hints.get(dec['steuer_kategorie'], row.get('steuer_web_hinweis', ''))
                out_rows.append(row)
            write_reviewed_export(out_rows)
            self._send_json({'ok': True, 'path': str(REVIEW_EXPORT_PATH), 'count': len(out_rows)})
            return

        self._send_json({'ok': False, 'error': 'not found'}, 404)


def main() -> None:
    server = HTTPServer((SETTINGS.status_web_host, SETTINGS.status_web_port), Handler)
    print(f"Status server running on http://{SETTINGS.status_web_host}:{SETTINGS.status_web_port}")
    server.serve_forever()


if __name__ == '__main__':
    main()
