// ================================================================
// 予備調査 (Step 2 サブタブ) ロジック
// ----------------------------------------------------------------
// - 表記揺れ展開 (LLM)
// - 検索 URL 生成
// - ワンクリック起動 (window.open)
// - 予備調査メモ保存 (analysis/hongan_understanding.md)
// ================================================================

// グローバル: 直近の生成結果を URL 一覧モーダルや保存処理から参照する
window._prelim = window._prelim || {
  candidates: [],   // 展開後の候補リスト (string[])
  selected: new Set(),  // チェックされている候補
  urls: [],         // 生成された URL リスト (server response)
  opened: [],       // 「開く」ボタンを押した URL の履歴
};

function _prelimRoot() {
  return document.getElementById('prelim-root');
}

function _prelimCaseId() {
  const root = _prelimRoot();
  return root ? root.dataset.caseId : null;
}

function _prelimField() {
  const sel = document.getElementById('prelim-field');
  return sel ? sel.value : 'generic';
}

function _prelimMsg(elemId, text, type) {
  const el = document.getElementById(elemId);
  if (!el) return;
  if (!text) { el.style.display = 'none'; el.textContent = ''; return; }
  el.style.display = 'block';
  el.textContent = text;
  // type: 'info' | 'error' | 'success'
  if (type === 'error') {
    el.style.background = '#7f1d1d'; el.style.color = '#fca5a5';
  } else if (type === 'success') {
    el.style.background = '#14532d'; el.style.color = '#86efac';
  } else {
    el.style.background = '#1e293b'; el.style.color = '#94a3b8';
  }
}

function _prelimEsc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

// 表記揺れ展開 -------------------------------------------------
async function prelimExpandSynonyms() {
  const term = (document.getElementById('prelim-term').value || '').trim();
  if (!term) {
    _prelimMsg('prelim-expand-msg', '成分名/用語を入力してください', 'error');
    return;
  }
  _prelimMsg('prelim-expand-msg', '⏳ LLM で表記揺れを列挙中... (10〜30秒)', 'info');
  try {
    const resp = await fetch('/api/preliminary_research/expand_synonyms', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({term, field: _prelimField()}),
    });
    const data = await resp.json();
    if (!resp.ok) {
      _prelimMsg('prelim-expand-msg', data.error || `エラー (HTTP ${resp.status})`, 'error');
      return;
    }
    const syns = (data.synonyms || []).filter(Boolean);
    if (!syns.length) {
      _prelimMsg('prelim-expand-msg', '候補が得られませんでした', 'error');
      return;
    }
    window._prelim.candidates = syns;
    window._prelim.selected = new Set(syns);  // デフォルト全選択
    _prelimRenderCandidates();
    document.getElementById('prelim-candidates-block').style.display = 'block';
    _prelimMsg('prelim-expand-msg',
      `${syns.length} 件の候補を取得 (元の用語含む)`, 'success');
  } catch (e) {
    _prelimMsg('prelim-expand-msg', 'エラー: ' + e.message, 'error');
  }
}

function _prelimRenderCandidates() {
  const wrap = document.getElementById('prelim-candidates');
  if (!wrap) return;
  const cands = window._prelim.candidates;
  const sel = window._prelim.selected;
  if (!cands.length) {
    wrap.innerHTML = '<span style="color:var(--text2); font-size:0.85rem;">候補なし</span>';
    return;
  }
  wrap.innerHTML = cands.map((c, i) => {
    const checked = sel.has(c) ? 'checked' : '';
    return `
      <label style="display:inline-flex; align-items:center; gap:0.3rem; padding:0.3rem 0.6rem; background:#1e293b; border:1px solid var(--border); border-radius:6px; cursor:pointer; font-size:0.85rem;">
        <input type="checkbox" data-prelim-cand="${_prelimEsc(c)}" ${checked} onchange="prelimToggleCand(this)">
        ${_prelimEsc(c)}
      </label>`;
  }).join('');
}

function prelimToggleCand(input) {
  const term = input.dataset.prelimCand;
  if (input.checked) window._prelim.selected.add(term);
  else window._prelim.selected.delete(term);
}

function prelimToggleAll(checked) {
  if (checked) {
    window._prelim.selected = new Set(window._prelim.candidates);
  } else {
    window._prelim.selected = new Set();
  }
  _prelimRenderCandidates();
}

function prelimAddManualCandidate() {
  const term = prompt('追加する候補を入力 (1 件)');
  if (!term) return;
  const t = term.trim();
  if (!t) return;
  if (!window._prelim.candidates.includes(t)) {
    window._prelim.candidates.push(t);
  }
  window._prelim.selected.add(t);
  _prelimRenderCandidates();
}

