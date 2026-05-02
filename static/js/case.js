const CASE_ID = window.CASE_BOOTSTRAP.case_id;

// === 画面幅モード切替 (3440x1440 / 2560x1440 のワイドディスプレイ向け) ===
function setWidthMode(mode) {
  if (mode !== 'narrow' && mode !== 'wide' && mode !== 'ultra') mode = 'wide';
  try { localStorage.setItem('pc-width-mode', mode); } catch (e) { /* noop */ }
  document.documentElement.dataset.widthMode = mode;
  document.body.dataset.widthMode = mode;
  ['narrow', 'wide', 'ultra'].forEach(function (m) {
    const btn = document.getElementById('wm-' + m);
    if (btn) btn.classList.toggle('active', m === mode);
  });
}
document.addEventListener('DOMContentLoaded', function () {
  let m = 'wide';
  try { m = localStorage.getItem('pc-width-mode') || 'wide'; } catch (e) { /* noop */ }
  setWidthMode(m);
});


function _jppNormalize(s) {
  if (!s) return '';
  // 全角数字・各種ハイフン・スラッシュ / 括弧 / 余分な「号公報/号/公報/平成/令和」などを除去して正規化
  let t = String(s).replace(/[０-９]/g, (c) =>
    String.fromCharCode(c.charCodeAt(0) - 0xFF10 + 0x30));
  t = t.replace(/[－−―—]/g, '-').replace(/[／]/g, '/');
  // 装飾や注記
  t = t.replace(/[()（）「」【】『』]/g, ' ');
  t = t.replace(/(号公報|号|公報|明細書)/g, ' ');
  // 先頭・末尾の空白除去、内部の空白はそのまま (後段の正規表現が \s* を許容)
  return t.trim();
}

function buildJplatpatUrl(pid) {
  if (!pid) return '';
  pid = _jppNormalize(pid);
  const B = 'https://www.j-platpat.inpit.go.jp/c1801/PU';
  let m;
  // 特開yyyy-nnnnnn
  if ((m = pid.match(/特開\s*(\d{4})\s*[-ー]\s*(\d+)/))) return `${B}/JP-${m[1]}-${m[2].padStart(6,'0')}/11/ja`;
  // 特願yyyy-nnnnnn
  if ((m = pid.match(/特願\s*(\d{4})\s*[-ー]\s*(\d+)/))) return `${B}/JP-${m[1]}-${m[2].padStart(6,'0')}/10/ja`;
  // 特表yyyy-nnnnnn
  if ((m = pid.match(/特表\s*(\d{4})\s*[-ー]\s*(\d+)/))) return `${B}/JP-${m[1]}-${m[2].padStart(6,'0')}/11/ja`;
  // 再公表yyyy-nnnnnn / 再表yyyy/nnnnnn (スラッシュ区切りも許容)
  if ((m = pid.match(/再(?:公)?表\s*(\d{4})\s*[-\/]\s*(\d+)/))) return `${B}/WO-A-${m[1]}-${m[2].padStart(6,'0')}/50/ja`;
  // 特許nnnnnnn
  if ((m = pid.match(/特許(?:第)?\s*(\d+)/))) return `${B}/JP-${m[1]}/15/ja`;
  // JP2023-123456A / JP2023123456A
  if ((m = pid.match(/JP\s*(\d{4})\s*[-]?\s*(\d{3,6})\s*A/i))) return `${B}/JP-${m[1]}-${m[2].padStart(6,'0')}/11/ja`;
  // JPnnnnnnnB
  if ((m = pid.match(/JP\s*(\d{5,8})\s*B\d?/i))) return `${B}/JP-${m[1]}/15/ja`;
  // WO2022/030405
  if ((m = pid.match(/WO\s*(\d{4})\s*[/]?\s*(\d+)/i))) return `${B}/WO-A-${m[1]}-${m[2].padStart(6,'0')}/50/ja`;
  // US公開
  if ((m = pid.match(/US\s*(\d{4})\s*[/]?\s*(\d+)\s*A\d?/i))) return `${B}/US-${m[1]}${m[2]}/50/ja`;
  // US登録
  if ((m = pid.match(/US\s*([\d,]+)\s*B\d?/i))) return `${B}/US-${m[1].replace(/,/g,'')}/50/ja`;
  // EP
  if ((m = pid.match(/EP\s*(\d+)/i))) return `${B}/EP-${m[1]}/50/ja`;
  // CN
  if ((m = pid.match(/CN\s*(\d+)/i))) return `${B}/CN-${m[1]}/50/ja`;
  return '';
}

// 特許番号入力 → J-PlatPat 固定URLを別タブで開く (拒絶理由からコピペ想定)
function jumpToJplatpatByQuery(q) {
  const src = (q || '').trim();
  const input = document.getElementById('jpp-quick-input');
  const msg = document.getElementById('jpp-quick-msg');
  const setMsg = (t, kind) => {
    if (!msg) return;
    msg.textContent = t || '';
    msg.className = 'jpp-quick-msg ' + (kind || '');
  };
  if (!src) {
    setMsg('番号が空です。例: 特開2014-141302号公報', 'err');
    return;
  }
  const url = buildJplatpatUrl(src);
  if (!url) {
    setMsg('対応形式で解釈できませんでした: ' + src, 'err');
    return;
  }
  const norm = _jppNormalize(src);
  setMsg('開きました: ' + norm, 'ok');
  window.open(url, '_blank', 'noopener');
  if (input) {
    input.select();
  }
}

function _jppQuickOnEnter(e) {
  if (e && e.key === 'Enter') {
    const v = document.getElementById('jpp-quick-input');
    jumpToJplatpatByQuery(v ? v.value : '');
  }
}

const panels = document.querySelectorAll('.panel');
const steps = document.querySelectorAll('.step');
let currentPanel = 0;

function showPanel(idx) {
  currentPanel = idx;
  panels.forEach((p, i) => {
    p.classList.toggle('active', i === idx);
  });
  steps.forEach((s, i) => {
    if (!s.classList.contains('done')) {
      s.classList.toggle('active', i === idx);
    }
  });
  if (idx === 3) { if (typeof refreshCitationBadges === 'function') refreshCitationBadges(); }
  if (idx === 4) { loadSearchRuns(); srJppCheckStatus(); srEnsureSnippetsLoaded(); }
  if (idx === 5) { if (typeof refreshCitationBadges === 'function') refreshCitationBadges(); }
  if (idx === 6) loadComparisonSummary();
}

// 初期表示: 最初の未完了ステップ
(function() {
  const b = window.CASE_BOOTSTRAP;
  if (!b.has_hongan) showPanel(0);
  else if (!b.has_segments) showPanel(1);
  else if (!b.has_keywords) showPanel(2);
  else if (!b.has_citations) showPanel(3);
  else showPanel(5);
})();

// ================================================================
// ページ全体のドラッグ&ドロップ制御
// ブラウザのデフォルト動作（PDFを開く）を完全に抑止
// ================================================================
let dragCounter = 0;
const overlay = document.getElementById('page-drop-overlay');
const dropHint = document.getElementById('drop-hint');

// ページ全体でデフォルト動作を完全にブロック
document.addEventListener('dragover', function(e) {
  e.preventDefault();
  e.stopPropagation();
}, false);

document.addEventListener('dragenter', function(e) {
  e.preventDefault();
  e.stopPropagation();
  dragCounter++;
  if (dragCounter === 1) {
    // どのステップにいるかでヒントを変える
    const hasHongan = window.CASE_BOOTSTRAP.has_hongan;
    if (!hasHongan || currentPanel === 0) {
      dropHint.textContent = 'ドロップすると本願PDFとして読み込みます';
    } else if (currentPanel === 3) {
      dropHint.textContent = 'ドロップすると引用文献PDFとして読み込みます';
    } else {
      dropHint.textContent = 'Step1 = 本願PDF / Step4 = 引用文献PDF';
    }
    overlay.classList.add('show');
  }
}, false);

document.addEventListener('dragleave', function(e) {
  e.preventDefault();
  e.stopPropagation();
  dragCounter--;
  if (dragCounter <= 0) {
    dragCounter = 0;
    overlay.classList.remove('show');
  }
}, false);

document.addEventListener('drop', function(e) {
  e.preventDefault();
  e.stopPropagation();
  dragCounter = 0;
  overlay.classList.remove('show');

  const files = e.dataTransfer.files;
  if (!files || files.length === 0) return;

  // PDFかチェック
  const pdfFiles = Array.from(files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (pdfFiles.length === 0) {
    alert('PDFファイルをドロップしてください。');
    return;
  }

  const hasHongan = window.CASE_BOOTSTRAP.has_hongan;

  // ファイル名にISR/IPERのヒントがあれば ISR/書面意見ルートへ
  const looksLikeSearchReport = pdfFiles.every(f => {
    const name = f.name.toLowerCase();
    return /(^|[^a-z])(isr|iper)([^a-z]|$)|isa[\- _]?210|isa[\- _]?237|ipea[\- _]?409|国際調査報告|書面意見|国際予備審査/i.test(name);
  });
  if (hasHongan && looksLikeSearchReport) {
    showPanel(3);
    uploadSearchReports(pdfFiles);
    return;
  }

  // 本願未登録 → 本願扱い（初回投入のみ）
  if (!hasHongan) {
    showPanel(0);
    uploadHongan(pdfFiles[0]);
    return;
  }

  // 本願登録済み → 既定は引用文献扱い。本願差し替えは Step 1 のdropzoneを使ってもらう
  showPanel(3);
  uploadCitations(pdfFiles);
}, false);

// 個別ドロップゾーン: 自前のdropハンドラでdocument側より先に処理
// idごとに対応するアップロード先を決める（誤振り分け防止）
const DZ_HANDLERS = {
  'dropzone-hongan':        (pdfs) => { showPanel(0); uploadHongan(pdfs[0]); },
  'dropzone-citation':      (pdfs) => { showPanel(3); uploadCitations(pdfs); },
  'dropzone-search-report': (pdfs) => { showPanel(3); uploadSearchReports(pdfs); },
};
document.querySelectorAll('.dropzone').forEach(dz => {
  dz.addEventListener('dragover',  e => { e.preventDefault(); e.stopPropagation(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', e => { e.stopPropagation(); dz.classList.remove('dragover'); });
  dz.addEventListener('drop', e => {
    const handler = DZ_HANDLERS[dz.id];
    if (!handler) return;  // 未登録はdocument側にフォールバック
    e.preventDefault();
    e.stopPropagation();
    dz.classList.remove('dragover');
    dragCounter = 0;
    overlay.classList.remove('show');
    const pdfs = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
    if (pdfs.length === 0) { alert('PDFファイルをドロップしてください。'); return; }
    handler(pdfs);
  });
});

// ===== 本願PDFをPDF-XChange Editorで開く =====
async function openHonganPdf() {
  try {
    const resp = await fetch(`/case/${CASE_ID}/hongan/open`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok || !data.success) {
      throw new Error(data.error || '起動に失敗しました');
    }
  } catch(e) {
    alert('本願PDFを開けませんでした: ' + e.message);
  }
}

// ===== J-PlatPat から本願分類 (IPC/FI/Fターム/テーマ) を取得 =====
async function fetchHonganClassification() {
  const btn = document.getElementById('btn-fetch-classification');
  const msg = document.getElementById('classification-msg');
  if (!btn || !msg) return;
  btn.disabled = true;
  const orig = btn.innerHTML;
  btn.innerHTML = '⏳ 取得中... (10-15秒)';
  msg.style.display = 'block';
  msg.style.background = '#1e293b';
  msg.style.color = '#94a3b8';
  msg.textContent = 'J-PlatPat 詳細ページを開いて書誌情報を抽出しています...';
  try {
    const resp = await fetch(`/case/${CASE_ID}/hongan/classification/fetch`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok || !data.success) {
      throw new Error(data.error || '取得失敗');
    }
    msg.style.background = '#14532d';
    msg.style.color = '#4ade80';
    msg.innerHTML = `✅ 取得完了: テーマ ${data.theme_codes.join(', ') || '-'} / IPC ${data.n_ipc} / FI ${data.n_fi} / Fターム ${data.n_fterm}`;
    // Step 3 のFターム候補を再読み込み
    if (typeof refreshFtermCatalog === 'function') {
      try { await refreshFtermCatalog(); } catch(_) {}
    }
  } catch(e) {
    msg.style.background = '#7f1d1d';
    msg.style.color = '#fecaca';
    msg.textContent = '❌ ' + e.message;
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

// ===== 本願アップロード =====
async function uploadHongan(file) {
  if (!file) return;
  const loading = document.getElementById('loading-hongan');
  loading.classList.add('show');

  const fd = new FormData();
  fd.append('file', file);

  try {
    const resp = await fetch(`/case/${CASE_ID}/upload/hongan`, { method: 'POST', body: fd });
    const data = await resp.json();
    loading.classList.remove('show');

    if (data.success) {
      const titlePart = data.patent_title ? ` — ${data.patent_title}` : '';
      document.getElementById('result-hongan').innerHTML =
        `<div style="padding:1rem; background:#14532d; border-radius:8px; margin-top:1rem; color:#4ade80;">
          抽出完了: ${data.patent_number}${titlePart} / 請求項${data.num_claims}件 / 段落${data.num_paragraphs}件
        </div>`;
      steps[0].classList.add('done');
      steps[1].classList.add('done');
      setTimeout(() => location.reload(), 1000);
    } else {
      document.getElementById('result-hongan').innerHTML =
        `<div style="padding:1rem; background:#450a0a; border-radius:8px; margin-top:1rem; color:#fca5a5;">
          エラー: ${data.error}
        </div>`;
    }
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

// ===== 引用文献アップロード =====
async function uploadCitations(files) {
  const loading = document.getElementById('loading-citation');
  loading.classList.add('show');

  for (const file of files) {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('role', document.getElementById('cit-role').value);
    fd.append('label', document.getElementById('cit-label').value || '');

    try {
      const resp = await fetch(`/case/${CASE_ID}/upload/citation`, { method: 'POST', body: fd });
      const data = await resp.json();
      if (data.success) {
        const list = document.getElementById('citations-list');
        list.innerHTML += `<div class="cit-card"><div class="cit-info">
          <h4>${data.doc_id}</h4>
          <p>請求項${data.num_claims}件 / 段落${data.num_paragraphs}件</p>
        </div></div>`;
      }
    } catch(e) {
      alert('エラー: ' + e.message);
    }
  }
  loading.classList.remove('show');
  setTimeout(() => location.reload(), 500);
}

// ===== 引用文献削除 =====
async function deleteCitation(citId) {
  if (!confirm(`文献 "${citId}" を削除しますか？\n（関連する回答データも削除されます）`)) return;
  try {
    const resp = await fetch(`/case/${CASE_ID}/citation/${citId}`, { method: 'DELETE' });
    const data = await resp.json();
    if (data.success) {
      const card = document.getElementById(`cit-card-${citId}`);
      if (card) card.remove();
      location.reload();
    } else {
      alert('削除失敗: ' + (data.error || ''));
    }
  } catch(e) {
    alert('エラー: ' + e.message);
  }
}

async function clearAllCitations() {
  if (!confirm('全ての引用文献と回答データを削除しますか？\nこの操作は取り消せません。')) return;
  try {
    const resp = await fetch(`/case/${CASE_ID}/citations/clear`, { method: 'DELETE' });
    const data = await resp.json();
    if (data.success) {
      location.reload();
    } else {
      alert('クリア失敗: ' + (data.error || ''));
    }
  } catch(e) {
    alert('エラー: ' + e.message);
  }
}

// ===== ISR/書面意見 (PCT) 取り込み =====

function _catBadge(cat) {
  cat = (cat || '').toUpperCase();
  if (cat === 'X') return '<span class="badge" style="background:#7f1d1d; color:#fca5a5;">X</span>';
  if (cat === 'Y') return '<span class="badge" style="background:#422006; color:#fbbf24;">Y</span>';
  if (cat === 'A') return '<span class="badge" style="background:var(--surface2); color:var(--text2);">A</span>';
  if (cat) return `<span class="badge" style="background:var(--surface2); color:var(--text2);">${cat}</span>`;
  return '';
}

function _fetchStatusBadge(status) {
  if (status === 'ok') return '<span class="badge badge-green">取得済</span>';
  if (status === 'failed') return '<span class="badge" style="background:#7f1d1d; color:#fca5a5;">取得失敗</span>';
  if (status === 'no_id') return '<span class="badge" style="background:var(--surface2); color:var(--text2);">番号未抽出</span>';
  if (status === 'extract_failed') return '<span class="badge" style="background:#7f1d1d; color:#fca5a5;">抽出失敗</span>';
  return '';
}

function _escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, ch => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  }[ch]));
}

function renderSearchReport(report) {
  const fname = report.filename;
  const safeFname = _escapeHtml(fname);
  const formLabel = ({
    ISR: 'ISR (PCT/ISA/210)',
    WOSA: '書面意見 (PCT/ISA/237)',
    IPER: 'IPER (PCT/IPEA/409)'
  })[report.form] || (report.form || '不明');

  const cits = report.citations || [];
  const rows = cits.map(c => {
    const id = c.fetched_doc_id || c.doc_id || '';
    const labelHtml = _escapeHtml(c.doc_label || '');
    const passages = _escapeHtml(c.passages || '');
    const fetchBadge = _fetchStatusBadge(c.fetch_status);
    const gpUrl = c.google_patents_url
      ? ` <a href="${_escapeHtml(c.google_patents_url)}" target="_blank" style="color:#60a5fa; font-size:0.75rem;">Google Patents</a>`
      : '';
    const checkbox = id
      ? `<input type="checkbox" class="sr-cit-check" data-num="${c.num}" ${c.fetch_status==='ok'?'disabled':''}>`
      : `<span style="color:var(--text2);">—</span>`;
    return `<tr>
      <td>${checkbox}</td>
      <td>${c.num}</td>
      <td>${_catBadge(c.category)}</td>
      <td>${labelHtml}<br><span style="font-size:0.75rem; color:var(--text2);">${_escapeHtml(id)}</span></td>
      <td>${_escapeHtml(c.claims || '')}</td>
      <td style="font-size:0.8rem;">${passages}</td>
      <td>${fetchBadge}${gpUrl}</td>
    </tr>`;
  }).join('');

  const summaryHtml = report.box_v_summary
    ? `<div style="background:var(--surface2); padding:0.8rem; border-radius:6px; margin-top:0.5rem; white-space:pre-wrap; font-size:0.85rem;">${_escapeHtml(report.box_v_summary)}</div>`
    : '';

  const hasBoxV = report.box_v && report.box_v.trim().length > 0;
  const hasCits = cits.length > 0;

  return `<div class="cit-card sr-card" data-filename="${safeFname}" style="display:block; padding:0.8rem;">
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
      <div>
        <h4 style="margin:0;">${safeFname}</h4>
        <p style="margin:0.2rem 0 0; font-size:0.8rem; color:var(--text2);">
          ${formLabel} / ${_escapeHtml(report.intl_app_no || '番号不明')} / 引例 ${cits.length}件
        </p>
      </div>
      <div style="display:flex; gap:4px; flex-wrap:wrap;">
        <a href="/case/${CASE_ID}/search-report/${encodeURIComponent(fname)}/pdf" target="_blank"
           class="btn btn-outline" style="padding:0.3rem 0.8rem; font-size:0.8rem;">PDF表示</a>
        ${hasBoxV ? `<button class="btn btn-outline" style="padding:0.3rem 0.8rem; font-size:0.8rem;"
                onclick="summarizeBoxV('${safeFname}', this)">Box V要約</button>` : ''}
        <button class="btn btn-danger" style="padding:0.3rem 0.8rem; font-size:0.8rem;"
                onclick="deleteSearchReport('${safeFname}')">削除</button>
      </div>
    </div>
    ${hasCits ? `
      <div style="margin-top:0.6rem;">
        <div style="font-size:0.8rem; margin-bottom:0.3rem;">
          <a href="#" onclick="toggleSrCheckboxes('${safeFname}', true); return false;">全選択</a> /
          <a href="#" onclick="toggleSrCheckboxes('${safeFname}', false); return false;">全解除</a>
        </div>
        <table style="width:100%; font-size:0.85rem; border-collapse:collapse;">
          <thead><tr style="border-bottom:1px solid var(--border);">
            <th></th><th>#</th><th>Cat</th><th>文献</th><th>クレーム</th><th>引用箇所</th><th>状態</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div class="btn-row">
          <button class="btn btn-success" style="padding:0.3rem 0.8rem; font-size:0.85rem;"
                  onclick="fetchCitedDocs('${safeFname}', this)">選択した文献をDL & 引用文献に追加</button>
          <span class="loading-msg sr-fetch-loading" style="display:none;">
            <span class="spinner"></span><span>取得中...</span>
          </span>
        </div>
        <div class="sr-fetch-result" style="margin-top:0.4rem;"></div>
      </div>
    ` : '<p style="color:var(--text2); font-size:0.85rem;">引用文献を抽出できませんでした。</p>'}
    ${summaryHtml}
  </div>`;
}

function toggleSrCheckboxes(filename, on) {
  const card = document.querySelector(`.sr-card[data-filename="${CSS.escape(filename)}"]`);
  if (!card) return;
  card.querySelectorAll('.sr-cit-check').forEach(cb => {
    if (!cb.disabled) cb.checked = on;
  });
}

async function loadSearchReports() {
  try {
    const resp = await fetch(`/case/${CASE_ID}/search-report/list`);
    const data = await resp.json();
    const list = document.getElementById('search-reports-list');
    if (!data.reports || data.reports.length === 0) {
      list.innerHTML = '';
      return;
    }
    list.innerHTML = data.reports.map(renderSearchReport).join('');
  } catch(e) {
    console.error('loadSearchReports', e);
  }
}

async function uploadSearchReports(files) {
  const loading = document.getElementById('loading-search-report');
  loading.classList.add('show');
  let errors = [];
  for (const file of files) {
    const fd = new FormData();
    fd.append('file', file);
    try {
      const resp = await fetch(`/case/${CASE_ID}/search-report/upload`, {
        method: 'POST', body: fd,
      });
      const data = await resp.json();
      if (!data.success) errors.push(`${file.name}: ${data.error || 'unknown'}`);
    } catch(e) {
      errors.push(`${file.name}: ${e.message}`);
    }
  }
  loading.classList.remove('show');
  if (errors.length) alert('一部失敗:\n' + errors.join('\n'));
  await loadSearchReports();
}

async function deleteSearchReport(filename) {
  if (!confirm(`"${filename}" を削除しますか？`)) return;
  try {
    const resp = await fetch(
      `/case/${CASE_ID}/search-report/${encodeURIComponent(filename)}`,
      { method: 'DELETE' }
    );
    const data = await resp.json();
    if (data.success) await loadSearchReports();
    else alert('削除失敗: ' + (data.error || ''));
  } catch(e) { alert('エラー: ' + e.message); }
}

async function summarizeBoxV(filename, btn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '要約中...';
  try {
    const resp = await fetch(
      `/case/${CASE_ID}/search-report/${encodeURIComponent(filename)}/summarize`,
      { method: 'POST' }
    );
    const data = await resp.json();
    if (data.success) await loadSearchReports();
    else alert('要約失敗: ' + (data.error || ''));
  } catch(e) { alert('エラー: ' + e.message); }
  finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function fetchCitedDocs(filename, btn) {
  const card = btn.closest('.sr-card');
  const checked = Array.from(card.querySelectorAll('.sr-cit-check:checked'));
  if (checked.length === 0) {
    alert('取得する文献を選択してください');
    return;
  }
  const nums = checked.map(cb => parseInt(cb.dataset.num, 10));
  const loading = card.querySelector('.sr-fetch-loading');
  const resultBox = card.querySelector('.sr-fetch-result');
  btn.disabled = true;
  loading.style.display = 'inline-flex';
  resultBox.innerHTML = '';
  try {
    const resp = await fetch(
      `/case/${CASE_ID}/search-report/${encodeURIComponent(filename)}/fetch`,
      {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ nums }),
      }
    );
    const data = await resp.json();
    if (data.success) {
      const ok = data.results.filter(r => r.success).length;
      const ng = data.results.length - ok;
      resultBox.innerHTML =
        `<div style="font-size:0.85rem;">取得結果: 成功 ${ok} / 失敗 ${ng}</div>` +
        data.results.map(r => r.success
          ? `<div style="font-size:0.8rem; color:#4ade80;">&#10003; ${_escapeHtml(r.label)} (${_escapeHtml(r.role||'')})</div>`
          : `<div style="font-size:0.8rem; color:#fca5a5;">&#10007; ${_escapeHtml(r.label)} — ${_escapeHtml(r.error||'')}
              ${r.google_patents_url ? `<a href="${_escapeHtml(r.google_patents_url)}" target="_blank" style="color:#60a5fa;">[GP]</a>` : ''}</div>`
        ).join('');
      await loadSearchReports();
      // 引用文献カードを更新するため少し待ってから case 全体をリロード
      if (ok > 0) setTimeout(() => location.reload(), 1500);
    } else {
      alert('取得失敗: ' + (data.error || ''));
    }
  } catch(e) { alert('エラー: ' + e.message); }
  finally {
    btn.disabled = false;
    loading.style.display = 'none';
  }
}

// 起動時に読み込み
document.addEventListener('DOMContentLoaded', loadSearchReports);

// ===== 文献リストをカテゴリ別（X/Y/A）にGoogle Patents形式でコピー =====

// citation.id（日本語表記含む）を Google Patents 用の英字IDに変換
function _toGooglePatentsId(id) {
  if (!id) return '';
  let m = id.match(/^特(?:開|表)\s*(\d{4})[\-－ー]?(\d+)/);
  if (m) return `JP${m[1]}${m[2].padStart(6,'0')}A`;
  m = id.match(/^再(?:公)?表\s*(\d{4})[\-－ー]?(\d+)/);
  if (m) return `JP${m[1]}${m[2].padStart(6,'0')}A`;
  m = id.match(/^特許(?:第)?\s*(\d+)/);
  if (m) return `JP${m[1]}B`;
  return id.replace(/[\s\-\/]/g, '');
}

// 引用カード1枚のカテゴリを判定: X/Y/A/空文字
// バッジ（対比結果）優先、無ければ役割にフォールバック
function _getCitationCategory(card) {
  const badges = Array.from(card.querySelectorAll('.badge'))
    .map(b => (b.textContent || '').trim());
  if (badges.includes('X')) return 'X';
  if (badges.includes('Y')) return 'Y';
  if (badges.includes('A')) return 'A';
  const roleText = (card.querySelector('.cit-info p')?.textContent || '');
  if (roleText.includes('主引例')) return 'X';
  if (roleText.includes('副引例')) return 'Y';
  if (roleText.includes('参考'))   return 'A';
  return '';
}

function _collectCategoryGroups() {
  const groups = { X: [], Y: [], A: [] };
  document.querySelectorAll('.cit-card[id^="cit-card-"]').forEach(card => {
    const cat = _getCitationCategory(card);
    if (!groups[cat]) return;
    const citId = card.id.replace('cit-card-', '');
    const gp = _toGooglePatentsId(citId);
    if (gp) groups[cat].push(gp);
  });
  for (const k of Object.keys(groups)) {
    groups[k] = Array.from(new Set(groups[k]));
  }
  return groups;
}

function _refreshCategoryGroups() {
  const container = document.getElementById('cat-id-groups');
  if (!container) return;
  const groups = _collectCategoryGroups();
  const catStyle = {
    X: 'background:#7f1d1d; color:#fca5a5;',
    Y: 'background:#422006; color:#fbbf24;',
    A: 'background:var(--surface2); color:var(--text2);',
  };
  container.innerHTML = ['X','Y','A'].map(cat => {
    const ids = groups[cat];
    const idText = ids.length ? ids.join(',') : '(該当なし)';
    return `<div style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap;">
      <span class="badge" style="${catStyle[cat]} min-width:1.5em; text-align:center; padding:0.1rem 0.5rem;">${cat}</span>
      <code style="flex:1 1 300px; font-family: ui-monospace, 'Consolas', monospace; font-size:0.85rem; color:var(--text2); word-break:break-all;">${idText}</code>
      <button class="btn btn-outline"
              style="padding:0.3rem 0.8rem; font-size:0.8rem;"
              onclick="copyCategoryIds('${cat}', this)"
              ${ids.length === 0 ? 'disabled' : ''}>
        ${cat}のみコピー (${ids.length})
      </button>
    </div>`;
  }).join('');
}

async function copyCategoryIds(cat, btn) {
  const ids = _collectCategoryGroups()[cat] || [];
  if (ids.length === 0) {
    alert(`${cat} 文献が見つかりません`);
    return;
  }
  try {
    await navigator.clipboard.writeText(ids.join(','));
    const orig = btn.textContent;
    btn.textContent = `コピー済 (${ids.length})`;
    setTimeout(() => { btn.textContent = orig; }, 2000);
  } catch (e) {
    alert('クリップボードコピー失敗: ' + e);
  }
}

document.addEventListener('DOMContentLoaded', _refreshCategoryGroups);

// ===== 先行技術検索 =====
let searchCandidates = [];

async function generateSearchPrompt() {
  const loading = document.getElementById('loading-search-prompt');
  loading.classList.add('show');

  try {
    const resp = await fetch(`/case/${CASE_ID}/search/prompt`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})
    });
    const data = await resp.json();
    loading.classList.remove('show');

    if (data.error) { alert(data.error); return; }

    document.getElementById('search-prompt-text').textContent = data.prompt;
    document.getElementById('search-prompt-text').style.display = 'block';
    document.getElementById('btn-copy-search').style.display = 'inline-block';
    document.getElementById('search-prompt-charcount').textContent =
      `${data.char_count.toLocaleString()} 文字`;
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

function copySearchPrompt() {
  const text = document.getElementById('search-prompt-text').textContent;
  if (!text) { alert('先にプロンプトを生成してください'); return; }
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('btn-copy-search');
    btn.textContent = 'コピー済!';
    setTimeout(() => btn.textContent = 'コピー', 1500);
  });
}

async function parseSearchResponse() {
  const text = document.getElementById('search-response-text').value;
  if (!text.trim()) { alert('回答を貼り付けてください'); return; }

  const loading = document.getElementById('loading-search-parse');
  loading.classList.add('show');

  try {
    const resp = await fetch(`/case/${CASE_ID}/search/response`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: text })
    });
    const data = await resp.json();
    loading.classList.remove('show');

    const errDiv = document.getElementById('search-parse-errors');
    errDiv.innerHTML = '';

    if (data.errors && data.errors.length > 0) {
      errDiv.innerHTML = `<div style="padding:0.8rem; background:#422006; border-radius:8px; color:#fbbf24; font-size:0.85rem;">
        <strong>警告:</strong><br>${data.errors.join('<br>')}
      </div>`;
    }

    if (data.success && data.candidates.length > 0) {
      searchCandidates = data.candidates;
      renderCandidatesList(searchCandidates);
      document.getElementById('search-candidates').style.display = 'block';
    } else if (!data.success) {
      errDiv.innerHTML += `<div style="padding:0.8rem; background:#450a0a; border-radius:8px; color:#fca5a5; font-size:0.85rem; margin-top:0.5rem;">
        パース失敗: JSON配列を抽出できませんでした。
      </div>`;
    }
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

