const CASE_ID = window.CASE_BOOTSTRAP.case_id;

function buildJplatpatUrl(pid) {
  if (!pid) return '';
  const B = 'https://www.j-platpat.inpit.go.jp/c1801/PU';
  let m;
  // 特開yyyy-nnnnnn
  if ((m = pid.match(/特開\s*(\d{4})\s*[-ー]\s*(\d+)/))) return `${B}/JP-${m[1]}-${m[2].padStart(6,'0')}/11/ja`;
  // 特願yyyy-nnnnnn
  if ((m = pid.match(/特願\s*(\d{4})\s*[-ー]\s*(\d+)/))) return `${B}/JP-${m[1]}-${m[2].padStart(6,'0')}/10/ja`;
  // 特表yyyy-nnnnnn
  if ((m = pid.match(/特表\s*(\d{4})\s*[-ー]\s*(\d+)/))) return `${B}/JP-${m[1]}-${m[2].padStart(6,'0')}/11/ja`;
  // 再表yyyy-nnnnnn
  if ((m = pid.match(/再(?:公)?表\s*(\d{4})\s*[-ー]\s*(\d+)/))) return `${B}/JP-${m[1]}-${m[2].padStart(6,'0')}/19/ja`;
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
  if (idx === 5) loadComparisonSummary();
}