// URL 生成 -----------------------------------------------------
async function prelimGenerateUrls() {
  const queries = Array.from(window._prelim.selected);
  if (!queries.length) {
    alert('最低 1 つは候補にチェックを入れてください');
    return;
  }
  try {
    const resp = await fetch('/api/preliminary_research/generate_urls', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({queries, field: _prelimField()}),
    });
    const data = await resp.json();
    if (!resp.ok) {
      alert(data.error || `エラー (HTTP ${resp.status})`);
      return;
    }
    window._prelim.urls = data.urls || [];
    window._prelim.opened = [];
    _prelimRenderUrls();
    document.getElementById('prelim-urls-block').style.display = 'block';
  } catch (e) {
    alert('エラー: ' + e.message);
  }
}

function _prelimRenderUrls() {
  const wrap = document.getElementById('prelim-urls');
  if (!wrap) return;
  const urls = window._prelim.urls;
  if (!urls.length) {
    wrap.innerHTML = '<span style="color:var(--text2);">URL なし</span>';
    return;
  }
  // 情報源ごとにグルーピングして表示
  const bySource = new Map();
  for (const u of urls) {
    if (!bySource.has(u.source_id)) {
      bySource.set(u.source_id, {name: u.source_name, desc: u.description, items: []});
    }
    bySource.get(u.source_id).items.push(u);
  }
  const blocks = [];
  for (const [sid, group] of bySource) {
    const rows = group.items.map(u => `
      <div style="display:flex; gap:0.5rem; align-items:center; padding:0.3rem 0.5rem; background:#0f172a; border-radius:6px;">
        <span style="flex:1; font-size:0.85rem; color:#e2e8f0;">${_prelimEsc(u.query)}</span>
        <a href="${_prelimEsc(u.url)}" target="_blank" rel="noopener noreferrer"
           onclick="prelimMarkOpened('${_prelimEsc(u.url)}')"
           style="font-size:0.75rem; color:#64748b; text-decoration:none;"
           title="${_prelimEsc(u.url)}">
          🔗 URL
        </a>
        <button class="btn btn-primary"
                style="font-size:0.78rem; padding:0.2rem 0.6rem;"
                onclick="prelimOpenUrl('${_prelimEsc(u.url)}')">
          開く
        </button>
      </div>
    `).join('');
    blocks.push(`
      <div style="margin-bottom:0.6rem;">
        <div style="font-weight:600; font-size:0.88rem; margin-bottom:0.3rem;">${_prelimEsc(group.name)}
          ${group.desc ? `<span style="color:var(--text2); font-weight:normal; font-size:0.78rem; margin-left:0.4rem;">${_prelimEsc(group.desc)}</span>` : ''}
        </div>
        <div style="display:flex; flex-direction:column; gap:0.25rem;">
          ${rows}
        </div>
      </div>
    `);
  }
  wrap.innerHTML = blocks.join('');
}

function prelimOpenUrl(url) {
  if (!url) return;
  prelimMarkOpened(url);
  window.open(url, '_blank', 'noopener,noreferrer');
}

function prelimMarkOpened(url) {
  if (!window._prelim.opened.includes(url)) {
    window._prelim.opened.push(url);
  }
}

// メモ保存 -----------------------------------------------------
async function prelimSaveNote() {
  const note = (document.getElementById('prelim-note').value || '').trim();
  const term = (document.getElementById('prelim-term').value || '').trim();
  if (!term) {
    alert('成分名/用語を入力してから保存してください');
    return;
  }
  if (!note && !window._prelim.opened.length) {
    if (!confirm('メモも参照履歴も空ですが、見出しだけ追記しますか?')) return;
  }
  try {
    const resp = await fetch('/api/preliminary_research/save_note', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        case_id: _prelimCaseId(),
        component: term,
        note: note,
        urls_opened: window._prelim.opened,
        queries: Array.from(window._prelim.selected),
        field: _prelimField(),
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      _prelimMsg('prelim-save-msg', data.error || `エラー (HTTP ${resp.status})`, 'error');
      return;
    }
    const msg = document.getElementById('prelim-save-msg');
    if (msg) {
      msg.textContent = `✅ 保存しました: ${data.saved_to}`;
      msg.style.color = '#86efac';
    }
  } catch (e) {
    _prelimMsg('prelim-save-msg', 'エラー: ' + e.message, 'error');
  }
}