function renderCandidatesList(candidates) {
  const container = document.getElementById('candidates-list');
  container.innerHTML = '';
  document.getElementById('candidates-count').textContent = `(${candidates.length}件)`;

  candidates.forEach((c, i) => {
    const relevanceClass = c.relevance === '主引例候補' ? 'relevance-main' :
                           c.relevance === '副引例候補' ? 'relevance-sub' : 'relevance-common';

    const segsHtml = (c.relevant_segments || []).map(s =>
      `<span class="seg-chip">${s}</span>`
    ).join('');

    const statusHtml = c.status === 'downloaded' ? '<span class="status-downloaded">DL済 &#10003;</span>' :
                       c.status === 'failed' ? '<span class="status-failed">DL失敗</span>' :
                       '<span class="status-pending">未DL</span>';

    let linksHtml = '';
    if (c.google_patents_url) {
      linksHtml += `<a href="${c.google_patents_url}" target="_blank" rel="noopener">Google Patents</a>`;
    }
    const jpUrl = c.jplatpat_url || buildJplatpatUrl(c.patent_id);
    if (jpUrl) {
      linksHtml += ` <a href="${jpUrl}" target="_blank" rel="noopener" style="color:#60a5fa;">J-PlatPat</a>`;
    }

    const checked = c.status !== 'downloaded' ? 'checked' : '';

    container.innerHTML += `
      <div class="candidate-item" data-index="${i}" data-patent-id="${c.patent_id}">
        <label>
          <input type="checkbox" class="cand-checkbox" value="${c.patent_id}" ${checked}
                 data-relevance="${c.relevance}">
          <div class="candidate-info">
            <div class="cand-title">
              ${c.patent_id}
              <span class="relevance-tag ${relevanceClass}">${c.relevance}</span>
            </div>
            <div class="cand-meta">
              ${c.title || ''}${c.applicant ? ' — ' + c.applicant : ''}${c.year ? ' (' + c.year + ')' : ''}
            </div>
            <div class="cand-reason">${c.reason || ''}</div>
            <div class="cand-segs">${segsHtml}</div>
          </div>
        </label>
        <div class="cand-status">
          ${statusHtml}
          <div class="cand-links">${linksHtml}</div>
        </div>
      </div>`;
  });
}

function toggleAllSearchCandidates(checked) {
  document.querySelectorAll('.cand-checkbox').forEach(cb => cb.checked = checked);
}

async function downloadSelectedCandidates() {
  const selected = Array.from(document.querySelectorAll('.cand-checkbox:checked'));
  if (selected.length === 0) { alert('ダウンロードする文献を選択してください'); return; }

  const resultDiv = document.getElementById('search-dl-result');
  resultDiv.innerHTML = '';

  const roleMap = { '主引例候補': '主引例', '副引例候補': '副引例', '技術常識': '技術常識' };
  const total = selected.length;
  let successCount = 0;
  let failHtml = '';

  // 進捗表示を初期化
  const loading = document.getElementById('loading-search-dl');
  loading.classList.add('show');
  const spinnerSpan = loading.querySelector('span');

  // 1件ずつ順番にダウンロード（進捗リアルタイム更新）
  for (let idx = 0; idx < selected.length; idx++) {
    const cb = selected[idx];
    const patentId = cb.value;
    const role = roleMap[cb.dataset.relevance] || '主引例';

    // 進捗テキスト更新
    spinnerSpan.textContent = `ダウンロード中... (${idx + 1}/${total}) ${patentId}`;

    // 該当行をハイライト
    const item = document.querySelector(`.candidate-item[data-patent-id="${patentId}"]`);
    if (item) {
      item.style.outline = '2px solid var(--accent)';
      const statusEl = item.querySelector('.cand-status');
      const currentLinks = statusEl.querySelector('.cand-links')?.outerHTML || '';
      statusEl.innerHTML = `<span style="color:var(--accent);"><span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> DL中</span>${currentLinks}`;
    }

    try {
      const resp = await fetch(`/case/${CASE_ID}/search/download`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ patent_ids: [patentId], role: role })
      });
      const data = await resp.json();
      const r = data.results?.[0];

      if (item) {
        item.style.outline = '';
        const statusEl = item.querySelector('.cand-status');
        const currentLinks = statusEl.querySelector('.cand-links')?.outerHTML || '';
        if (r && r.success) {
          statusEl.innerHTML = `<span class="status-downloaded">DL済 &#10003;</span>${currentLinks}`;
          cb.checked = false;
          successCount++;
        } else {
          let failLinks = currentLinks;
          if (r?.google_patents_url) {
            failLinks = `<div class="cand-links"><a href="${r.google_patents_url}" target="_blank" rel="noopener">Google Patentsで確認</a></div>`;
          }
          statusEl.innerHTML = `<span class="status-failed">DL失敗</span>${failLinks}`;
          const errMsg = r?.error || 'ダウンロード失敗';
          failHtml += `<div style="font-size:0.85rem; color:#fbbf24;">${patentId}: ${errMsg}`;
          if (r?.google_patents_url) {
            failHtml += ` — <a href="${r.google_patents_url}" target="_blank" rel="noopener" style="color:var(--accent);">手動DL</a>`;
          }
          failHtml += '</div>';
        }
      }
    } catch(e) {
      if (item) {
        item.style.outline = '';
        const statusEl = item.querySelector('.cand-status');
        statusEl.innerHTML = `<span class="status-failed">エラー</span>`;
      }
      failHtml += `<div style="font-size:0.85rem; color:#fbbf24;">${patentId}: ${e.message}</div>`;
    }
  }

  loading.classList.remove('show');
  spinnerSpan.textContent = 'ダウンロード中...';

  let html = '';
  if (successCount > 0) {
    html += `<div style="padding:0.8rem; background:#14532d; border-radius:8px; color:#4ade80; font-size:0.85rem;">
      ${successCount}/${total}件のPDFをダウンロード・引用文献に追加しました。</div>`;
  }
  if (failHtml) {
    html += `<div style="padding:0.8rem; background:#422006; border-radius:8px; margin-top:0.5rem; font-size:0.85rem;">
      <strong style="color:#fbbf24;">DL失敗分:</strong><br>${failHtml}
      <p style="color:var(--text2); margin-top:0.5rem;">手動でPDFをダウンロードし、下のドロップゾーンからアップロードしてください。</p>
    </div>`;
  }
  resultDiv.innerHTML = html;

  if (successCount > 0) {
    setTimeout(() => location.reload(), 2500);
  }
}

// ===== 分節エディタ =====
function renumberSegIds(claimBlock) {
  const rows = claimBlock.querySelectorAll('.seg-row');
  const claimNum = claimBlock.dataset.claim;
  const alpha = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  rows.forEach((row, i) => {
    const id = i < 26 ? `${claimNum}${alpha[i]}` : `${claimNum}A${i - 25}`;
    row.dataset.segId = id;
    row.querySelector('.seg-id').textContent = id;
  });
}

function splitSeg(btn) {
  const row = btn.closest('.seg-row');
  const textEl = row.querySelector('.seg-text-edit');
  const sel = window.getSelection();
  let splitPos = textEl.textContent.length;
  // カーソル位置で分割（選択がテキスト内にあれば）
  if (sel.rangeCount > 0 && textEl.contains(sel.anchorNode)) {
    splitPos = sel.anchorOffset;
  }
  const fullText = textEl.textContent;
  const before = fullText.substring(0, splitPos).trim();
  const after = fullText.substring(splitPos).trim();
  if (!before || !after) {
    // カーソルが端にある場合は空行を追加
    addSeg(btn);
    return;
  }
  textEl.textContent = before;
  // 新しい行を挿入
  const newRow = row.cloneNode(true);
  newRow.querySelector('.seg-text-edit').textContent = after;
  row.parentNode.insertBefore(newRow, row.nextSibling);
  renumberSegIds(row.closest('.seg-claim-block'));
}

function mergeSeg(btn) {
  const row = btn.closest('.seg-row');
  const next = row.nextElementSibling;
  if (!next || !next.classList.contains('seg-row')) { return; }
  const thisText = row.querySelector('.seg-text-edit').textContent.trim();
  const nextText = next.querySelector('.seg-text-edit').textContent.trim();
  row.querySelector('.seg-text-edit').textContent = thisText + ' ' + nextText;
  next.remove();
  renumberSegIds(row.closest('.seg-claim-block'));
}

function addSeg(btn) {
  const row = btn.closest('.seg-row');
  const newRow = row.cloneNode(true);
  newRow.querySelector('.seg-text-edit').textContent = '';
  row.parentNode.insertBefore(newRow, row.nextSibling);
  renumberSegIds(row.closest('.seg-claim-block'));
  // 新行にフォーカス
  newRow.querySelector('.seg-text-edit').focus();
}

function delSeg(btn) {
  const row = btn.closest('.seg-row');
  const block = row.closest('.seg-claim-block');
  const rows = block.querySelectorAll('.seg-row');
  if (rows.length <= 1) { alert('最後の1行は削除できません'); return; }
  row.remove();
  renumberSegIds(block);
}

// 「保存 → 自動再対比」確認の同セッション内 ack
window._segmentsSaveWarnAcked = window._segmentsSaveWarnAcked || false;
// 自動再対比を「やる」「やらない」のセッション内記憶
window._segmentsAutoRecompareAcked = window._segmentsAutoRecompareAcked || null; // null | true | false

async function saveSegmentsFromEditor(opts) {
  opts = opts || {};
  const skipAutoRecompare = !!opts.skipAutoRecompare;
  // 対比結果が既にある場合は警告 + 自動再対比の意思確認 (silent stale 防止)
  // ただし skipAutoRecompare=true (本願 PDF ブックマーク等の chained 呼び出し) なら
  // 警告だけ出して再対比は実行しない (呼び出し側の処理を中断させない)。
  const fresh = (window.CASE_BOOTSTRAP || {}).freshness || {};
  const targetIds = (fresh.citation_ids_with_responses || []).slice();
  let shouldRecompare = false;
  if (fresh.has_responses && targetIds.length && !skipAutoRecompare) {
    if (!window._segmentsSaveWarnAcked) {
      const n = fresh.response_count || 0;
      const ok = confirm(
        `この案件には既に ${n} 件の対比結果 (responses/*.json) があります。\n\n` +
        `分節を変更すると、対比結果に古い分節 ID が残り、Excel 出力で\n` +
        `「-」(判定なし) が並んだり、孤立した判定データが無視されたりします。\n\n` +
        `保存後、その ${n} 件で自動的に再対比を実行します (Claude 5〜10 分)。\n` +
        `[OK] = 保存 + 自動再対比 を実行 / [キャンセル] = 何もしない\n\n` +
        `(本セッション中はこの確認を再表示しません)`
      );
      if (!ok) return false;
      window._segmentsSaveWarnAcked = true;
      window._segmentsAutoRecompareAcked = true;
    }
    shouldRecompare = window._segmentsAutoRecompareAcked === true;
  }

  const blocks = document.querySelectorAll('.seg-claim-block');
  const segments = [];
  blocks.forEach(block => {
    const claimNum = parseInt(block.dataset.claim);
    const rows = block.querySelectorAll('.seg-row');
    const segs = [];
    rows.forEach(row => {
      const id = row.querySelector('.seg-id').textContent.trim();
      const text = row.querySelector('.seg-text-edit').textContent.trim();
      if (text) segs.push({ id, text });
    });
    // 元データからclaim情報を復元
    const origData = window.CASE_BOOTSTRAP.segments || [];
    const orig = origData.find(c => c.claim_number === claimNum) || {};
    segments.push({
      claim_number: claimNum,
      is_independent: orig.is_independent !== undefined ? orig.is_independent : true,
      dependencies: orig.dependencies || [],
      full_text: orig.full_text || '',
      segments: segs,
    });
  });
  try {
    await fetch(`/case/${CASE_ID}/segments`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(segments)
    });
    const msg = document.getElementById('seg-save-msg');
    msg.textContent = '保存しました';
    msg.style.display = 'block';
    setTimeout(() => msg.style.display = 'none', 2000);
  } catch(e) {
    alert('保存エラー: ' + e.message);
    return false;
  }
  // 保存成功 → 自動再対比 (ユーザーが ack 済みの場合のみ)
  if (shouldRecompare) {
    await _runAutoRecompare(targetIds);
  }
  return true;
}

// ----------------------------------------------------------------
// 分節保存後の「前回選択分での自動再対比」
// ----------------------------------------------------------------
function _showRecompareOverlay(text) {
  let ov = document.getElementById('auto-recompare-overlay');
  if (!ov) {
    ov = document.createElement('div');
    ov.id = 'auto-recompare-overlay';
    ov.style.cssText = `
      position:fixed; inset:0; z-index:9999; background:rgba(0,0,0,0.7);
      display:flex; align-items:center; justify-content:center;
    `;
    ov.innerHTML = `
      <div style="background:#0f172a; border:1px solid var(--border); border-radius:10px;
                  padding:1.4rem 1.8rem; min-width:360px; max-width:80vw; text-align:center;
                  box-shadow:0 20px 60px rgba(0,0,0,0.7);">
        <div style="font-size:2rem; margin-bottom:0.6rem;">⏳</div>
        <div id="auto-recompare-text" style="color:#e2e8f0; font-size:0.95rem; line-height:1.5;"></div>
        <div style="margin-top:0.7rem; color:var(--text2); font-size:0.78rem;">
          完了まで 5〜10 分かかります。タブを閉じないでください。
        </div>
      </div>
    `;
    document.body.appendChild(ov);
  }
  document.getElementById('auto-recompare-text').textContent = text;
  ov.style.display = 'flex';
}
function _hideRecompareOverlay() {
  const ov = document.getElementById('auto-recompare-overlay');
  if (ov) ov.style.display = 'none';
}

async function _runAutoRecompare(citationIds) {
  if (!citationIds || !citationIds.length) return;
  _showRecompareOverlay(
    `分節保存完了。前回選択の ${citationIds.length} 件で自動再対比を実行中...`
  );
  try {
    const resp = await fetch(`/case/${CASE_ID}/execute`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ citation_ids: citationIds }),
    });
    const data = await resp.json();
    _hideRecompareOverlay();
    if (!resp.ok || data.error) {
      alert('自動再対比でエラー: ' + (data.error || `HTTP ${resp.status}`));
      return;
    }
    const docs = (data.saved_docs || []).join(', ');
    alert(
      `✅ 自動再対比 完了 (${data.num_docs || citationIds.length} 件)\n` +
      (docs ? `保存先: ${docs}\n` : '') +
      `ページを再読み込みして整合性バナーが消えることを確認してください。`
    );
    // freshness を反映するためページリロード
    location.reload();
  } catch (e) {
    _hideRecompareOverlay();
    alert('自動再対比 通信エラー: ' + e.message);
  }
}

// ================================================================
// 関連段落の検出・表示 / 本願PDFブックマーク
// ================================================================
const INITIAL_RELATED = window.CASE_BOOTSTRAP.related_paragraphs || {};

function renderRelatedParagraphs(related) {
  document.querySelectorAll('.seg-related').forEach(el => {
    const sid = el.dataset.segId;
    const paras = (related && related[sid]) || [];
    if (!paras.length) {
      el.innerHTML = '';
      return;
    }
    el.innerHTML = paras.map((p, i) => {
      const cls = i === 0 ? 'para-chip primary' : 'para-chip';
      const title = `ページ ${p.page}` + (p.matched && p.matched.length ? ` / 一致: ${p.matched.join(', ')}` : '');
      const typeLabel = p.type ? `${p.type}` : '';
      return `<span class="${cls}" title="${title.replace(/"/g,'&quot;')}">${typeLabel}【${p.id}】</span>`;
    }).join('');
  });
}

function showRelatedMsg(text, type) {
  const box = document.getElementById('related-msg');
  if (!box) return;
  box.textContent = text;
  if (type === 'error') {
    box.style.background = '#450a0a'; box.style.color = '#fca5a5'; box.style.border = '1px solid #7f1d1d';
  } else {
    box.style.background = '#14532d'; box.style.color = '#4ade80'; box.style.border = '1px solid #166534';
  }
  box.style.display = 'block';
  setTimeout(() => box.style.display = 'none', 4000);
}

async function detectRelatedParagraphs() {
  showRelatedMsg('検出中...', 'info');
  try {
    // 現在の編集内容を先に保存
    await saveSegmentsFromEditor();
    const resp = await fetch(`/case/${CASE_ID}/segments/related`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok || !data.success) throw new Error(data.error || '検出失敗');
    renderRelatedParagraphs(data.related);
    const n = Object.values(data.related).filter(v => v && v.length).length;
    showRelatedMsg(`関連段落を検出しました（${n}分節）`, 'info');
  } catch(e) {
    showRelatedMsg('関連段落の検出に失敗: ' + e.message, 'error');
  }
}

async function bookmarkHongan() {
  if (!confirm('分節の編集内容を保存し、本願PDFにブックマーク付きコピーを作成して PDF-XChange で開きます。よろしいですか？')) return;
  showRelatedMsg('ブックマーク生成中...', 'info');
  // 分節保存 → 本願 PDF 生成 → 開く の順を確実に走らせる。
  // 自動再対比は PDF を開いた後で「実際に不整合がある場合に限り」別途プロンプト
  // (再対比失敗で PDF 開けない問題を回避 / 既に整合済なら聞かない)。
  try {
    const saved = await saveSegmentsFromEditor({skipAutoRecompare: true});
    if (saved === false) {
      showRelatedMsg('保存をキャンセルしました', 'info');
      return;
    }
    const resp = await fetch(`/case/${CASE_ID}/hongan/bookmark`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok || !data.success) throw new Error(data.error || 'ブックマーク作成失敗');
    const openedMsg = data.opened ? ' / PDF-XChangeで開きました' : ' （開くのは失敗）';
    showRelatedMsg(`ブックマーク付きPDF作成: ${data.filename}（${data.num_bookmarks}件）${openedMsg}`, 'info');
    // 関連段落UIも更新（最新計算値を取得）
    try {
      const rr = await fetch(`/case/${CASE_ID}/segments/related`);
      const rd = await rr.json();
      if (rd.related) renderRelatedParagraphs(rd.related);
    } catch(_) {}
  } catch(e) {
    showRelatedMsg('ブックマーク作成に失敗: ' + e.message, 'error');
  }
  // PDF を開いた後で「実際に不整合がある場合だけ」再対比を確認
  // (response が存在するだけでは聞かない — 既に整合済なら無音)
  setTimeout(async () => {
    try {
      const fr = await fetch(`/case/${CASE_ID}/segments/freshness`);
      if (!fr.ok) return;
      const fd = await fr.json();
      if (!fd.needs_recompare) return;  // 不整合なし → 何もしない
      const targetIds = fd.citation_ids_with_responses || [];
      if (!targetIds.length) return;
      if (window._segmentsAutoRecompareAcked === false) return;  // 同セッションで「No」済
      const ok = confirm(
        `本願 PDF を開きました。\n\n` +
        `分節編集と既存対比結果の間に不整合があります (${targetIds.length} 件)。\n` +
        `Claude で再対比を実行しますか? (5〜10 分)\n\n` +
        `[OK] = 再対比を実行 / [キャンセル] = あとで Step 5 から実行`
      );
      window._segmentsAutoRecompareAcked = ok;
      if (ok) await _runAutoRecompare(targetIds);
    } catch(_) { /* freshness 取得失敗は静かに */ }
  }, 600);
}

// 初期描画: サーバーから引き渡された関連段落データがあれば表示
if (INITIAL_RELATED && Object.keys(INITIAL_RELATED).length > 0) {
  document.addEventListener('DOMContentLoaded', () => renderRelatedParagraphs(INITIAL_RELATED));
}

// ================================================================
// 本願 PDF 図表 Vision 抽出
// ================================================================
function _appendTableProgress(line) {
  const box = document.getElementById('hongan-tables-progress');
  if (!box) return;
  box.style.display = 'block';
  const ts = new Date().toLocaleTimeString();
  box.textContent += `[${ts}] ${line}\n`;
  box.scrollTop = box.scrollHeight;
}

async function extractHonganTables() {
  const btn = document.getElementById('btn-extract-hongan-tables');
  const progress = document.getElementById('hongan-tables-progress');
  const result = document.getElementById('hongan-tables-result');
  if (!confirm('本願PDFから図表を Vision で抽出します。\n1 表あたり約 25 秒、サブスク消費 $0.05〜0.12/表 の目安です。続行しますか？')) return;
  btn.disabled = true;
  progress.textContent = '';
  progress.style.display = 'block';
  result.innerHTML = '';
  _appendTableProgress('抽出を開始します...');
  try {
    const resp = await fetch(`/case/${CASE_ID}/hongan/extract-tables`, {
      method: 'POST',
      headers: {'Accept': 'text/event-stream'},
    });
    if (!resp.ok) {
      const err = await resp.text();
      throw new Error(err.slice(0, 200));
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let summary = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop();
      for (const block of events) {
        const line = block.split('\n').find(l => l.startsWith('data: '));
        if (!line) continue;
        let evt;
        try { evt = JSON.parse(line.slice(6)); } catch(_) { continue; }
        if (evt.stage === 'start') {
          _appendTableProgress(`PDF: ${evt.pdf}`);
        } else if (evt.stage === 'scan') {
          _appendTableProgress(`スキャン中: ${evt.info}`);
        } else if (evt.stage === 'extract') {
          _appendTableProgress(`[${evt.current}/${evt.total}] ${evt.info}`);
        } else if (evt.stage === 'done') {
          summary = evt.summary;
          _appendTableProgress(`完了: ${summary.n_table} 表抽出 / 所要 ${(summary.total_duration_ms/1000).toFixed(1)}s / コスト相当 $${summary.total_cost_usd_equivalent}`);
        } else if (evt.stage === 'error') {
          throw new Error(evt.message);
        }
      }
    }
    if (summary) {
      await loadHonganTables();
    }
  } catch(e) {
    _appendTableProgress(`!! エラー: ${e.message}`);
    alert('表抽出に失敗: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function loadHonganTables() {
  const result = document.getElementById('hongan-tables-result');
  if (!result) return;
  result.innerHTML = '<p style="color:var(--text2); font-size:0.85rem;">読み込み中...</p>';
  try {
    const resp = await fetch(`/case/${CASE_ID}/hongan/tables`);
    const data = await resp.json();
    if (!data.exists) {
      result.innerHTML = '<p style="color:var(--text2); font-size:0.85rem;">まだ抽出されていません。「本願 実施例表を抽出」ボタンを押してください。</p>';
      return;
    }
    _renderHonganTables(data.data);
  } catch(e) {
    result.innerHTML = `<p style="color:#f87171;">読み込み失敗: ${e.message}</p>`;
  }
}

function _renderHonganTables(payload) {
  const result = document.getElementById('hongan-tables-result');
  const tables = (payload.tables || []).filter(t => t.is_table);
  if (!tables.length) {
    result.innerHTML = '<p style="color:var(--text2);">抽出された表がありません。</p>';
    return;
  }
  const refs = (payload.body_table_references || []).filter(r => /表|Table/i.test(r));
  const html = [
    `<div style="font-size:0.82rem; color:var(--text2); margin-bottom:0.6rem;">`,
    `  本文中の表参照: ${refs.length} 件 / 検出キャプション付き画像: ${payload.candidates_targeted ?? '?'} / 抽出成功: ${tables.length}`,
    `  &nbsp;|&nbsp; 所要 ${((payload.total_duration_ms||0)/1000).toFixed(1)}s &nbsp;|&nbsp; サブスク相当 $${payload.total_cost_usd_equivalent ?? 0}`,
    `</div>`,
  ];
  for (const t of tables) {
    html.push(_renderOneTable(t));
  }
  result.innerHTML = html.join('\n');
}

function _renderOneTable(t) {
  const title = t.title || t.caption_label || `表 (p.${t.page_num})`;
  const headers = t.headers || [];
  const rows = t.rows || [];
  const head = headers.map(h => `<th style="padding:4px 8px; border:1px solid #475569; background:#1e293b; font-weight:600; white-space:nowrap;">${_esc(h)}</th>`).join('');
  const body = rows.map(r => {
    const cells = (r.cells || []).map(c => `<td style="padding:4px 8px; border:1px solid #334155; vertical-align:top;">${_esc(c)}</td>`).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  return [
    `<details open style="margin-bottom:0.8rem; border:1px solid var(--border); border-radius:8px; padding:0.6rem 0.8rem; background:#0f172a;">`,
    `  <summary style="cursor:pointer; font-weight:600; color:#cbd5e1;">${_esc(title)} <span style="color:#64748b; font-size:0.8rem; font-weight:normal;">(p.${t.page_num}, ${rows.length} 行)</span></summary>`,
    `  <div style="overflow:auto; margin-top:0.5rem;">`,
    `    <table style="border-collapse:collapse; font-size:0.8rem;">`,
    `      <thead><tr>${head}</tr></thead>`,
    `      <tbody>${body}</tbody>`,
    `    </table>`,
    `  </div>`,
    `</details>`,
  ].join('\n');
}

function _esc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// 初期表示: 既に抽出済みなら自動ロード (軽量)
document.addEventListener('DOMContentLoaded', () => {
  const result = document.getElementById('hongan-tables-result');
  if (!result) return;
  fetch(`/case/${CASE_ID}/hongan/tables`).then(r => r.json()).then(d => {
    if (d.exists) _renderHonganTables(d.data);
  }).catch(() => {});
});

// ================================================================
// キーワードグループ State
// ================================================================
let kwGroups = window.CASE_BOOTSTRAP.keywords || [];

const GROUP_COLORS = [
  '#ef4444','#a855f7','#ec4899','#3b82f6',
  '#22c55e','#f97316','#14b8a6','#6b7280',
];

function groupColor(gid) {
  return GROUP_COLORS[(gid - 1) % GROUP_COLORS.length];
}

// セグメント情報（分節テキスト参照用）
const SEG_DATA = {};
(function() {
  const segs = window.CASE_BOOTSTRAP.segments || [];
  segs.forEach(claim => {
    (claim.segments || []).forEach(seg => {
      SEG_DATA[seg.id] = {
        text: seg.text,
        claim: claim.claim_number,
        isIndep: !!claim.is_independent,
      };
    });
  });
})();

// ================================================================
// ================================================================
// キーワードハイライト (kw-marked) 永続化: ページ再読込でも残るよう localStorage に保存
// ================================================================
const _KW_MARK_STORAGE_KEY = 'patent-compare:kw-marks:' + (CASE_ID || 'unknown');

function _kwMarkSave() {
  try {
    const marks = { kw: [], ft: [] };
    document.querySelectorAll('.kw-tag.kw-marked').forEach(el => {
      marks.kw.push((el.dataset.gid || '') + '|' + (el.dataset.term || ''));
    });
    document.querySelectorAll('.fterm-tag.kw-marked').forEach(el => {
      marks.ft.push((el.dataset.gid || '') + '|' + (el.dataset.code || ''));
    });
    localStorage.setItem(_KW_MARK_STORAGE_KEY, JSON.stringify(marks));
  } catch (e) { /* localStorage 不可 (シークレット等) は黙ってスキップ */ }
}

function _kwMarkRestore() {
  let marks = null;
  try {
    const raw = localStorage.getItem(_KW_MARK_STORAGE_KEY);
    if (raw) marks = JSON.parse(raw);
  } catch (e) { return; }
  if (!marks) return;
  const kwSet = new Set(marks.kw || []);
  const ftSet = new Set(marks.ft || []);
  document.querySelectorAll('.kw-tag').forEach(el => {
    const key = (el.dataset.gid || '') + '|' + (el.dataset.term || '');
    if (kwSet.has(key)) el.classList.add('kw-marked');
  });
  document.querySelectorAll('.fterm-tag').forEach(el => {
    const key = (el.dataset.gid || '') + '|' + (el.dataset.code || '');
    if (ftSet.has(key)) el.classList.add('kw-marked');
  });
}

// メイン描画
// ================================================================
function renderGroups() {
  if (typeof _pkmInvalidateIndex === 'function') _pkmInvalidateIndex();
  const container = document.getElementById('kw-groups-container');
  if (!kwGroups || kwGroups.length === 0) {
    container.innerHTML = `
      <div style="text-align:center; padding:2rem; color:var(--text2);">
        キーワードグループがありません。「AI自動提案」または「+ グループ追加」で作成してください。
      </div>`;
  } else {
    container.innerHTML = kwGroups.map(g => renderGroup(g)).join('');
  }
  // Fターム候補カタログ側も追加先グループの状態に合わせて同期（存在する場合のみ）
  if (typeof renderFtermCatalog === 'function') {
    try { renderFtermCatalog(); } catch(_) { /* no-op */ }
  }
  // 描画完了後にハイライト状態を復元 (再読込でも消えないように)
  if (typeof _kwMarkRestore === 'function') _kwMarkRestore();
}

function renderGroup(g) {
  const color = groupColor(g.group_id);
  const segPreviews = (g.segment_ids || []).map(sid => {
    const s = SEG_DATA[sid];
    return s ? `<span style="color:var(--text2);">[${sid}]</span> ${escHtml(s.text.substring(0, 60))}${s.text.length > 60 ? '...' : ''}` : sid;
  }).join('<br>');

  const allSegs = Object.keys(SEG_DATA);
  const segChips = allSegs.map(sid => {
    const sel = (g.segment_ids || []).includes(sid);
    return `<button class="kw-seg-chip ${sel ? 'selected' : ''}"
      data-seg-id="${sid}" data-group="${g.group_id}"
      onclick="toggleSegment(${g.group_id}, '${sid}', this)">${sid}</button>`;
  }).join('');

  const tags = (g.keywords || []).map(kw => renderKwTag(g.group_id, kw)).join('');

  return `
<div class="kw-group" id="kw-group-${g.group_id}" data-group-id="${g.group_id}"
     style="border-left:4px solid ${color}; margin-bottom:1rem; padding:1rem;
            border-radius:8px; border:1px solid var(--border);">

  <div class="kw-header" style="display:flex; align-items:center; gap:0.5rem; flex-wrap:wrap; margin-bottom:6px;">
    <span style="color:var(--text2); font-weight:normal; font-size:0.85rem;">グループ${g.group_id}:</span>
    <input class="kw-label-edit" type="text" value="${escAttr(g.label)}"
           data-group="${g.group_id}"
           onblur="renameGroup(${g.group_id}, this.value)"
           onkeydown="if(event.key==='Enter'){this.blur();}">
    <div style="display:flex; gap:4px; margin-left:auto; flex-shrink:0;">
      <button class="kw-group-btn" title="このグループのキーワードを他のグループにコピー"
              onclick="openCopyModal(${g.group_id})">コピー</button>
      <button class="kw-group-btn" title="このグループ内のハイライトをすべて外す"
              onclick="clearSelection(${g.group_id})">ハイライト解除</button>
      <button class="kw-group-btn kw-group-btn-del" title="赤字でハイライトしていない語を削除。ハイライト（赤字）した語は残ります。"
              onclick="deleteUnselected(${g.group_id})">ハイライト外を削除</button>
      <button class="kw-group-btn kw-group-btn-del" title="グループ削除"
              onclick="deleteGroup(${g.group_id})">グループ削除</button>
    </div>
  </div>

  ${segPreviews ? `<div style="margin-bottom:6px; padding:6px 10px; background:var(--bg); border-radius:6px;
    font-size:0.8rem; color:var(--text2); line-height:1.6;">${segPreviews}</div>` : ''}

  <div class="kw-seg-picker" data-group="${g.group_id}"
       style="display:flex; gap:4px; flex-wrap:wrap; margin-bottom:6px;">
    <span style="color:var(--text2); font-size:0.75rem; margin-right:2px; align-self:center;">分節:</span>
    ${segChips}
  </div>

  <div class="kw-tags" data-group="${g.group_id}" style="margin-top:4px;">
    ${tags}
    <span class="kw-add-form" style="display:inline-flex; align-items:center; gap:4px; margin:2px; vertical-align:middle;">
      <input class="kw-add-input" type="text" placeholder="追加..."
             data-group="${g.group_id}"
             onkeydown="if(event.key==='Enter'){addKeyword(${g.group_id}, this);}">
      <button class="kw-add-btn" title="追加" onclick="addKeyword(${g.group_id}, this.previousElementSibling)">+</button>
    </span>
  </div>

  ${renderFterms(g)}
</div>`;
}

function renderKwTag(gid, kw) {
  const typeLabel = kw.type || '';
  const badgeClass = typeToBadgeClass(typeLabel);
  return `<span class="kw-tag" data-term="${escAttr(kw.term)}" data-gid="${gid}"
    title="クリック: 残す/外すを切替（赤=残す） / ダブルクリック: 編集">
    <span class="badge ${badgeClass}" style="font-size:0.65rem; padding:1px 4px; border-radius:3px;">${escHtml(typeLabel)}</span>
    <span class="kw-term-text">${escHtml(kw.term)}</span>
  </span>`;
}

function renderFterms(g) {
  const fterms = g.search_codes && g.search_codes.fterm ? g.search_codes.fterm : [];
  const items = fterms.map(ft =>
    `<span class="fterm-tag" data-gid="${g.group_id}" data-code="${escAttr(ft.code)}"
      title="クリックで残す/外すを切替（赤=残す）">
      ${escHtml(ft.code)} <span class="fterm-desc">${escHtml(ft.desc || '')}</span>
    </span>`
  ).join('');
  return `<div style="margin-top:6px; border-top:1px solid var(--border); padding-top:6px;">
    <span style="font-size:0.75rem; color:var(--text2);">Fterm:</span> ${items}
    <span class="fterm-picker-wrap" style="display:inline-flex; align-items:center; gap:3px; margin-left:4px; position:relative;">
      <input class="fterm-add-input" type="text" placeholder="コード追加..." data-gid="${g.group_id}"
        style="font-size:0.75rem; padding:1px 6px; border:1px solid var(--border); border-radius:4px;
               background:var(--bg); color:var(--text); width:160px;"
        onfocus="openFtermPicker(${g.group_id}, this)"
        oninput="filterFtermPicker(this)"
        onkeydown="if(event.key==='Enter'){addFterm(${g.group_id}, this); closeFtermPicker();}
                   if(event.key==='Escape'){closeFtermPicker();}">
      <button style="background:none; border:1px solid var(--border); color:#60a5fa; cursor:pointer;
                     font-size:0.75rem; padding:1px 6px; border-radius:4px;"
              onclick="addFterm(${g.group_id}, this.previousElementSibling); closeFtermPicker();">+</button>
      <div class="fterm-dropdown" id="fterm-dd-${g.group_id}" style="display:none;"></div>
    </span>
  </div>`;
}

function typeToBadgeClass(t) {
  if (!t) return '';
  if (t.includes('正規表現') || t.includes('カタカナ') || t.includes('漢字')) return 'badge-regex';
  if (t.includes('同義語') || t.includes('辞書') || t.includes('INCI') || t.includes('Fterm')) return 'badge-dict';
  if (t.includes('AI') || t.includes('明細書') || t.includes('関連')) return 'badge-ai';
  if (t.includes('手動') || t.includes('追加')) return 'badge-manual';
  return '';
}

function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function escAttr(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ================================================================
// グループ操作
// ================================================================

async function suggestKeywords() {
  const btn = document.getElementById('btn-suggest');
  const loading = document.getElementById('loading-suggest');
  btn.disabled = true;
  loading.classList.add('show');
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/suggest`, { method: 'POST' });
    if (!res.ok) { const e = await res.json(); throw new Error(e.error || 'エラー'); }
    kwGroups = await res.json();   // サーバー側でreplaceモード: AI結果で全置換（手動追加は保持）
    renderGroups();
    showKwSaveMsg('AI提案で全グループを再生成しました');
  } catch(e) {
    alert('エラー: ' + e.message);
  } finally {
    btn.disabled = false;
    loading.classList.remove('show');
  }
}

// Step 4 Stage 1 の tech_analysis.json を真実の源として、Step 3 のキーワードグループを
// 技術概念単位 (E1/E2/...) に作り直す。
// 既存の手動 KW / Fターム は segment_ids の重なりで新グループへ自動移行する。
async function rebuildGroupsFromTechAnalysis() {
  const ok = confirm(
    'Step 4 Stage 1 の技術構造化 (要素 E1/E2/...) に合わせて Step 3 のグループを作り直します。\n' +
    '\n' +
    '・グループは要素ごとに 1 つずつ作り直されます\n' +
    '・既存の手動追加キーワード/Fターム は segment_ids の重なりで新グループへ自動移行されます\n' +
    '・ハイライト (赤字) 状態は失われる場合があります\n' +
    '\n' +
    'よろしいですか?'
  );
  if (!ok) return;
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/rebuild-from-tech-analysis`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      alert(data.error || '再構築に失敗しました');
      return;
    }
    kwGroups = data.groups || [];
    renderGroups();
    showKwSaveMsg(`Step 4 構造に合わせて ${data.num_groups} グループに再構築しました`);
  } catch (e) {
    alert('エラー: ' + e.message);
  }
}

async function addNewGroup(evt) {
  if (evt) { evt.preventDefault(); evt.stopPropagation(); }
  const label = '新規グループ';
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/group/add`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ label, segment_ids: [] })
    });
    if (!res.ok) throw new Error('追加失敗');
    const d = await res.json();
    kwGroups.push({
      group_id: d.group_id,
      label,
      segment_ids: [],
      keywords: [],
      search_codes: { fterm: [], fi: [] }
    });
    renderGroups();
    // 追加したグループのラベル入力にフォーカス
    setTimeout(() => {
      const el = document.querySelector(`#kw-group-${d.group_id} .kw-label-edit`);
      if (el) { el.focus(); el.select(); }
    }, 100);
    showKwSaveMsg('グループを追加しました');
  } catch(e) { alert(e.message); }
}

