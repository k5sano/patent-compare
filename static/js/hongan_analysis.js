// ================================================================
// 本願分析 (Step 2 SUB 3) ロジック
// ----------------------------------------------------------------
// テンプレート hongan_analysis_v0.1.yaml に従って構造化分析を実行・表示する。
// ================================================================

function _hanCaseId() {
  const root = document.getElementById('hongan-analysis-root');
  return root ? root.dataset.caseId : (window.CASE_ID || null);
}

function _hanStatus(text, type) {
  const el = document.getElementById('han-status');
  if (!el) return;
  el.textContent = text || '';
  if (type === 'error') el.style.color = '#fca5a5';
  else if (type === 'success') el.style.color = '#86efac';
  else el.style.color = 'var(--text2)';
}

function _hanEsc(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

// 1.3 技術分野を特化フォーマットでコンパクト表示
function _hanFormatClassification(v) {
  if (!v || typeof v !== 'object') return _hanFormatValue(v);
  const lines = [];
  const ipc = v.IPC || [];
  if (ipc.length) {
    const codes = ipc.map(x => _hanEsc(x.code || x)).join('、');
    lines.push(`<div><strong style="color:#94a3b8;">IPC：</strong>${codes}</div>`);
  }
  const fi = v.FI || [];
  if (fi.length) {
    // 「コード（説明）」をカンマ区切りで 1 行に
    const parts = fi.map(x => {
      const c = _hanEsc(x.code || '');
      const lab = (x.label || '').trim();
      return lab ? `${c}（${_hanEsc(lab)}）` : c;
    });
    lines.push(`<div><strong style="color:#94a3b8;">FI：</strong>${parts.join('、')}</div>`);
  }
  const theme = v["テーマコード"] || v.theme_codes || [];
  if (theme.length) {
    lines.push(`<div><strong style="color:#94a3b8;">テーマコード：</strong>${theme.map(_hanEsc).join('、')}</div>`);
  }
  const grouped = v["Fターム_grouped"] || {};
  const themes = Object.keys(grouped);
  if (themes.length) {
    lines.push('<div style="margin-top:0.25rem;"><strong style="color:#94a3b8;">Fターム：</strong></div>');
    for (const t of themes) {
      const g = grouped[t] || {};
      const tlabel = g.theme_label ? `（${_hanEsc(g.theme_label)}）` : '';
      const items = (g.items || []).map(it => {
        const code = _hanEsc(it.code || '');
        const lab = (it.label || '').trim();
        return lab ? `${code}（${_hanEsc(lab)}）` : code;
      }).join('、');
      lines.push(
        `<div style="margin-left:0.7rem; font-size:0.83rem;">` +
        `<span style="color:#cbd5e1;">${_hanEsc(t)}${tlabel}：</span>${items}</div>`
      );
    }
  }
  return lines.join('');
}

function _hanFormatValue(v, ctxId) {
  if (v == null || v === '') return '<span style="color:#64748b;">(未取得)</span>';
  if (typeof v === 'string') {
    return _hanEsc(v).replace(/\n/g, '<br>');
  }
  if (Array.isArray(v)) {
    if (!v.length) return '<span style="color:#64748b;">(空)</span>';
    if (v.every(x => typeof x === 'string')) {
      return v.map(x =>
        `<span style="display:inline-block; padding:0.15rem 0.55rem; margin:0.1rem 0.2rem 0.1rem 0; background:#1e293b; border:1px solid var(--border); border-radius:4px; font-size:0.8rem;">${_hanEsc(x)}</span>`
      ).join('');
    }
    return '<ul style="margin:0; padding-left:1.2rem;">' +
      v.map(x => `<li>${_hanFormatValue(x)}</li>`).join('') + '</ul>';
  }
  if (typeof v === 'object') {
    // 1.3 用の特化表示
    if (ctxId === '1.3') return _hanFormatClassification(v);
    const rows = Object.entries(v).map(([k, val]) =>
      `<div style="margin:0.15rem 0;"><strong style="color:#94a3b8;">${_hanEsc(k)}:</strong> ${_hanFormatValue(val)}</div>`
    );
    return rows.join('');
  }
  return _hanEsc(String(v));
}

function _hanRenderResult(data, summary) {
  const wrap = document.getElementById('han-result');
  if (!wrap) return;
  if (!data || !data.sections) {
    wrap.innerHTML = '<p style="color:var(--text2);">データなし</p>';
    return;
  }
  const blocks = [];
  if (summary) {
    blocks.push(`
      <div style="margin-bottom:0.7rem; padding:0.5rem 0.8rem; background:#0f172a; border:1px solid var(--border); border-radius:6px; font-size:0.82rem; color:#94a3b8;">
        ${summary}
      </div>
    `);
  }
  for (const sec of data.sections) {
    const items = (sec.items || []).map(it => {
      const typeBadge =
        it.type === 'auto'   ? '<span style="font-size:0.68rem; padding:0.1rem 0.4rem; background:#0f3a2c; color:#86efac; border-radius:3px;">AUTO</span>' :
        it.type === 'llm'    ? '<span style="font-size:0.68rem; padding:0.1rem 0.4rem; background:#1e3a8a; color:#93c5fd; border-radius:3px;">LLM</span>' :
        it.type === 'manual' ? '<span style="font-size:0.68rem; padding:0.1rem 0.4rem; background:#3f3f46; color:#d4d4d8; border-radius:3px;">MANUAL</span>' : '';
      const desc = it.description
        ? `<div style="font-size:0.74rem; color:#64748b; margin-bottom:0.25rem;">${_hanEsc(it.description)}</div>` : '';
      return `
        <div style="margin-bottom:0.7rem; padding:0.55rem 0.7rem; background:#0f172a; border:1px solid var(--border); border-radius:6px;">
          <div style="display:flex; gap:0.5rem; align-items:center; margin-bottom:0.3rem;">
            <strong style="color:#cbd5e1; font-size:0.85rem;">${_hanEsc(it.id)} ${_hanEsc(it.label)}</strong>
            ${typeBadge}
          </div>
          ${desc}
          <div style="font-size:0.85rem; color:#e2e8f0; line-height:1.55;">
            ${_hanFormatValue(it.value, it.id)}
          </div>
        </div>
      `;
    }).join('');
    blocks.push(`
      <details open style="margin-bottom:0.8rem; border:1px solid var(--border); border-radius:8px; padding:0.6rem 0.8rem; background:#1e293b;">
        <summary style="cursor:pointer; font-weight:600; color:#cbd5e1; font-size:0.95rem;">
          ${_hanEsc(sec.id)} ${_hanEsc(sec.title)}
          <span style="color:#64748b; font-weight:normal; font-size:0.78rem; margin-left:0.4rem;">${_hanEsc(sec.description || '')}</span>
        </summary>
        <div style="margin-top:0.6rem;">${items}</div>
      </details>
    `);
  }
  wrap.innerHTML = blocks.join('');
}

async function hanRunAnalysis(skipLlm) {
  const caseId = _hanCaseId();
  if (!caseId) return;
  if (!skipLlm) {
    if (!confirm(
      '本願分析を実行します。Claude (5〜10 分) を呼んで LLM 項目を埋めます。\n' +
      '続行しますか?\n\n(auto 項目だけが欲しいなら ⚡ ボタン側を使ってください)'
    )) return;
  }
  _hanStatus(skipLlm ? '⏳ auto 項目を解決中...' : '⏳ Claude 実行中... (5〜10 分かかります)');
  try {
    const resp = await fetch(`/case/${caseId}/hongan-analysis/run`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({version: 'v0.1', skip_llm: !!skipLlm}),
    });
    const d = await resp.json();
    if (!resp.ok || d.error) {
      _hanStatus(`エラー: ${d.error || 'HTTP ' + resp.status}`, 'error');
      return;
    }
    const summary = `保存先: ${d.saved_to}` +
      ` | LLM: ${d.llm_filled_count || 0}/${d.llm_item_count || 0}` +
      (d.llm_error ? ` | ⚠ ${d.llm_error}` : '');
    _hanRenderResult(d.data, summary);
    _hanStatus(skipLlm ? 'auto 項目を解決しました' : '分析完了', 'success');
  } catch (e) {
    _hanStatus('通信エラー: ' + e.message, 'error');
  }
}

async function hanLoadAnalysis() {
  const caseId = _hanCaseId();
  if (!caseId) return;
  _hanStatus('⏳ 既存の分析結果を読み込み中...');
  try {
    const resp = await fetch(`/case/${caseId}/hongan-analysis`);
    const d = await resp.json();
    if (!resp.ok || d.error) {
      _hanStatus(`エラー: ${d.error || 'HTTP ' + resp.status}`, 'error');
      return;
    }
    if (!d.exists) {
      _hanStatus('まだ分析されていません。「本願分析を実行」を押してください', 'info');
      const wrap = document.getElementById('han-result');
      if (wrap) wrap.innerHTML = '';
      return;
    }
    _hanRenderResult(d.data);
    _hanStatus('読み込み完了', 'success');
  } catch (e) {
    _hanStatus('通信エラー: ' + e.message, 'error');
  }
}

// 初回 SUB 3 が開かれたタイミングで既存データを軽くロードしておく
document.addEventListener('DOMContentLoaded', () => {
  const ind = document.getElementById('step2-sub-ind-3');
  if (!ind) return;
  let loaded = false;
  ind.addEventListener('click', () => {
    if (!loaded) {
      loaded = true;
      hanLoadAnalysis();
    }
  });
});