// 初期表示: 最初の未完了ステップ
(function() {
  const b = window.CASE_BOOTSTRAP;
  if (!b.has_hongan) showPanel(0);
  else if (!b.has_segments) showPanel(1);
  else if (!b.has_keywords) showPanel(2);
  else if (!b.has_citations) showPanel(3);
  else showPanel(4);
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

async function saveSegmentsFromEditor() {
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
  try {
    await saveSegmentsFromEditor();
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
}

// 初期描画: サーバーから引き渡された関連段落データがあれば表示
if (INITIAL_RELATED && Object.keys(INITIAL_RELATED).length > 0) {
  document.addEventListener('DOMContentLoaded', () => renderRelatedParagraphs(INITIAL_RELATED));
}

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
// メイン描画
// ================================================================
function renderGroups() {
  const container = document.getElementById('kw-groups-container');
  if (!kwGroups || kwGroups.length === 0) {
    container.innerHTML = `
      <div style="text-align:center; padding:2rem; color:var(--text2);">
        キーワードグループがありません。「AI自動提案」または「+ グループ追加」で作成してください。
      </div>`;
    return;
  }
  container.innerHTML = kwGroups.map(g => renderGroup(g)).join('');
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
      <button class="kw-group-btn" title="このグループ内のチェックをすべて外す"
              onclick="clearSelection(${g.group_id})">選択解除</button>
      <button class="kw-group-btn kw-group-btn-del" title="チェックが入っていないキーワード・Ftermを削除。チェック中のものは残ります。"
              onclick="deleteUnselected(${g.group_id})">未選択を削除</button>
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
  return `<span class="kw-tag" style="background:var(--surface2); display:inline-flex; align-items:center;
    gap:4px; padding:2px 8px; margin:2px; border-radius:12px; font-size:0.8rem;" data-term="${escAttr(kw.term)}" data-gid="${gid}">
    <input type="checkbox" class="kw-sel" data-gid="${gid}" data-term="${escAttr(kw.term)}"
      style="margin:0; cursor:pointer; accent-color:#22c55e;">
    <span class="badge ${badgeClass}" style="font-size:0.65rem; padding:1px 4px; border-radius:3px;">${escHtml(typeLabel)}</span>
    <span class="kw-term-text" title="ダブルクリックで編集"
      style="cursor:text;">${escHtml(kw.term)}</span>
  </span>`;
}

function renderFterms(g) {
  const fterms = g.search_codes && g.search_codes.fterm ? g.search_codes.fterm : [];
  const items = fterms.map(ft =>
    `<span style="font-size:0.75rem; padding:1px 6px; background:#1e3a5f; color:#60a5fa;
      border-radius:4px; margin:1px; display:inline-flex; align-items:center; gap:3px;">
      <input type="checkbox" class="fterm-sel" data-gid="${g.group_id}" data-code="${escAttr(ft.code)}"
        style="margin:0; cursor:pointer; accent-color:#22c55e; width:12px; height:12px;">
      ${escHtml(ft.code)} <span style="color:var(--text2);">${escHtml(ft.desc || '')}</span>
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
  groupEl.querySelectorAll('.kw-sel, .fterm-sel').forEach(cb => { cb.checked = false; });
}

async function deleteUnselected(gid) {
  const groupEl = document.getElementById('kw-group-' + gid);
  if (!groupEl) return;
  const g = kwGroups.find(g => g.group_id === gid);
  if (!g) return;

  // チェックされていない = 削除対象（チェック = 残す）
  const delKws = Array.from(groupEl.querySelectorAll('.kw-sel:not(:checked)')).map(cb => cb.dataset.term);
  const delFts = Array.from(groupEl.querySelectorAll('.fterm-sel:not(:checked)')).map(cb => cb.dataset.code);

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
  renderGroups();

  // イベントデリゲーション: キーワードタグの編集・削除
  const kwContainer = document.getElementById('kw-groups-container');
  if (kwContainer) {
    // ダブルクリックで編集
    kwContainer.addEventListener('dblclick', (e) => {
      const termSpan = e.target.closest('.kw-term-text');
      if (termSpan) { startEditKeyword(termSpan); }
    });
    // チェックボックスのクリックがバブルアップしてdblclick等に干渉しないようにする
    kwContainer.addEventListener('click', (e) => {
      // チェックボックス自体はデフォルト動作に任せる
    });
  }
});

// ===== 対比（複数文献対応） =====
function getSelectedCitationIds() {
  return Array.from(document.querySelectorAll('.cit-checkbox:checked')).map(cb => cb.value);
}
function toggleAllCitations(checked) {
  document.querySelectorAll('.cit-checkbox').forEach(cb => cb.checked = checked);
}

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

// ===== 対比サマリ読み込み =====
async function loadComparisonSummary() {
  try {
    const segResp = await fetch(`/case/${CASE_ID}/segments`);
    if (!segResp.ok) return;
    const segs = await segResp.json();

    const tbody = document.getElementById('summary-tbody');
    if (!tbody) return;

    const citIds = window.CASE_BOOTSTRAP.cit_ids || [];
    if (citIds.length === 0) return;

    // 各文献の回答を取得
    const responses = {};
    let hasAny = false;
    for (const citId of citIds) {
      try {
        const r = await fetch(`/case/${CASE_ID}/response/${citId}`);
        if (r.ok) {
          responses[citId] = await r.json();
          hasAny = true;
        }
      } catch(e) {}
    }

    if (!hasAny) {
      tbody.innerHTML = '<tr><td colspan="' + (citIds.length + 1) + '" style="text-align:center; color:var(--text2);">Step 5で回答を取り込むと、ここに対比サマリが表示されます。</td></tr>';
      return;
    }

    // 請求項1の構成要件IDリストを取得
    const claim1 = segs.find(c => c.claim_number === 1);
    if (!claim1) return;
    const segIds = claim1.segments.map(s => s.id);

    // judgmentの表示クラス
    function jClass(j) {
      if (j === '○') return 'j-ok';
      if (j === '△') return 'j-partial';
      if (j === '×') return 'j-ng';
      return '';
    }

    // テーブル構築
    let html = '';
    for (const segId of segIds) {
      const segText = claim1.segments.find(s => s.id === segId)?.text || '';
      // 判定行
      html += '<tr>';
      html += `<td rowspan="2" style="vertical-align:top; font-weight:700; white-space:nowrap;">${segId}</td>`;
      for (const citId of citIds) {
        const resp = responses[citId];
        const comp = resp?.comparisons?.find(c => c.requirement_id === segId);
        const j = comp?.judgment || '—';
        html += `<td class="${jClass(j)}">${j}</td>`;
      }
      html += '</tr>';
      // 理由行
      html += '<tr>';
      for (const citId of citIds) {
        const resp = responses[citId];
        const comp = resp?.comparisons?.find(c => c.requirement_id === segId);
        if (comp) {
          const reason = comp.judgment_reason || '';
          const loc = comp.cited_location ? `<br><span style="color:var(--text2); font-size:0.75rem;">📍 ${comp.cited_location}</span>` : '';
          html += `<td style="font-size:0.8rem;">${reason}${loc}</td>`;
        } else {
          html += '<td style="color:var(--text2);">—</td>';
        }
      }
      html += '</tr>';
    }

    // 従属請求項サマリ
    const subClaims = segs.filter(c => c.claim_number !== 1);
    if (subClaims.length > 0) {
      html += `<tr><th colspan="${citIds.length + 1}" style="text-align:left; padding-top:1rem;">従属請求項</th></tr>`;
      for (const claim of subClaims) {
        html += '<tr>';
        html += `<td style="font-weight:700;">請求項${claim.claim_number}</td>`;
        for (const citId of citIds) {
          const resp = responses[citId];
          const sub = resp?.sub_claims?.find(sc => sc.claim_number === claim.claim_number);
          if (sub) {
            const j = sub.judgment || '—';
            html += `<td class="${jClass(j)}" style="font-size:0.85rem;">${j} <span style="font-size:0.75rem; font-weight:normal;">${sub.judgment_reason || ''}</span></td>`;
          } else {
            html += '<td style="color:var(--text2);">—</td>';
          }
        }
        html += '</tr>';
      }
    }

    // 文献サマリ
    html += `<tr><th colspan="${citIds.length + 1}" style="text-align:left; padding-top:1rem;">文献サマリ</th></tr>`;
    html += '<tr><td style="font-weight:700;">総合評価</td>';
    for (const citId of citIds) {
      const resp = responses[citId];
      if (resp) {
        const cat = resp.category_suggestion || '';
        const summary = resp.overall_summary || '';
        html += `<td style="font-size:0.8rem;"><strong>${cat}</strong><br>${summary}</td>`;
      } else {
        html += '<td style="color:var(--text2);">未回答</td>';
      }
    }
    html += '</tr>';

    tbody.innerHTML = html;
  } catch(e) {
    console.error('loadComparisonSummary error:', e);
  }
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
async function exportExcel() {
  const loading = document.getElementById('loading-export');
  loading.classList.add('show');
  try {
    const resp = await fetch(`/case/${CASE_ID}/export/excel`, { method: 'POST' });
    const data = await resp.json();
    loading.classList.remove('show');

    const result = document.getElementById('export-result');
    if (data.success) {
      result.innerHTML = `<div style="padding:1rem; background:#14532d; border-radius:8px; color:#4ade80;">
        Excel出力完了!
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
async function annotateCitation(citId, btn) {
  const origText = btn.textContent;
  btn.textContent = '生成中...';
  btn.disabled = true;

  try {
    const resp = await fetch(`/case/${CASE_ID}/annotate/${encodeURIComponent(citId)}`, {
      method: 'POST'
    });
    const data = await resp.json();

    if (data.success) {
      btn.textContent = data.opened ? '開いた' : '生成済';
      btn.classList.remove('btn-outline');
      btn.classList.add('btn-success');
      btn.disabled = false;
      // 再クリックで同じPDFを再オープン（再生成）
      btn.onclick = () => annotateCitation(citId, btn);
      setTimeout(() => { btn.textContent = '注釈PDF再表示'; }, 1500);
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
        html += `<details class="citation-fulltext" style="margin-bottom:1rem;" data-citation-text="${citId}">`;
        html += `<summary>${citId} の対比結果テキスト</summary>`;
        html += '<div style="padding:0.5rem;">';
        for (const comp of respData.comparisons) {
          const j = comp.judgment || '—';
          const jColor = j === '○' ? 'var(--green)' : j === '×' ? 'var(--red)' : 'var(--yellow)';
          html += `<div style="margin-bottom:0.5rem; padding:0.4rem 0.6rem; border-left:3px solid ${jColor}; background:var(--surface2); border-radius:0 4px 4px 0;">`;
          html += `<div style="font-size:0.8rem;"><strong>${comp.requirement_id}</strong> <span style="color:${jColor};">${j}</span></div>`;
          if (comp.cited_location) {
            html += `<div class="para-text" style="margin-top:2px;">📍 ${comp.cited_location}</div>`;
          }
          if (comp.judgment_reason) {
            html += `<div class="para-text" style="margin-top:2px;">${comp.judgment_reason}</div>`;
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
  if (idx === 5) {
    loadCitationFullTexts();
  }
};