async function deleteGroup(gid) {
  if (!confirm(`グループ${gid}を削除しますか？`)) return;
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/group/delete`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid })
    });
    if (!res.ok) throw new Error('削除失敗');
    kwGroups = kwGroups.filter(g => g.group_id !== gid);
    renderGroups();
    showKwSaveMsg('グループを削除しました');
  } catch(e) { alert(e.message); }
}

function clearSelection(gid) {
  const groupEl = document.getElementById('kw-group-' + gid);
  if (!groupEl) return;
  groupEl.querySelectorAll('.kw-tag.kw-marked, .fterm-tag.kw-marked').forEach(el => el.classList.remove('kw-marked'));
  if (typeof _kwMarkSave === 'function') _kwMarkSave();
}

async function deleteUnselected(gid) {
  const groupEl = document.getElementById('kw-group-' + gid);
  if (!groupEl) return;
  const g = kwGroups.find(g => g.group_id === gid);
  if (!g) return;

  // 赤字ハイライト = 残す。ハイライトされていないものを削除対象とする。
  const delKws = Array.from(groupEl.querySelectorAll('.kw-tag:not(.kw-marked)')).map(el => el.dataset.term).filter(Boolean);
  const delFts = Array.from(groupEl.querySelectorAll('.fterm-tag:not(.kw-marked)')).map(el => el.dataset.code).filter(Boolean);

  if (delKws.length === 0 && delFts.length === 0) {
    alert('チェックを外した項目がありません（すべて選択中のため削除対象なし）');
    return;
  }

  const msg = [];
  if (delKws.length > 0) msg.push(`キーワード ${delKws.length}件`);
  if (delFts.length > 0) msg.push(`Fterm ${delFts.length}件`);
  if (!confirm(`${msg.join('、')}を削除しますか？（チェックが入っている項目は残ります）`)) return;

  // キーワード削除
  for (const term of delKws) {
    try {
      await fetch(`/case/${CASE_ID}/keywords/delete`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ group_id: gid, term })
      });
    } catch(e) { console.warn('kw delete failed', term, e); }
  }
  g.keywords = (g.keywords || []).filter(kw => !delKws.includes(kw.term));

  // Fterm削除
  for (const code of delFts) {
    try {
      await fetch(`/case/${CASE_ID}/keywords/fterm/delete`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ group_id: gid, code })
      });
    } catch(e) { console.warn('fterm delete failed', code, e); }
  }
  if (g.search_codes && g.search_codes.fterm) {
    g.search_codes.fterm = g.search_codes.fterm.filter(ft => !delFts.includes(ft.code));
  }

  renderGroups();
  showKwSaveMsg(`${msg.join('、')}を削除しました`);
}

async function renameGroup(gid, newLabel) {
  const g = kwGroups.find(g => g.group_id === gid);
  if (!g || g.label === newLabel) return;
  g.label = newLabel;
  try {
    await fetch(`/case/${CASE_ID}/keywords/group/update`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid, label: newLabel })
    });
  } catch(e) { console.warn('rename failed', e); }
}

async function toggleSegment(gid, segId, btn) {
  const g = kwGroups.find(g => g.group_id === gid);
  if (!g) return;
  if (!g.segment_ids) g.segment_ids = [];
  const idx = g.segment_ids.indexOf(segId);
  if (idx >= 0) { g.segment_ids.splice(idx, 1); btn.classList.remove('selected'); }
  else { g.segment_ids.push(segId); btn.classList.add('selected'); }
  const groupEl = document.getElementById('kw-group-' + gid);
  if (groupEl) {
    const prev = groupEl.querySelector('[style*="line-height:1.6"]');
    if (prev) {
      const newPrev = g.segment_ids.map(sid => {
        const s = SEG_DATA[sid];
        return s
          ? `<span style="color:var(--text2);">[${sid}]</span> ${escHtml(s.text.substring(0,60))}${s.text.length>60?'...':''}`
          : sid;
      }).join('<br>');
      prev.innerHTML = newPrev;
    }
  }
  try {
    await fetch(`/case/${CASE_ID}/keywords/group/update`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid, segment_ids: g.segment_ids })
    });
  } catch(e) { console.warn('segment toggle failed', e); }
}

async function addKeyword(gid, inputEl) {
  const term = (inputEl.value || '').trim();
  if (!term) return;
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/add`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid, term })
    });
    if (!res.ok) throw new Error('追加失敗');
    const g = kwGroups.find(g => g.group_id === gid);
    if (g) {
      const newKw = { term, source: '手動', type: '手動追加' };
      g.keywords.push(newKw);
      const tagsEl = document.querySelector(`.kw-tags[data-group="${gid}"]`);
      if (tagsEl) {
        const form = tagsEl.querySelector('.kw-add-form');
        form.insertAdjacentHTML('beforebegin', renderKwTag(gid, newKw));
      }
    }
    inputEl.value = '';
    showKwToast(`「${term}」を追加しました`);
  } catch(e) { alert(e.message); }
}

async function deleteKeyword(gid, term) {
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/delete`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid, term })
    });
    if (!res.ok) throw new Error('削除失敗');
    const g = kwGroups.find(g => g.group_id === gid);
    if (g) g.keywords = g.keywords.filter(k => k.term !== term);
    const tagsEl = document.querySelector(`.kw-tags[data-group="${gid}"]`);
    if (tagsEl) {
      const tag = tagsEl.querySelector(`[data-term="${CSS.escape(term)}"]`);
      if (tag) tag.remove();
    }
    showKwToast(`「${term}」を削除しました`);
  } catch(e) { alert(e.message); }
}

function startEditKeyword(spanEl) {
  const tag = spanEl.closest('.kw-tag');
  const gid = parseInt(tag.dataset.gid);
  const oldTerm = tag.dataset.term;

  // 既に編集中なら無視
  if (tag.querySelector('.kw-edit-input')) return;

  const input = document.createElement('input');
  input.type = 'text';
  input.value = oldTerm;
  input.className = 'kw-edit-input';
  input.style.cssText = 'font-size:0.8rem; padding:1px 4px; border:1px solid var(--primary); border-radius:4px; background:var(--bg); color:var(--text1); width:' + Math.max(80, oldTerm.length * 12) + 'px; outline:none;';

  input.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); saveEditKeyword(tag, gid, oldTerm, input.value); }
    if (e.key === 'Escape') { e.preventDefault(); cancelEditKeyword(tag, oldTerm); }
  };
  input.onblur = () => {
    // 少し遅延して、blur時に保存（Enterで既に保存されていたらスキップ）
    setTimeout(() => {
      if (tag.querySelector('.kw-edit-input')) {
        saveEditKeyword(tag, gid, oldTerm, input.value);
      }
    }, 150);
  };

  spanEl.replaceWith(input);
  input.focus();
  input.select();
}

function cancelEditKeyword(tag, oldTerm) {
  const input = tag.querySelector('.kw-edit-input');
  if (!input) return;
  const span = document.createElement('span');
  span.className = 'kw-term-text';
  span.setAttribute('ondblclick', 'startEditKeyword(this)');
  span.title = 'ダブルクリックで編集';
  span.style.cursor = 'text';
  span.textContent = oldTerm;
  input.replaceWith(span);
}

async function saveEditKeyword(tag, gid, oldTerm, newTerm) {
  newTerm = (newTerm || '').trim();
  if (!newTerm || newTerm === oldTerm) {
    cancelEditKeyword(tag, oldTerm);
    return;
  }
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/edit`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid, old_term: oldTerm, new_term: newTerm })
    });
    if (!res.ok) throw new Error('修正失敗');

    // ローカルデータ更新
    const g = kwGroups.find(g => g.group_id === gid);
    if (g) {
      const kw = g.keywords.find(k => k.term === oldTerm);
      if (kw) kw.term = newTerm;
    }

    // DOM更新
    tag.dataset.term = newTerm;
    // 削除ボタンのonclickも更新
    const delBtn = tag.querySelector('.kw-del');
    if (delBtn) delBtn.setAttribute('onclick', `deleteKeyword(${gid}, '${escAttr(newTerm)}')`);
    // input → span に戻す
    const input = tag.querySelector('.kw-edit-input');
    if (input) {
      const span = document.createElement('span');
      span.className = 'kw-term-text';
      span.setAttribute('ondblclick', 'startEditKeyword(this)');
      span.title = 'ダブルクリックで編集';
      span.style.cursor = 'text';
      span.textContent = newTerm;
      input.replaceWith(span);
    }

    showKwToast(`「${oldTerm}」→「${newTerm}」に修正しました`);
  } catch(e) {
    alert(e.message);
    cancelEditKeyword(tag, oldTerm);
  }
}

// ================================================================
// Fterm ピッカー（候補ドロップダウン）
// ================================================================
let _ftermCandidates = null;  // キャッシュ
let _ftermPickerGid = null;

async function _loadFtermCandidates() {
  if (_ftermCandidates) return _ftermCandidates;
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/fterm/candidates`);
    if (res.ok) _ftermCandidates = await res.json();
    else _ftermCandidates = [];
  } catch(e) { _ftermCandidates = []; }
  return _ftermCandidates;
}

async function openFtermPicker(gid, inputEl) {
  _ftermPickerGid = gid;
  const candidates = await _loadFtermCandidates();
  const dd = document.getElementById(`fterm-dd-${gid}`);
  if (!dd) return;

  // 既にグループに追加済みのコードを除外
  const g = kwGroups.find(g => g.group_id === gid);
  const existing = new Set();
  if (g && g.search_codes && g.search_codes.fterm) {
    g.search_codes.fterm.forEach(ft => existing.add(ft.code));
  }

  const filtered = candidates.filter(c => !existing.has(c.code));
  _renderFtermDropdown(dd, filtered, gid, '');
  dd.style.display = 'block';

  // 外側クリックで閉じる
  setTimeout(() => {
    document.addEventListener('click', _closeFtermPickerOutside, { once: true, capture: true });
  }, 100);
}

function _closeFtermPickerOutside(e) {
  if (e.target.closest('.fterm-picker-wrap')) {
    // ピッカー内のクリックなら再度リスナー設定
    setTimeout(() => {
      document.addEventListener('click', _closeFtermPickerOutside, { once: true, capture: true });
    }, 100);
    return;
  }
  closeFtermPicker();
}

function closeFtermPicker() {
  document.querySelectorAll('.fterm-dropdown').forEach(dd => dd.style.display = 'none');
  _ftermPickerGid = null;
}

function filterFtermPicker(inputEl) {
  const gid = parseInt(inputEl.dataset.gid);
  const dd = document.getElementById(`fterm-dd-${gid}`);
  if (!dd || !_ftermCandidates) return;

  const q = (inputEl.value || '').trim().toLowerCase();
  const g = kwGroups.find(g => g.group_id === gid);
  const existing = new Set();
  if (g && g.search_codes && g.search_codes.fterm) {
    g.search_codes.fterm.forEach(ft => existing.add(ft.code));
  }

  const filtered = _ftermCandidates.filter(c => {
    if (existing.has(c.code)) return false;
    if (!q) return true;
    return c.code.toLowerCase().includes(q)
      || (c.label || '').toLowerCase().includes(q)
      || ((c.examples || []).join(' ')).toLowerCase().includes(q);
  });

  _renderFtermDropdown(dd, filtered, gid, q);
  dd.style.display = 'block';
}

function _renderFtermDropdown(dd, items, gid, query) {
  if (!items.length) {
    dd.innerHTML = '<div style="padding:8px 10px; color:var(--text2);">候補なし</div>';
    return;
  }

  // ソース別にグループ化（本願分類を先頭に）
  const bySource = {};
  const sourceOrder = ['本願分類', '既存グループ', '辞書'];
  items.forEach(c => {
    const src = c.source || '辞書';
    if (!bySource[src]) bySource[src] = [];
    bySource[src].push(c);
  });

  let html = '';
  for (const src of sourceOrder) {
    const group = bySource[src];
    if (!group || !group.length) continue;
    html += `<div class="fterm-dd-section">${escHtml(src)}（${group.length}件）</div>`;
    const limit = src === '辞書' ? 30 : group.length;  // 辞書は多いので30件まで
    for (let i = 0; i < Math.min(limit, group.length); i++) {
      const c = group[i];
      const examples = (c.examples || []).join(', ');
      html += `<div class="fterm-dd-item" data-code="${escAttr(c.code)}" data-label="${escAttr(c.label || '')}"
        onclick="selectFtermCandidate(${gid}, '${escAttr(c.code)}', '${escAttr(c.label || '')}')">
        <span class="fterm-dd-code">${escHtml(c.code)}</span>
        <span class="fterm-dd-label">${escHtml(c.label || '')}${examples ? `<span class="fterm-dd-examples"> (${escHtml(examples)})</span>` : ''}</span>
        ${c.type ? `<span class="fterm-dd-source">${escHtml(c.type)}</span>` : ''}
      </div>`;
    }
    if (src === '辞書' && group.length > limit) {
      html += `<div style="padding:4px 10px; color:var(--text2); font-size:0.7rem;">...他${group.length - limit}件（入力で絞り込み）</div>`;
    }
  }
  dd.innerHTML = html;
}

async function selectFtermCandidate(gid, code, label) {
  closeFtermPicker();
  // 入力欄にコードを設定してaddFtermを呼ぶ
  const input = document.querySelector(`.fterm-add-input[data-gid="${gid}"]`);
  if (input) input.value = code;

  // descをlabelで設定してサーバーに送信
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/fterm/add`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid, code, desc: label })
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.error || '追加失敗'); }
    const g = kwGroups.find(g => g.group_id === gid);
    if (g) {
      if (!g.search_codes) g.search_codes = {};
      if (!g.search_codes.fterm) g.search_codes.fterm = [];
      g.search_codes.fterm.push({ code, desc: label });
    }
    if (input) input.value = '';
    renderGroups();
    showKwToast(`Fterm「${code} ${label}」を追加しました`);
  } catch(e) { alert(e.message); }
}

// ================================================================
// Fterm 追加・削除
// ================================================================
async function addFterm(gid, inputEl) {
  const code = (inputEl.value || '').trim();
  if (!code) return;
  // 候補リストからlabelを取得
  let desc = '';
  if (_ftermCandidates) {
    const match = _ftermCandidates.find(c => c.code === code);
    if (match) desc = match.label || '';
  }
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/fterm/add`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid, code, desc })
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.error || '追加失敗'); }
    const d = await res.json();
    const g = kwGroups.find(g => g.group_id === gid);
    if (g) {
      if (!g.search_codes) g.search_codes = {};
      if (!g.search_codes.fterm) g.search_codes.fterm = [];
      g.search_codes.fterm.push({ code: d.code, desc: d.desc || '' });
    }
    inputEl.value = '';
    renderGroups();
    showKwToast(`Fterm「${d.code}」を追加しました`);
  } catch(e) { alert(e.message); }
}

async function deleteFterm(gid, code) {
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/fterm/delete`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid, code })
    });
    if (!res.ok) throw new Error('削除失敗');
    const g = kwGroups.find(g => g.group_id === gid);
    if (g && g.search_codes && g.search_codes.fterm) {
      g.search_codes.fterm = g.search_codes.fterm.filter(ft => ft.code !== code);
    }
    renderGroups();
    showKwToast(`Fterm「${code}」を削除しました`);
  } catch(e) { alert(e.message); }
}

// ================================================================
// Fターム候補カタログ (Step 3)
// ================================================================
const FTERM_SOURCE_KEYS = {
  '本願分類': 'classification',
  '既存グループ': 'group',
  '辞書': 'dict',
};
const FTERM_SOURCE_ORDER = ['本願分類', '既存グループ', '辞書'];
const FTERM_DICT_SECTION_LIMIT = 60;

let _ftermCatalogCollapsed = false;

function toggleFtermCatalog() {
  _ftermCatalogCollapsed = !_ftermCatalogCollapsed;
  const body = document.getElementById('fterm-catalog-body');
  const ind = document.getElementById('fterm-collapse-ind');
  if (!body || !ind) return;
  body.classList.toggle('collapsed', _ftermCatalogCollapsed);
  ind.classList.toggle('open', !_ftermCatalogCollapsed);
}

async function refreshFtermCatalog(ev) {
  if (ev) ev.stopPropagation();
  _ftermCandidates = null;
  await renderFtermCatalog();
  showKwToast('Fターム候補を再読み込みしました');
}

function _ftermGroupsByCode() {
  const map = new Map();
  (kwGroups || []).forEach(g => {
    const fts = (g.search_codes && g.search_codes.fterm) ? g.search_codes.fterm : [];
    fts.forEach(ft => {
      if (!ft.code) return;
      if (!map.has(ft.code)) map.set(ft.code, new Set());
      map.get(ft.code).add(g.group_id);
    });
  });
  return map;
}

function _renderFtermCatalogItem(c, usedGroupsByCode) {
  const usedSet = usedGroupsByCode.get(c.code) || new Set();
  const chips = (kwGroups || []).map(g => {
    const color = groupColor(g.group_id);
    const used = usedSet.has(g.group_id);
    const title = used
      ? `グループ${g.group_id} (${g.label}) に追加済み`
      : `グループ${g.group_id} (${g.label}) に追加`;
    const handler = used
      ? ''
      : `onclick="addFtermFromCatalog('${escAttr(c.code)}', ${JSON.stringify(c.label || '').replace(/"/g, '&quot;')}, ${g.group_id})"`;
    return `<span class="fterm-add-chip ${used ? 'already' : ''}"
      style="background:${color};" ${handler} title="${escAttr(title)}">${g.group_id}</span>`;
  }).join('');

  const examples = (c.examples || []).join(', ');
  const typeTag = c.type
    ? `<span class="type-tag type-${escAttr(String(c.type).toLowerCase())}">${escHtml(c.type)}</span>`
    : '';
  const note = c.note
    ? `<span class="note" title="${escAttr(c.note)}">${escHtml(c.note.length > 120 ? c.note.substring(0, 117) + '…' : c.note)}</span>`
    : '';
  const examplesHtml = examples
    ? `<span class="examples">例: ${escHtml(examples)}</span>` : '';

  const haystack = [c.code, c.label || '', c.note || '', examples].join(' ').toLowerCase();

  return `<div class="fterm-cat-item" data-code="${escAttr(c.code)}" data-haystack="${escAttr(haystack)}">
    <div class="fterm-cat-code">${escHtml(c.code)}${typeTag}</div>
    <div class="fterm-cat-desc">
      <span class="label">${escHtml(c.label || '(説明なし)')}</span>
      ${examplesHtml}
      ${note}
    </div>
    <div class="fterm-cat-actions">
      ${chips || '<span style="font-size:0.7rem; color:var(--text2);">グループ未作成</span>'}
    </div>
  </div>`;
}

// テーマコードを Fターム コードから抽出 (例: "4C083AB13" → "4C083" / 短縮形なら "OTHER")
function _ftermThemeOf(code) {
  const m = String(code || '').match(/^(\d{1,2}[A-Z]\d{3})/);
  return m ? m[1] : 'その他';
}

// テーマ表示順 (固定優先 + 残りはコード昇順)
const _FTERM_THEME_PRIORITY = ['4C083', '4H003', '4F100', '3E086'];
function _ftermThemesSorted(themesSet) {
  const all = Array.from(themesSet);
  const prio = _FTERM_THEME_PRIORITY.filter(t => themesSet.has(t));
  const rest = all.filter(t => !prio.includes(t)).sort();
  return prio.concat(rest);
}

// テーマの「人間向けラベル」(可能な範囲で)
const _FTERM_THEME_LABELS = {
  '4C083': '化粧料',
  '4H003': '洗浄性組成物',
  '4F100': '積層体',
  '3E086': '被包体・包装体',
  'その他': '辞書外/不明',
};

let _ftermActiveTheme = null;  // 現在選択中のテーマ (null なら最初の有効テーマ)
let _ftermShowDictCandidates = false;  // 辞書ベース候補も表示するか (デフォルト OFF)

function selectFtermTheme(theme) {
  _ftermActiveTheme = theme;
  renderFtermCatalog();
}

function toggleFtermShowDict(checked) {
  _ftermShowDictCandidates = !!checked;
  renderFtermCatalog();
}

async function renderFtermCatalog() {
  const panel = document.getElementById('fterm-catalog');
  const body = document.getElementById('fterm-catalog-body');
  const countEl = document.getElementById('fterm-catalog-count');
  const hintEl = document.getElementById('fterm-catalog-hint');
  if (!panel || !body) return;

  const candidatesAll = await _loadFtermCandidates();
  if (!candidatesAll || candidatesAll.length === 0) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = '';

  // 「本願に付与されているもの」優先 — 辞書 source は toggle で出す。
  // 本願分類 / 既存グループ は常に表示、辞書は _ftermShowDictCandidates が true のときのみ。
  const candidates = candidatesAll.filter(c => {
    if (_ftermShowDictCandidates) return true;
    return (c.source === '本願分類' || c.source === '既存グループ');
  });

  // 集計表示用 (全数とフィルタ後の件数を両方持つ)
  const totalAll = candidatesAll.length;
  const totalAssigned = candidatesAll.filter(c =>
    c.source === '本願分類' || c.source === '既存グループ').length;
  const totalDict = totalAll - totalAssigned;

  // テーマ別 → src 別に二重グルーピング (フィルタ後)
  const byTheme = {};         // theme → src → [candidates]
  const themeCounts = {};     // theme → total count
  const themeSet = new Set();
  candidates.forEach(c => {
    const theme = _ftermThemeOf(c.code);
    themeSet.add(theme);
    if (!byTheme[theme]) byTheme[theme] = {};
    const src = c.source || '辞書';
    if (!byTheme[theme][src]) byTheme[theme][src] = [];
    byTheme[theme][src].push(c);
    themeCounts[theme] = (themeCounts[theme] || 0) + 1;
  });

  if (countEl) {
    countEl.textContent = _ftermShowDictCandidates
      ? `（全${totalAll}件 / 本願分類+既存 ${totalAssigned} + 辞書 ${totalDict}）`
      : `（本願分類+既存 ${totalAssigned}件 / 辞書 ${totalDict}件は非表示）`;
  }

  // テーマタブ構築
  const themes = _ftermThemesSorted(themeSet);
  if (!themes.includes(_ftermActiveTheme)) _ftermActiveTheme = themes[0] || null;

  const tabsHtml = themes.map(t => {
    const label = _FTERM_THEME_LABELS[t] || '';
    const active = t === _ftermActiveTheme;
    return `<button type="button" class="fterm-theme-tab ${active ? 'active' : ''}"
      onclick="selectFtermTheme('${escAttr(t)}')"
      title="${escAttr(label)}">
      <span class="fterm-theme-code">${escHtml(t)}</span>
      ${label ? `<span class="fterm-theme-label">${escHtml(label)}</span>` : ''}
      <span class="fterm-theme-count">${themeCounts[t]}</span>
    </button>`;
  }).join('');

  if (hintEl) {
    // hint は現在テーマの src サマリ
    const srcOfActive = byTheme[_ftermActiveTheme] || {};
    const srcSummary = FTERM_SOURCE_ORDER
      .filter(s => srcOfActive[s] && srcOfActive[s].length)
      .map(s => `${s} ${srcOfActive[s].length}件`).join(' / ');
    hintEl.textContent = srcSummary;
  }

  const usedByCode = _ftermGroupsByCode();

  // 現在テーマのカードを描画
  const srcOfActive = byTheme[_ftermActiveTheme] || {};
  let cardsHtml = '';
  for (const src of FTERM_SOURCE_ORDER) {
    const list = srcOfActive[src];
    if (!list || !list.length) continue;
    const srcKey = FTERM_SOURCE_KEYS[src] || 'dict';
    const limit = src === '辞書' ? FTERM_DICT_SECTION_LIMIT : list.length;
    const shown = list.slice(0, limit);

    cardsHtml += `<div class="fterm-cat-section">
      <div class="fterm-cat-section-head">
        <span class="src-badge src-${srcKey}">${escHtml(src)}</span>
        <span>${list.length}件${list.length > limit ? `（${limit}件を表示・検索で絞り込み）` : ''}</span>
      </div>`;
    cardsHtml += shown.map(c => _renderFtermCatalogItem(c, usedByCode)).join('');
    cardsHtml += `</div>`;
  }
  if (!cardsHtml) {
    cardsHtml = `<div class="fterm-cat-empty">テーマ ${escHtml(_ftermActiveTheme)} に該当する候補がありません</div>`;
  }

  // 辞書未整備テーマの注意書き
  let warnHtml = '';
  if (_ftermActiveTheme === 'その他') {
    warnHtml = `<div class="fterm-cat-warn">⚠ テーマコード未識別の Fターム コードです。短縮形 (AA01 等) や辞書未登録テーマが混在しています。</div>`;
  } else if (themeCounts[_ftermActiveTheme] && !(byTheme[_ftermActiveTheme] || {})['辞書']) {
    warnHtml = `<div class="fterm-cat-warn">ℹ このテーマの辞書候補が出ていません。本願分類 + 既存グループのみ。</div>`;
  }

  // 辞書 toggle (本願に付与されてない辞書候補もブラウズしたい時用)
  const toggleHtml = `<label class="fterm-dict-toggle" title="辞書全体から候補を出す (本願に付与されていないものを含む)">
    <input type="checkbox" ${_ftermShowDictCandidates ? 'checked' : ''}
      onchange="toggleFtermShowDict(this.checked)">
    <span>辞書候補も表示 (${totalDict})</span>
  </label>`;

  // 本願分類が空 → 注意書き
  const noClassificationWarn = (totalAssigned === 0)
    ? `<div class="fterm-cat-warn">⚠ 本願 PDF からテーマコード/Fターム/FI が抽出できていません。Step 2 のテキスト抽出を確認するか、「辞書候補も表示」で辞書からブラウズしてください。</div>`
    : '';

  body.innerHTML = `
    <div class="fterm-theme-tabs-row">
      <div class="fterm-theme-tabs">${tabsHtml}</div>
      ${toggleHtml}
    </div>
    ${noClassificationWarn}
    ${warnHtml}
    ${cardsHtml}
  `;

  const ind = document.getElementById('fterm-collapse-ind');
  if (ind) ind.classList.toggle('open', !_ftermCatalogCollapsed);
  body.classList.toggle('collapsed', _ftermCatalogCollapsed);

  const searchInput = document.getElementById('fterm-catalog-search');
  if (searchInput && searchInput.value) filterFtermCatalog();
}

function filterFtermCatalog() {
  const input = document.getElementById('fterm-catalog-search');
  const body = document.getElementById('fterm-catalog-body');
  if (!input || !body) return;
  const q = input.value.trim().toLowerCase();
  const items = body.querySelectorAll('.fterm-cat-item');
  let matched = 0;
  items.forEach(el => {
    const hs = el.dataset.haystack || '';
    const hit = !q || hs.includes(q);
    el.classList.toggle('hidden', !hit);
    if (hit) matched++;
  });
  body.querySelectorAll('.fterm-cat-section').forEach(sec => {
    const visible = sec.querySelectorAll('.fterm-cat-item:not(.hidden)').length;
    sec.style.display = visible > 0 ? '' : 'none';
  });
}

async function addFtermFromCatalog(code, label, gid) {
  try {
    const res = await fetch(`/case/${CASE_ID}/keywords/fterm/add`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ group_id: gid, code, desc: label || '' })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '追加失敗');
    const g = kwGroups.find(g => g.group_id === gid);
    if (g) {
      if (!g.search_codes) g.search_codes = {};
      if (!g.search_codes.fterm) g.search_codes.fterm = [];
      g.search_codes.fterm.push({ code: data.code, desc: data.desc || '' });
    }
    renderGroups();
    showKwToast(`Fterm「${data.code}」をグループ${gid}に追加しました`);
  } catch(e) { alert(e.message); }
}

// ================================================================
// コピー機能
// ================================================================
let _copySrcGid = null;

function openCopyModal(srcGid) {
  const srcGroup = kwGroups.find(g => g.group_id === srcGid);
  if (!srcGroup) return;
  _copySrcGid = srcGid;
  document.getElementById('copy-src-label').textContent = srcGroup.label;
  const targetList = document.getElementById('copy-target-list');
  targetList.innerHTML = kwGroups
    .filter(g => g.group_id !== srcGid)
    .map(g => `
      <label style="display:flex; align-items:center; gap:8px; padding:6px 10px;
             background:var(--surface2); border-radius:6px; cursor:pointer;">
        <input type="checkbox" value="${g.group_id}"
               style="width:16px; height:16px; accent-color:var(--accent);">
        <span style="font-size:0.88rem;">グループ${g.group_id}: <strong>${escHtml(g.label)}</strong>
          <span style="color:var(--text2); font-size:0.78rem;"> (${g.keywords.length}語)</span></span>
      </label>`).join('');
  document.getElementById('copy-modal').style.display = 'flex';
}

function closeCopyModal() {
  document.getElementById('copy-modal').style.display = 'none';
  _copySrcGid = null;
}

async function execCopy() {
  if (_copySrcGid === null) return;
  const srcGroup = kwGroups.find(g => g.group_id === _copySrcGid);
  if (!srcGroup) return;
  const checked = [...document.querySelectorAll('#copy-target-list input:checked')];
  if (!checked.length) { alert('コピー先を選択してください'); return; }
  const targetGids = checked.map(c => parseInt(c.value));
  let addedTotal = 0;
  for (const tgid of targetGids) {
    const tg = kwGroups.find(g => g.group_id === tgid);
    if (!tg) continue;
    const existing = new Set(tg.keywords.map(k => k.term));
    const toAdd = srcGroup.keywords.filter(k => !existing.has(k.term));
    for (const kw of toAdd) {
      try {
        await fetch(`/case/${CASE_ID}/keywords/add`, {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ group_id: tgid, term: kw.term })
        });
        tg.keywords.push({ ...kw });
        addedTotal++;
      } catch(e) { console.warn('copy item failed', e); }
    }
  }
  closeCopyModal();
  renderGroups();
  showKwToast(`${addedTotal}語をコピーしました`);
}

// ================================================================
// ユーティリティ
// ================================================================
function showKwSaveMsg(msg) {
  const el = document.getElementById('kw-save-msg');
  el.textContent = msg;
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 3000);
}

function showKwToast(msg) {
  const t = document.createElement('div');
  t.className = 'kw-toast';
  t.textContent = msg;
  t.style.cssText = 'position:fixed; bottom:20px; right:20px; background:#14532d; color:#4ade80; padding:8px 16px; border-radius:8px; font-size:0.85rem; z-index:10000;';
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2500);
}

document.addEventListener('DOMContentLoaded', () => {
  renderGroups();  // renderGroups() 内で renderFtermCatalog() も呼ばれる

  // イベントデリゲーション: キーワードタグの編集・選択
  const kwContainer = document.getElementById('kw-groups-container');
  if (kwContainer) {
    // クリックで選択（赤字）トグル。dblclick が来れば取り消して編集モードに入る。
    let _kwClickTimer = null;
    kwContainer.addEventListener('click', (e) => {
      // 編集中の input や追加用 input は無視
      if (e.target.closest('input, button')) return;
      const tag = e.target.closest('.kw-tag, .fterm-tag');
      if (!tag) return;
      e.preventDefault();
      if (_kwClickTimer) clearTimeout(_kwClickTimer);
      _kwClickTimer = setTimeout(() => {
        tag.classList.toggle('kw-marked');
        _kwClickTimer = null;
        _kwMarkSave();
      }, 220);
    });
    kwContainer.addEventListener('dblclick', (e) => {
      if (_kwClickTimer) { clearTimeout(_kwClickTimer); _kwClickTimer = null; }
      const termSpan = e.target.closest('.kw-term-text');
      if (termSpan) { startEditKeyword(termSpan); }
    });
  }
});

// ===== 対比（複数文献対応） =====
function getSelectedCitationIds() {
  return Array.from(document.querySelectorAll('.cit-checkbox:checked')).map(cb => cb.value);
}
function toggleAllCitations(checked) {
  document.querySelectorAll('.cit-checkbox').forEach(cb => cb.checked = checked);
  _updateStep5SelectedCount();
}

// Step 4/5 文献バッジ（回答済 / X / Y / A）を /response/<id> から拾い直して再描画。
// パース直後や Step を開いたときに stale な表示を更新するためのもの。
async function refreshCitationBadges() {
  const ids = (window.CASE_BOOTSTRAP && window.CASE_BOOTSTRAP.cit_ids) || [];
  if (ids.length === 0) return;
  for (const cid of ids) {
    let respData = null;
    let hasResp = false;
    try {
      const r = await fetch('/case/' + encodeURIComponent(CASE_ID) +
                            '/response/' + encodeURIComponent(cid));
      if (r.ok) {
        respData = await r.json();
        hasResp = true;
      }
    } catch (e) { /* 404 は通常ケース（未回答） */ }
    const cat = (() => {
      if (!respData) return '';
      const s = String(respData.category_suggestion || '').trim().toUpperCase();
      if (s.startsWith('X')) return 'X';
      if (s.startsWith('Y')) return 'Y';
      if (s.startsWith('A')) return 'A';
      return '';
    })();
    _setCitBadgesHtml(cid, hasResp, cat);
    _updateBootstrapCategory(cid, cat);
  }
  // 既に開いている Step 6 対比表のヘッダも巻き直し
  if (typeof renderCompSummaryTable === 'function' && _compSummaryData) {
    renderCompSummaryTable();
  }
}

function _setCitBadgesHtml(citId, hasResp, cat) {
  const respHtml = hasResp ? '<span class="badge badge-green">回答済</span>' : '';
  let catHtml = '';
  if (cat === 'X') catHtml = '<span class="badge" style="background:#7f1d1d; color:#fca5a5;">X</span>';
  else if (cat === 'Y') catHtml = '<span class="badge" style="background:#422006; color:#fbbf24;">Y</span>';
  else if (cat === 'A') catHtml = '<span class="badge" style="background:var(--surface2); color:var(--text2);">A</span>';
  // Step 5: .step5-cit-row の .step5-cit-badges
  document.querySelectorAll('.step5-cit-row .cit-checkbox').forEach((cb) => {
    if (cb.value !== citId) return;
    const row = cb.closest('.step5-cit-row');
    const badges = row && row.querySelector('.step5-cit-badges');
    if (badges) badges.innerHTML = respHtml + catHtml;
  });
  // Step 4: cit-card-{citId} の .cit-info p 内のバッジ
  const card = document.getElementById('cit-card-' + citId);
  if (card) {
    const info = card.querySelector('.cit-info p');
    if (info) {
      // role テキストを残し、バッジ部分だけ差し替える: 既存 .badge を全削除して末尾に追加
      info.querySelectorAll('.badge').forEach((b) => b.remove());
      if (respHtml || catHtml) {
        info.insertAdjacentHTML('beforeend', ' ' + respHtml + catHtml);
      }
    }
  }
}

function _updateBootstrapCategory(citId, cat) {
  const list = (window.CASE_BOOTSTRAP && window.CASE_BOOTSTRAP.citations_meta) || [];
  for (const m of list) {
    if (m && String(m.id) === String(citId)) {
      m.category = cat;
      break;
    }
  }
}
function _updateStep5SelectedCount() {
  const el = document.getElementById('step5-selected-count');
  if (!el) return;
  const all = document.querySelectorAll('.cit-checkbox');
  const sel = document.querySelectorAll('.cit-checkbox:checked');
  el.textContent = `${sel.length} / ${all.length} 件選択中`;
}
document.addEventListener('DOMContentLoaded', () => {
  _updateStep5SelectedCount();
  document.querySelectorAll('.cit-checkbox').forEach(cb => {
    cb.addEventListener('change', _updateStep5SelectedCount);
  });
});

async function generatePrompt() {
  const citIds = getSelectedCitationIds();
  if (citIds.length === 0) { alert('対象文献を1つ以上選択してください'); return; }

  try {
    const resp = await fetch(`/case/${CASE_ID}/prompt`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ citation_ids: citIds })
    });
    const data = await resp.json();
    if (data.error) { alert(data.error); return; }
    document.getElementById('prompt-text').textContent = data.prompt;
    document.getElementById('prompt-text').style.display = 'block';
    const label = data.num_citations > 1 ? `${data.num_citations}件の文献` : '1件の文献';
    document.getElementById('prompt-charcount').textContent =
      `${data.char_count.toLocaleString()} 文字 (${label})`;
  } catch(e) {
    alert('エラー: ' + e.message);
  }
}

function copyPrompt() {
  const text = document.getElementById('prompt-text').textContent;
  if (!text) { alert('先にプロンプトを生成してください'); return; }
  navigator.clipboard.writeText(text).then(() => {
    alert('クリップボードにコピーしました。Claudeチャットに貼り付けてください。');
  });
}

async function parseResponse() {
  const text = document.getElementById('response-text').value;
  if (!text.trim()) { alert('回答を貼り付けてください'); return; }

  const loading = document.getElementById('loading-parse');
  loading.classList.add('show');

  try {
    // 複数文献対応エンドポイントを使用
    const resp = await fetch(`/case/${CASE_ID}/response`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: text })
    });
    const data = await resp.json();
    loading.classList.remove('show');

    const result = document.getElementById('parse-result');
    result.innerHTML = '';

    if (data.errors && data.errors.length > 0) {
      result.innerHTML += `<div style="padding:1rem; background:#422006; border-radius:8px; color:#fbbf24; margin-bottom:0.5rem;">
        <strong>警告:</strong><br>${data.errors.join('<br>')}
      </div>`;
    }
    if (data.success) {
      const docs = data.saved_docs || [];
      result.innerHTML += `<div style="padding:1rem; background:#14532d; border-radius:8px; color:#4ade80;">
        パース成功! ${data.num_docs}件の文献の対比結果を保存しました。<br>
        保存先: ${docs.join(', ')}
      </div>`;
      loadComparisonSummary();
      refreshCitationBadges();
    } else if (!data.errors || data.errors.length === 0) {
      result.innerHTML = `<div style="padding:1rem; background:#450a0a; border-radius:8px; color:#fca5a5;">
        パース失敗: JSONを抽出できませんでした。
      </div>`;
    }
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

// ===== 対比サマリ読み込み & 表描画（列表示/詳細・充足切替） =====
let _compSummaryData = null;
let _compSummaryWired = false;

function _compMetaForCit(citId) {
  const list = (window.CASE_BOOTSTRAP && window.CASE_BOOTSTRAP.citations_meta) || [];
  const s = String(citId);
  return list.find((x) => x != null && String(x.id) === s) || { id: citId, label: citId, category: '' };
}

function _compCategoryForCit(citId, resp) {
  const m = _compMetaForCit(citId);
  if (m.category) {
    const t = String(m.category).trim().toUpperCase();
    if (t.startsWith('X')) return 'X';
    if (t.startsWith('Y')) return 'Y';
    if (t.startsWith('A')) return 'A';
  }
  const s = (resp && resp.category_suggestion) ? String(resp.category_suggestion) : '';
  const u = s.trim().toUpperCase();
  if (u.indexOf('X') === 0) return 'X';
  if (u.indexOf('Y') === 0) return 'Y';
  if (u.indexOf('A') === 0) return 'A';
  if (s.includes('X') || s.includes('x')) return 'X';
  if (s.includes('Y') || s.includes('y')) return 'Y';
  if (s.includes('A') && !/AB|AR/i.test(s)) return 'A';
  return '';
}

function _compCategoryBadgeHtml(cat) {
  if (cat === 'X') return '<span class="comp-cat comp-cat-x" title="X 引例">X</span>';
  if (cat === 'Y') return '<span class="comp-cat comp-cat-y" title="Y 引例">Y</span>';
  if (cat === 'A') return '<span class="comp-cat comp-cat-a" title="A 文献">A</span>';
  return '<span class="comp-cat comp-cat-na" title="分類未設定">—</span>';
}

function _compJClass(j) {
  if (j === '○') return 'j-ok';
  if (j === '△') return 'j-partial';
  if (j === '×') return 'j-ng';
  return '';
}

// === 対比表ペースト用クリップボード書き出し ===
// 1 行 = 1 分節。請求項1 (1a/1b/1c...) は連続、請求項 2 以降は前に空行 1 つ。
// セル内容: [判定prefix(?|x|空)][cited_location 記法] (/comment があれば末尾)
//   判定: ○→prefix なし、△→"?"、×→"x"
//   comment: cited_location 内の `"..."` 部分。空なら judgment_reason の短縮版でフォールバック。
function _stripCommentMemoFromLoc(raw) {
  if (!raw) return '';
  // ; 区切りでトークンを取り直し、" や // で始まるものを除外
  return raw.split(';')
    .map((s) => s.trim())
    .filter((s) => s && !s.startsWith('"') && !s.startsWith('//'))
    .map((s) => {
      // トークン内の " や // 以降を切り捨て
      const cIdx = s.indexOf('"');
      const mIdx = s.indexOf('//');
      const cuts = [cIdx, mIdx].filter((i) => i >= 0);
      if (cuts.length) return s.substring(0, Math.min(...cuts)).trim();
      return s;
    })
    .filter((s) => s)
    .join(';');
}

function _shortReason(s) {
  // judgment_reason を最初の句点までで切る。途中切り捨て (…) はしない。
  // LLM プロンプトで「相違点を 1 文で簡潔に」と指示している前提。
  if (!s) return '';
  let t = String(s).replace(/[\r\n]+/g, ' ').replace(/\s{2,}/g, ' ').trim();
  const dot = t.search(/[。．]/);
  if (dot > 0) t = t.substring(0, dot);
  return t;
}

function _formatCompForPaste(comp) {
  if (!comp) return '';
  const j = (comp.judgment || '').trim();
  let prefix = '';
  if (j === '△') prefix = '?';
  else if (j === '×') prefix = '!';  // 該当箇所なしは !
  // ○ や空は prefix なし

  const raw = comp.cited_location || '';
  const locOnly = _stripCommentMemoFromLoc(raw);
  // コメントは raw の "..." を最優先、なければ judgment_reason をフォールバック (△/× 時のみ)
  let comment = comp.cited_location_comment || '';
  if (!comment && (j === '△' || j === '×') && comp.judgment_reason) {
    comment = _shortReason(comp.judgment_reason);
  }

  let out = prefix + locOnly;
  if (comment) out += '/' + comment.replace(/[\r\n\t]+/g, ' ').trim();
  return out;
}

function _buildClipboardForCit(d, citId) {
  const resp = d.responses && d.responses[citId];
  if (!resp) return '';
  const lines = [];
  // 請求項1 の分節 (1a, 1b, 1c, ...) - 空行なし
  for (const segId of (d.segIds || [])) {
    const comp = (resp.comparisons || []).find((c) => c.requirement_id === segId);
    lines.push(_formatCompForPaste(comp));
  }
  // 従属請求項 (請求項2 以降) - 各々の前に空行 1 つ
  // master 側 (d.subClaims) の順序でループし、response 側 (resp.sub_claims) を引き当てる
  // response が欠落している従属請求項は空文字 (空のセル) を出す
  const masterSubs = (d.subClaims || []).slice().sort(
    (a, b) => (a.claim_number || 0) - (b.claim_number || 0)
  );
  for (const claim of masterSubs) {
    const sub = (resp.sub_claims || []).find((sc) => sc.claim_number === claim.claim_number);
    lines.push('');
    lines.push(_formatCompForPaste(sub));
  }
  return lines.join('\n');
}

async function copyCitClipboard(citId, btn) {
  const d = _compSummaryData;
  if (!d) {
    alert('対比表データが未取得です。読み込んでから実行してください。');
    return;
  }
  const text = _buildClipboardForCit(d, citId);
  if (!text) {
    alert('コピーする対比結果がありません: ' + citId);
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = '✓ コピー済';
      btn.classList.add('comp-copy-done');
      setTimeout(() => {
        btn.textContent = orig;
        btn.classList.remove('comp-copy-done');
      }, 1400);
    }
  } catch (e) {
    // フォールバック: textarea 経由
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e2) { /* */ }
    document.body.removeChild(ta);
    if (btn) {
      btn.textContent = '✓ コピー済';
      setTimeout(() => { btn.textContent = '📋 コピー'; }, 1400);
    }
  }
}

// 判定セル表示用: ○ は「先頭に何もつけない」慣行 → 空表示
// raw な judgment は class/色判定に使い、表示は judgment_display を優先
function _compJDisp(comp) {
  if (!comp) return '—';
  if (typeof comp.judgment_display === 'string') return comp.judgment_display;
  const j = comp.judgment || '';
  if (j === '○' || j === 'o' || j === 'O') return '';
  return j;
}

// cited_location 表示: サーバ展開済み (cited_location_expanded) を優先、無ければ raw
// 備考 (cited_location_comment) があれば併記
function _compLocHtml(comp, citId) {
  if (!comp) return '';
  const loc = (typeof comp.cited_location_expanded === 'string' && comp.cited_location_expanded)
    ? comp.cited_location_expanded
    : (comp.cited_location || '');
  if (!loc) return '';
  let html = '<br><span class="comp-cite-loc">📍 ' + _compLinkifyParaRefs(_escapeHtml(loc), citId) + '</span>';
  const cmt = comp.cited_location_comment || '';
  if (cmt) {
    html += '<br><span class="comp-cite-comment">📝 ' + _escapeHtml(cmt) + '</span>';
  }
  return html;
}

function wireCompSummaryIfNeeded() {
  if (_compSummaryWired) return;
  const root = document.getElementById('comparison-summary');
  if (!root) return;
  _compSummaryWired = true;
  root.addEventListener('change', (e) => {
    const t = e.target;
    if (t && ((t.classList && t.classList.contains('comp-col-toggle')) || t.name === 'comp-view')) {
      renderCompSummaryTable();
    }
  });
  root.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target.closest('.comp-colbar-quick-btn') : null;
    if (!t) return;
    e.preventDefault();
    _compColbarApplyQuickSelect(t.getAttribute('data-quick') || '');
  });
}

// 対比サマリ表の外（引例テキスト全文エリアなど）からの段落リンクも拾う
(function _wireGlobalParaRef() {
  if (typeof document === 'undefined') return;
  document.addEventListener('click', (e) => {
    const ref = e.target && e.target.closest ? e.target.closest('.comp-para-ref') : null;
    if (!ref) return;
    e.preventDefault();
    e.stopPropagation();
    _compShowParagraphPopup(
      ref.getAttribute('data-cit-id') || '',
      ref.getAttribute('data-para-id') || '',
      ref,
    );
  }, true);
})();

// 判定理由テキスト内の 【0053】 / 【００５３】 を段落リンクに変換。
// 入力は _escapeHtml 済みテキストを想定。
function _compLinkifyParaRefs(escaped, citId) {
  if (!escaped || !citId) return escaped || '';
  const cidAttr = _escapeHtml(citId);
  return escaped.replace(/【([０-９0-9]{1,6})】/g, (_m, num) => {
    const norm = String(num).replace(/[０-９]/g, (c) =>
      String.fromCharCode(c.charCodeAt(0) - 0xFF10 + 0x30));
    return '<a href="#" class="comp-para-ref" data-cit-id="' + cidAttr +
      '" data-para-id="' + norm + '" title="段落 【' + norm + '】 を表示">【' +
      num + '】</a>';
  });
}

async function _compShowParagraphPopup(citId, paraId, anchorEl) {
  _compClosePopup();
  if (!citId || !paraId) return;
  const pop = document.createElement('div');
  pop.id = 'comp-para-popup';
  pop.className = 'comp-para-popup';
  pop.innerHTML =
    '<div class="comp-para-popup-hdr">' +
      '<span class="comp-para-popup-title">' + _escapeHtml(citId) +
        ' <span class="comp-para-popup-pid">【' + _escapeHtml(paraId) + '】</span></span>' +
      '<button class="comp-para-popup-close" type="button" title="閉じる">×</button>' +
    '</div>' +
    '<div class="comp-para-popup-body">' +
      '<div class="comp-para-popup-loading">読み込み中...</div>' +
    '</div>';
  document.body.appendChild(pop);
  _compPositionPopup(pop, anchorEl);
  pop.querySelector('.comp-para-popup-close').addEventListener('click', _compClosePopup);
  setTimeout(() => { document.addEventListener('click', _compPopupOutsideClick, true); }, 0);

  const body = pop.querySelector('.comp-para-popup-body');
  try {
    const url = '/case/' + encodeURIComponent(CASE_ID) +
      '/citation/' + encodeURIComponent(citId) +
      '/paragraph/' + encodeURIComponent(paraId);
    const resp = await fetch(url);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      body.innerHTML = '<div class="comp-para-popup-err">' +
        _escapeHtml(err.error || ('HTTP ' + resp.status)) + '</div>';
      return;
    }
    const data = await resp.json();
    let html = '';
    if (data.section || data.page) {
      html += '<div class="comp-para-popup-sec">' +
        _escapeHtml(String(data.section || '')) +
        (data.page ? ' <span class="comp-para-popup-page">p.' + _escapeHtml(String(data.page)) + '</span>' : '') +
        '</div>';
    }
    html += '<div class="comp-para-popup-text">' + _escapeHtml(String(data.text || '')) + '</div>';
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = '<div class="comp-para-popup-err">読み込み失敗: ' +
      _escapeHtml(String(e && e.message ? e.message : e)) + '</div>';
  }
}

function _compPositionPopup(pop, anchorEl) {
  pop.style.position = 'fixed';
  pop.style.zIndex = '10050';
  const maxW = Math.min(560, Math.round(window.innerWidth * 0.9));
  const maxH = 340;
  pop.style.maxWidth = maxW + 'px';
  pop.style.maxHeight = maxH + 'px';
  const rect = anchorEl && anchorEl.getBoundingClientRect
    ? anchorEl.getBoundingClientRect()
    : { top: 100, bottom: 120, left: 100, right: 200 };
  let top = rect.bottom + 6;
  let left = rect.left;
  if (top + maxH > window.innerHeight - 8) {
    top = Math.max(8, rect.top - maxH - 6);
  }
  if (left + maxW > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - maxW - 8);
  }
  pop.style.top = top + 'px';
  pop.style.left = left + 'px';
}

function _compPopupOutsideClick(e) {
  const pop = document.getElementById('comp-para-popup');
  if (!pop) return;
  if (pop.contains(e.target)) return;
  if (e.target && e.target.closest && e.target.closest('.comp-para-ref')) return;
  _compClosePopup();
}

function _compClosePopup() {
  const pop = document.getElementById('comp-para-popup');
  if (pop) pop.remove();
  document.removeEventListener('click', _compPopupOutsideClick, true);
}

function _compColbarApplyQuickSelect(action) {
  const d = _compSummaryData;
  if (!d) return;
  const colbar = document.getElementById('comp-summary-colbar');
  if (!colbar) return;
  const boxes = Array.from(colbar.querySelectorAll('.comp-col-toggle'));

  if (action === 'ALL' || action === 'NONE') {
    const on = (action === 'ALL');
    boxes.forEach((cb) => { cb.checked = on; });
    renderCompSummaryTable();
    return;
  }

  // X / Y / A: そのカテゴリだけトグル（他は触らない）。
  // 一つでも OFF があれば全 ON、全部 ON なら全 OFF にする。
  const matching = boxes.filter((cb) => {
    const cid = cb.getAttribute('data-cit-id');
    return _compCategoryForCit(cid, d.responses[cid]) === action;
  });
  if (matching.length === 0) return;
  const anyOff = matching.some((cb) => !cb.checked);
  matching.forEach((cb) => { cb.checked = anyOff; });
  renderCompSummaryTable();
}

function _buildColbarHtml(d, visMap) {
  let cb = '<div class="comp-colbar-row1">' +
    '<span class="comp-colbar-lbl">表示する列</span>' +
    '<div class="comp-colbar-quick" role="group" aria-label="カテゴリ別トグル">' +
      '<button type="button" class="comp-colbar-quick-btn comp-colbar-quick-x" data-quick="X" title="X 引例の表示 ON/OFF">X</button>' +
      '<button type="button" class="comp-colbar-quick-btn comp-colbar-quick-y" data-quick="Y" title="Y 引例の表示 ON/OFF">Y</button>' +
      '<button type="button" class="comp-colbar-quick-btn comp-colbar-quick-a" data-quick="A" title="A 文献の表示 ON/OFF">A</button>' +
      '<button type="button" class="comp-colbar-quick-btn" data-quick="ALL" title="全文献を表示">全選択</button>' +
      '<button type="button" class="comp-colbar-quick-btn comp-colbar-quick-clear" data-quick="NONE" title="すべてのチェックを外す">All clear</button>' +
    '</div>' +
    '</div>' +
    '<div class="comp-colbar-chips">';
  for (const cid of d.citIds) {
    const meta = _compMetaForCit(cid);
    const cat = _compCategoryForCit(cid, d.responses[cid]);
    const ch = (visMap[cid] !== false) ? ' checked' : '';
    cb += '<label class="comp-col-chip">' +
      '<input type="checkbox" class="comp-col-toggle" data-cit-id="' + _escapeHtml(cid) + '"' + ch + ' title="表にこの文献列を含める">' +
      '<span class="comp-col-chip-name">' + _escapeHtml(meta.label || cid) + '</span>' +
      '<span class="comp-cat comp-cat-' + (cat === 'X' ? 'x' : cat === 'Y' ? 'y' : cat === 'A' ? 'a' : 'na') + ' comp-cat-inline">' + (cat || '—') + '</span></label>';
  }
  cb += '</div>';
  return cb;
}

function renderCompSummaryTable() {
  const tbody = document.getElementById('summary-tbody');
  const thead = document.getElementById('comp-summary-thead');
  const colbar = document.getElementById('comp-summary-colbar');
  if (!tbody) return;
  wireCompSummaryIfNeeded();

  const d = _compSummaryData;
  if (!d) {
    tbody.innerHTML = '<tr><td class="comp-loading-cell" colspan="2" style="text-align:center; color:var(--text2);">読み込み中...</td></tr>';
    if (thead) thead.innerHTML = '';
    if (colbar) colbar.innerHTML = '';
    return;
  }

  const citIds = d.citIds;
  if (citIds.length === 0) {
    tbody.innerHTML = '<tr><td class="comp-loading-cell" colspan="2">—</td></tr>';
    return;
  }

  if (!d.hasAny) {
    tbody.innerHTML = '<tr><td class="comp-loading-cell" colspan="' + (citIds.length + 1) + '" style="text-align:center; color:var(--text2);">Step 5で回答を取り込むと、ここに対比サマリが表示されます。</td></tr>';
    if (thead) thead.innerHTML = '';
    if (colbar) colbar.innerHTML = '';
    return;
  }
  if (!d.claim1) {
    tbody.innerHTML = '<tr><td class="comp-loading-cell" colspan="2">分節データを確認してください。</td></tr>';
    if (thead) thead.innerHTML = '';
    if (colbar) colbar.innerHTML = '';
    return;
  }

  const visMap = {};
  citIds.forEach((id) => { visMap[id] = true; });
  if (colbar) {
    colbar.querySelectorAll('.comp-col-toggle').forEach((el) => {
      const id = el.getAttribute('data-cit-id');
      if (id) visMap[id] = el.checked;
    });
  }
  const activeCits = citIds.filter((id) => visMap[id] !== false);
  if (activeCits.length === 0) {
    tbody.innerHTML = '<tr><td class="comp-loading-cell" colspan="2" style="text-align:center; color:var(--text2);">少なくとも1件の「表示する列」にチェックを入れてください。</td></tr>';
    if (thead) thead.innerHTML = '';
    if (colbar) colbar.innerHTML = _buildColbarHtml(d, visMap);
    return;
  }

  if (colbar) colbar.innerHTML = _buildColbarHtml(d, visMap);

  const view = (document.querySelector('#comparison-summary input[name="comp-view"]:checked') || {}).value || 'detail';
  const isCompact = view === 'compact';
  const spanAll = activeCits.length + 1;

  let h = '<tr><th class="comp-th-req" scope="col">構成要件</th>';
  for (const citId of activeCits) {
    const meta = _compMetaForCit(citId);
    const cat = _compCategoryForCit(citId, d.responses[citId]);
    const safeCitId = String(citId).replace(/'/g, "\\'");
    h += '<th class="comp-th-cit" scope="col" data-cit-th="' + _escapeHtml(citId) + '">' +
      '<div class="comp-th-cit-line1">' + _escapeHtml(meta.label || citId) + '</div>' +
      '<div class="comp-th-cat">' + _compCategoryBadgeHtml(cat) + '</div>' +
      '<button type="button" class="comp-copy-btn" title="該当箇所/コメントを対比表ペースト用にクリップボードへコピー" ' +
      'onclick="copyCitClipboard(\'' + safeCitId + '\', this); event.stopPropagation();">📋 コピー</button>' +
      '</th>';
  }
  h += '</tr>';
  if (thead) thead.innerHTML = h;

  const segIds = d.segIds;
  const segTextMap = d.segTextMap || {};
  let body = '';
  for (const segId of segIds) {
    const segText = segTextMap[segId] || '';
    const reqCellHtml = '<span class="comp-req-id">' + _escapeHtml(segId) + '</span>' +
      (segText ? '<span class="comp-req-text">' + _escapeHtml(segText) + '</span>' : '');
    if (isCompact) {
      body += '<tr>';
      body += '<td class="comp-td-req">' + reqCellHtml + '</td>';
      for (const citId of activeCits) {
        const resp = d.responses[citId];
        const comp = resp && resp.comparisons && resp.comparisons.find((c) => c.requirement_id === segId);
        const jRaw = (comp && comp.judgment) ? String(comp.judgment) : '';
        const jDisp = comp ? _compJDisp(comp) : '—';
        body += '<td class="comp-td-j ' + _compJClass(jRaw) + ' comp-td-zen">' + _escapeHtml(jDisp) + '</td>';
      }
      body += '</tr>';
    } else {
      body += '<tr>';
      body += '<td class="comp-td-req" rowspan="2" style="vertical-align:top;">' + reqCellHtml + '</td>';
      for (const citId of activeCits) {
        const resp = d.responses[citId];
        const comp = resp && resp.comparisons && resp.comparisons.find((c) => c.requirement_id === segId);
        const jRaw = (comp && comp.judgment) ? String(comp.judgment) : '';
        const jDisp = comp ? _compJDisp(comp) : '—';
        body += '<td class="comp-td-j ' + _compJClass(jRaw) + '">' + _escapeHtml(jDisp) + '</td>';
      }
      body += '</tr><tr>';
      for (const citId of activeCits) {
        const resp = d.responses[citId];
        const comp = resp && resp.comparisons && resp.comparisons.find((c) => c.requirement_id === segId);
        if (comp) {
          const reason = _compLinkifyParaRefs(_escapeHtml(comp.judgment_reason || ''), citId);
          const loc = _compLocHtml(comp, citId);
          body += '<td class="comp-td-reason">' + reason + loc + '</td>';
        } else {
          body += '<td class="comp-td-reason" style="color:var(--text2);">—</td>';
        }
      }
      body += '</tr>';
    }
  }

  const subClaims = d.subClaims;
  if (subClaims && subClaims.length > 0) {
    body += '<tr><th class="comp-section-h" colspan="' + spanAll + '" style="text-align:left; padding-top:0.8rem;">従属請求項</th></tr>';
    for (const claim of subClaims) {
      const claimText = claim.full_text || (Array.isArray(claim.segments) ? claim.segments.map((s) => s.text || '').join('') : '');
      const subReqHtml = '<span class="comp-req-id">請求項' + String(claim.claim_number) + '</span>' +
        (claimText ? '<span class="comp-req-text">' + _escapeHtml(claimText) + '</span>' : '');
      if (isCompact) {
        body += '<tr><td class="comp-td-req">' + subReqHtml + '</td>';
        for (const citId of activeCits) {
          const resp = d.responses[citId];
          const sub = resp && resp.sub_claims && resp.sub_claims.find((sc) => sc.claim_number === claim.claim_number);
          const jRaw = (sub && sub.judgment) ? String(sub.judgment) : '';
          const jDisp = sub ? _compJDisp(sub) : '—';
          body += '<td class="comp-td-j ' + _compJClass(jRaw) + ' comp-td-zen">' + _escapeHtml(jDisp) + '</td>';
        }
        body += '</tr>';
      } else {
        body += '<tr><td class="comp-td-req" style="vertical-align:top;">' + subReqHtml + '</td>';
        for (const citId of activeCits) {
          const resp = d.responses[citId];
          const sub = resp && resp.sub_claims && resp.sub_claims.find((sc) => sc.claim_number === claim.claim_number);
          if (sub) {
            const jRaw = (sub.judgment) ? String(sub.judgment) : '';
            const jDisp = _compJDisp(sub);
            const jr = _compLinkifyParaRefs(_escapeHtml(sub.judgment_reason || ''), citId);
            const loc = _compLocHtml(sub, citId);
            body += '<td class="comp-td-j ' + _compJClass(jRaw) + '" style="font-size:0.85rem;">' + _escapeHtml(jDisp) + ' <span class="comp-sub-rationale">' + jr + loc + '</span></td>';
          } else {
            body += '<td style="color:var(--text2);">—</td>';
          }
        }
        body += '</tr>';
      }
    }
  }

  body += '<tr><th class="comp-section-h" colspan="' + spanAll + '" style="text-align:left; padding-top:0.8rem;">文献サマリ</th></tr>';
  body += '<tr><td class="comp-td-req" style="font-weight:700;">総合評価</td>';
  for (const citId of activeCits) {
    const resp = d.responses[citId];
    if (isCompact) {
      if (resp) {
        const cat = _escapeHtml(String(resp.category_suggestion || ''));
        body += '<td class="comp-td-sum-compact"><div class="comp-sum-catline">' + cat + '</div></td>';
      } else {
        body += '<td class="comp-td-sum" style="color:var(--text2);">未回答</td>';
      }
    } else {
      if (resp) {
        const cat = _escapeHtml(String(resp.category_suggestion || ''));
        const summary = _escapeHtml(String(resp.overall_summary || ''));
        body += '<td class="comp-td-sum"><strong>' + cat + '</strong><br><span class="comp-sum-text">' + summary + '</span></td>';
      } else {
        body += '<td class="comp-td-sum" style="color:var(--text2);">未回答</td>';
      }
    }
  }
  body += '</tr>';
  tbody.innerHTML = body;
}

function _setCompSummaryLoadError(msg) {
  const tbody = document.getElementById('summary-tbody');
  const thead = document.getElementById('comp-summary-thead');
  const colbar = document.getElementById('comp-summary-colbar');
  _compSummaryData = null;
  if (tbody) {
    tbody.innerHTML = '<tr><td class="comp-loading-cell" colspan="2" style="text-align:left; color:#f87171; padding:0.75rem;">' +
      _escapeHtml(msg) + '</td></tr>';
  }
  if (thead) thead.innerHTML = '';
  if (colbar) colbar.innerHTML = '';
}

async function loadComparisonSummary() {
  wireCompSummaryIfNeeded();
  const tbody = document.getElementById('summary-tbody');
  if (!tbody) return;

  const citIds = (window.CASE_BOOTSTRAP && window.CASE_BOOTSTRAP.cit_ids) || [];
  if (citIds.length === 0) {
    _compSummaryData = null;
    tbody.innerHTML = '<tr><td class="comp-loading-cell" colspan="2" style="text-align:center; color:var(--text2);">引用文献がありません。</td></tr>';
    const thead = document.getElementById('comp-summary-thead');
    const colbar = document.getElementById('comp-summary-colbar');
    if (thead) thead.innerHTML = '';
    if (colbar) colbar.innerHTML = '';
    return;
  }

  try {
    const segResp = await fetch('/case/' + CASE_ID + '/segments');
    if (!segResp.ok) {
      let detail = '分節データを取得できませんでした。';
      try {
        const err = await segResp.json();
        if (err && err.error) detail = String(err.error);
      } catch (e) { /* */ }
      _setCompSummaryLoadError('対比表: ' + detail + ' (HTTP ' + segResp.status + ')');
      return;
    }
    const segs = await segResp.json();

    const claim1 = segs.find((c) => c.claim_number === 1);
    const segIds = claim1 ? claim1.segments.map((s) => s.id) : [];
    const subClaims = segs.filter((c) => c.claim_number !== 1);

    const responses = {};
    let hasAny = false;
    for (const citId of citIds) {
      try {
        const r = await fetch('/case/' + CASE_ID + '/response/' + encodeURIComponent(citId));
        if (r.ok) {
          responses[citId] = await r.json();
          hasAny = true;
        }
      } catch (e) { /* */ }
    }

    const segTextMap = {};
    if (claim1 && Array.isArray(claim1.segments)) {
      claim1.segments.forEach((s) => { if (s && s.id) segTextMap[s.id] = s.text || ''; });
    }
    _compSummaryData = {
      segs,
      claim1,
      segIds,
      segTextMap,
      citIds,
      responses,
      subClaims,
      hasAny: hasAny
    };
    renderCompSummaryTable();
  } catch (e) {
    console.error('loadComparisonSummary error:', e);
    _setCompSummaryLoadError('対比表の読み込みに失敗しました: ' + (e && e.message ? e.message : String(e)));
  }
}

if (window.CASE_BOOTSTRAP && window.CASE_BOOTSTRAP.has_citations) {
  loadComparisonSummary();
}

// ===== 案件情報編集 =====
function editCaseMeta() {
  document.getElementById('meta-edit-modal').style.display = 'flex';
}
function closeMetaModal() {
  document.getElementById('meta-edit-modal').style.display = 'none';
}
async function saveCaseMeta() {
  const patentNumber = document.getElementById('edit-patent-number').value.trim();
  const patentTitle = document.getElementById('edit-patent-title').value.trim();
  try {
    await fetch(`/case/${CASE_ID}/meta`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ patent_number: patentNumber, patent_title: patentTitle })
    });
    document.getElementById('header-patent-number').textContent = patentNumber || CASE_ID;
    document.getElementById('header-patent-title').textContent = patentTitle || '（題名未取得）';
    closeMetaModal();
  } catch(e) {
    alert('保存エラー: ' + e.message);
  }
}

// ===== Excel出力 =====
// Step 6 のチェックボックス (`.comp-col-toggle`) で表示中の文献のみ Excel に出力する。
// 表示前 (チェックボックスが未生成) の場合は null を渡し、サーバ側で全件扱い。
function _getSelectedCitIdsForExport() {
  const colbar = document.getElementById('comp-summary-colbar');
  if (!colbar) return null;
  const boxes = Array.from(colbar.querySelectorAll('.comp-col-toggle'));
  if (boxes.length === 0) return null;
  const ids = boxes.filter((b) => b.checked).map((b) => b.getAttribute('data-cit-id')).filter(Boolean);
  return ids;
}

async function exportExcel() {
  const loading = document.getElementById('loading-export');
  loading.classList.add('show');
  const result = document.getElementById('export-result');
  try {
    const selected = _getSelectedCitIdsForExport();
    if (selected !== null && selected.length === 0) {
      loading.classList.remove('show');
      result.innerHTML = `<div style="padding:1rem; background:#450a0a; border-radius:8px; color:#fca5a5;">
        対比表に出力する文献が選択されていません。Step 6 の「表示する列」で 1 件以上チェックしてください。
      </div>`;
      return;
    }
    const body = (selected !== null) ? JSON.stringify({ citation_ids: selected }) : JSON.stringify({});
    const resp = await fetch(`/case/${CASE_ID}/export/excel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    const data = await resp.json();
    loading.classList.remove('show');

    if (data.success) {
      const total = (window.CASE_BOOTSTRAP && window.CASE_BOOTSTRAP.cit_ids || []).length;
      const cnt = data.num_citations;
      const subset = (selected !== null && cnt < total) ? `（${cnt}/${total} 件）` : '';
      result.innerHTML = `<div style="padding:1rem; background:#14532d; border-radius:8px; color:#4ade80;">
        Excel出力完了 ${subset}
        <a href="/case/${CASE_ID}/download/${data.filename}" style="color:#fff; text-decoration:underline; margin-left:1rem;">ダウンロード</a>
      </div>`;
    } else {
      result.innerHTML = `<div style="padding:1rem; background:#450a0a; border-radius:8px; color:#fca5a5;">
        エラー: ${data.error}
      </div>`;
    }
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

// ===== 注釈PDF =====
/** 対比回答が未取り込みのとき（responses/{id}.json なし）の注釈ボタン用 */
function srAnnotateNeedStep5() {
  alert(
    '注釈PDFを作るには、当該文献について Step 5（対比）の「回答をパース」まで完了している必要があります。\n\n' +
    '手順: 上のステップから「5 対比」を開く → 対象文献にチェック → プロンプト生成 → ' +
    'Claude 等で実行 → 回答を貼り付け →「回答をパース」。\n\n' +
    '完了後にこの画面を再読み込みすると、注釈PDFボタンが有効になります。'
  );
}

async function annotateCitation(citId, btn, opts) {
  opts = opts || {};
  const force = !!opts.force;
  const origText = btn.textContent;
  btn.textContent = force ? '再生成中...' : '生成中...';
  btn.disabled = true;

  try {
    const resp = await fetch(`/case/${CASE_ID}/annotate/${encodeURIComponent(citId)}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({force_new_file: force}),
    });
    const data = await resp.json();

    if (data.success) {
      const label = data.opened ? `開いた (${data.filename})` : `生成済 (${data.filename})`;
      btn.textContent = label;
      btn.classList.remove('btn-outline');
      btn.classList.add('btn-success');
      btn.disabled = false;
      // 再クリックで通常再生成 (上書き)
      btn.onclick = () => annotateCitation(citId, btn);
      setTimeout(() => { btn.textContent = '注釈PDF再表示'; }, 1800);
    } else {
      btn.textContent = origText;
      btn.disabled = false;
      const jpUrl = data.jplatpat_url || buildJplatpatUrl(citId);
      let msg = '注釈PDF生成エラー: ' + (data.error || '');
      if (jpUrl) msg += '\n\nJ-PlatPatで確認: ' + jpUrl;
      alert(msg);
    }
  } catch(e) {
    btn.textContent = origText;
    btn.disabled = false;
    alert('エラー: ' + e.message);
  }
}

// ===== 進歩性判断 =====
async function generateInventiveStepPrompt() {
  const loading = document.getElementById('loading-inv-prompt');
  loading.classList.add('show');

  try {
    const resp = await fetch(`/case/${CASE_ID}/inventive-step/prompt`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})
    });
    const data = await resp.json();
    loading.classList.remove('show');

    if (data.error) { alert(data.error); return; }

    document.getElementById('inv-prompt-text').textContent = data.prompt;
    document.getElementById('inv-prompt-text').style.display = 'block';
    document.getElementById('btn-copy-inv').style.display = 'inline-block';
    document.getElementById('inv-prompt-charcount').textContent =
      `${data.char_count.toLocaleString()} 文字`;
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

function copyInventiveStepPrompt() {
  const text = document.getElementById('inv-prompt-text').textContent;
  if (!text) { alert('先にプロンプトを生成してください'); return; }
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('btn-copy-inv');
    btn.textContent = 'コピー済!';
    setTimeout(() => btn.textContent = 'コピー', 1500);
  });
}

async function parseInventiveStepResponse() {
  const text = document.getElementById('inv-response-text').value;
  if (!text.trim()) { alert('回答を貼り付けてください'); return; }

  const loading = document.getElementById('loading-inv-parse');
  loading.classList.add('show');

  try {
    const resp = await fetch(`/case/${CASE_ID}/inventive-step/response`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: text })
    });
    const data = await resp.json();
    loading.classList.remove('show');

    const result = document.getElementById('inv-result');
    result.innerHTML = '';

    if (data.errors && data.errors.length > 0) {
      result.innerHTML += `<div style="padding:0.8rem; background:#422006; border-radius:8px; color:#fbbf24; margin-bottom:0.5rem; font-size:0.85rem;">
        <strong>警告:</strong><br>${data.errors.join('<br>')}
      </div>`;
    }

    if (data.success && data.data) {
      result.innerHTML += renderInventiveStepResult(data.data);
    } else if (!data.success) {
      result.innerHTML += `<div style="padding:0.8rem; background:#450a0a; border-radius:8px; color:#fca5a5; font-size:0.85rem;">
        パース失敗: 進歩性分析のJSONを抽出できませんでした。
      </div>`;
    }
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

function renderInventiveStepResult(data) {
  let html = '';

  // 総合評価（最重要 — 先頭に表示）
  const oa = data.overall_assessment;
  if (oa) {
    const stepColor = oa.inventive_step === 'あり' ? '#14532d' : oa.inventive_step === 'なし' ? '#450a0a' : '#422006';
    const stepTextColor = oa.inventive_step === 'あり' ? '#4ade80' : oa.inventive_step === 'なし' ? '#fca5a5' : '#fbbf24';
    html += `<div style="padding:1rem; background:${stepColor}; border-radius:8px; margin-bottom:1rem;">
      <div style="font-size:1.2rem; font-weight:700; color:${stepTextColor}; margin-bottom:0.5rem;">
        進歩性: ${oa.inventive_step}
      </div>
      <div style="font-size:0.9rem; color:var(--text); margin-bottom:0.5rem;">${oa.reasoning || ''}</div>
      ${oa.rejection_logic ? `<div style="font-size:0.85rem; margin-top:0.5rem;"><strong style="color:var(--text2);">拒絶理由の論理構成:</strong><br>${oa.rejection_logic}</div>` : ''}
      ${oa.vulnerable_points ? `<div style="font-size:0.85rem; margin-top:0.5rem; color:#fbbf24;"><strong>反論されやすいポイント:</strong><br>${oa.vulnerable_points}</div>` : ''}
      ${oa.strengthening_suggestions ? `<div style="font-size:0.85rem; margin-top:0.5rem; color:#60a5fa;"><strong>論理強化の提案:</strong><br>${oa.strengthening_suggestions}</div>` : ''}
    </div>`;
  }

  // 主引用発明
  const pr = data.primary_reference;
  if (pr) {
    html += `<div style="padding:0.8rem; background:var(--surface2); border-radius:8px; margin-bottom:0.8rem;">
      <strong>主引用発明:</strong> ${pr.document_id}<br>
      <span style="font-size:0.85rem; color:var(--text2);">${pr.selection_reason || ''}</span>
    </div>`;
  }

  // 一致点
  if (data.common_features && data.common_features.length > 0) {
    html += `<div style="margin-bottom:0.8rem;">
      <strong>一致点:</strong>
      <ul style="margin:0.3rem 0 0 1.5rem; font-size:0.85rem;">`;
    for (const cf of data.common_features) {
      const segs = (cf.segment_ids || []).join(', ');
      html += `<li><span style="color:var(--green); font-weight:600;">[${segs}]</span> ${cf.description}</li>`;
    }
    html += '</ul></div>';
  }

  // 相違点
  if (data.differences && data.differences.length > 0) {
    html += `<div style="margin-bottom:0.8rem;"><strong>相違点:</strong>`;
    for (const diff of data.differences) {
      const res = diff.resolution || {};
      const methodColor = res.method === '論理付け不可' ? '#fca5a5' : '#fbbf24';
      html += `<div style="padding:0.8rem; background:var(--bg); border-radius:6px; margin-top:0.5rem; border-left:3px solid var(--red);">
        <div style="font-weight:600; color:var(--red);">${diff.segment_id}: ${diff.description || ''}</div>
        <div style="font-size:0.85rem; color:var(--text2); margin-top:0.3rem;">技術的意義: ${diff.technical_significance || ''}</div>
        <div style="font-size:0.85rem; margin-top:0.5rem;">
          <span style="color:${methodColor}; font-weight:600;">手法: ${res.method || ''}</span>
          ${res.secondary_reference ? ` (副引例: ${res.secondary_reference})` : ''}
          ${res.design_change_type ? ` [${res.design_change_type}]` : ''}
        </div>`;
      if (res.motivation) {
        const m = res.motivation;
        html += `<div style="font-size:0.8rem; margin-top:0.3rem; padding-left:0.8rem; border-left:2px solid var(--border);">`;
        if (m.technical_field) html += `<div>技術分野: ${m.technical_field}</div>`;
        if (m.common_problem) html += `<div>課題共通性: ${m.common_problem}</div>`;
        if (m.common_function) html += `<div>作用・機能: ${m.common_function}</div>`;
        if (m.suggestion) html += `<div>示唆: ${m.suggestion}</div>`;
        html += '</div>';
      }
      if (res.inhibiting_factors && res.inhibiting_factors.length > 0) {
        html += `<div style="font-size:0.8rem; margin-top:0.3rem; color:#fbbf24;">阻害要因: ${res.inhibiting_factors.join(', ')}</div>`;
      }
      html += `<div style="font-size:0.85rem; margin-top:0.3rem;">${res.conclusion || ''}</div>`;
      html += '</div>';
    }
    html += '</div>';
  }

  // 有利な効果
  const ae = data.advantageous_effects;
  if (ae) {
    html += `<div style="padding:0.8rem; background:var(--surface2); border-radius:8px; margin-bottom:0.8rem;">
      <strong>有利な効果:</strong><br>
      <span style="font-size:0.85rem;">${ae.claimed_effects || ''}</span><br>
      <span style="font-size:0.8rem; color:var(--text2);">
        異質: ${ae.is_heterogeneous ? '○' : '×'} /
        際だって優れた: ${ae.is_remarkably_superior ? '○' : '×'} /
        予測可能: ${ae.is_predictable ? '○' : '×'}
      </span><br>
      <span style="font-size:0.85rem;">${ae.assessment || ''}</span>
    </div>`;
  }

  return html;
}

async function annotateAll() {
  const loading = document.getElementById('loading-annotate');
  loading.classList.add('show');
  const resultDiv = document.getElementById('annotate-result');
  resultDiv.innerHTML = '';

  try {
    const resp = await fetch(`/case/${CASE_ID}/annotate/all`, { method: 'POST' });
    const data = await resp.json();
    loading.classList.remove('show');

    let html = '';
    if (data.success_count > 0) {
      const opened = data.opened_count || 0;
      html += `<div style="padding:0.8rem; background:#14532d; border-radius:8px; color:#4ade80; font-size:0.85rem;">
        ${data.success_count}件の注釈PDFを生成し、${opened}件をPDF-XChange Editorで開きました。</div>`;
    }
    const failures = (data.results || []).filter(r => !r.success);
    if (failures.length > 0) {
      const failLines = failures.map(f => {
        let line = `${f.citation_id}: ${f.error}`;
        const jpUrl = f.jplatpat_url || buildJplatpatUrl(f.citation_id);
        if (jpUrl) {
          line += ` <a href="${jpUrl}" target="_blank" style="color:#60a5fa;">J-PlatPat</a>`;
        }
        return line;
      }).join('<br>');
      html += `<div style="padding:0.8rem; background:#422006; border-radius:8px; margin-top:0.5rem; color:#fbbf24; font-size:0.85rem;">
        ${failures.length}件失敗:<br>${failLines}
      </div>`;
    }
    resultDiv.innerHTML = html;
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

// ================================================================
// Claude CLI 直接実行
// ================================================================

// ページロード時にClaude CLI利用可能かチェック → ボタン表示
(async function checkClaudeAvailability() {
  try {
    const resp = await fetch('/api/claude-status');
    const data = await resp.json();
    if (data.available) {
      document.querySelectorAll('.btn-execute').forEach(btn => {
        btn.style.display = 'inline-block';
      });
      // Web検索APIの状態表示
      const apiStatus = document.getElementById('search-api-status');
      if (apiStatus) {
        if (data.search_available) {
          apiStatus.style.display = 'inline-block';
        } else {
          apiStatus.style.display = 'inline-block';
          apiStatus.textContent = 'Web検索OFF';
          apiStatus.style.background = '#422006';
          apiStatus.style.color = '#fbbf24';
        }
      }
    }
  } catch(e) { /* Claude未対応環境ではボタン非表示のまま */ }
})();

// --- Step 3: 先行技術検索 直接実行 ---
async function executeSearch() {
  const btn = event.target.closest('.btn-execute');
  const progress = document.getElementById('exec-search-progress');
  btn.disabled = true;
  progress.classList.add('show');

  try {
    const resp = await fetch(`/case/${CASE_ID}/search/execute`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})
    });
    const data = await resp.json();
    progress.classList.remove('show');
    btn.disabled = false;

    if (data.error) { alert('エラー: ' + data.error); return; }

    const errDiv = document.getElementById('search-parse-errors');
    if (errDiv) {
      errDiv.innerHTML = '';
      if (data.errors && data.errors.length > 0) {
        errDiv.innerHTML = `<div style="padding:0.8rem; background:#422006; border-radius:8px; color:#fbbf24; font-size:0.85rem;">
          <strong>警告:</strong><br>${data.errors.join('<br>')}
        </div>`;
      }
    }

    if (data.success && data.candidates.length > 0) {
      searchCandidates = data.candidates;
      renderCandidatesList(searchCandidates);
      document.getElementById('search-candidates').style.display = 'block';
    }

    document.getElementById('search-prompt-charcount').textContent =
      `直接実行完了: ${(data.char_count || 0).toLocaleString()}文字 → ${data.candidates.length}件の候補`;
  } catch(e) {
    progress.classList.remove('show');
    btn.disabled = false;
    alert('通信エラー: ' + e.message);
  }
}

// --- Step 5: 対比 直接実行 ---
async function executeCompare() {
  const citIds = getSelectedCitationIds();
  if (citIds.length === 0) { alert('対象文献を1つ以上選択してください'); return; }

  const btn = event.target.closest('.btn-execute');
  const progress = document.getElementById('exec-compare-progress');
  btn.disabled = true;
  progress.classList.add('show');
  document.getElementById('exec-compare-status').textContent =
    `Claude CLIで${citIds.length}件の文献を対比分析中...`;

  try {
    const resp = await fetch(`/case/${CASE_ID}/execute`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ citation_ids: citIds })
    });
    const data = await resp.json();
    progress.classList.remove('show');
    btn.disabled = false;

    if (data.error) { alert('エラー: ' + data.error); return; }

    const result = document.getElementById('parse-result');
    result.innerHTML = '';

    if (data.errors && data.errors.length > 0) {
      result.innerHTML += `<div style="padding:1rem; background:#422006; border-radius:8px; color:#fbbf24; margin-bottom:0.5rem;">
        <strong>警告:</strong><br>${data.errors.join('<br>')}
      </div>`;
    }
    if (data.success) {
      const docs = data.saved_docs || [];
      result.innerHTML += `<div style="padding:1rem; background:#14532d; border-radius:8px; color:#4ade80;">
        直接実行完了! ${data.num_docs}件の対比結果を保存しました。<br>
        保存先: ${docs.join(', ')}<br>
        <span style="font-size:0.8rem; color:var(--text2);">
          プロンプト: ${(data.char_count || 0).toLocaleString()}文字 /
          応答: ${(data.response_length || 0).toLocaleString()}文字
        </span>
      </div>`;
    } else if (!data.errors || data.errors.length === 0) {
      result.innerHTML = `<div style="padding:1rem; background:#450a0a; border-radius:8px; color:#fca5a5;">
        パース失敗: Claudeの応答からJSONを抽出できませんでした。
      </div>`;
    }
  } catch(e) {
    progress.classList.remove('show');
    btn.disabled = false;
    alert('通信エラー: ' + e.message);
  }
}

// --- Step 6: 進歩性判断 直接実行 ---
async function executeInventiveStep() {
  const btn = event.target.closest('.btn-execute');
  const progress = document.getElementById('exec-inv-progress');
  btn.disabled = true;
  progress.classList.add('show');

  try {
    const resp = await fetch(`/case/${CASE_ID}/inventive-step/execute`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})
    });
    const data = await resp.json();
    progress.classList.remove('show');
    btn.disabled = false;

    if (data.error) { alert('エラー: ' + data.error); return; }

    const result = document.getElementById('inv-result');
    result.innerHTML = '';

    if (data.errors && data.errors.length > 0) {
      result.innerHTML += `<div style="padding:0.8rem; background:#422006; border-radius:8px; color:#fbbf24; margin-bottom:0.5rem; font-size:0.85rem;">
        <strong>警告:</strong><br>${data.errors.join('<br>')}
      </div>`;
    }

    if (data.success && data.data) {
      result.innerHTML += renderInventiveStepResult(data.data);
      document.getElementById('inv-prompt-charcount').textContent =
        `直接実行完了: ${(data.char_count || 0).toLocaleString()}文字`;
    } else if (!data.success) {
      result.innerHTML += `<div style="padding:0.8rem; background:#450a0a; border-radius:8px; color:#fca5a5; font-size:0.85rem;">
        パース失敗: 進歩性分析のJSONを抽出できませんでした。
      </div>`;
    }
  } catch(e) {
    progress.classList.remove('show');
    btn.disabled = false;
    alert('通信エラー: ' + e.message);
  }
}

// ================================================================
// ===== テキスト選択 → キーワード追加ポップオーバー =====
// ================================================================

// 分節IDリスト（ポップオーバー用）
window._segmentIds = Object.keys(SEG_DATA);

document.addEventListener('mouseup', function(e) {
  const selection = window.getSelection().toString().trim();

  if (selection.length < 2) {
    hideKwPopover();
    return;
  }
  // ポップオーバー内部のクリックは無視
  if (e.target.closest('#kw-add-popover')) return;

  // 対比サマリ、引例テキスト、パース結果エリア内のみ反応
  const targetArea = e.target.closest('#comparison-summary, #citation-fulltext-area, #parse-result, .citation-fulltext');
  if (!targetArea) {
    hideKwPopover();
    return;
  }

  showKwPopover(selection, e.pageX, e.pageY);
});

function showKwPopover(term, x, y) {
  const popover = document.getElementById('kw-add-popover');
  document.getElementById('kw-add-term').textContent = term;

  const segDiv = document.getElementById('kw-add-segments');
  segDiv.innerHTML = '';

  const segIds = window._segmentIds || [];
  for (const segId of segIds) {
    const btn = document.createElement('button');
    btn.className = 'pop-seg-btn';
    btn.textContent = segId;
    btn.onclick = () => addKeywordToSegment(term, segId);
    segDiv.appendChild(btn);
  }

  // 位置調整
  popover.style.left = Math.min(x, window.innerWidth - 340) + 'px';
  popover.style.top = (y + 10) + 'px';
  popover.style.display = 'block';
}

function hideKwPopover() {
  document.getElementById('kw-add-popover').style.display = 'none';
}

// ポップオーバー外クリックで閉じる
document.addEventListener('mousedown', function(e) {
  if (!e.target.closest('#kw-add-popover')) {
    // mousedown で即座に閉じるとmouseupと競合するので少し遅延
  }
});

async function addKeywordToSegment(term, segmentId) {
  const resp = await fetch(`/case/${CASE_ID}/keywords/add-to-segment`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({term: term, segment_id: segmentId}),
  });

  if (resp.ok) {
    hideKwPopover();
    // キーワード一覧を更新
    const dataResp = await fetch(`/case/${CASE_ID}/keywords/segments`);
    if (dataResp.ok) {
      renderSegmentKeywords(await dataResp.json());
    }
    showKwToast(`「${term}」を ${segmentId} に追加しました`);
  } else {
    const err = await resp.json();
    alert(err.error || 'エラーが発生しました');
  }
}

function showKwToast(msg) {
  const toast = document.createElement('div');
  toast.className = 'kw-toast';
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
}

// ================================================================
// ===== 引例テキスト全文表示 =====
// ================================================================

async function loadCitationFullTexts() {
  const container = document.getElementById('citation-fulltext-area');
  if (!container) return;

  const citIds = window.CASE_BOOTSTRAP.cit_ids || [];
  if (citIds.length === 0) return;

  let html = '<h3 style="font-size:1rem; margin-bottom:0.8rem;">引例テキスト <span style="font-size:0.8rem; color:var(--text2);">（テキスト選択でキーワードに追加可能）</span></h3>';

  for (const citId of citIds) {
    try {
      const resp = await fetch(`/case/${CASE_ID}/response/${citId}`);
      if (!resp.ok) continue;
      const respData = await resp.json();

      // 対比結果から cited_text / cited_location を表示
      if (respData.comparisons) {
        const jpUrl = buildJplatpatUrl(citId);
        const jpLink = jpUrl
          ? ` <a href="${jpUrl}" target="_blank" rel="noopener noreferrer" class="cit-jplatpat-link" onclick="event.stopPropagation();">J-PlatPat（経過情報）</a>`
          : '';
        html += `<details class="citation-fulltext" style="margin-bottom:1rem;" data-citation-text="${String(citId).replace(/"/g, '&quot;')}">`;
        html += `<summary style="cursor:pointer; align-items:center; gap:0.5rem;">${_escapeHtml(citId)} の対比結果テキスト${jpLink}</summary>`;
        html += '<div style="padding:0.5rem;">';
        for (const comp of respData.comparisons) {
          const jRaw = comp.judgment || '';
          const jDisp = _compJDisp(comp) || '—';
          const jColor = jRaw === '○' ? 'var(--green)' : jRaw === '×' ? 'var(--red)' : 'var(--yellow)';
          html += `<div style="margin-bottom:0.5rem; padding:0.4rem 0.6rem; border-left:3px solid ${jColor}; background:var(--surface2); border-radius:0 4px 4px 0;">`;
          html += `<div style="font-size:0.8rem;"><strong>${comp.requirement_id}</strong> <span style="color:${jColor};">${jDisp}</span></div>`;
          const locTxt = comp.cited_location_expanded || comp.cited_location || '';
          if (locTxt) {
            html += `<div class="para-text" style="margin-top:2px;">📍 ${_compLinkifyParaRefs(_escapeHtml(locTxt), citId)}</div>`;
          }
          if (comp.cited_location_comment) {
            html += `<div class="para-text" style="margin-top:2px; color:var(--text2);">📝 ${_escapeHtml(comp.cited_location_comment)}</div>`;
          }
          if (comp.judgment_reason) {
            html += `<div class="para-text" style="margin-top:2px;">${_compLinkifyParaRefs(_escapeHtml(comp.judgment_reason), citId)}</div>`;
          }
          html += '</div>';
        }
        html += '</div></details>';
      }
    } catch(e) {}
  }

  container.innerHTML = html;
}

// ================================================================
// ===== 3段階検索ワークフロー =====
// ================================================================

const STAGE_ENDPOINTS = {
  1: { prompt: 'presearch/prompt', parse: 'presearch/parse', execute: 'presearch/execute' },
  2: { prompt: 'classify/prompt', parse: 'classify/parse', execute: 'classify/execute' },
  3: { prompt: 'keywords/prompt', parse: 'keywords/parse', execute: 'keywords/execute' },
};

function showStage(n) {
  for (let i = 1; i <= 3; i++) {
    document.getElementById(`stage-panel-${i}`).classList.toggle('active', i === n);
    const ind = document.getElementById(`stage-ind-${i}`);
    if (!ind.classList.contains('done')) {
      ind.classList.toggle('active', i === n);
    }
  }
}

async function loadSearchStatus() {
  try {
    const resp = await fetch(`/case/${CASE_ID}/search/status`);
    if (!resp.ok) return;
    const data = await resp.json();
    const completed = data.completed_stages || 0;
    document.getElementById('stage-overall').textContent = `Stage ${completed}/3`;

    for (let i = 1; i <= 3; i++) {
      const ind = document.getElementById(`stage-ind-${i}`);
      if (i <= completed) {
        ind.classList.add('done');
        ind.classList.remove('active');
      }
    }

    // 既存データがあればresultを表示
    if (data.stage1 && data.stage1.tech_analysis) {
      loadStageResult(1);
    }
    if (data.stage2 && data.stage2.classification) {
      loadStageResult(2);
    }
    if (data.stage3 && data.stage3.keyword_dictionary) {
      loadStageResult(3);
    }
  } catch(e) {}
}

async function loadStageResult(stage) {
  const fileMap = {1: 'tech_analysis.json', 2: 'classification.json', 3: 'keyword_dictionary.json'};
  try {
    const resp = await fetch(`/case/${CASE_ID}/search/data/${fileMap[stage]}`);
    if (!resp.ok) return;
    const data = await resp.json();
    renderStageResult(stage, data);
  } catch(e) {}
}

function renderStageResult(stage, data) {
  const container = document.getElementById(`stage${stage}-result`);
  container.style.display = 'block';
  let html = '';

  if (stage === 1) {
    // tech_analysis
    html += '<div class="stage-result-box"><h4>技術構造化</h4>';
    if (data.core_sentence) {
      html += `<div style="margin-bottom:0.5rem; font-style:italic; color:var(--text2);">${data.core_sentence}</div>`;
    }
    const elements = data.elements || {};
    for (const [key, elem] of Object.entries(elements)) {
      html += `<div class="stage-elem-card">`;
      html += `<span class="elem-key">${key}</span>`;
      html += `<strong>${elem.label || ''}</strong>`;
      if (elem.terms_ja && elem.terms_ja.length > 0) {
        html += `<div style="margin-top:2px;">${elem.terms_ja.map(t => `<span class="stage-tag">${t}</span>`).join('')}</div>`;
      }
      if (elem.terms_en && elem.terms_en.length > 0) {
        html += `<div style="margin-top:2px;">${elem.terms_en.map(t => `<span class="stage-tag">${t}</span>`).join('')}</div>`;
      }
      html += '</div>';
    }
    html += '</div>';
  } else if (stage === 2) {
    // classification
    html += '<div class="stage-result-box"><h4>特許分類</h4>';
    for (const system of ['fi', 'fterm', 'cpc', 'ipc']) {
      const items = data[system];
      if (items && items.length > 0) {
        html += `<div style="margin-bottom:0.4rem;"><strong>${system.toUpperCase()}:</strong> `;
        html += items.map(item => {
          const code = item.code || item;
          const desc = item.description || '';
          return `<span class="stage-tag">${code}${desc ? ' (' + desc + ')' : ''}</span>`;
        }).join('');
        html += '</div>';
      }
    }
    html += '</div>';
  } else if (stage === 3) {
    // keyword_dictionary
    html += '<div class="stage-result-box"><h4>キーワード辞書</h4>';
    const elements = data.elements || {};
    for (const [key, elem] of Object.entries(elements)) {
      html += `<div class="stage-elem-card">`;
      html += `<span class="elem-key">${key}</span><strong>${elem.label || ''}</strong>`;
      // core
      const cores = (elem.core_terms || []).map(t => t.term || t);
      if (cores.length > 0) {
        html += `<div style="margin-top:2px;">コア: ${cores.map(t => `<span class="stage-tag core">${t}</span>`).join('')}</div>`;
      }
      // extended
      const exts = (elem.extended_terms || []).map(t => t.term || t);
      if (exts.length > 0) {
        html += `<div style="margin-top:2px;">拡張: ${exts.map(t => `<span class="stage-tag extended">${t}</span>`).join('')}</div>`;
      }
      // not
      const nots = (elem.not_terms || []).map(t => t.term || t);
      if (nots.length > 0) {
        html += `<div style="margin-top:2px;">NOT: ${nots.map(t => `<span class="stage-tag not">${t}</span>`).join('')}</div>`;
      }
      html += '</div>';
    }
    // search_formulas
    if (data.search_formulas) {
      html += '<div style="margin-top:0.8rem;"><h4>検索式</h4>';
      for (const [level, formula] of Object.entries(data.search_formulas)) {
        if (formula) {
          html += `<div style="margin-bottom:0.3rem;"><strong>${level}:</strong>
            <code style="font-size:0.8rem; color:var(--text); background:var(--bg); padding:2px 6px; border-radius:4px; word-break:break-all;">${formula}</code></div>`;
        }
      }
      html += '</div>';
    }
    html += '</div>';
  }

  container.innerHTML = html;
}

async function stageGeneratePrompt(stage) {
  const loading = document.getElementById(`loading-stage${stage}-prompt`);
  loading.classList.add('show');

  try {
    const ep = STAGE_ENDPOINTS[stage];
    const resp = await fetch(`/case/${CASE_ID}/search/${ep.prompt}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})
    });
    const data = await resp.json();
    loading.classList.remove('show');

    if (data.error) { alert(data.error); return; }

    document.getElementById(`stage${stage}-prompt`).textContent = data.prompt;
    document.getElementById(`stage${stage}-prompt`).style.display = 'block';
    document.getElementById(`btn-copy-stage${stage}`).style.display = 'inline-block';
    document.getElementById(`stage${stage}-charcount`).textContent =
      `${data.char_count.toLocaleString()} 文字`;
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

function stageCopyPrompt(stage) {
  const text = document.getElementById(`stage${stage}-prompt`).textContent;
  if (!text) { alert('先にプロンプトを生成してください'); return; }
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById(`btn-copy-stage${stage}`);
    btn.textContent = 'コピー済!';
    setTimeout(() => btn.textContent = 'コピー', 1500);
  });
}

async function stageParse(stage) {
  const text = document.getElementById(`stage${stage}-response`).value;
  if (!text.trim()) { alert('回答を貼り付けてください'); return; }

  const loading = document.getElementById(`loading-stage${stage}-parse`);
  loading.classList.add('show');

  try {
    const ep = STAGE_ENDPOINTS[stage];
    const resp = await fetch(`/case/${CASE_ID}/search/${ep.parse}`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text: text })
    });
    const data = await resp.json();
    loading.classList.remove('show');

    if (data.error) { alert(data.error); return; }

    if (data.errors && data.errors.length > 0) {
      alert('警告: ' + data.errors.join('\n'));
    }

    if (data.success) {
      // 結果を表示
      if (stage === 1 && data.tech_analysis) {
        renderStageResult(1, data.tech_analysis);
      } else if (stage === 2 && data.classification) {
        renderStageResult(2, data.classification);
      } else if (stage === 3 && data.keyword_dictionary) {
        renderStageResult(3, data.keyword_dictionary);
      }

      // ステージインジケーター更新
      document.getElementById(`stage-ind-${stage}`).classList.add('done');
      loadSearchStatus();

      // 次のステージに自動遷移
      if (stage < 3) {
        setTimeout(() => showStage(stage + 1), 500);
      }

      showKwToast(`Stage ${stage} 完了!`);
    } else {
      alert('パース失敗: JSONを抽出できませんでした。');
    }
  } catch(e) {
    loading.classList.remove('show');
    alert('エラー: ' + e.message);
  }
}

async function stageExecute(stage) {
  // Stage 1 はストリーミング実行（リアルタイム進捗表示）
  if (stage === 1) {
    return stageExecuteStream();
  }

  // Stage 2, 3 は従来通り
  const progress = document.getElementById(`exec-stage${stage}-progress`);
  const btn = document.getElementById(`btn-stage${stage}-execute`);
  btn.disabled = true;
  progress.classList.add('show');

  try {
    const callResp = await fetch(`/case/${CASE_ID}/search/stage-execute`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ stage: stage })
    });
    const callData = await callResp.json();

    progress.classList.remove('show');
    btn.disabled = false;

    if (callData.error) { alert('エラー: ' + callData.error); return; }

    if (callData.errors && callData.errors.length > 0) {
      alert('警告: ' + callData.errors.join('\n'));
    }

    if (callData.success) {
      if (stage === 2 && callData.classification) {
        renderStageResult(2, callData.classification);
      } else if (stage === 3 && callData.keyword_dictionary) {
        renderStageResult(3, callData.keyword_dictionary);
      }

      document.getElementById(`stage-ind-${stage}`).classList.add('done');
      loadSearchStatus();

      if (stage < 3) {
        setTimeout(() => showStage(stage + 1), 500);
      }
      showKwToast(`Stage ${stage} 直接実行完了!`);
    } else {
      alert('パース失敗');
    }
  } catch(e) {
    progress.classList.remove('show');
    btn.disabled = false;
    alert('通信エラー: ' + e.message);
  }
}

function addExecMsg(container, text, cls) {
  const div = document.createElement('div');
  div.className = 'exec-msg' + (cls ? ' ' + cls : '');
  div.textContent = text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

async function stageExecuteStream() {
  const progress = document.getElementById('exec-stage1-progress');
  const btn = document.getElementById('btn-stage1-execute');
  btn.disabled = true;
  progress.classList.add('show');

  // メッセージログ領域を作成/リセット
  let msgLog = progress.querySelector('.exec-msg-log');
  if (!msgLog) {
    msgLog = document.createElement('div');
    msgLog.className = 'exec-msg-log';
    progress.appendChild(msgLog);
  }
  msgLog.innerHTML = '';
  addExecMsg(msgLog, 'Claude CLI 起動中...');

  try {
    const resp = await fetch(`/case/${CASE_ID}/search/stage-execute-stream`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.error || 'サーバーエラー');
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;
        let evt;
        try { evt = JSON.parse(line); } catch(e) { continue; }

        if (evt.type === 'search') {
          addExecMsg(msgLog, '\u{1F50D} 「' + evt.query + '」を検索中...', 'search');
        } else if (evt.type === 'candidate') {
          addExecMsg(msgLog, '\u{1F4C4} 候補' + evt.count + ': ' + evt.number + ' 発見', 'candidate');
        } else if (evt.type === 'status') {
          addExecMsg(msgLog, evt.message);
        } else if (evt.type === 'result') {
          progress.classList.remove('show');
          btn.disabled = false;

          if (evt.errors && evt.errors.length > 0) {
            alert('警告: ' + evt.errors.join('\n'));
          }
          if (evt.success) {
            if (evt.tech_analysis) renderStageResult(1, evt.tech_analysis);
            document.getElementById('stage-ind-1').classList.add('done');
            loadSearchStatus();
            setTimeout(() => showStage(2), 500);
            showKwToast('Stage 1 直接実行完了!');
          } else {
            alert('パース失敗');
          }
        } else if (evt.type === 'error') {
          progress.classList.remove('show');
          btn.disabled = false;
          alert('エラー: ' + evt.message);
        }
      }
    }

    // ストリーム終了後もまだprogressが表示中なら閉じる
    if (progress.classList.contains('show')) {
      progress.classList.remove('show');
      btn.disabled = false;
    }
  } catch(e) {
    progress.classList.remove('show');
    btn.disabled = false;
    alert('通信エラー: ' + e.message);
  }
}

// ページロード時にステージ状態を復元
loadSearchStatus();

// Step 6 表示時に引例テキストも読み込み
const _origShowPanel = showPanel;
showPanel = function(idx) {
  _origShowPanel(idx);
  if (idx === 6) {
    loadCitationFullTexts();
  }
};


// ================================================================
// Step 4.5: J-PlatPat 検索 & 候補スクリーニング
// ================================================================

let _srCurrentRun = null;        // 現在表示中の run データ
let _srFormulas = {};            // Stage 3 由来の検索式
let _srRuns = [];                // 検索ラン一覧
let _srParentRunId = null;       // 「複製して編集」で参照している親ラン ID
let _srKwSnippets = null;        // キーワード辞書スニペット
let _srValidateTimer = null;     // 式バリデーションのデバウンス用

const SR_SCREEN_LABELS = {
  star: '★', triangle: '△', reject: '×', hold: '…', pending: '—',
};
const SR_SCREEN_CLASS = {
  star: 'sr-star', triangle: 'sr-triangle', reject: 'sr-reject',
  hold: 'sr-hold', pending: 'sr-pending',
};

async function loadSearchRuns() {
  try {
    // Stage 3 式
    const fResp = await fetch(`/case/${CASE_ID}/search-run/formulas`);
    const fData = await fResp.json();
    _srFormulas = fData.formulas || {};
    renderSrFormulaCandidates();
  } catch (e) { console.warn('formula load error', e); }

  try {
    const resp = await fetch(`/case/${CASE_ID}/search-run/list`);
    const data = await resp.json();
    _srRuns = data.runs || [];
    renderSrRunsList();
  } catch (e) { console.warn('runs load error', e); }
}

function renderSrFormulaCandidates() {
  const el = document.getElementById('sr-formula-candidates');
  if (!el) return;
  const keys = Object.keys(_srFormulas || {});
  if (keys.length === 0) {
    el.innerHTML = '<em>Stage 3 のキーワード辞書が未生成です。下の textarea に手動で検索式を書いてください。</em>';
    return;
  }
  let needsFix = false;
  const cards = keys.map(k => {
    const f = _srFormulas[k] || {};
    const raw = (f.formula_jplatpat || '').trim();
    const fixed = srAutoFixFormulaText(raw);
    if (fixed !== raw) needsFix = true;
    const desc = f.description || '';
    const safeDesc = desc.replace(/</g, '&lt;');
    const mark = fixed !== raw
      ? `<span class="sr-cand-fixed" title="自動修正済み (NOT=-, /TX 付与など)">🔧 AUTO-FIX 済</span>`
      : '';
    return `<div class="sr-cand-card" data-level="${k}">
      <div class="sr-cand-head">
        <strong class="sr-cand-level">${k}</strong>
        <span class="sr-cand-desc">${safeDesc}</span>
        ${mark}
        <button class="sr-tool-btn" style="margin-left:auto;"
          onclick="srLoadCandidateToEditor('${k}')"
          title="この式を下のエディタに読み込む">⬇ エディタへ</button>
        <button class="sr-tool-btn" onclick="srCopyCandidate('${k}')" title="クリップボードにコピー">📋</button>
      </div>
      <textarea class="sr-cand-edit" id="sr-cand-ta-${k}" rows="1"
        oninput="srOnCandidateEdit('${k}')"
        spellcheck="false" wrap="soft">${fixed.replace(/</g, '&lt;')}</textarea>
      <div class="sr-cand-meta">
        <span id="sr-cand-valid-${k}" class="sr-formula-valid"></span>
      </div>
    </div>`;
  }).join('');
  const hint = needsFix
    ? `<div class="sr-preset-hint">🔧 マーク: 古い構文を自動修正したものを表示しています。恒久的には Stage 3 を再生成してください。</div>`
    : '';
  el.innerHTML = cards + hint;
  // 各候補の初期バリデーション + 高さを内容に合わせる
  keys.forEach(k => {
    srValidateCandidate(k);
    srAutoResizeTextarea(document.getElementById(`sr-cand-ta-${k}`));
  });
}

// テキストエリアの高さを内容に合わせて自動調整 (スクロール不要に)
function srAutoResizeTextarea(ta) {
  if (!ta) return;
  // 非表示要素は scrollHeight が 0 のため遅延
  if (ta.offsetParent === null) {
    setTimeout(() => srAutoResizeTextarea(ta), 120);
    return;
  }
  ta.style.height = 'auto';
  const h = Math.max(40, ta.scrollHeight + 2);
  ta.style.height = h + 'px';
}

// 画面幅変更時に全候補 textarea を再計算
window.addEventListener('resize', () => {
  document.querySelectorAll('.sr-cand-edit').forEach(ta => srAutoResizeTextarea(ta));
  const main = document.getElementById('sr-formula');
  if (main) srAutoResizeTextarea(main);
});

// 候補エディタ → 下のメインエディタへロード
function srLoadCandidateToEditor(level) {
  const ta = document.getElementById(`sr-cand-ta-${level}`);
  if (!ta) return;
  const main = document.getElementById('sr-formula');
  const levelSel = document.getElementById('sr-level');
  if (!main) return;
  main.value = ta.value;
  if (levelSel) levelSel.value = level;
  srOnFormulaChange();
  _srShowToast(`${level} の式をエディタに読み込みました`);
  main.scrollIntoView({behavior: 'smooth', block: 'center'});
  main.focus();
}

// 候補テキストエリアの編集 → ローカル `_srFormulas` も更新し、検証
function srOnCandidateEdit(level) {
  const ta = document.getElementById(`sr-cand-ta-${level}`);
  if (!ta) return;
  _srFormulas[level] = _srFormulas[level] || {};
  _srFormulas[level].formula_jplatpat = ta.value;
  srValidateCandidate(level);
  srAutoResizeTextarea(ta);
}

function srValidateCandidate(level) {
  const ta = document.getElementById(`sr-cand-ta-${level}`);
  const el = document.getElementById(`sr-cand-valid-${level}`);
  if (!ta || !el) return;
  const v = (ta.value || '').trim();
  if (!v) { el.textContent = ''; el.className = 'sr-formula-valid'; return; }
  fetch(`/case/${CASE_ID}/search-run/validate-formula`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({formula: v}),
  }).then(r => r.json()).then(d => {
    if (d.ok && !(d.warnings || []).length) {
      el.textContent = '✓ OK'; el.className = 'sr-formula-valid ok';
    } else if (d.ok) {
      el.textContent = `⚠ ${(d.warnings || []).join(' / ')}`;
      el.className = 'sr-formula-valid warn';
    } else {
      el.textContent = `✗ ${(d.errors || []).join(' / ')}`;
      el.className = 'sr-formula-valid err';
    }
  }).catch(() => {});
}

function srCopyCandidate(level) {
  const ta = document.getElementById(`sr-cand-ta-${level}`);
  if (!ta) return;
  navigator.clipboard.writeText(ta.value || '').then(() => {
    _srShowToast('クリップボードにコピーしました');
  }).catch(() => {
    _srShowToast('コピーに失敗しました');
  });
}

function srSelectFormula(level) {
  const f = _srFormulas[level];
  if (!f) return;
  document.getElementById('sr-level').value = level;
  const raw = f.formula_jplatpat || '';
  const fixed = srAutoFixFormulaText(raw);
  document.getElementById('sr-formula').value = fixed;
  if (fixed !== raw) {
    _srShowToast('提示式を J-PlatPat 構文に自動修正しました (NOT=−, 構造タグ /TX 付与 など)');
  }
  srOnFormulaChange();
}

// J-PlatPat 論理式構文への自動修正ロジック。
//   - 全角演算子 (＊＋－) → 半角 (* + -) ※演算子として使う位置のみ
//   - 全角括弧 （） → ( )
//   - NOT 演算子: " / " → " - "
//   - キーワード内の半角 '-' を全角 '－' に変換
//     (例: "フィルム-電池" や "SUS-304" は J-PlatPat では '-' が NOT 扱い → キーワードとして
//      扱わせるには全角 '－' が必須)
//   - キーワード丸括弧 (...) の直後にタグが無ければ /TX を付与
function srAutoFixFormulaText(src) {
  if (!src) return '';
  let s = String(src);

  // 全角 → 半角 (演算子)
  s = s.replace(/＊/g, '*')
       .replace(/＋/g, '+')
       .replace(/（/g, '(')
       .replace(/）/g, ')');
  // 全角ハイフン類 → 半角 (キーワード内ハイフン以外) は後で再判定

  // NOT 演算子の誤用: "キーワード / キーワード" または ") / (" を "-" に変換
  //   構造タグの /TX /CL /FI /FT ... は維持 (大文字 2-4 文字で始まる)
  //   スペース区切りで "/非大文字" が来たら NOT と判定
  s = s.replace(/\s\/\s+(?![A-Z]{2,4}\b)/g, ' - ');

  // 連続スペース正規化
  s = s.replace(/[ \t]+/g, ' ');

  // キーワード内ハイフンの全角化
  //   両端が「ワード文字」(ASCII英数字 / かな / カナ / CJK漢字) のときだけ全角化。
  //   演算子位置 (空白 or 括弧 or 他演算子に隣接) はそのまま。
  const wordClass = '[\\w\\u3040-\\u309F\\u30A0-\\u30FF\\u4E00-\\u9FFF]';
  const hyphenInWord = new RegExp(`(${wordClass})-(${wordClass})`, 'g');
  // 反復適用: "A-B-C" のように連続するケースに対応
  let prev;
  do {
    prev = s;
    s = s.replace(hyphenInWord, '$1－$2');
  } while (s !== prev);

  // 各 ')' の直後に構造タグが無い場合 /TX を付与
  //   - ネストした内側の ')' はスキップ (paren depth で判定)
  //   - 既にタグあり → スキップ
  //   - 次が ) ] → 上位グループがあるのでスキップ
  //   - 近傍検索の第1キーワード直後の ) → スキップ
  let out = '';
  let pDepth = 0;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    out += ch;
    if (ch === '(') {
      pDepth++;
    } else if (ch === ')') {
      pDepth = Math.max(0, pDepth - 1);
      if (pDepth !== 0) continue; // 内側の閉じ括弧はタグ対象外
      const tail = s.slice(i + 1);
      if (/^\s*\/[A-Z]{2,4}(?:\+[A-Z]{2,4})*/.test(tail)) continue;
      if (/^\s*[\])]/.test(tail)) continue;
      if (/^\s*,\d+[CcNn],/.test(tail)) continue;
      out += '/TX';
    }
  }
  s = out;

  return s.trim();
}

// エディタ上の式に自動修正を適用するボタン用
function srAutoFixFormula() {
  const ta = document.getElementById('sr-formula');
  if (!ta) return;
  const before = ta.value;
  const after = srAutoFixFormulaText(before);
  if (before === after) {
    _srShowToast('修正対象の記法は見つかりませんでした');
    return;
  }
  ta.value = after;
  srOnFormulaChange();
  _srShowToast(`検索式を自動修正しました (${before.length}→${after.length}字)`);
}

// ========== J-PlatPat メタ情報 (出願日/優先日/テーマコード) ==========

// 汎用クリップボードコピー + ボタン点灯
async function srCopyText(text, btn) {
  const t = (text || '').trim();
  if (!t || t === '未設定') {
    _srShowToast('コピーする内容がありません');
    return;
  }
  try {
    await navigator.clipboard.writeText(t);
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = '✅';
      setTimeout(() => { btn.textContent = orig; }, 900);
    }
    _srShowToast('コピーしました: ' + (t.length > 30 ? t.slice(0, 30) + '…' : t));
  } catch (e) {
    _srShowToast('コピー失敗: ' + e.message);
  }
}

// 出願日/優先日の編集 (YYYY-MM-DD)
async function srEditDate(field) {
  const label = field === 'filing_date' ? '出願日' : '優先日';
  const el = document.getElementById(field === 'filing_date' ? 'sr-filing-date' : 'sr-priority-date');
  const cur = (el.textContent || '').trim();
  const curVal = (cur === '未設定') ? '' : cur;
  const v = prompt(`${label} を YYYY-MM-DD 形式で入力してください (空にすると削除)`, curVal);
  if (v === null) return;
  const trimmed = v.trim();
  if (trimmed && !/^\d{4}-\d{1,2}-\d{1,2}$/.test(trimmed)) {
    if (!confirm('形式が YYYY-MM-DD ではありません。このまま保存しますか?')) return;
  }
  try {
    const r = await fetch(`/case/${CASE_ID}/meta`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({[field]: trimmed}),
    });
    const d = await r.json();
    if (d.success) {
      el.textContent = trimmed || '未設定';
      _srShowToast(`${label}を更新しました`);
    } else {
      _srShowToast('保存失敗: ' + (d.error || '不明'));
    }
  } catch (e) {
    _srShowToast('通信エラー: ' + e.message);
  }
}

// テーマコード chips 描画
function srRenderThemeChips(themeCodes) {
  const el = document.getElementById('sr-theme-chips');
  if (!el) return;
  if (!themeCodes || !themeCodes.length) {
    el.innerHTML = '<span class="sr-theme-empty">(抽出できませんでした)</span>';
    return;
  }
  el.innerHTML = themeCodes.map(code =>
    `<button class="sr-theme-chip" title="クリックでコピー" onclick="srCopyText('${code}', this)">${code}</button>`
  ).join('');
}

// ========== J-PlatPat 半自動化フロー ==========

function _srJppSetBadge(state, text) {
  const el = document.getElementById('sr-jpp-session-badge');
  if (!el) return;
  el.textContent = text;
  el.className = 'sr-jpp-badge ' +
    (state === 'on' ? 'sr-jpp-badge-on' :
     state === 'busy' ? 'sr-jpp-badge-busy' : 'sr-jpp-badge-off');
}

function _srJppSetStatus(html, cls) {
  const el = document.getElementById('sr-jpp-status');
  if (!el) return;
  el.innerHTML = html || '';
  el.className = 'sr-jpp-status ' + (cls || '');
}

async function srJppOpen() {
  const btn = document.getElementById('btn-jpp-open');
  if (btn) btn.disabled = true;
  _srJppSetBadge('busy', '🟡 起動中…');
  _srJppSetStatus('ブラウザを起動しています…', 'info');
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/jplatpat/open`, {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      _srJppSetBadge('on', '🟢 起動中');
      _srJppSetStatus(
        '<strong>J-PlatPat を開きました。</strong> ' +
        '次に J-PlatPat 側で「<b>論理式入力</b>」タブに切り替えてから、<b>②</b> を押してください。',
        'success');
    } else {
      _srJppSetBadge('off', '🔴 失敗');
      _srJppSetStatus('起動失敗: ' + (d.error || '不明なエラー'), 'error');
    }
  } catch (e) {
    _srJppSetBadge('off', '🔴 失敗');
    _srJppSetStatus('通信エラー: ' + e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function srJppFill() {
  const formula = (document.getElementById('sr-formula').value || '').trim();
  if (!formula) { alert('検索式を入力してください'); return; }
  const btn = document.getElementById('btn-jpp-fill');
  if (btn) btn.disabled = true;
  _srJppSetStatus('式を入力中…', 'info');
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/jplatpat/fill`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({formula}),
    });
    const d = await r.json();
    if (d.ok) {
      _srJppSetStatus(
        `<strong>式を入力しました (${d.filled_chars}文字)</strong>。 ` +
        'J-PlatPat 画面で「<b>検索</b>」ボタンを押して結果を表示させた後、<b>③</b> を押してください。',
        'success');
    } else {
      _srJppSetStatus('入力失敗: ' + (d.error || '不明なエラー'), 'error');
    }
  } catch (e) {
    _srJppSetStatus('通信エラー: ' + e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function srJppScrape() {
  const formula = (document.getElementById('sr-formula').value || '').trim();
  if (!formula) { alert('検索式を入力してください (ラン保存に必要)'); return; }
  const level = document.getElementById('sr-level').value;
  const maxResults = parseInt(document.getElementById('sr-max').value || '50', 10);
  const btn = document.getElementById('btn-jpp-scrape');
  if (btn) btn.disabled = true;
  _srJppSetStatus('結果を読み取り中…', 'info');
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/jplatpat/scrape`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        formula, formula_level: level,
        max_results: maxResults,
        parent_run_id: _srParentRunId || null,
        save_run: true,
      }),
    });
    const d = await r.json();
    if (d.ok) {
      const n = d.count ?? 0;
      let msg = `<strong>${n} 件取り込みました</strong> (ラン ID: ${d.run?.run_id || '?'})`;
      if (d.diff_summary) {
        const ds = d.diff_summary;
        msg += ` / 親ラン比: 共通${ds.common} 新規${ds.added} 消失${ds.removed}`;
      }
      _srJppSetStatus(msg, 'success');
      srClearParent();
      await loadSearchRuns();
      if (d.run?.run_id) srOpenRun(d.run.run_id);
    } else {
      _srJppSetStatus('取り込み失敗: ' + (d.error || '不明なエラー'), 'error');
    }
  } catch (e) {
    _srJppSetStatus('通信エラー: ' + e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function srJppClose() {
  try {
    await fetch(`/case/${CASE_ID}/search-run/jplatpat/close`, {method: 'POST'});
  } catch (e) {}
  _srJppSetBadge('off', '🔴 未起動');
  _srJppSetStatus('セッションを閉じました。', 'info');
}

// 起動時にセッション状態を反映
async function srJppCheckStatus() {
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/jplatpat/status`);
    const d = await r.json();
    if (d.alive) {
      _srJppSetBadge('on', '🟢 起動中');
    } else {
      _srJppSetBadge('off', '🔴 未起動');
    }
  } catch (e) {}
}

// 現在のメインエディタの式をクリップボードへコピー
function srCopyMainFormula() {
  const ta = document.getElementById('sr-formula');
  if (!ta) return;
  navigator.clipboard.writeText(ta.value || '').then(() => {
    _srShowToast('検索式をクリップボードにコピーしました');
  }).catch(() => {
    _srShowToast('コピーに失敗しました');
  });
}

// 検索を実行せず、式だけ保存 (source='formula_only', ヒット 0 件のラン)
async function srSaveFormulaOnly() {
  const formula = document.getElementById('sr-formula').value.trim();
  if (!formula) { alert('検索式を入力してください'); return; }
  const level = document.getElementById('sr-level').value;
  const status = document.getElementById('sr-exec-status');
  if (status) status.textContent = '保存中…';
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/execute`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        formula, formula_level: level,
        source: 'formula_only',
        max_results: 0,
        auto_click_search: false,
        parent_run_id: _srParentRunId || null,
      }),
    });
    const data = await r.json();
    if (!r.ok) { if (status) status.textContent = ''; alert(data.error || '保存エラー'); return; }
    if (status) status.textContent = '式を保存しました (0件)';
    _srShowToast('検索式をラン登録しました (0件)');
    srClearParent();
    await loadSearchRuns();
    if (data.run?.run_id) { srOpenRun(data.run.run_id); }
  } catch (e) {
    if (status) status.textContent = '';
    alert('通信エラー: ' + e.message);
  }
}

// 軽量トースト
function _srShowToast(msg, ms = 2800) {
  let el = document.getElementById('_sr_toast');
  if (!el) {
    el = document.createElement('div');
    el.id = '_sr_toast';
    el.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1e293b;color:#fff;padding:10px 16px;border-radius:8px;z-index:2147483647;font-size:13px;box-shadow:0 6px 20px rgba(0,0,0,.3);opacity:0;transition:opacity .2s';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.opacity = '1';
  clearTimeout(el._tm);
  el._tm = setTimeout(() => { el.style.opacity = '0'; }, ms);
}

function renderSrRunsList() {
  const el = document.getElementById('sr-runs-list');
  const countEl = document.getElementById('sr-runs-count');
  if (!el) return;
  if (!_srRuns.length) {
    el.innerHTML = '<div style="font-size:0.85rem; color:var(--text2); padding:0.5rem;">まだ検索ランがありません</div>';
    if (countEl) countEl.textContent = '';
    return;
  }
  if (countEl) countEl.textContent = `(${_srRuns.length}件)`;
  el.innerHTML = _srRuns.map(r => {
    const hit = r.hit_count ?? 0;
    const star = r.stars ?? 0;
    const fx = (r.formula || '').replace(/</g, '&lt;');
    const short = fx.length > 80 ? fx.slice(0, 80) + '…' : fx;
    const parentBadge = r.parent_run_id
      ? `<span class="sr-parent-badge" title="親ラン: ${r.parent_run_id}">← ${r.parent_run_id.slice(9, 15)}</span>`
      : '';
    return `<div class="sr-run-item" data-run-id="${r.run_id}">
      <div class="sr-run-head">
        <span class="sr-run-level">${r.formula_level || '?'}</span>
        <span class="sr-run-source">${r.source || '?'}</span>
        <span class="sr-run-date">${(r.created_at || '').slice(0, 16)}</span>
        <span class="sr-run-count">${hit}件 / ★${star}</span>
        ${parentBadge}
        <span style="flex:1"></span>
        <button class="btn btn-primary" style="padding:0.2rem 0.6rem; font-size:0.75rem;"
                onclick="srOpenRun('${r.run_id}')">開く</button>
        <button class="btn btn-outline" style="padding:0.2rem 0.6rem; font-size:0.75rem;"
                onclick="srDuplicateForEdit('${r.run_id}')"
                title="この式を複製して編集モードに">複製して編集</button>
        <button class="btn btn-danger" style="padding:0.2rem 0.6rem; font-size:0.75rem;"
                onclick="srDeleteRun('${r.run_id}')">削除</button>
      </div>
      <div class="sr-run-formula">${short}</div>
    </div>`;
  }).join('');
}

async function srDeleteRun(runId) {
  if (!confirm(`ラン ${runId} を削除しますか？`)) return;
  const r = await fetch(`/case/${CASE_ID}/search-run/${runId}`, {method: 'DELETE'});
  if (r.ok) {
    loadSearchRuns();
    if (_srCurrentRun && _srCurrentRun.run_id === runId) {
      _srCurrentRun = null;
      document.getElementById('sr-hits-panel').style.display = 'none';
    }
  }
}

async function srOpenRun(runId) {
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/${runId}`);
    if (!r.ok) { alert('ランの読み込みに失敗'); return; }
    _srCurrentRun = await r.json();
    document.getElementById('sr-hits-panel').style.display = 'block';
    document.getElementById('sr-hits-title').textContent =
      `候補一覧: ${_srCurrentRun.formula_level || ''} (${_srCurrentRun.source || ''})`;
    await srRenderDiffSummary();
    // 表抽出ステータスもロード (failed silently は ignore)
    if (typeof srLoadTableExtractStatus === 'function') {
      await srLoadTableExtractStatus();
    }
    renderSrHits();
    document.getElementById('sr-hits-panel').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (e) { alert('エラー: ' + e.message); }
}

async function srRenderDiffSummary() {
  const el = document.getElementById('sr-diff-summary');
  if (!el) return;
  el.style.display = 'none';
  el.innerHTML = '';
  if (!_srCurrentRun || !_srCurrentRun.parent_run_id) return;
  const parent = _srCurrentRun.parent_run_id;
  try {
    const r = await fetch(
      `/case/${CASE_ID}/search-run/${_srCurrentRun.run_id}/diff?base=${encodeURIComponent(parent)}`
    );
    if (!r.ok) return;
    const d = await r.json();
    const s = d.summary || {};
    el.innerHTML = `
      <span class="sr-diff-label">親ラン <code>${parent}</code> との差分:</span>
      <span class="sr-diff-chip sr-diff-common" title="両方のランに共通する文献">共通 ${s.common || 0}</span>
      <span class="sr-diff-chip sr-diff-added" title="このランで新たに出現した文献">新規 ${s.added || 0}</span>
      <span class="sr-diff-chip sr-diff-removed" title="親ランにはあったがこのランでは消えた文献">消失 ${s.removed || 0}</span>
      <button class="sr-tool-btn" style="margin-left:auto;" onclick="srShowDiffDetail()">詳細</button>
    `;
    el.dataset.diff = JSON.stringify(d);
    el.style.display = 'flex';
  } catch (e) { /* silent */ }
}

function srShowDiffDetail() {
  const el = document.getElementById('sr-diff-summary');
  if (!el || !el.dataset.diff) return;
  let d; try { d = JSON.parse(el.dataset.diff); } catch (e) { return; }
  const section = (label, items, cls) => {
    if (!items || !items.length) return `<div class="sr-diff-detail-sec"><h4 class="${cls}">${label} (0)</h4><div class="muted">なし</div></div>`;
    const rows = items.slice(0, 30).map(h => {
      const pid = (h.patent_id || '').replace(/</g, '&lt;');
      const title = (h.title || '').replace(/</g, '&lt;');
      const scr = h.screening || 'pending';
      return `<li><code>${pid}</code> <span class="sr-diff-scr-${scr}">[${scr}]</span> ${title}</li>`;
    }).join('');
    const more = items.length > 30 ? `<li class="muted">...他 ${items.length - 30} 件</li>` : '';
    return `<div class="sr-diff-detail-sec">
      <h4 class="${cls}">${label} (${items.length})</h4>
      <ul class="sr-diff-list">${rows}${more}</ul>
    </div>`;
  };
  const modal = document.createElement('div');
  modal.className = 'sr-modal-backdrop';
  modal.innerHTML = `
    <div class="sr-modal">
      <div class="sr-modal-head">
        <h3>検索ラン差分詳細</h3>
        <button class="sr-tool-btn" onclick="this.closest('.sr-modal-backdrop').remove()">閉じる</button>
      </div>
      <div class="sr-modal-body">
        <p style="font-size:0.8rem; color:var(--text2);">
          比較: <code>${d.run_id}</code> vs 親 <code>${d.base_run_id}</code>
        </p>
        ${section('新規 (このランでのみ出現)', d.only_new, 'sr-diff-added')}
        ${section('消失 (親ランにのみ)', d.only_base, 'sr-diff-removed')}
        ${section('共通', d.common, 'sr-diff-common')}
      </div>
    </div>`;
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.remove();
  });
  document.body.appendChild(modal);
}

async function searchRunExecute() {
  const formula = document.getElementById('sr-formula').value.trim();
  if (!formula) { alert('検索式を入力してください'); return; }
  const level = document.getElementById('sr-level').value;
  let source = document.getElementById('sr-source').value;
  const maxResults = parseInt(document.getElementById('sr-max').value || '50', 10);

  // J-PlatPat 自動遷移トグルが OFF の場合は式の保存のみ
  const autoToggle = document.getElementById('sr-enable-jplatpat-auto');
  if (source === 'jplatpat' && autoToggle && !autoToggle.checked) {
    const ok = confirm(
      'J-PlatPat 自動遷移は OFF です。\n\n' +
      '検索を実行せず「式の保存のみ」のランを作成します。よろしいですか?\n' +
      '(自動遷移したい場合はチェックを入れてください)'
    );
    if (!ok) return;
    return srSaveFormulaOnly();
  }

  const btn = document.getElementById('btn-sr-execute');
  const loading = document.getElementById('loading-sr-exec');
  const status = document.getElementById('sr-exec-status');
  btn.disabled = true;
  loading.classList.add('show');
  status.textContent = '実行中…';

  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/execute`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        formula, formula_level: level, source,
        max_results: maxResults,
        auto_click_search: true,
        parent_run_id: _srParentRunId || null,
      }),
    });
    const data = await r.json();
    btn.disabled = false;
    loading.classList.remove('show');
    if (!r.ok) { status.textContent = ''; alert(data.error || '実行エラー'); return; }
    const hitN = data.run?.hit_count ?? 0;
    let summary = `${hitN}件取得`;
    if (data.diff_summary) {
      const d = data.diff_summary;
      summary += ` / 親ラン比: 共通${d.common} 新規${d.added} 消失${d.removed}`;
    }
    status.textContent = summary;
    srClearParent();
    await loadSearchRuns();
    if (data.run?.run_id) { srOpenRun(data.run.run_id); }
  } catch (e) {
    btn.disabled = false;
    loading.classList.remove('show');
    status.textContent = '';
    alert('通信エラー: ' + e.message);
  }
}

function renderSrHits() {
  const el = document.getElementById('sr-hits-list');
  if (!el) return;

  // グループ語フィルタ・ソート の UI は run の有無に関わらず同期
  _srRenderGroupFilterUI();
  _srRebuildSortDropdown();

  if (!_srCurrentRun) { el.innerHTML = ''; return; }

  const filter = (document.getElementById('sr-filter') || {}).value || '';
  const sortBy = (document.getElementById('sr-sort') || {}).value || 'default';
  let hits = [..._srCurrentRun.hits];

  if (filter === 'star') hits = hits.filter(h => h.screening === 'star');
  else if (filter === 'triangle') hits = hits.filter(h => h.screening === 'triangle');
  else if (filter === 'pending') hits = hits.filter(h => (h.screening || 'pending') === 'pending');
  else if (filter === 'not-rejected') hits = hits.filter(h => h.screening !== 'reject');

  // グループヒット数のメモ化（フィルタ・ソートで複数回参照されるため）
  const _countsMemo = new Map();
  const getCounts = (h) => {
    const k = h.patent_id || '';
    if (!_countsMemo.has(k)) _countsMemo.set(k, _srComputeHitGroupCounts(h));
    return _countsMemo.get(k);
  };

  // グループ語フィルタ（選択されたグループの語が文献に含まれるか）
  if (_srGroupFilter && _srGroupFilter.size > 0) {
    const sel = Array.from(_srGroupFilter); // 文字列キー
    hits = hits.filter(h => {
      const counts = getCounts(h);
      return _srGroupFilterMode === 'or'
        ? sel.some(gid => (counts[gid] || 0) > 0)
        : sel.every(gid => (counts[gid] || 0) > 0);
    });
  }

  if (sortBy === 'ai_score') {
    hits.sort((a, b) => (b.ai_score ?? -1) - (a.ai_score ?? -1));
  } else if (sortBy === 'date') {
    hits.sort((a, b) => (b.publication_date || '').localeCompare(a.publication_date || ''));
  } else if (sortBy === 'group_total') {
    // 選択中のグループがあれば選択分の合計、なければ全グループの合計
    const sel = (_srGroupFilter && _srGroupFilter.size > 0)
      ? Array.from(_srGroupFilter)
      : (kwGroups || []).map(g => String(g.group_id));
    const sum = (h) => {
      const c = getCounts(h);
      return sel.reduce((s, gid) => s + (c[gid] || 0), 0);
    };
    hits.sort((a, b) => sum(b) - sum(a));
  } else if (typeof sortBy === 'string' && sortBy.startsWith('group:')) {
    const gid = sortBy.slice('group:'.length);
    hits.sort((a, b) => (getCounts(b)[gid] || 0) - (getCounts(a)[gid] || 0));
  }

  // 統計
  const stats = _srCurrentRun.hits.reduce((acc, h) => {
    const k = h.screening || 'pending';
    acc[k] = (acc[k] || 0) + 1;
    return acc;
  }, {});
  const gfNote = (_srGroupFilter && _srGroupFilter.size > 0)
    ? ` / グループ絞込み:${hits.length}件`
    : '';
  document.getElementById('sr-hits-stats').textContent =
    `全${_srCurrentRun.hits.length}件 / ★${stats.star || 0} △${stats.triangle || 0} ×${stats.reject || 0} …${stats.hold || 0} 未${stats.pending || 0}${gfNote}`;

  if (!hits.length) {
    el.innerHTML = '<div style="font-size:0.85rem; color:var(--text2); padding:0.5rem;">フィルタ該当なし</div>';
    return;
  }

  el.innerHTML = hits.map(h => srRenderHitCard(h)).join('');
}

// ===== グループ語フィルタ =====
let _srGroupFilter = new Set();
let _srGroupFilterMode = 'and'; // 'and' | 'or'

// ヒット 1 件についてグループごとの語ヒット数（本文+抽出表）を返す
function _srComputeHitGroupCounts(h) {
  const idx = _pkmGetIndex();
  if (!idx.length) return {};
  const pid = h.patent_id || '';
  const ft = (window._pkmFullTexts && window._pkmFullTexts[pid]) || null;
  const titleSrc = (ft && ft.title) || h.title || '';
  const abstractSrc = (ft && ft.abstract) || h.abstract || '';
  const claim1Src = h.claim1 || '';
  const counts = {};
  const addTo = c => Object.keys(c || {}).forEach(k => { counts[k] = (counts[k] || 0) + c[k]; });
  addTo(pkmHighlight(titleSrc, idx).counts);
  addTo(pkmHighlight(abstractSrc, idx).counts);
  addTo(pkmHighlight(claim1Src, idx).counts);
  if (ft && Array.isArray(ft.claims)) addTo(pkmHighlight(ft.claims.join('\n\n'), idx).counts);
  if (ft && ft.description) addTo(pkmHighlight(ft.description, idx).counts);
  const cellText = (window._tableExtractCells && window._tableExtractCells[pid]) || '';
  if (cellText) addTo(pkmHighlight(cellText, idx).counts);
  return counts;
}

function srToggleGroupFilter(gid) {
  const key = String(gid);
  if (_srGroupFilter.has(key)) _srGroupFilter.delete(key);
  else _srGroupFilter.add(key);
  renderSrHits();
}

function srClearGroupFilter() {
  if (_srGroupFilter.size === 0) return;
  _srGroupFilter.clear();
  renderSrHits();
}

function srSetGroupFilterMode(mode) {
  const next = (mode === 'or') ? 'or' : 'and';
  if (_srGroupFilterMode === next) return;
  _srGroupFilterMode = next;
  renderSrHits();
}

function _srRebuildSortDropdown() {
  const sel = document.getElementById('sr-sort');
  if (!sel) return;
  const groups = (kwGroups || []);
  const desired = ['default', 'ai_score', 'date'];
  if (groups.length > 0) desired.push('group_total');
  for (const g of groups) desired.push('group:' + String(g.group_id));

  // 既存と一致するなら再構築しない（ユーザー選択を維持＆無駄な DOM 更新回避）
  const existing = Array.from(sel.options).map(o => o.value);
  const sameLen = existing.length === desired.length;
  const same = sameLen && existing.every((v, i) => v === desired[i]);
  const current = sel.value || 'default';
  if (same) return;

  const esc = (s) => _pkmEsc(s);
  const trunc = (s, n) => {
    const arr = [...String(s || '')];
    return arr.length > n ? arr.slice(0, n).join('') + '…' : arr.join('');
  };

  const opts = [
    '<option value="default">元の順</option>',
    '<option value="ai_score">AIスコア(高→低)</option>',
    '<option value="date">公開日(新→古)</option>',
  ];
  if (groups.length > 0) {
    opts.push('<option value="group_total">グループ語ヒット数 合計(高→低)</option>');
    for (const g of groups) {
      const gid = String(g.group_id);
      const lbl = trunc(g.label || ('group' + gid), 14);
      opts.push(`<option value="group:${esc(gid)}">🎯 ${esc(lbl)} のヒット数(高→低)</option>`);
    }
  }
  sel.innerHTML = opts.join('');
  sel.value = desired.includes(current) ? current : 'default';
}

function _srRenderGroupFilterUI() {
  const el = document.getElementById('sr-group-filter');
  if (!el) return;
  const groups = (kwGroups || []);
  if (!groups.length) {
    el.innerHTML = '<span class="sr-gf-empty">Step 3 のキーワードグループ未登録 — グループ絞り込みは利用できません</span>';
    return;
  }

  // 選択集合に存在しない gid は掃除（kwGroups 変更時の不整合対策）
  const valid = new Set(groups.map(g => String(g.group_id)));
  for (const gid of Array.from(_srGroupFilter)) {
    if (!valid.has(gid)) _srGroupFilter.delete(gid);
  }

  const buttons = groups.map(g => {
    const gidStr = String(g.group_id);
    const c = groupColor(g.group_id);
    const on = _srGroupFilter.has(gidStr);
    const cls = on ? 'sr-gf-btn sr-gf-btn-on' : 'sr-gf-btn';
    const label = _pkmEsc(g.label || ('group' + g.group_id));
    const tip = `「${g.label || ''}」の語を含む文献に絞り込み`;
    return `<button class="${cls}" style="--c:${c};" onclick="srToggleGroupFilter('${gidStr}')" title="${_pkmEsc(tip)}">${label}</button>`;
  }).join('');

  const activeCount = _srGroupFilter.size;
  const mode = _srGroupFilterMode;
  const modeRow = activeCount >= 2
    ? `<span class="sr-gf-mode-row">
         <span class="sr-gf-mode-label">複数選択時:</span>
         <button class="sr-gf-mode ${mode === 'and' ? 'on' : ''}" onclick="srSetGroupFilterMode('and')" title="選択した全グループの語を含む文献のみ表示">AND</button>
         <button class="sr-gf-mode ${mode === 'or' ? 'on' : ''}" onclick="srSetGroupFilterMode('or')" title="選択したいずれかのグループの語を含む文献を表示">OR</button>
       </span>`
    : '';
  const clearBtn = activeCount > 0
    ? `<button class="sr-gf-clear" onclick="srClearGroupFilter()" title="フィルタを全解除">× クリア (${activeCount})</button>`
    : '';

  el.innerHTML = `
    <span class="sr-gf-label">グループ語で絞込み:</span>
    ${buttons}
    ${modeRow}
    ${clearBtn}
  `;
}

function srRenderHitCard(h) {
  const screening = h.screening || 'pending';
  const scrClass = SR_SCREEN_CLASS[screening] || 'sr-pending';
  const pid = h.patent_id || '';
  const date = h.publication_date || '';
  const ipc = (h.ipc || []).slice(0, 3).join(' ');
  const aiScore = (h.ai_score != null) ? `<span class="sr-score">AI: ${h.ai_score}</span>` : '';
  const aiReason = _pkmEsc(h.ai_reason || '');
  const dled = h.downloaded_as_citation ? '<span class="sr-dled">引用登録済</span>' : '';
  const jpUrl = (typeof buildJplatpatUrl === 'function') ? (buildJplatpatUrl(pid) || '') : '';
  const gpUrl = `https://patents.google.com/?q=${encodeURIComponent(pid)}`;

  const idx = _pkmGetIndex();
  const useHighlight = idx.length > 0 && _pkmEnabled;
  // 全文キャッシュ
  const ft = (window._pkmFullTexts && window._pkmFullTexts[pid]) || null;
  // 全文取得済みなら、ヒット側の値より取得時のメタ情報を優先（特に title が空の場合の救出）
  const titleSrc = (ft && ft.title) || h.title || '';
  const applicantSrc = (ft && ft.assignee) || h.applicant || '';
  const abstractSrc = h.abstract || '';
  const claim1Src = h.claim1 || '';

  // ヒット数集計（本文集計と表集計を分けて持つ）
  const bodyCounts = {};
  const tableCounts = {};
  const addTo = (target, c) => Object.keys(c || {}).forEach(k => { target[k] = (target[k] || 0) + c[k]; });
  if (useHighlight) {
    addTo(bodyCounts, pkmHighlight(titleSrc, idx).counts);
    addTo(bodyCounts, pkmHighlight((ft && ft.abstract) || abstractSrc, idx).counts);
    addTo(bodyCounts, pkmHighlight(claim1Src, idx).counts);
    if (ft && Array.isArray(ft.claims)) addTo(bodyCounts, pkmHighlight(ft.claims.join('\n\n'), idx).counts);
    if (ft && ft.description) addTo(bodyCounts, pkmHighlight(ft.description, idx).counts);
    // 抽出済み表のセルから集計
    const cellText = (window._tableExtractCells && window._tableExtractCells[pid]) || '';
    if (cellText) addTo(tableCounts, pkmHighlight(cellText, idx).counts);
  }

  // チップ（kwGroups があれば常に表示。0件もグレーで見せて「対象未ヒット」を伝える）
  let countBar = '';
  const totalCounts = {};
  const allKeys = new Set([...Object.keys(bodyCounts), ...Object.keys(tableCounts)]);
  for (const k of allKeys) totalCounts[k] = (bodyCounts[k] || 0) + (tableCounts[k] || 0);
  const groups = (kwGroups || []).slice().sort((a, b) =>
    (totalCounts[b.group_id] || 0) - (totalCounts[a.group_id] || 0));
  if (groups.length > 0) {
    const chips = groups.map(g => {
      const nb = bodyCounts[g.group_id] || 0;
      const nt = tableCounts[g.group_id] || 0;
      const n = nb + nt;
      const c = groupColor(g.group_id);
      const cls = n > 0 ? 'sr-pkm-chip-on' : 'sr-pkm-chip-off';
      const tblMark = nt > 0 ? `<span class="sr-pkm-chip-tbl" title="表内ヒット ${nt} 件">📊${nt}</span>` : '';
      const tip = `${(g.label || ('group'+g.group_id))} | 本文 ${nb} / 表 ${nt}`;
      return `<span class="sr-pkm-chip ${cls}" style="--c:${c};" title="${_pkmEsc(tip)}">${_pkmEsc((g.label || '').slice(0, 8))}<span class="sr-pkm-chip-n">${nb}</span>${tblMark}</span>`;
    }).join('');
    const scope = ft ? '全文' : (abstractSrc || claim1Src ? '要約のみ' : '未取得');
    const tableScope = (window._tableExtractCells && window._tableExtractCells[pid]) ? ' + 表' : '';
    countBar = `<div class="sr-pkm-counts" title="集計対象: ${scope}${tableScope}">${chips}<span class="sr-pkm-scope">${scope}${tableScope}</span></div>`;
  }

  // 取得元バッジ
  const srcBadge = (() => {
    if (!ft || !ft.source) {
      return '<span class="sr-hit-src sr-src-none">未取得</span>';
    }
    const map = {
      'jplatpat': { label: 'J-PlatPat', cls: 'sr-src-jp', tip: 'J-PlatPat から取得' },
      'google': { label: 'Google', cls: 'sr-src-gp', tip: 'Google Patents から取得' },
      'google_fallback': { label: 'Google', cls: 'sr-src-gp', tip: 'J-PlatPat 失敗 → Google Patents から代替取得' },
    };
    const m = map[ft.source] || { label: ft.source, cls: '', tip: '' };
    return `<span class="sr-hit-src ${m.cls}" title="${_pkmEsc(m.tip)}">${m.label}</span>`;
  })();

  // 個別取得ボタン（コンパクト）
  const fetchBtnLabel = ft ? '🔄' : '📄';
  const fetchBtnTitle = ft ? '全文を再取得' : 'この文献の全文を取得';

  // 表抽出可能条件: 引用登録済み (PDF あり) または全文取得済み (Google から images 取得済み)
  // ft (window._pkmFullTexts[pid]) は srFetchHitFullText 後に images を含んでいる
  const extractStatus = (window._tableExtractStatus && window._tableExtractStatus[pid]) || null;
  const hasImages = !!(ft && Array.isArray(ft.images) && ft.images.length > 0);
  const canExtract = !!h.downloaded_as_citation || hasImages;
  const extractBadge = (() => {
    if (!canExtract) return '';
    if (extractStatus && extractStatus.extracted) {
      const n = extractStatus.n_table || 0;
      const tip = `クリックで抽出済みの表を表示 (${n}件 / コスト相当 $${extractStatus.cost || 0})`;
      return `<span class="sr-tbl-done" title="${_pkmEsc(tip)}" onclick="srShowCitationTables('${_pkmEsc(pid).replace(/'/g, "\\'")}')" style="cursor:pointer;">📊×${n}</span>`;
    }
    return '';
  })();
  const extractBtnLabel = (extractStatus && extractStatus.extracted) ? '🔄' : '📊';
  const extractBtnTitle = (extractStatus && extractStatus.extracted)
    ? '表を再抽出 (Vision サブスク消費)'
    : (canExtract
        ? `この引例の図表を Vision で抽出 (${h.downloaded_as_citation ? 'PDF' : 'Google画像'} ${hasImages ? `${ft.images.length}枚` : ''})`
        : '抽出するには 📄 で全文取得 か ☆→DL を先に実行してください');
  // 一括選択チェックボックス（常に選択可。一括抽出実行時に自動で全文取得を試行）
  const selChecked = (window._tableExtractSelected && window._tableExtractSelected.has(pid)) ? 'checked' : '';
  const selTitle = canExtract
    ? '表抽出の一括選択'
    : 'PDF/画像 未取得 — 一括抽出時に自動で全文取得を試みます';

  // タイトル → 全文ハイライトビューを新タブで開く
  const viewUrl = `/case/${encodeURIComponent(CASE_ID)}/search-run/hit/${encodeURIComponent(pid)}/view`;
  const titleEsc = _pkmEsc(titleSrc) || '<em style="color:var(--text2);">(タイトル未取得 — クリックで全文取得)</em>';

  return `<div class="sr-hit-card ${scrClass}" data-pid="${pid}">
    <div class="sr-hit-row1">
      <div class="sr-hit-actions">
        <input type="checkbox" class="sr-hit-tblsel" data-pid="${pid}" ${selChecked} title="${_pkmEsc(selTitle)}" onchange="srToggleTableSel('${pid}', this.checked)">
        ${['star', 'triangle', 'pending', 'hold', 'reject'].map(s => {
          const active = (s === screening) ? 'active' : '';
          return `<button class="sr-btn-${s} ${active}" title="${s}"
                   onclick="srSetScreening('${pid}', '${s}')">${SR_SCREEN_LABELS[s]}</button>`;
        }).join('')}
      </div>
      <div class="sr-hit-meta">
        <span class="sr-hit-pid">${pid}</span>
        <span class="sr-hit-date">${date}</span>
        <span class="sr-hit-ipc">${ipc}</span>
        ${aiScore}${dled}${extractBadge}
        <span style="flex:1"></span>
        ${srcBadge}
        <button class="sr-hit-fetch-btn" onclick="srFetchHitFullText('${_pkmEsc(pid).replace(/'/g, "\\'")}', this)" title="${fetchBtnTitle}">${fetchBtnLabel}</button>
        <button class="sr-hit-fetch-btn" onclick="srExtractOneTable('${_pkmEsc(pid).replace(/'/g, "\\'")}', this)" title="${extractBtnTitle}" ${canExtract ? '' : 'disabled'}>${extractBtnLabel}</button>
        ${jpUrl ? `<a href="${jpUrl}" target="_blank" class="sr-hit-link">J-PlatPat</a>` : ''}
        <a href="${gpUrl}" target="_blank" class="sr-hit-link">Google Patents</a>
      </div>
    </div>
    <h4 class="sr-hit-title-row"><a href="${viewUrl}" target="_blank" rel="noopener" class="sr-hit-title-link" title="クリックで全文ハイライトビューを新タブで開く">${titleEsc} <span class="sr-hit-open-icon">↗</span></a></h4>
    <div class="sr-hit-applicant">${_pkmEsc(applicantSrc)}</div>
    ${countBar}
    ${aiReason ? `<div class="sr-hit-reason">AI: ${aiReason}</div>` : ''}
  </div>`;
}

// ヒットの全文を Google Patents から取得しセッションキャッシュへ
window._pkmFullTexts = window._pkmFullTexts || {};

function _pkmGetSelectedSource() {
  const el = document.getElementById('sr-pkm-source');
  return (el && el.value) || 'auto';
}

async function srFetchHitFullText(patentId, btn) {
  if (!patentId) return;
  const origLabel = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '取得中...'; }
  const source = _pkmGetSelectedSource();
  // 既にキャッシュ (window._pkmFullTexts) を持っていれば「再取得」とみなし force=true。
  // そうでなければ初回取得 (force=false。サーバ側ファイルキャッシュがあればそれを使う)。
  const isRefetch = !!(window._pkmFullTexts && window._pkmFullTexts[patentId]);
  try {
    const r = await fetch(`/case/${encodeURIComponent(CASE_ID)}/search-run/hit/${encodeURIComponent(patentId)}/fetch-text`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({force: isRefetch, source}),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.error || ('HTTP ' + r.status));
    }
    const data = await r.json();
    window._pkmFullTexts[patentId] = data;
    srRerenderOneHitCard(patentId);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = origLabel; }
    alert('全文取得失敗: ' + (e && e.message ? e.message : String(e)));
  }
}

// 1 件のヒットカードだけ再描画（全件 renderSrHits より高速＆視覚的）
function srRerenderOneHitCard(patentId) {
  if (!_srCurrentRun) return;
  const h = (_srCurrentRun.hits || []).find(x => x.patent_id === patentId);
  if (!h) return;
  const card = document.querySelector(`.sr-hit-card[data-pid="${CSS.escape(patentId)}"]`);
  if (!card) return;
  const tmp = document.createElement('div');
  tmp.innerHTML = srRenderHitCard(h);
  const fresh = tmp.firstElementChild;
  if (fresh) card.replaceWith(fresh);
}

// N 並列ワーカーでアイテムを処理（順序保持しない、完了したものから処理）
async function _runWithConcurrency(items, fn, concurrency) {
  let idx = 0;
  const workers = [];
  for (let w = 0; w < concurrency; w++) {
    workers.push((async () => {
      while (idx < items.length) {
        const i = idx++;
        try { await fn(items[i], i); } catch (e) { /* 個別失敗はスキップ */ }
      }
    })());
  }
  await Promise.all(workers);
}

async function srFetchAllHitsFullText(opts) {
  opts = opts || {};
  const force = !!opts.force;
  if (!_srCurrentRun) {
    alert('検索ランが選択されていません。先に検索を実行してください。');
    return;
  }
  const hits = _srCurrentRun.hits || [];
  if (hits.length === 0) { alert('ヒットがありません。'); return; }
  const source = _pkmGetSelectedSource();
  // force=true なら全件、そうでなければ未取得のみ
  const target = force ? hits.slice() : hits.filter(h => !window._pkmFullTexts[h.patent_id]);
  if (target.length === 0) {
    alert('全ヒットの全文は既に取得済みです（強制再取得は「🔄 全部再取得」から）');
    return;
  }
  if (force) {
    if (!confirm(`${target.length} 件の全文を取得元(${source})から強制再取得します。実行しますか？`)) {
      return;
    }
  }
  const btn = document.querySelector('button[onclick="srFetchAllHitsFullText()"]');
  const status = document.getElementById('sr-pkm-bulk-status');
  const bar = document.getElementById('sr-pkm-bulk-bar');
  const barFill = document.getElementById('sr-pkm-bulk-bar-fill');
  if (btn) btn.disabled = true;
  if (bar) bar.style.display = 'block';

  // 並列度: 4 (Ryzen 9 でブラウザ4インスタンス並走 — 体感 3〜4倍速)
  const CONCURRENCY = 4;
  let done = 0, ok = 0, ng = 0;
  const total = target.length;
  const t0 = Date.now();

  await _runWithConcurrency(target, async (h) => {
    try {
      const r = await fetch(`/case/${encodeURIComponent(CASE_ID)}/search-run/hit/${encodeURIComponent(h.patent_id)}/fetch-text`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({force, source}),
      });
      if (r.ok) {
        window._pkmFullTexts[h.patent_id] = await r.json();
        ok++;
        srRerenderOneHitCard(h.patent_id);
      } else {
        ng++;
      }
    } catch (e) { ng++; }
    done++;
    if (barFill) barFill.style.width = `${Math.round(done / total * 100)}%`;
    if (status) {
      const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
      status.textContent = `${done}/${total} 完了 (${elapsed}s, 並列${CONCURRENCY})`;
    }
  }, CONCURRENCY);

  const total_s = ((Date.now() - t0) / 1000).toFixed(1);
  if (status) {
    status.innerHTML = `完了 (${total_s}s): ✅ ${ok}件 ${ng > 0 ? `/ ❌ ${ng}件失敗` : ''}`;
  }
  if (btn) btn.disabled = false;
}

// ================================================================
// PKM 風キーワードハイライト（Step 3 キーワードグループを色で重畳）
// ================================================================
let _pkmEnabled = true;
let _pkmIndexCache = null;

function _pkmInvalidateIndex() { _pkmIndexCache = null; }

function _pkmGetIndex() {
  if (_pkmIndexCache) return _pkmIndexCache;
  const items = [];
  for (const g of (kwGroups || [])) {
    const color = groupColor(g.group_id);
    for (const kw of (g.keywords || [])) {
      const t = (kw && kw.term ? String(kw.term) : '').trim();
      if (!t) continue;
      items.push({ term: t, gid: g.group_id, color });
    }
  }
  // 長い term を先にマッチさせる（短いものに食われないように）
  items.sort((a, b) => b.term.length - a.term.length);
  _pkmIndexCache = items;
  return items;
}

function _pkmEsc(s) {
  return String(s || '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function pkmHighlight(text, index) {
  const t = String(text || '');
  if (!t || !index || !index.length) return { html: _pkmEsc(t), counts: {} };
  const tLower = t.toLowerCase();
  const positions = [];
  for (const item of index) {
    const term = item.term;
    const tlow = term.toLowerCase();
    if (!tlow) continue;
    let pos = 0;
    while ((pos = tLower.indexOf(tlow, pos)) >= 0) {
      const overlap = positions.some(p =>
        !(pos + term.length <= p.start || pos >= p.start + p.length));
      if (!overlap) {
        positions.push({ start: pos, length: term.length, gid: item.gid, color: item.color });
      }
      pos += term.length;
    }
  }
  positions.sort((a, b) => a.start - b.start);
  const counts = {};
  positions.forEach(p => { counts[p.gid] = (counts[p.gid] || 0) + 1; });
  let out = '';
  let prev = 0;
  for (const p of positions) {
    out += _pkmEsc(t.slice(prev, p.start));
    const matched = _pkmEsc(t.slice(p.start, p.start + p.length));
    out += `<mark class="pkm-mark" style="--c:${p.color};" data-gid="${p.gid}">${matched}</mark>`;
    prev = p.start + p.length;
  }
  out += _pkmEsc(t.slice(prev));
  return { html: out, counts };
}

function srTogglePkm() {
  _pkmEnabled = !_pkmEnabled;
  const btn = document.getElementById('btn-sr-pkm');
  if (btn) {
    btn.textContent = _pkmEnabled ? '🎨 ハイライト ON' : '🎨 ハイライト OFF';
    btn.classList.toggle('active', _pkmEnabled);
  }
  if (typeof renderSrHits === 'function') renderSrHits();
}

async function srSetScreening(patentId, screening) {
  if (!_srCurrentRun) return;
  // 楽観的更新
  const hit = _srCurrentRun.hits.find(h => h.patent_id === patentId);
  if (hit) hit.screening = screening;
  renderSrHits();
  try {
    await fetch(`/case/${CASE_ID}/search-run/${_srCurrentRun.run_id}/screening`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({patent_id: patentId, screening}),
    });
  } catch (e) { console.warn('screening save error', e); }
}

async function srEnrich() {
  if (!_srCurrentRun) return;
  const loading = document.getElementById('loading-sr-enrich');
  loading.classList.add('show');
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/${_srCurrentRun.run_id}/enrich`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({limit: 20}),
    });
    const data = await r.json();
    loading.classList.remove('show');
    if (!r.ok) { alert(data.error || 'Enrichエラー'); return; }
    _srCurrentRun = data.run;
    renderSrHits();
  } catch (e) {
    loading.classList.remove('show');
    alert('通信エラー: ' + e.message);
  }
}

async function srAiScore() {
  if (!_srCurrentRun) return;
  const pending = (_srCurrentRun.hits || []).filter(h => h.ai_score == null).length;
  if (!confirm(`Claudeを呼び出して関連度スコアを計算します。\n未スコア ${pending} 件すべてを実行します。`)) return;
  const loading = document.getElementById('loading-sr-aiscore');
  loading.classList.add('show');
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/${_srCurrentRun.run_id}/ai-score`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),  // limit 未指定 = 全件
    });
    const data = await r.json();
    loading.classList.remove('show');
    if (!r.ok) { alert(data.error || 'AIスコアエラー'); return; }
    _srCurrentRun = data.run;
    document.getElementById('sr-sort').value = 'ai_score';
    renderSrHits();
  } catch (e) {
    loading.classList.remove('show');
    alert('通信エラー: ' + e.message);
  }
}

// ===== 引用文献の表抽出 (Step 4.5) =====
window._tableExtractSelected = window._tableExtractSelected || new Set();
window._tableExtractStatus = window._tableExtractStatus || {};
window._tableExtractCells = window._tableExtractCells || {};  // pid → flat cell text

async function srLoadTableExtractStatus() {
  try {
    const r = await fetch(`/case/${CASE_ID}/citations/tables-status`);
    const d = await r.json();
    const map = {};
    for (const it of (d.items || [])) map[it.citation_id] = it;
    window._tableExtractStatus = map;
  } catch(_) { /* ignore */ }
  // セル本文も同時ロード (ハイライト集計用)
  try {
    const r2 = await fetch(`/case/${CASE_ID}/citations/tables-cells`);
    const d2 = await r2.json();
    window._tableExtractCells = d2.cells || {};
  } catch(_) { /* ignore */ }
}

function srToggleTableSel(pid, checked) {
  const sel = window._tableExtractSelected;
  if (checked) sel.add(pid); else sel.delete(pid);
  // 全体カウント表示の更新
  const lbl = document.getElementById('sr-pkm-bulk-status');
  if (lbl) {
    const n = sel.size;
    lbl.textContent = n > 0 ? `表抽出選択: ${n} 件` : '';
  }
}

async function srExtractOneTable(pid, btn) {
  if (!confirm(`引例 ${pid} の図表を Vision で抽出します。\n所要 1〜2 分、サブスク消費 約 $0.05〜$0.3。続行しますか？`)) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳';
  try {
    const resp = await fetch(`/case/${encodeURIComponent(CASE_ID)}/citation/${encodeURIComponent(pid)}/extract-tables`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({force: orig === '🔄'}),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop();
      for (const block of events) {
        const line = block.split('\n').find(l => l.startsWith('data: '));
        if (!line) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.stage === 'extract' && evt.total) {
            btn.textContent = `${evt.current}/${evt.total}`;
          } else if (evt.stage === 'done') {
            const s = evt.summary || {};
            alert(`✅ ${pid}: ${s.n_table || 0} 表抽出 / 所要 ${((s.total_duration_ms || 0)/1000).toFixed(1)}s / コスト相当 $${s.total_cost_usd_equivalent || 0}`);
          } else if (evt.stage === 'skip') {
            alert(`スキップ: ${pid} は既に抽出済みです (再抽出するには 🔄 を使用)`);
          } else if (evt.stage === 'error') {
            throw new Error(evt.message);
          }
        } catch(e) {
          if (e instanceof SyntaxError) continue;
          throw e;
        }
      }
    }
    await srLoadTableExtractStatus();
    renderSrHits();
  } catch(e) {
    alert('表抽出失敗: ' + e.message);
  } finally {
    btn.disabled = false;
    // textContent は renderSrHits で再生成されるので戻し不要
  }
}

async function srShowCitationTables(pid) {
  // 既に開いていれば閉じる
  let modal = document.getElementById('cit-tables-modal');
  if (modal) { modal.remove(); return; }
  modal = document.createElement('div');
  modal.id = 'cit-tables-modal';
  modal.className = 'cit-tables-modal';
  modal.innerHTML = `
    <div class="cit-tables-overlay" onclick="document.getElementById('cit-tables-modal').remove()"></div>
    <div class="cit-tables-box">
      <div class="cit-tables-head">
        <span class="cit-tables-title">📊 ${_pkmEsc(pid)} の抽出表</span>
        <button class="cit-tables-close" onclick="document.getElementById('cit-tables-modal').remove()">×</button>
      </div>
      <div id="cit-tables-body" class="cit-tables-body">読み込み中...</div>
    </div>
  `;
  document.body.appendChild(modal);
  try {
    const r = await fetch(`/case/${encodeURIComponent(CASE_ID)}/citation/${encodeURIComponent(pid)}/tables`);
    const d = await r.json();
    const body = document.getElementById('cit-tables-body');
    if (!d.exists) {
      body.innerHTML = '<p style="color:var(--text2);">抽出データが見つかりません。</p>';
      return;
    }
    body.innerHTML = _renderCitationTablesHtml(d.data, pid);
  } catch(e) {
    document.getElementById('cit-tables-body').innerHTML = `<p style="color:#f87171;">読み込み失敗: ${_pkmEsc(e.message)}</p>`;
  }
}

function _renderCitationTablesHtml(payload, pid) {
  const tables = (payload.tables || []).filter(t => t.is_table);
  const errs = (payload.tables || []).filter(t => !t.is_table && t.error);
  const head = [
    `<div style="font-size:0.82rem; color:var(--text2); margin-bottom:0.6rem;">`,
    `  画像候補: ${payload.candidates_total || 0} / 表対象: ${payload.candidates_targeted || 0}`,
    `   / 抽出成功: ${tables.length} / エラー: ${errs.length}`,
    `   / 所要 ${((payload.total_duration_ms||0)/1000).toFixed(1)}s`,
    `   / サブスク相当 $${payload.total_cost_usd_equivalent || 0}`,
    `   <span style="margin-left:0.6rem; color:#fb923c;">source: ${_pkmEsc(payload.source_kind || 'pdf')}</span>`,
    `</div>`,
  ];
  if (!tables.length) {
    head.push('<p style="color:#f87171;">抽出された表がありません。</p>');
    if (errs.length) {
      head.push('<details><summary style="cursor:pointer; color:var(--text2);">エラー詳細 (' + errs.length + '件)</summary><ul>');
      for (const e of errs) head.push(`<li style="font-size:0.78rem; color:#fca5a5;">${_pkmEsc(e.error || '')}</li>`);
      head.push('</ul></details>');
    }
    return head.join('\n');
  }
  for (const t of tables) head.push(_renderOneTable(t));
  return head.join('\n');
}

// 静かに 1 件の全文を取得（alert を出さない・進捗ラベル更新もしない）。成功なら true。
async function _srFetchHitTextSilent(pid) {
  const source = _pkmGetSelectedSource();
  const isRefetch = !!(window._pkmFullTexts && window._pkmFullTexts[pid]);
  try {
    const r = await fetch(
      `/case/${encodeURIComponent(CASE_ID)}/search-run/hit/${encodeURIComponent(pid)}/fetch-text`,
      {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({force: isRefetch, source}),
      },
    );
    if (!r.ok) return false;
    const data = await r.json();
    window._pkmFullTexts[pid] = data;
    return true;
  } catch (e) {
    return false;
  }
}

async function srExtractTablesBulk() {
  const sel = Array.from(window._tableExtractSelected || []);
  if (!sel.length) { alert('チェックを入れた引例がありません'); return; }

  // 事前判定: 未 DL かつ画像未取得 = 全文取得が必要
  const hits = (_srCurrentRun && _srCurrentRun.hits) || [];
  const needsFetch = sel.filter(pid => {
    const h = hits.find(x => x.patent_id === pid);
    if (!h) return false;
    if (h.downloaded_as_citation) return false;
    const ft = window._pkmFullTexts && window._pkmFullTexts[pid];
    if (ft && Array.isArray(ft.images) && ft.images.length > 0) return false;
    return true;
  });

  let confirmMsg = `選択した ${sel.length} 件の引例の図表を Vision で抽出します。\n1 件 1〜2 分、合計サブスク消費 ${(sel.length * 0.3).toFixed(1)} 程度の見込み。`;
  if (needsFetch.length > 0) {
    confirmMsg += `\n\nうち ${needsFetch.length} 件は PDF/画像 未取得のため、先に全文取得を試みます。`;
  }
  confirmMsg += `\n続行しますか？`;
  if (!confirm(confirmMsg)) return;
  const lbl = document.getElementById('sr-pkm-bulk-status');

  // 事前取得フェーズ
  if (needsFetch.length > 0) {
    if (lbl) lbl.textContent = `事前: 全文取得 0/${needsFetch.length} ...`;
    let i = 0;
    const failed = [];
    for (const pid of needsFetch) {
      i++;
      if (lbl) lbl.textContent = `事前: 全文取得 ${i}/${needsFetch.length}: ${pid}`;
      const ok = await _srFetchHitTextSilent(pid);
      if (!ok) failed.push(pid);
    }
    // 取得後に再判定: 画像も PDF も無いまま残っているもの
    const stillEmpty = sel.filter(pid => {
      const h = hits.find(x => x.patent_id === pid);
      if (!h) return false;
      if (h.downloaded_as_citation) return false;
      const ft = window._pkmFullTexts && window._pkmFullTexts[pid];
      return !(ft && Array.isArray(ft.images) && ft.images.length > 0);
    });
    if (stillEmpty.length > 0) {
      const skip = confirm(
        `次の ${stillEmpty.length} 件は全文取得しても抽出可能な画像/PDFが見つかりませんでした。\nこれらをスキップして残り ${sel.length - stillEmpty.length} 件で抽出を続行しますか？\n\n` +
        stillEmpty.slice(0, 10).join('\n') + (stillEmpty.length > 10 ? '\n...' : ''),
      );
      if (!skip) {
        if (lbl) lbl.textContent = '';
        return;
      }
      // 抽出可能なものだけに絞る
      const skipSet = new Set(stillEmpty);
      for (let k = sel.length - 1; k >= 0; k--) {
        if (skipSet.has(sel[k])) sel.splice(k, 1);
      }
      if (!sel.length) {
        alert('抽出可能な引例がなくなりました。');
        if (lbl) lbl.textContent = '';
        return;
      }
    }
    // ヒットカードに 📊 ボタン状態などを反映
    renderSrHits();
  }

  if (lbl) lbl.textContent = `表抽出 0/${sel.length} ...`;
  try {
    const resp = await fetch(`/case/${encodeURIComponent(CASE_ID)}/citations/extract-tables-bulk`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({citation_ids: sel}),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let perCid = {};
    let currentI = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop();
      for (const block of events) {
        const line = block.split('\n').find(l => l.startsWith('data: '));
        if (!line) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.stage === 'bulk_item_start') {
            currentI = evt.current;
            if (lbl) lbl.textContent = `表抽出 ${currentI}/${evt.total}: ${evt.citation_id} 開始`;
          } else if (evt.stage === 'extract' && evt.total) {
            if (lbl) lbl.textContent = `表抽出 ${currentI}/${sel.length}: ${evt.citation_id} ${evt.current}/${evt.total}`;
          } else if (evt.stage === 'done' || evt.stage === 'skip') {
            perCid[evt.citation_id] = evt;
          } else if (evt.stage === 'error') {
            perCid[evt.citation_id] = evt;
          } else if (evt.stage === 'bulk_done') {
            const ok = Object.values(evt.summary_per_citation || {}).filter(e => e.stage === 'done').length;
            const skipped = Object.values(evt.summary_per_citation || {}).filter(e => e.stage === 'skip').length;
            const err = Object.values(evt.summary_per_citation || {}).filter(e => e.stage === 'error').length;
            const cost = Object.values(evt.summary_per_citation || {})
              .filter(e => e.stage === 'done')
              .reduce((s, e) => s + (e.summary?.total_cost_usd_equivalent || 0), 0);
            alert(`完了: 成功 ${ok} / スキップ ${skipped} / エラー ${err}\nサブスク消費相当 $${cost.toFixed(3)}`);
          }
        } catch(e) {
          if (e instanceof SyntaxError) continue;
          throw e;
        }
      }
    }
    await srLoadTableExtractStatus();
    renderSrHits();
    if (lbl) lbl.textContent = '';
  } catch(e) {
    alert('一括抽出失敗: ' + e.message);
    if (lbl) lbl.textContent = '';
  }
}

async function srDownloadStarred() {
  if (!_srCurrentRun) return;
  const stars = _srCurrentRun.hits.filter(h => h.screening === 'star' && !h.downloaded_as_citation);
  if (!stars.length) { alert('☆の候補がありません (既にDL済みの可能性あり)'); return; }
  const role = prompt(`☆マーク ${stars.length} 件をPDF DL&引用登録します。\n役割を入力 (主引例/副引例/技術常識):`, '副引例');
  if (!role) return;
  const r = await fetch(`/case/${CASE_ID}/search-run/${_srCurrentRun.run_id}/download-starred`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({role}),
  });
  const data = await r.json();
  if (!r.ok) { alert(data.error || 'DLエラー'); return; }
  // ラン再読み込み
  await srOpenRun(_srCurrentRun.run_id);
  const results = data.results || [];
  const ok = results.filter(x => x.success).length;
  const fail = results.length - ok;
  alert(`完了: 成功 ${ok} / 失敗 ${fail}\n失敗した文献は J-PlatPat リンクから手動DLしてください。`);
}


// ===== Step 4.5 エディタ: 複製 / 演算子挿入 / 括弧チェック / キーワードヘルパー =====

function srSetParentRun(runId, levelLabel, formula) {
  _srParentRunId = runId || null;
  const banner = document.getElementById('sr-parent-banner');
  const info = document.getElementById('sr-parent-info');
  if (!banner || !info) return;
  if (_srParentRunId) {
    const short = (formula || '').slice(0, 60);
    info.innerHTML = `<code>${runId}</code> <span style="color:var(--text2);">(${levelLabel || ''})</span> <span style="color:var(--text2); font-size:0.75rem;">${short}${(formula || '').length > 60 ? '…' : ''}</span>`;
    banner.style.display = 'flex';
  } else {
    banner.style.display = 'none';
  }
}

function srClearParent() { srSetParentRun(null); }

async function srDuplicateForEdit(runId) {
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/${runId}`);
    if (!r.ok) { alert('ラン読み込みエラー'); return; }
    const run = await r.json();
    // textarea と UI をセット
    document.getElementById('sr-formula').value = run.formula || '';
    const lvlSel = document.getElementById('sr-level');
    if (lvlSel) {
      const lvl = run.formula_level || 'custom';
      // options から一致があれば選択、なければ custom
      let found = false;
      for (const o of lvlSel.options) {
        if (o.value === lvl) { lvlSel.value = lvl; found = true; break; }
      }
      if (!found) lvlSel.value = 'custom';
    }
    const srcSel = document.getElementById('sr-source');
    if (srcSel && run.source) srcSel.value = run.source;
    srSetParentRun(runId, run.formula_level, run.formula);
    srOnFormulaChange();
    document.querySelector('.sr-formula-picker')?.scrollIntoView({behavior: 'smooth', block: 'start'});
    document.getElementById('sr-formula').focus();
  } catch (e) { alert('エラー: ' + e.message); }
}

function srDuplicateCurrent() {
  if (!_srCurrentRun) return;
  srDuplicateForEdit(_srCurrentRun.run_id);
}

function srInsertOp(op) {
  const ta = document.getElementById('sr-formula');
  if (!ta) return;
  const start = ta.selectionStart ?? ta.value.length;
  const end = ta.selectionEnd ?? ta.value.length;
  // *+ はスペース不要、- (NOT) は前後空白があると見やすい
  const insert = op;
  ta.value = ta.value.slice(0, start) + insert + ta.value.slice(end);
  ta.focus();
  const pos = start + insert.length;
  ta.setSelectionRange(pos, pos);
  srOnFormulaChange();
}

function srWrapParens() {
  _srWrapWith('(', ')');
}

function srWrapBrackets() {
  _srWrapWith('[', ']');
}

function _srWrapWith(open, close) {
  const ta = document.getElementById('sr-formula');
  if (!ta) return;
  const start = ta.selectionStart ?? 0;
  const end = ta.selectionEnd ?? 0;
  if (start === end) {
    ta.value = ta.value.slice(0, start) + open + close + ta.value.slice(end);
    ta.setSelectionRange(start + 1, start + 1);
  } else {
    const sel = ta.value.slice(start, end);
    ta.value = ta.value.slice(0, start) + open + sel + close + ta.value.slice(end);
    ta.setSelectionRange(start + 1, end + 1);
  }
  ta.focus();
  srOnFormulaChange();
}

// 近傍検索テンプレート挿入: キーワード1,<距離><C|N>,キーワード2/TX
function srInsertProximity() {
  const distStr = prompt('キーワード間の距離を指定してください (1〜99)', '5');
  if (distStr === null) return;
  const dist = parseInt(distStr, 10);
  if (!(dist >= 1 && dist <= 99)) { alert('1〜99 の数字で指定してください'); return; }
  const orderRaw = prompt('語順あり=C / 語順なし=N', 'C');
  if (orderRaw === null) return;
  const order = orderRaw.toUpperCase() === 'N' ? 'N' : 'C';
  const kw1 = prompt('キーワード1', '');
  if (!kw1) return;
  const kw2 = prompt('キーワード2', '');
  if (!kw2) return;
  const snippet = `${kw1},${dist}${order},${kw2}/TX`;
  srInsertText(snippet);
}

// キーワード term を J-PlatPat 用にサニタイズ:
//   半角 '-' を全角 '－' に (NOT 演算子と解釈されないように)
function _kwSanitizeTerm(term) {
  const t = String(term || '').trim();
  // 両端がワード文字 (英数 / カナ / かな / 漢字) の '-' を '－' に置換 (反復)
  const re = /([\w぀-ゟ゠-ヿ一-鿿])-([\w぀-ゟ゠-ヿ一-鿿])/;
  let prev = null, cur = t;
  while (prev !== cur) { prev = cur; cur = cur.replace(re, '$1－$2'); }
  return cur;
}

// Step 3 のキーワードグループから J-PlatPat 検索式を組み立てる。
// ルール:
//   - 「赤字ハイライト (.kw-marked)」を**選択中**とみなし、それだけを使う
//   - 何も選んでいない場合: 確認ダイアログを出して、押されたら全件使用
//   - 各グループ内: `(a+b+c)/CL` 形式で OR 結合
//   - 全グループは `*` (AND) で結合
//   - FI/Fterm コードもハイライト中のものだけを末尾に AND
//   - キーワード内の '-' は J-PlatPat の NOT と解釈されるため全角 '－' に置換
async function srBuildFormulaFromKeywords() {
  const groups = kwGroups || [];
  if (groups.length === 0) {
    alert('Step 3 にキーワードグループがありません。先にグループを作成してください。');
    return;
  }

  // DOM のハイライト状態を集計
  const markedKw = new Set();   // "gid|term"
  const markedFt = new Set();   // "gid|code"
  document.querySelectorAll('.kw-tag.kw-marked').forEach(el => {
    markedKw.add((el.dataset.gid || '') + '|' + (el.dataset.term || ''));
  });
  document.querySelectorAll('.fterm-tag.kw-marked').forEach(el => {
    markedFt.add((el.dataset.gid || '') + '|' + (el.dataset.code || ''));
  });

  let useAll = false;
  if (markedKw.size === 0 && markedFt.size === 0) {
    if (!confirm(
      'ハイライト (赤字) した語が無いため、各グループの全キーワードを使います。\n\n' +
      '選別したい場合は Step 3 で残したい語をクリックして赤くしてからもう一度実行してください。\n\n' +
      '全キーワードで組み立てますか？'
    )) {
      return;
    }
    useAll = true;
  }

  // タグ選択
  const tag = (prompt(
    'キーワードに付ける構造タグ:\n  /CL = 請求の範囲 (推奨)\n  /TX = 全文\n  /AB = 要約\n  /TI = 発明の名称',
    '/CL'
  ) || '').trim();
  if (!tag) return;
  if (!/^\/[A-Z]{2,4}$/.test(tag)) {
    alert('構造タグは /CL や /TX のような形式で入力してください');
    return;
  }

  const parts = [];
  let totalKw = 0, totalFt = 0;

  for (const g of groups) {
    const gid = String(g.group_id);
    const allTerms = (g.keywords || []).map(kw => kw && kw.term).filter(Boolean);
    const terms = (useAll ? allTerms : allTerms.filter(t => markedKw.has(gid + '|' + t)))
      .map(_kwSanitizeTerm)
      .filter(Boolean);
    totalKw += terms.length;
    if (terms.length === 1) {
      parts.push(`${terms[0]}${tag}`);
    } else if (terms.length > 1) {
      parts.push(`(${terms.join('+')})${tag}`);
    }
  }

  // FI/Fterm: 各グループの search_codes から、ハイライト中のものだけ
  for (const g of groups) {
    const gid = String(g.group_id);
    const codes = ((g.search_codes || {}).fterm) || [];
    for (const ft of codes) {
      const code = (ft && ft.code ? ft.code : (typeof ft === 'string' ? ft : '')).trim();
      if (!code) continue;
      if (useAll || markedFt.has(gid + '|' + code)) {
        parts.push(/\/FT\b/.test(code) ? code : `${code}/FT`);
        totalFt++;
      }
    }
    const fiCodes = ((g.search_codes || {}).fi) || [];
    for (const fi of fiCodes) {
      const code = (fi && fi.code ? fi.code : (typeof fi === 'string' ? fi : '')).trim();
      if (!code) continue;
      // FI には専用ハイライト UI が無いので useAll の時のみ含める
      if (useAll) {
        parts.push(/\/FI\b/.test(code) ? code : `${code}/FI`);
      }
    }
  }

  if (parts.length === 0) {
    alert('組立対象がありません。ハイライトした語が空、または用語が登録されていません。');
    return;
  }

  const formula = parts.join('*');
  const ta = document.getElementById('sr-formula');
  if (!ta) return;
  if (ta.value.trim() && !confirm('現在のエディタの内容を上書きします。よろしいですか？')) {
    return;
  }
  ta.value = formula;
  srOnFormulaChange();
  if (typeof srAutoResizeTextarea === 'function') srAutoResizeTextarea(ta);
  ta.focus();
  const note = useAll ? '(全件使用)' : `(ハイライト ${totalKw} 語 / ${totalFt} F-term)`;
  if (typeof _srShowToast === 'function') {
    _srShowToast(`検索式を組立 ${note}: ${formula.length} 字`);
  }
}

function srInsertText(text) {
  const ta = document.getElementById('sr-formula');
  if (!ta) return;
  const start = ta.selectionStart ?? ta.value.length;
  const end = ta.selectionEnd ?? ta.value.length;
  // 前の文字が英数・閉じ括弧なら半角スペース or '*' 推奨; とはいえ単純挿入にとどめる
  ta.value = ta.value.slice(0, start) + text + ta.value.slice(end);
  const pos = start + text.length;
  ta.setSelectionRange(pos, pos);
  ta.focus();
  srOnFormulaChange();
}

// 構造タグを付与または置換する。
// 優先順位:
//   1. カーソルが既存の構造タグ (/XX や /XX+YY) の中/末端にあれば → そのタグを差し替え
//   2. カーソル直後に構造タグがあれば → それを差し替え
//   3. 選択範囲があれば終端に挿入 (既タグ続く場合は差し替え)
//   4. 直前の ')' または ']' に挿入 (既タグ続く場合は差し替え)
//   5. 末尾に追記
function srAppendTag(tag) {
  const ta = document.getElementById('sr-formula');
  if (!ta) return;
  const v = ta.value;
  const selStart = ta.selectionStart ?? v.length;
  const selEnd = ta.selectionEnd ?? v.length;

  // 構造タグ全体マッチ用 regex (start anchor 用に matchAll でも可)
  const TAG_RE = /\/[A-Z]{2,4}(?:\+[A-Z]{2,4})*/g;

  // (1)+(2): カーソル位置が既存タグ内/直後/直前にあるか確認
  if (selStart === selEnd) {
    const cursor = selEnd;
    let m;
    TAG_RE.lastIndex = 0;
    while ((m = TAG_RE.exec(v)) !== null) {
      const start = m.index;
      const end = start + m[0].length;
      // タグの開始('/')以降〜終了+1 までの範囲ならヒットとみなす
      // (「/CL」の C や L にカーソルがある時、/CL 直後にカーソルがある時、両方拾う)
      if (cursor >= start && cursor <= end) {
        const newVal = v.slice(0, start) + tag + v.slice(end);
        const newPos = start + tag.length;
        ta.value = newVal;
        ta.setSelectionRange(newPos, newPos);
        ta.focus();
        srOnFormulaChange();
        return;
      }
    }
  }

  // (3)〜(5): 従来動作 — 選択終端 or 直前の閉じ括弧位置に挿入
  let insertAt = selEnd;
  if (selStart === selEnd) {
    const before = v.slice(0, selEnd);
    const lastParen = before.lastIndexOf(')');
    const lastBracket = before.lastIndexOf(']');
    const last = Math.max(lastParen, lastBracket);
    insertAt = (last >= 0) ? last + 1 : v.length;
  }

  const tail = v.slice(insertAt);
  const m = tail.match(/^\s*(\/[A-Z]{2,4}(?:\+[A-Z]{2,4})*)/);
  let newVal, newPos;
  if (m) {
    newVal = v.slice(0, insertAt) + tag + v.slice(insertAt + m[0].length);
    newPos = insertAt + tag.length;
  } else {
    newVal = v.slice(0, insertAt) + tag + v.slice(insertAt);
    newPos = insertAt + tag.length;
  }
  ta.value = newVal;
  ta.setSelectionRange(newPos, newPos);
  ta.focus();
  srOnFormulaChange();
}

function srOnFormulaChange() {
  // 入力毎に自動リサイズ (デバウンスなし)
  srAutoResizeTextarea(document.getElementById('sr-formula'));
  clearTimeout(_srValidateTimer);
  _srValidateTimer = setTimeout(async () => {
    const ta = document.getElementById('sr-formula');
    const el = document.getElementById('sr-formula-valid');
    if (!ta || !el) return;
    const formula = ta.value;
    if (!formula.trim()) { el.textContent = ''; el.className = 'sr-formula-valid'; return; }
    try {
      const r = await fetch(`/case/${CASE_ID}/search-run/validate-formula`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({formula}),
      });
      const d = await r.json();
      if (d.ok && !(d.warnings || []).length) {
        el.textContent = '✓ OK';
        el.className = 'sr-formula-valid ok';
      } else if (d.ok) {
        el.textContent = `⚠ ${(d.warnings || []).join(' / ')}`;
        el.className = 'sr-formula-valid warn';
      } else {
        el.textContent = `✗ ${(d.errors || []).join(' / ')}`;
        el.className = 'sr-formula-valid err';
      }
    } catch (e) { /* silent */ }
  }, 300);
}

async function srToggleKwHelper() {
  const panel = document.getElementById('sr-kw-helper');
  if (!panel) return;
  const show = panel.style.display === 'none';
  panel.style.display = show ? 'block' : 'none';
  if (show && _srKwSnippets === null) {
    await srLoadKwSnippets();
  }
}

// Step 4 を開いたとき、snippets がまだ読み込まれていなければ取得してテーマ chips を描画
async function srEnsureSnippetsLoaded() {
  if (_srKwSnippets === null) {
    await srLoadKwSnippets();
  } else {
    srRenderThemeChips(_srKwSnippets.theme_codes || []);
  }
}

async function srLoadKwSnippets() {
  try {
    const r = await fetch(`/case/${CASE_ID}/search-run/snippets`);
    _srKwSnippets = await r.json();
  } catch (e) { _srKwSnippets = {groups: [], fi_codes: [], fterm_codes: [], theme_codes: []}; }
  srRenderKwSnippets();
  srRenderThemeChips(_srKwSnippets.theme_codes || []);
}

function srRenderKwSnippets() {
  const g = document.getElementById('sr-kw-groups');
  const c = document.getElementById('sr-kw-codes');
  if (!g || !c) return;
  const snip = _srKwSnippets || {};
  const groups = snip.groups || [];
  if (!groups.length) {
    g.innerHTML = '<div class="muted" style="font-size:0.8rem;">Step 3 のキーワード辞書が未生成または group なし</div>';
  } else {
    g.innerHTML = groups.map((grp, i) => {
      const terms = grp.terms || [];
      const sanitized = grp.terms_sanitized || terms;
      const termChips = terms.map((t, idx) => {
        const safe = t.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        const insertTerm = sanitized[idx] || t;
        const esc = JSON.stringify(insertTerm);
        const tip = (insertTerm !== t)
          ? `挿入時は全角ハイフン化: ${insertTerm}`
          : '単語のみ挿入';
        return `<button class="sr-kw-term" onclick='srInsertText(${esc})' title="${tip}">${safe}</button>`;
      }).join('');
      const groupTxt = grp.jplatpat_group || ''; // 例: (a+b+c)/TX
      const groupRaw = grp.jplatpat_group_raw || groupTxt.replace(/\/[A-Z]{2,4}(?:\+[A-Z]{2,4})*$/, '');
      const groupEscTx = JSON.stringify(groupTxt);
      const groupEscRaw = JSON.stringify(groupRaw);
      return `<div class="sr-kw-group">
        <div class="sr-kw-group-head">
          <span class="sr-kw-group-label">${(grp.label || `group${i+1}`).replace(/</g, '&lt;')}</span>
          <button class="sr-tool-btn" onclick='srInsertText(${groupEscTx})' title="(a+b+c)/TX を挿入 (全文検索)">(グループ)/TX</button>
          <button class="sr-tool-btn" onclick='srInsertText(${groupEscRaw})' title="(a+b+c) のみ挿入 (タグは後で付与)">(グループ)</button>
        </div>
        <div class="sr-kw-terms">${termChips}</div>
      </div>`;
    }).join('');
  }
  const fi = snip.fi_codes || [];
  const ft = snip.fterm_codes || [];
  // 既に /FI などが付いていればそのまま、無ければ付与
  const withFI = (c) => /\/FI\b/.test(c) ? c : `${c}/FI`;
  const withFT = (c) => /\/FT\b/.test(c) ? c : `${c}/FT`;
  const fiBtns = fi.map(code => {
    const tagged = withFI(code);
    const esc = JSON.stringify(tagged);
    return `<button class="sr-kw-code" onclick='srInsertText(${esc})' title="${tagged.replace(/"/g, '&quot;')}">${code.replace(/</g, '&lt;')}</button>`;
  }).join('');
  const ftBtns = ft.map(code => {
    const tagged = withFT(code);
    const esc = JSON.stringify(tagged);
    return `<button class="sr-kw-code" onclick='srInsertText(${esc})' title="${tagged.replace(/"/g, '&quot;')}">${code.replace(/</g, '&lt;')}</button>`;
  }).join('');
  c.innerHTML = (fiBtns || ftBtns)
    ? `
      ${fiBtns ? `<div class="sr-kw-codes-row"><span class="sr-kw-codes-label">FI:</span>${fiBtns}</div>` : ''}
      ${ftBtns ? `<div class="sr-kw-codes-row"><span class="sr-kw-codes-label">Fterm:</span>${ftBtns}</div>` : ''}
    `
    : '';
}
