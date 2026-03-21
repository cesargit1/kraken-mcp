// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
function fmt(n) {
  if (n == null) return '—';
  const num = parseFloat(n);
  if (isNaN(num)) return '—';
  return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtDate(s) {
  if (!s) return '—';
  const d = new Date(s);
  return d.toLocaleDateString('en-US', {month:'short',day:'numeric'}) + ' ' +
    d.toLocaleTimeString('en-US', {hour:'2-digit',minute:'2-digit'});
}
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function pillClass(ac) {
  const base = 'inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset';
  const a = (ac || '').toLowerCase();
  if (a === 'buy'  || a === 'long')  return base + ' bg-green-400/10 text-green-400 ring-green-400/20';
  if (a === 'short')                  return base + ' bg-red-400/10 text-red-400 ring-red-400/20';
  if (a === 'sell' || a === 'cover')  return base + ' bg-yellow-400/10 text-yellow-400 ring-yellow-400/20';
  return base + ' bg-gray-400/10 text-gray-400 ring-gray-400/20';
}

function openSidebar() {
  document.getElementById('sidebar').classList.remove('-translate-x-full');
  document.getElementById('sidebar-backdrop').classList.remove('hidden');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.add('-translate-x-full');
  document.getElementById('sidebar-backdrop').classList.add('hidden');
}

// ─────────────────────────────────────────────
// Tab switching
// ─────────────────────────────────────────────
function switchTab(name, clickedEl) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('#sidebar-nav .nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (clickedEl) clickedEl.classList.add('active');
  else {
    const item = document.querySelector(`#sidebar-nav .nav-item[data-page="${name}"]`);
    if (item) item.classList.add('active');
  }
  // Close mobile sidebar
  closeSidebar();
  if (name === 'positions') loadPositions();
  if (name === 'watchlist') loadWatchlist();
  if (name === 'settings')  loadSettings();
  if (name === 'agent')     { loadHistory(); refreshActiveRuns(); }
  if (name === 'candles')   loadCandles();
}

// ─────────────────────────────────────────────
// Positions tab
// ─────────────────────────────────────────────
async function loadPositions() {
  document.getElementById('positions-updated').textContent = '';
  try {
    const res = await fetch('/api/positions');
    const data = await res.json();

    // Mode badge (sidebar + mobile)
    const badgeCls = data.mode === 'live'
      ? 'inline-flex items-center rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset bg-red-400/10 text-red-400 ring-red-400/20 ml-auto'
      : 'inline-flex items-center rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset bg-green-400/10 text-green-400 ring-green-400/20 ml-auto';
    document.querySelectorAll('#mode-badge, #mode-badge-mobile').forEach(b => {
      b.textContent = data.mode;
      b.className = badgeCls;
    });

    const s   = data.summary || {};
    const ops = data.open_positions || [];

    document.getElementById('capital-val').textContent =
      s.paper_capital != null ? '$' + fmt(s.paper_capital) : '—';

    document.getElementById('total-cost').textContent = s.total_cost ? '$' + fmt(s.total_cost) : '$0';
    document.getElementById('total-cost-pct').textContent =
      (s.paper_capital && s.total_cost)
        ? ((s.total_cost / s.paper_capital) * 100).toFixed(1) + '% of capital' : '';

    document.getElementById('cash-val').textContent = s.cash != null ? '$' + fmt(s.cash) : '—';
    document.getElementById('cash-sub').textContent =
      (s.paper_capital && s.cash != null)
        ? ((s.cash / s.paper_capital) * 100).toFixed(1) + '% remaining' : '';

    document.getElementById('total-size').textContent = s.total_size ? '$' + fmt(s.total_size) : '—';
    document.getElementById('total-size-sub').textContent =
      (s.total_cost && s.total_size)
        ? (s.total_size / s.total_cost).toFixed(1) + 'x avg leverage' : '';

    document.getElementById('pos-count').textContent = s.pos_count ?? ops.length;

    const pnlEl = document.getElementById('total-pnl');
    const totalPnl = s.total_pnl ?? 0;
    pnlEl.textContent = ops.length ? (totalPnl >= 0 ? '+' : '') + '$' + fmt(totalPnl) : '$0';
    pnlEl.className = 'text-2xl font-semibold tracking-tight pnl ' + (totalPnl >= 0 ? 'pos' : 'neg');
    const pctEl = document.getElementById('total-pnl-pct');
    if (s.pnl_pct != null && ops.length) {
      pctEl.textContent = (s.pnl_pct >= 0 ? '+' : '') + s.pnl_pct + '% on margin';
      pctEl.className = 'text-xs pnl ' + (totalPnl >= 0 ? 'pos' : 'neg');
    } else { pctEl.textContent = ''; }

    const realEl = document.getElementById('realized-pnl');
    const realPnl = s.realized_pnl ?? 0;
    realEl.textContent = (realPnl >= 0 ? '+' : '') + '$' + fmt(realPnl);
    realEl.className = 'text-2xl font-semibold tracking-tight pnl ' + (realPnl >= 0 ? 'pos' : 'neg');

    const feesEl  = document.getElementById('total-fees');
    const feesVal = s.total_fees ?? 0;
    feesEl.textContent = feesVal > 0 ? '-$' + fmt(feesVal) : '$0';

    const marginEl  = document.getElementById('total-margin-cost');
    const marginVal = s.total_margin_cost ?? 0;
    marginEl.textContent = marginVal > 0 ? '-$' + fmt(marginVal) : '$0';

    // Open positions table
    const opBody = document.getElementById('open-positions-body');
    if (!ops.length) {
      opBody.innerHTML = '<div class="py-10 text-center text-gray-500 text-sm">No open positions tracked in DB</div>';
    } else {
      opBody.innerHTML = `
        <table class="w-full whitespace-nowrap text-left">
          <thead class="border-b border-white/10 text-sm/6 text-white">
            <tr>
              <th class="py-2 pl-4 pr-8 font-semibold sm:pl-6 lg:pl-8">ticker</th>
              <th class="py-2 pl-0 pr-8 font-semibold">side</th>
              <th class="py-2 pl-0 pr-8 font-semibold">quantity</th>
              <th class="py-2 pl-0 pr-8 font-semibold">entry_price</th>
              <th class="py-2 pl-0 pr-8 font-semibold">notional</th>
              <th class="py-2 pl-0 pr-8 font-semibold">margin</th>
              <th class="py-2 pl-0 pr-8 font-semibold">stop_loss</th>
              <th class="py-2 pl-0 pr-8 font-semibold">price</th>
              <th class="py-2 pl-0 pr-8 font-semibold">unrealized_pnl</th>
              <th class="py-2 pl-0 pr-8 font-semibold">leverage</th>
              <th class="py-2 pl-0 pr-8 font-semibold">margin_cost</th>
              <th class="py-2 pl-0 pr-4 font-semibold text-right sm:pr-6 lg:pr-8">opened_at</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-white/5">
          ${ops.map(p => {
            const sizeUsd = (p.entry_price && p.volume) ? p.entry_price * p.volume : null;
            const costUsd = (sizeUsd && p.leverage) ? sizeUsd / p.leverage : sizeUsd;
            const slPct = (p.stop_loss && p.entry_price)
              ? (((p.stop_loss - p.entry_price) / p.entry_price) * 100).toFixed(1) : null;
            const slLabel = p.stop_loss
              ? `$${fmt(p.stop_loss)}<br><span class="text-xs text-gray-500">${slPct}%</span>` : '—';
            const posId = p.ticker.replace(/[^a-z0-9]/gi, '_');
            return `<tr data-pos-id="${posId}">
              <td class="py-4 pl-4 pr-8 sm:pl-6 lg:pl-8"><div class="truncate text-sm/6 font-medium text-white">${esc(p.ticker)}</div></td>
              <td class="py-4 pl-0 pr-8 text-sm/6"><span class="${pillClass(p.action)}">${esc(p.action)}</span></td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-400">${p.volume ?? '—'}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-400">$${fmt(p.entry_price)}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-white">${sizeUsd != null ? '$' + fmt(sizeUsd) : '—'}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-400">${costUsd != null ? '$' + fmt(costUsd) : '—'}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-red-400">${slLabel}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-white" id="pos-price-${posId}">${p.current_price ? '$' + fmt(p.current_price) : '—'}</td>
              <td class="py-4 pl-0 pr-8 text-sm/6 pnl ${p.pnl == null ? '' : p.pnl >= 0 ? 'pos' : 'neg'}" id="pos-pnl-${posId}">
                ${(() => {
                  if (p.pnl == null) return '—';
                  const notional = (p.entry_price || 0) * (p.volume || 0);
                  const margin   = notional / Math.max(p.leverage || 1, 1);
                  const pct      = margin > 0 ? ((p.pnl / margin) * 100).toFixed(2) : null;
                  const sign     = p.pnl >= 0 ? '+' : '';
                  return `${sign}$${fmt(p.pnl)}${pct != null ? `<br><span class="text-xs">${sign}${pct}%</span>` : ''}`;
                })()}
              </td>
              <td class="py-4 pl-0 pr-8 text-sm/6 text-gray-400">${p.leverage ?? 1}x</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-orange-400" id="pos-mc-${posId}">${p.margin_cost > 0 ? '-$' + fmt(p.margin_cost) : '—'}</td>
              <td class="py-4 pl-0 pr-4 text-right text-sm/6 text-gray-400 sm:pr-6 lg:pr-8">${fmtDate(p.opened_at)}</td>
            </tr>`;
          }).join('')}
          </tbody>
        </table>`;
    }

    // Closed positions table
    const closed = data.closed_positions || [];
    const cpBody = document.getElementById('closed-positions-body');
    if (!closed.length) {
      cpBody.innerHTML = '<div class="py-10 text-center text-gray-500 text-sm">No closed positions yet</div>';
    } else {
      cpBody.innerHTML = `
        <table class="w-full whitespace-nowrap text-left">
          <thead class="border-b border-white/10 text-sm/6 text-white">
            <tr>
              <th class="py-2 pl-4 pr-8 font-semibold sm:pl-6 lg:pl-8">ticker</th>
              <th class="py-2 pl-0 pr-8 font-semibold">side</th>
              <th class="py-2 pl-0 pr-8 font-semibold">quantity</th>
              <th class="py-2 pl-0 pr-8 font-semibold">entry_price</th>
              <th class="py-2 pl-0 pr-8 font-semibold">close_price</th>
              <th class="py-2 pl-0 pr-8 font-semibold">notional</th>
              <th class="py-2 pl-0 pr-8 font-semibold">realized_pnl</th>
              <th class="py-2 pl-0 pr-8 font-semibold">fee</th>
              <th class="py-2 pl-0 pr-8 font-semibold">margin_cost</th>
              <th class="py-2 pl-0 pr-8 font-semibold">close_reason</th>
              <th class="py-2 pl-0 pr-8 font-semibold">opened_at</th>
              <th class="py-2 pl-0 pr-4 font-semibold text-right sm:pr-6 lg:pr-8">closed_at</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-white/5">
          ${closed.map(p => {
            const sizeUsd = (p.entry_price && p.quantity) ? p.entry_price * p.quantity : null;
            const cost    = sizeUsd ? sizeUsd / Math.max(p.leverage || 1, 1) : null;
            const pnl     = p.realized_pnl;
            const pnlPct  = (pnl != null && cost) ? ((pnl / cost) * 100).toFixed(2) : null;
            const sign    = pnl >= 0 ? '+' : '';
            return `<tr>
              <td class="py-4 pl-4 pr-8 sm:pl-6 lg:pl-8"><div class="truncate text-sm/6 font-medium text-white">${esc(p.ticker)}</div></td>
              <td class="py-4 pl-0 pr-8 text-sm/6"><span class="${pillClass(p.side)}">${esc(p.side)}</span></td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-400">${p.quantity ?? '—'}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-400">$${fmt(p.entry_price)}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-400">$${fmt(p.close_price)}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-400">${sizeUsd != null ? '$' + fmt(sizeUsd) : '—'}</td>
              <td class="py-4 pl-0 pr-8 text-sm/6 pnl ${pnl == null ? '' : pnl >= 0 ? 'pos' : 'neg'}">
                ${pnl != null ? `${sign}$${fmt(pnl)}${pnlPct != null ? `<br><span class="text-xs">${sign}${pnlPct}%</span>` : ''}` : '—'}
              </td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-red-400">${p.total_fees > 0 ? '-$' + fmt(p.total_fees) : '—'}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-orange-400">${p.margin_cost > 0 ? '-$' + fmt(p.margin_cost) : '—'}</td>
              <td class="py-4 pl-0 pr-8 text-sm/6 text-gray-500">${esc(p.close_reason || '—')}</td>
              <td class="py-4 pl-0 pr-8 text-sm/6 text-gray-400">${fmtDate(p.opened_at)}</td>
              <td class="py-4 pl-0 pr-4 text-right text-sm/6 text-gray-400 sm:pr-6 lg:pr-8">${fmtDate(p.closed_at)}</td>
            </tr>`;
          }).join('')}
          </tbody>
        </table>`;
    }

    // Recent trades
    const trades = data.recent_trades || [];
    const rtBody = document.getElementById('recent-trades-body');
    if (!trades.length) {
      rtBody.innerHTML = '<div class="py-10 text-center text-gray-500 text-sm">No trades yet</div>';
    } else {
      rtBody.innerHTML = `
        <table class="w-full whitespace-nowrap text-left">
          <thead class="border-b border-white/10 text-sm/6 text-white">
            <tr>
              <th class="py-2 pl-4 pr-8 font-semibold sm:pl-6 lg:pl-8">id</th>
              <th class="py-2 pl-0 pr-8 font-semibold">ticker</th>
              <th class="py-2 pl-0 pr-8 font-semibold">action</th>
              <th class="py-2 pl-0 pr-8 font-semibold">quantity</th>
              <th class="py-2 pl-0 pr-8 font-semibold">price</th>
              <th class="py-2 pl-0 pr-8 font-semibold">notional</th>
              <th class="py-2 pl-0 pr-8 font-semibold">leverage</th>
              <th class="py-2 pl-0 pr-8 font-semibold">fee</th>
              <th class="py-2 pl-0 pr-8 font-semibold">status</th>
              <th class="py-2 pl-0 pr-4 font-semibold text-right sm:pr-6 lg:pr-8">event_time</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-white/5">
          ${trades.map(t => {
            const price = parseFloat(t.price) || 0;
            const qty   = parseFloat(t.quantity) || 0;
            const cost  = parseFloat(t.cost) || (price * qty);
            const fee   = parseFloat(t.fee_amount) || 0;
            const lev   = parseInt(t.leverage) || 1;
            const pnl   = t.realized_pnl != null ? parseFloat(t.realized_pnl) : null;
            const statusCls = t.status === 'completed'
              ? 'text-green-400' : t.status === 'failed' ? 'text-red-400' : 'text-gray-500';
            const statusLabel = t.status === 'completed' ? '✓' : t.status === 'failed' ? '✗' : '—';
            return `<tr>
              <td class="py-4 pl-4 pr-8 text-sm/6 text-gray-500 sm:pl-6 lg:pl-8">#${t.id}</td>
              <td class="py-4 pl-0 pr-8"><div class="truncate text-sm/6 font-medium text-white">${esc(t.ticker)}</div></td>
              <td class="py-4 pl-0 pr-8 text-sm/6"><span class="${pillClass(t.action)}">${esc(t.action)}</span></td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-400">${qty ? qty.toFixed(4) : '—'}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-400">$${fmt(price)}</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-white">$${fmt(cost)}</td>
              <td class="py-4 pl-0 pr-8 text-sm/6 text-gray-400">${lev}x</td>
              <td class="py-4 pl-0 pr-8 font-mono text-sm/6 text-gray-500">${fee ? '$' + fmt(fee) : '—'}</td>
              <td class="py-4 pl-0 pr-8 text-sm/6 ${statusCls}">${statusLabel}</td>
              <td class="py-4 pl-0 pr-4 text-right text-sm/6 text-gray-400 sm:pr-6 lg:pr-8">${fmtDate(t.event_time)}</td>
            </tr>`;
          }).join('')}
          </tbody>
        </table>`;
    }

    document.getElementById('positions-updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch(e) {
    console.error(e);
  }
}

async function refreshPositionPrices(btn) {
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.textContent = '⟳ Refreshing…'; btn.disabled = true; }
  try {
    const res  = await fetch('/api/positions/prices', { cache: 'no-store' });
    const data = await res.json();

    for (const r of (data.positions || [])) {
      const posId = r.ticker.replace(/[^a-z0-9]/gi, '_');

      const priceEl = document.getElementById(`pos-price-${posId}`);
      if (priceEl) priceEl.textContent = r.current_price ? '$' + fmt(r.current_price) : '—';

      const mcEl = document.getElementById(`pos-mc-${posId}`);
      if (mcEl) mcEl.textContent = r.margin_cost > 0 ? '-$' + fmt(r.margin_cost) : '—';

      const pnlEl = document.getElementById(`pos-pnl-${posId}`);
      if (pnlEl) {
        if (r.pnl == null) {
          pnlEl.textContent = '—';
          pnlEl.className = 'py-4 pl-0 pr-8 text-sm/6 pnl';
        } else {
          // Need entry_price, volume, leverage from the row — read from sibling cells in the DOM
          const row = pnlEl.closest('tr');
          const cells = row ? row.querySelectorAll('td') : [];
          // cells: 0=ticker,1=side,2=qty,3=entry,4=notional,5=margin,6=stop,7=price,8=pnl,9=leverage
          const entryText  = cells[3]?.textContent?.replace(/[$,]/g,'') || '0';
          const qtyText    = cells[2]?.textContent?.replace(/,/g,'') || '0';
          const leverageText = cells[9]?.textContent?.replace('x','') || '1';
          const notional   = parseFloat(entryText) * parseFloat(qtyText);
          const margin     = notional / Math.max(parseFloat(leverageText) || 1, 1);
          const pct        = margin > 0 ? ((r.pnl / margin) * 100).toFixed(2) : null;
          const sign       = r.pnl >= 0 ? '+' : '';
          pnlEl.innerHTML  = `${sign}$${fmt(r.pnl)}${pct != null ? `<br><span class="text-xs">${sign}${pct}%</span>` : ''}`;
          pnlEl.className  = 'py-4 pl-0 pr-8 text-sm/6 pnl ' + (r.pnl >= 0 ? 'pos' : 'neg');
        }
      }
    }

    // Update summary tiles
    const s = data.summary || {};
    if (s.total_pnl != null) {
      const pnlEl = document.getElementById('total-pnl');
      if (pnlEl) {
        pnlEl.textContent = (s.total_pnl >= 0 ? '+' : '') + '$' + fmt(s.total_pnl);
        pnlEl.className = 'text-2xl font-semibold tracking-tight pnl ' + (s.total_pnl >= 0 ? 'pos' : 'neg');
      }
      const pctEl = document.getElementById('total-pnl-pct');
      if (pctEl && s.pnl_pct != null) {
        pctEl.textContent = (s.pnl_pct >= 0 ? '+' : '') + s.pnl_pct + '% on margin';
        pctEl.className = 'text-xs pnl ' + (s.total_pnl >= 0 ? 'pos' : 'neg');
      }
    }
    if (s.total_margin_cost != null) {
      const mcEl = document.getElementById('total-margin-cost');
      if (mcEl) mcEl.textContent = s.total_margin_cost > 0 ? '-$' + fmt(s.total_margin_cost) : '$0';
    }

    document.getElementById('positions-updated').textContent = 'Prices updated ' + new Date().toLocaleTimeString();
  } catch(e) {
    console.error(e);
  } finally {
    if (btn) { btn.textContent = orig; btn.disabled = false; }
  }
}

// ─────────────────────────────────────────────
// Active Runs summary (above history)
// ─────────────────────────────────────────────
const _uiRuns = {};           // { lid: { ticker, stage, status } }
let _activeRunsTimer = null;

const STAGE_LABELS = {
  candles: 'Fetching candles',
  indicators: 'Computing indicators',
  price_check: 'Checking price',
  stop_loss: 'Stop-loss check',
  flag_check: 'Evaluating flags',
  ai_pipeline: 'Running AI pipeline',
  context_fetch: 'Fetching context',
  specialists: 'Running specialists',
  x_search: 'Fetching X posts',
  technical: 'Technical analyst',
  social: 'Social analyst',
  risk: 'Risk analyst',
  decision: 'Decision agent',
  trade: 'Executing trade',
  execution: 'Executing trade',
  complete: 'Complete',
  skipped: 'Skipped',
  no_data: 'No data',
  starting: 'Starting\u2026',
};

function _stageLabel(stage) {
  return STAGE_LABELS[stage] || stage || '—';
}

function _renderActiveRuns(botTickers, uiRuns) {
  const el = document.getElementById('active-runs-body');
  if (!el) return;

  // Merge bot-loop running tickers and UI-triggered runs
  const lines = [];

  // Bot-loop tickers that are actually running
  (botTickers || []).forEach(t => {
    if (t.status === 'running') {
      lines.push({ ticker: t.ticker, stage: t.stage, source: 'bot' });
    }
  });

  // UI-triggered runs
  Object.values(uiRuns).forEach(r => {
    if (r.status === 'running') {
      lines.push({ ticker: r.ticker, stage: r.stage, source: 'ui' });
    }
  });

  if (!lines.length) {
    el.innerHTML = '<p class="text-sm text-gray-500">No agents running</p>';
    return;
  }

  el.innerHTML = '<ul class="space-y-2">' + lines.map(l => {
    const badge = l.source === 'ui'
      ? '<span class="ml-2 text-[10px] font-medium text-indigo-400 bg-indigo-400/10 rounded px-1.5 py-0.5 ring-1 ring-inset ring-indigo-400/20">manual</span>'
      : '<span class="ml-2 text-[10px] font-medium text-cyan-400 bg-cyan-400/10 rounded px-1.5 py-0.5 ring-1 ring-inset ring-cyan-400/20">bot</span>';
    return `<li class="flex items-center gap-3 text-sm">
      <span class="spinner"></span>
      <strong class="font-mono text-white">${esc(l.ticker)}</strong>
      <span class="text-gray-400">${esc(_stageLabel(l.stage))}</span>
      ${badge}
    </li>`;
  }).join('') + '</ul>';
}

async function refreshActiveRuns() {
  try {
    const res = await fetch('/api/bot-status');
    const json = await res.json();
    _renderActiveRuns(json.tickers || [], _uiRuns);
  } catch(_) {
    _renderActiveRuns([], _uiRuns);
  }
}

function _startActiveRunsPolling() {
  if (_activeRunsTimer) return;
  _activeRunsTimer = setInterval(refreshActiveRuns, 3000);
}

function _stopActiveRunsPolling() {
  if (_activeRunsTimer) { clearInterval(_activeRunsTimer); _activeRunsTimer = null; }
}

// Start polling when the page loads
_startActiveRunsPolling();

// ─────────────────────────────────────────────
// Run History table
// ─────────────────────────────────────────────
const _expandedRows = new Set();
const _rowDetailCache = {};
let _historyPage = 1;

async function loadHistory(page) {
  if (page != null) _historyPage = page;
  const body = document.getElementById('history-body');
  const btn  = document.getElementById('history-refresh-btn');
  body.innerHTML = '<div class="py-10 text-center text-gray-500 text-sm">Loading…</div>';
  if (btn) btn.disabled = true;
  try {
    const res    = await fetch(`/api/agent-history?page=${_historyPage}`);
    const json   = await res.json();
    const trades = json.data || [];
    const totalPages = json.total_pages || 1;
    const currentPage = json.page || 1;
    _historyPage = currentPage;
    if (btn) btn.disabled = false;
    if (!trades.length && currentPage === 1) {
      body.innerHTML = '<div class="py-10 text-center text-gray-500 text-sm">No analysis runs yet</div>';
      return;
    }
    body.innerHTML = `
      <div class="mt-2 flow-root">
        <div class="-mx-4 -my-2 overflow-x-auto sm:-mx-6 lg:-mx-8">
          <div class="inline-block min-w-full py-2 align-middle sm:px-6 lg:px-8">
            <table class="min-w-full divide-y divide-white/15">
              <thead>
                <tr>
                  <th class="whitespace-nowrap py-3.5 pl-4 pr-3 text-left text-sm font-semibold text-white sm:pl-0">id</th>
                  <th class="whitespace-nowrap px-2 py-3.5 text-left text-sm font-semibold text-white">ts</th>
                  <th class="whitespace-nowrap px-2 py-3.5 text-left text-sm font-semibold text-white">ticker</th>
                  <th class="whitespace-nowrap px-2 py-3.5 text-left text-sm font-semibold text-white">position</th>
                  <th class="whitespace-nowrap px-2 py-3.5 text-left text-sm font-semibold text-white">action</th>
                  <th class="whitespace-nowrap py-3.5 pl-2 pr-4 text-left text-sm font-semibold text-white sm:pr-0">trigger_flags</th>
                  <th class="whitespace-nowrap py-3.5 pl-2 pr-4 text-left text-sm font-semibold text-white sm:pr-0" style="width:28px"></th>
                </tr>
              </thead>
              <tbody id="history-tbody" class="divide-y divide-white/10">
                ${trades.map(t => _buildHistoryRow(t)).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      ${_buildPagination(currentPage, totalPages)}`;

    // Re-expand any rows that were open before refresh
    _expandedRows.forEach(id => {
      const detail  = document.getElementById(`hist-detail-${id}`);
      const summRow = document.getElementById(`hist-row-${id}`);
      if (detail && _rowDetailCache[id]) {
        detail.innerHTML = _rowDetailCache[id];
        detail.style.display = 'table-row';
      }
      if (summRow) summRow.classList.add('expanded');
    });
  } catch(e) {
    if (btn) btn.disabled = false;
    body.innerHTML = '<div class="py-10 text-center text-gray-500 text-sm">⚠ Could not load history</div>';
  }
}

function _buildPagination(current, totalPages) {
  if (totalPages <= 1) return '';

  const prevSvg = '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" class="mr-3 size-5 text-gray-500"><path d="M18 10a.75.75 0 0 1-.75.75H4.66l2.1 1.95a.75.75 0 1 1-1.02 1.1l-3.5-3.25a.75.75 0 0 1 0-1.1l3.5-3.25a.75.75 0 1 1 1.02 1.1l-2.1 1.95h12.59A.75.75 0 0 1 18 10Z" clip-rule="evenodd" fill-rule="evenodd" /></svg>';
  const nextSvg = '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" class="ml-3 size-5 text-gray-500"><path d="M2 10a.75.75 0 0 1 .75-.75h12.59l-2.1-1.95a.75.75 0 1 1 1.02-1.1l3.5 3.25a.75.75 0 0 1 0 1.1l-3.5 3.25a.75.75 0 1 1-1.02-1.1l2.1-1.95H2.75A.75.75 0 0 1 2 10Z" clip-rule="evenodd" fill-rule="evenodd" /></svg>';

  const prevBtn = current > 1
    ? `<a href="#" onclick="event.preventDefault();loadHistory(${current - 1})" class="inline-flex items-center border-t-2 border-transparent pr-1 pt-4 text-sm font-medium text-gray-400 hover:border-white/20 hover:text-gray-200">${prevSvg} Previous</a>`
    : '';
  const nextBtn = current < totalPages
    ? `<a href="#" onclick="event.preventDefault();loadHistory(${current + 1})" class="inline-flex items-center border-t-2 border-transparent pl-1 pt-4 text-sm font-medium text-gray-400 hover:border-white/20 hover:text-gray-200">Next ${nextSvg}</a>`
    : '';

  // Build page numbers with ellipsis
  const pages = [];
  const addPage = (p) => {
    if (p === current) {
      pages.push(`<a href="#" onclick="event.preventDefault()" aria-current="page" class="inline-flex items-center border-t-2 border-indigo-400 px-4 pt-4 text-sm font-medium text-indigo-400">${p}</a>`);
    } else {
      pages.push(`<a href="#" onclick="event.preventDefault();loadHistory(${p})" class="inline-flex items-center border-t-2 border-transparent px-4 pt-4 text-sm font-medium text-gray-400 hover:border-white/20 hover:text-gray-200">${p}</a>`);
    }
  };
  const addEllipsis = () => {
    pages.push('<span class="inline-flex items-center border-t-2 border-transparent px-4 pt-4 text-sm font-medium text-gray-500">…</span>');
  };

  // Always show first, last, and pages around current
  const delta = 1;
  let last = 0;
  for (let p = 1; p <= totalPages; p++) {
    if (p === 1 || p === totalPages || (p >= current - delta && p <= current + delta)) {
      if (last && p - last > 1) addEllipsis();
      addPage(p);
      last = p;
    }
  }

  return `<nav class="flex items-center justify-between border-t border-white/10 px-4 sm:px-0">
    <div class="-mt-px flex w-0 flex-1">${prevBtn}</div>
    <div class="hidden md:-mt-px md:flex">${pages.join('')}</div>
    <div class="-mt-px flex w-0 flex-1 justify-end">${nextBtn}</div>
  </nav>`;
}

function _positionLabel(t) {
  const side = (t.position_side || '').toLowerCase();
  if (side === 'long')  return { label: 'LONG',  color: 'var(--green)' };
  if (side === 'short') return { label: 'SHORT', color: 'var(--red)' };
  return { label: 'FLAT', color: 'var(--muted)' };
}

function _buildHistoryRow(t) {
  const ac  = (t.action || '').toLowerCase();
  const pos = _positionLabel(t);
  return `<tr id="hist-row-${t.id}" class="hist-row hover:bg-white/5 cursor-pointer" onclick="toggleHistoryRow(${t.id}, this)">
    <td class="whitespace-nowrap py-2 pl-4 pr-3 text-sm font-mono text-gray-500 sm:pl-0">${t.id}</td>
    <td class="whitespace-nowrap px-2 py-2 text-sm text-gray-400">${fmtDate(t.ts)}</td>
    <td class="whitespace-nowrap px-2 py-2 text-sm font-medium text-white">${esc(t.ticker)}</td>
    <td class="whitespace-nowrap px-2 py-2 text-sm font-medium" style="color:${pos.color}">${pos.label}</td>
    <td class="whitespace-nowrap px-2 py-2 text-sm"><span class="${pillClass(ac)}">${esc(t.action)}</span></td>
    <td class="px-2 py-2 text-sm text-gray-400">${esc((t.trigger_flags || '').replace(/,/g, ' '))}</td>
    <td class="whitespace-nowrap py-2 pl-2 pr-4 text-center text-gray-500 text-sm sm:pr-0" id="hist-chevron-${t.id}">›</td>
  </tr>
  <tr id="hist-detail-${t.id}" style="display:none">
    <td colspan="7" class="p-0 bg-white/[0.03]">
      <div id="hist-detail-inner-${t.id}" class="px-4 py-3">
        <div class="py-4 text-center text-gray-500 text-sm">Loading…</div>
      </div>
    </td>
  </tr>`;
}

async function toggleHistoryRow(id, summaryTr) {
  const detailRow   = document.getElementById(`hist-detail-${id}`);
  const detailInner = document.getElementById(`hist-detail-inner-${id}`);
  const chevron     = document.getElementById(`hist-chevron-${id}`);
  if (!detailRow) return;

  const isOpen = detailRow.style.display !== 'none';
  if (isOpen) {
    detailRow.style.display = 'none';
    if (chevron) chevron.style.transform = '';
    summaryTr.classList.remove('expanded');
    _expandedRows.delete(id);
    return;
  }

  detailRow.style.display = 'table-row';
  if (chevron) chevron.style.transform = 'rotate(90deg)';
  summaryTr.classList.add('expanded');
  _expandedRows.add(id);

  if (_rowDetailCache[id]) {
    detailInner.innerHTML = _rowDetailCache[id];
    return;
  }

  try {
    const res = await fetch(`/api/agent-log/${id}`);
    if (!res.ok) throw new Error('not found');
    const t = await res.json();
    const html = _renderTradeDetail(t);
    _rowDetailCache[id] = html;
    detailInner.innerHTML = html;
    _backfillSummaryRow(id, t);
  } catch(e) {
    detailInner.innerHTML = `<div class="py-3 text-sm" style="color:var(--red)">⚠ Could not load detail</div>`;
  }
}

function _backfillSummaryRow(id, t) {
  // All summary fields are populated at render time from get_recent_agent_runs data — nothing to backfill.
}

function _renderIndicatorsTable(snap) {
  if (!snap || !Object.keys(snap).length)
    return `<div class="text-xs mb-2" style="color:var(--muted)">indicators_snapshot — no data</div>`;

  const TIMEFRAMES = ['1h', '4h', '1d', '1w'];
  const tfs = TIMEFRAMES.filter(tf => snap[tf]);
  if (!tfs.length)
    return `<div class="text-xs mb-2" style="color:var(--muted)">indicators_snapshot — no data</div>`;

  const n = (v, digits=2) => (v == null) ? '<span style="color:var(--muted)">—</span>' : `<span style="font-family:var(--font-mono)">${parseFloat(v).toFixed(digits)}</span>`;
  const pct = (v) => (v == null) ? '<span style="color:var(--muted)">—</span>' : `<span style="font-family:var(--font-mono)">${parseFloat(v).toFixed(1)}</span>`;

  // Determine which optional columns have any data across timeframes
  const hasVwap = tfs.some(tf => snap[tf].vwap != null);

  const thStyle = 'text-left text-xs font-mono px-2 py-1 whitespace-nowrap border-b border-white/20';
  const tdStyle = 'text-xs px-2 py-1 whitespace-nowrap border-b border-white/10 align-top';

  const headers = [
    'tf', 'close', 'RSI', 'MACD', 'MACD sig', 'MACD hist',
    'BB upper', 'BB mid', 'BB lower',
    'EMA 20', 'EMA 50', 'OBV', 'ATR',
    ...(hasVwap ? ['VWAP'] : []),
    'flags',
  ];

  const rows = tfs.map(tf => {
    const d = snap[tf];
    const flagsHtml = (d.threshold_flags || []).length
      ? d.threshold_flags.map(f => `<span class="inline-flex items-center rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset bg-gray-400/10 text-gray-400 ring-gray-400/20 font-mono mr-0.5">${f}</span>`).join('')
      : '<span style="color:var(--muted)">—</span>';

    return `<tr>
      <td class="${tdStyle}" style="font-family:var(--font-mono);color:var(--accent);font-weight:600">${tf}</td>
      <td class="${tdStyle}">${n(d.latest_close)}</td>
      <td class="${tdStyle}" style="color:${d.rsi>70?'var(--red)':d.rsi<30?'var(--green)':'inherit'}">${pct(d.rsi)}</td>
      <td class="${tdStyle}">${n(d.macd)}</td>
      <td class="${tdStyle}">${n(d.macd_signal)}</td>
      <td class="${tdStyle}" style="color:${d.macd_hist>0?'var(--green)':d.macd_hist<0?'var(--red)':'inherit'}">${n(d.macd_hist)}</td>
      <td class="${tdStyle}">${n(d.bb_upper)}</td>
      <td class="${tdStyle}">${n(d.bb_middle)}</td>
      <td class="${tdStyle}">${n(d.bb_lower)}</td>
      <td class="${tdStyle}">${n(d.ema_20)}</td>
      <td class="${tdStyle}">${n(d.ema_50)}</td>
      <td class="${tdStyle}">${n(d.obv, 0)}</td>
      <td class="${tdStyle}">${n(d.atr)}</td>
      ${hasVwap ? `<td class="${tdStyle}">${n(d.vwap)}</td>` : ''}
      <td class="${tdStyle}">${flagsHtml}</td>
    </tr>`;
  }).join('');

  return `
    <details class="bg-white/[0.03] border border-white/10 rounded-lg mb-1">
      <summary class="py-1.5 min-h-0 text-xs font-mono flex items-center gap-3 px-3" style="color:var(--muted)">
        indicators_snapshot
        <span class="opacity-40 font-normal">${tfs.map(tf => { const fl = (snap[tf].threshold_flags || []); return fl.length ? fl.map(f => `${tf}:${f}`).join(' ') : tf; }).join(' · ')}</span>
      </summary>
      <div class="pb-2 overflow-x-auto px-3">
        <table class="w-full border-collapse" style="min-width:600px">
          <thead>
            <tr>${headers.map(h => `<th class="${thStyle}" style="color:var(--muted)">${h}</th>`).join('')}</tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </details>`;
}

function _renderDecision(t) {
  const d = t.decision_json;
  // Fallback: if decision_json is missing (old rows), show reasoning text only
  if (!d || !Object.keys(d).length) {
    if (!t.decision_reasoning) return `<div class="text-xs mb-2" style="color:var(--muted)">decision — no data</div>`;
    return `<div class="mt-1 mb-2">
      <div class="text-xs font-mono mb-1" style="color:var(--muted)">decision_reasoning</div>
      <p class="text-xs text-gray-200 leading-relaxed whitespace-pre-wrap bg-white/[0.03] border border-white/10 rounded p-2">${esc(t.decision_reasoning)}</p>
    </div>`;
  }

  const agreementColors = { full: 'var(--green)', partial: 'var(--yellow, #f0ad4e)', conflicting: 'var(--red)' };
  const action = d.action || t.action || '—';
  const confidence = d.confidence != null ? d.confidence : '—';
  const agreement = d.specialist_agreement || '—';
  const contradictions = (d.key_contradictions || []).filter(Boolean);

  const tdL = 'text-xs font-mono py-0.5 pr-3 whitespace-nowrap align-top';
  const tdR = 'text-xs py-0.5 align-top';

  return `
    <details class="bg-white/[0.03] border border-white/10 rounded-lg mb-1">
      <summary class="py-1.5 min-h-0 text-xs font-mono flex items-center gap-3 px-3" style="color:var(--muted)">
        decision
        <span class="${pillClass(action)}">${esc(action)}</span>
        <span class="opacity-60">${confidence !== '—' ? confidence + '% confidence' : ''}</span>
      </summary>
      <div class="pb-2 px-3">
        <table class="w-full">
          <tr><td class="${tdL}" style="color:var(--muted)">action</td>
              <td class="${tdR}"><span class="${pillClass(action)}">${esc(action)}</span></td></tr>
          <tr><td class="${tdL}" style="color:var(--muted)">confidence</td>
              <td class="${tdR}"><span class="font-mono">${confidence}%</span></td></tr>
          <tr><td class="${tdL}" style="color:var(--muted)">size_usd</td>
              <td class="${tdR}"><span class="font-mono">${d.size_usd != null ? '$' + Number(d.size_usd).toLocaleString() : '<span style="color:var(--muted)">null</span>'}</span></td></tr>
          <tr><td class="${tdL}" style="color:var(--muted)">leverage</td>
              <td class="${tdR}"><span class="font-mono">${d.leverage != null ? d.leverage + 'x' : '<span style="color:var(--muted)">null</span>'}</span></td></tr>
          <tr><td class="${tdL}" style="color:var(--muted)">stop_loss</td>
              <td class="${tdR}"><span class="font-mono">${d.stop_loss != null ? '$' + Number(d.stop_loss).toLocaleString() : '<span style="color:var(--muted)">null</span>'}</span></td></tr>
          <tr><td class="${tdL}" style="color:var(--muted)">specialist_agreement</td>
              <td class="${tdR}"><span class="inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset bg-gray-400/10 ring-gray-400/20 font-mono" style="color:${agreementColors[agreement] || 'inherit'}">${esc(agreement)}</span></td></tr>
          ${contradictions.length ? `<tr><td class="${tdL}" style="color:var(--muted)">key_contradictions</td>
              <td class="${tdR}">${contradictions.map(c => `<div class="text-xs text-gray-300 leading-snug">• ${esc(c)}</div>`).join('')}</td></tr>` : ''}
        </table>
        ${d.reasoning ? `<div class="mt-2">
          <div class="text-xs font-mono mb-0.5" style="color:var(--muted)">reasoning</div>
          <p class="text-xs text-gray-200 leading-relaxed whitespace-pre-wrap bg-white/[0.04] border border-white/10 rounded p-2">${esc(d.reasoning)}</p>
        </div>` : ''}
      </div>
    </details>`;
}

function _renderTradeDetail(t) {
  // ── shared helpers ────────────────────────────────────────────────────────

  function renderVal(v) {
    if (v === null || v === undefined)
      return `<span style="color:var(--muted)">null</span>`;
    if (typeof v === 'boolean')
      return `<span style="color:${v ? 'var(--green)' : 'var(--red)'};font-family:var(--font-mono)">${v}</span>`;
    if (typeof v === 'number')
      return `<span style="font-family:var(--font-mono)">${v}</span>`;
    if (typeof v === 'string')
      return `<span>${esc(v)}</span>`;
    if (Array.isArray(v)) {
      if (!v.length) return `<span style="color:var(--muted);font-family:var(--font-mono)">[]</span>`;
      return `<div class="ml-3 border-l border-white/10 pl-2 mt-0.5">
        ${v.map((item, i) => `
          <div class="py-0.5 flex gap-2">
            <span class="shrink-0 font-mono" style="color:var(--muted);min-width:20px">${i}</span>
            <div>${renderVal(item)}</div>
          </div>`).join('')}
      </div>`;
    }
    if (typeof v === 'object') {
      const entries = Object.entries(v);
      if (!entries.length) return `<span style="color:var(--muted);font-family:var(--font-mono)">{}</span>`;
      return `<div class="ml-3 border-l border-white/10 pl-2 mt-0.5">
        ${entries.map(([k, val]) => `
          <div class="py-0.5 flex items-start gap-2">
            <span class="shrink-0 font-mono text-xs" style="color:var(--muted);min-width:140px">${esc(k)}</span>
            <div class="text-xs text-gray-100">${renderVal(val)}</div>
          </div>`).join('')}
      </div>`;
    }
    return esc(String(v));
  }

  const row = (col, html, w = '130px') => `
    <div class="flex items-start gap-2 py-1 border-b border-white/10">
      <span class="text-xs shrink-0 font-mono" style="color:var(--muted);min-width:${w}">${col}</span>
      <div class="text-xs text-gray-100">${html}</div>
    </div>`;

  const jsonBlock = (col, obj, hint) => {
    if (!obj || !Object.keys(obj).length)
      return row(col, `<span style="color:var(--muted)">null</span>`);
    const body = Object.entries(obj).map(([k, v]) => `
      <div class="flex items-start gap-2 py-0.5">
        <span class="text-xs shrink-0 font-mono" style="color:var(--muted);min-width:140px">${esc(k)}</span>
        <div class="text-xs text-gray-100">${renderVal(v)}</div>
      </div>`).join('');
    const summaryHint = hint != null ? hint : `{${Object.keys(obj).join(', ')}}`;
    return `
      <details class="bg-white/[0.03] border border-white/10 rounded-lg mb-1">
        <summary class="py-1.5 min-h-0 text-xs font-mono flex items-center gap-3 px-3" style="color:var(--muted)">
          ${col}
          <span class="opacity-40 font-normal normal-case">${summaryHint}</span>
        </summary>
        <div class="pb-2 px-3">${body}</div>
      </details>`;
  };

  const sectionHead = (label) =>
    `<div class="text-xs font-mono mt-3 mb-1 pb-1 border-b border-white/20" style="color:var(--accent)">${label}</div>`;

  // ── agent_log section ─────────────────────────────────────────────────────
  const agentSection = `
    <div class="flex flex-col mb-2">
      ${row('ts',       `<span style="font-family:var(--font-mono)">${esc(t.ts || '—')}</span>`)}
      ${row('executed', t.executed
          ? `<span style="color:var(--green);font-family:var(--font-mono)">true</span>`
          : `<span style="color:var(--muted);font-family:var(--font-mono)">false</span>`)}
    </div>
    ${_renderIndicatorsTable(t.indicators_snapshot)}
    ${jsonBlock('technical_analysis', t.technical_analysis || {}, (() => { const o = t.technical_analysis || {}; return [o.signal, o.confidence != null ? o.confidence + '%' : null, o.pattern, o.key_levels ? 'key_levels' : null].filter(Boolean).join(' · ') || null; })())}
    ${jsonBlock('social_analysis',    t.social_analysis    || {}, (() => { const o = t.social_analysis    || {}; return [o.signal, o.confidence != null ? o.confidence + '%' : null, o.sentiment_score != null ? 'sent:' + o.sentiment_score : null].filter(Boolean).join(' · ') || null; })())}
    ${jsonBlock('risk_analysis',      t.risk_analysis      || {}, (() => { const o = t.risk_analysis      || {}; return [o.stop_loss_pct != null ? 'sl:' + o.stop_loss_pct + '%' : null, o.max_position_usd != null ? '$' + o.max_position_usd : null, o.recommended_leverage != null ? o.recommended_leverage + 'x lev' : null].filter(Boolean).join(' · ') || null; })())}
    ${_renderDecision(t)}
    `;

  // ── trades section ────────────────────────────────────────────────────────
  const txns = t.transactions || [];
  let txSectionBody;
  if (!txns.length) {
    txSectionBody = `<div class="text-xs py-2 px-3" style="color:var(--muted)">No trades recorded for this agent run.</div>`;
  } else {
    txSectionBody = txns.map((tx, i) => {
      const cols = [
        ['id',                tx.id   ? `<span style="font-family:var(--font-mono);font-size:10px;color:var(--muted)">${esc(String(tx.id))}</span>` : null],
        ['event_time',        tx.event_time ? `<span style="font-family:var(--font-mono)">${esc(tx.event_time)}</span>` : null],
        ['status',            tx.status     ? esc(tx.status) : null],
        ['side',              tx.side       ? `<span class="${pillClass(tx.side)}">${esc(tx.side)}</span>` : null],
        ['action',            tx.action     ? esc(tx.action) : null],
        ['ticker',            tx.ticker     ? esc(tx.ticker) : null],
        ['quantity',          tx.quantity  != null ? `<span style="font-family:var(--font-mono)">${tx.quantity}</span>`  : null],
        ['price',             tx.price     != null ? `<span style="font-family:var(--font-mono)">$${fmt(tx.price)}</span>` : null],
        ['cost',              tx.cost      != null ? `<span style="font-family:var(--font-mono)">$${fmt(tx.cost)}</span>` : null],
        ['fee_amount',        tx.fee_amount != null ? `<span style="font-family:var(--font-mono)">$${fmt(tx.fee_amount)}</span>` : null],
        ['leverage',          tx.leverage   ? esc(String(tx.leverage)) + 'x' : null],
      ].filter(([, v]) => v !== null);

      const sidePill = tx.side ? `<span class="${pillClass(tx.side)}">${esc(tx.side)}</span>` : '';
      const priceStr = tx.price != null ? `<span class="font-mono" style="color:var(--muted)">$${fmt(tx.price)}</span>` : '';
      return `
        <details class="bg-white/[0.03] border border-white/10 rounded-lg mb-2">
          <summary class="py-1.5 min-h-0 text-xs font-mono flex items-center gap-2 px-3 cursor-pointer" style="color:var(--muted)">
            trade ${i + 1} of ${txns.length}
            ${sidePill}
            ${priceStr}
          </summary>
          <div class="flex flex-col pb-2 px-1">
            ${cols.map(([col, html]) => row(col, html, '150px')).join('')}
          </div>
        </details>`;
    }).join('');
  }

  const txSection = `
    <details class="bg-white/[0.03] border border-white/10 rounded-lg mb-1">
      <summary class="py-1.5 min-h-0 text-xs font-mono flex items-center gap-3 px-3" style="color:var(--muted)">
        trades
        <span class="opacity-40 font-normal">${txns.length} record${txns.length !== 1 ? 's' : ''}</span>
      </summary>
      <div class="pb-2 px-3">${txSectionBody}</div>
    </details>`;

  return agentSection + txSection;
}

// ─────────────────────────────────────────────
// Settings tab
// ─────────────────────────────────────────────
async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
    const s = await res.json();
    document.getElementById('s-paper_capital').value         = s.paper_capital ?? '';
    document.getElementById('s-max_position_pct').value      = s.max_position_pct ?? '';
    document.getElementById('s-max_position_usd').value      = s.max_position_usd ?? '';
    document.getElementById('s-max_leverage').value          = s.max_leverage ?? '';
    document.getElementById('s-max_open_positions').value    = s.max_open_positions ?? '';
    document.getElementById('s-risk_per_trade_pct').value    = s.risk_per_trade_pct ?? '';
    document.getElementById('s-stop_loss_pct_default').value = s.stop_loss_pct_default ?? '';
    document.getElementById('s-poll_interval_sec').value     = s.poll_interval_sec ?? '';
    document.getElementById('s-ai_timer_min').value          = s.ai_timer_min ?? '';
    document.getElementById('s-cooldown_min').value          = s.cooldown_min ?? '';
    document.getElementById('settings-status').textContent  = '';
  } catch(e) {
    document.getElementById('settings-status').textContent = '⚠ Could not load settings';
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const btn    = document.getElementById('settings-save-btn');
  const status = document.getElementById('settings-status');
  btn.disabled = true;
  status.textContent = 'Saving…';
  status.style.color = 'var(--muted)';

  const raw_usd = document.getElementById('s-max_position_usd').value;
  const payload = {
    paper_capital:          parseFloat(document.getElementById('s-paper_capital').value),
    max_position_pct:       parseFloat(document.getElementById('s-max_position_pct').value),
    max_position_usd:       raw_usd !== '' ? parseFloat(raw_usd) : null,
    max_leverage:           parseInt(document.getElementById('s-max_leverage').value),
    max_open_positions:     parseInt(document.getElementById('s-max_open_positions').value),
    risk_per_trade_pct:     parseFloat(document.getElementById('s-risk_per_trade_pct').value),
    stop_loss_pct_default:  parseFloat(document.getElementById('s-stop_loss_pct_default').value),
    poll_interval_sec:      parseInt(document.getElementById('s-poll_interval_sec').value),
    ai_timer_min:           parseInt(document.getElementById('s-ai_timer_min').value),
    cooldown_min:           parseInt(document.getElementById('s-cooldown_min').value),
  };

  try {
    const res = await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { const err = await res.json(); msg = err.detail || msg; } catch(_) { msg = await res.text() || msg; }
      throw new Error(msg);
    }
    status.textContent = '✓ Saved at ' + new Date().toLocaleTimeString();
    status.style.color = 'var(--green)';
  } catch(e) {
    status.textContent  = '⚠ ' + e.message;
    status.style.color = 'var(--red)';
  } finally {
    btn.disabled = false;
  }
}

// ─────────────────────────────────────────────
// Watchlist tab
// ─────────────────────────────────────────────

async function addWatchlistTicker() {
  const ticker = (document.getElementById('wl-ticker').value || '').trim().toUpperCase();
  const status = document.getElementById('wl-add-status');
  if (!ticker) { status.textContent = 'Ticker required'; return; }
  status.textContent = 'Adding…';
  status.style.color = 'var(--muted)';
  try {
    const res = await fetch('/api/watchlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker:      ticker,
        asset_class: document.getElementById('wl-aclass').value,
        search_name: (document.getElementById('wl-sname').value || '').trim() || null,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || res.statusText);
    }
    status.textContent = '✓ Added';
    status.style.color = 'var(--green)';
    document.getElementById('wl-ticker').value = '';
    document.getElementById('wl-sname').value = '';
    loadWatchlist();
  } catch(e) {
    status.textContent = '⚠ ' + e.message;
    status.style.color = 'var(--red)';
  }
}

async function toggleWatchlistActive(ticker, active) {
  try {
    await fetch(`/api/watchlist/${encodeURIComponent(ticker)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active }),
    });
    loadWatchlist();
  } catch(e) { console.error(e); }
}

async function deleteWatchlistTicker(ticker) {
  if (!confirm(`Remove "${ticker}" from watchlist? This also deletes its candle/indicator history association.`)) return;
  try {
    const res = await fetch(`/api/watchlist/${encodeURIComponent(ticker)}`, { method: 'DELETE' });
    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || 'Delete failed');
      return;
    }
    loadWatchlist();
  } catch(e) { alert(e.message); }
}

async function loadWatchlist() {
  const body = document.getElementById('watchlist-body');
  body.innerHTML = '<div class="py-10 text-center text-gray-500 text-sm">Loading…</div>';
  try {
    const res = await fetch('/api/watchlist');
    const rows = await res.json();
    if (!rows.length) {
      body.innerHTML = '<div class="py-10 text-center text-gray-500 text-sm">Watchlist is empty — add a ticker above</div>';
      return;
    }
    body.innerHTML = `
      <table class="w-full whitespace-nowrap text-left">
        <thead class="border-b border-white/10 text-sm/6 text-white"><tr>
          <th class="py-2 pl-4 pr-8 font-semibold sm:pl-6 lg:pl-8">Ticker</th>
          <th class="py-2 pl-0 pr-8 font-semibold">Asset Class</th>
          <th class="py-2 pl-0 pr-8 font-semibold">Search Name</th>
          <th class="py-2 pl-0 pr-8 font-semibold">Active</th>
          <th class="py-2 pl-0 pr-8 font-semibold">Del</th>
          <th class="py-2 pl-0 pr-4 font-semibold text-right sm:pr-6 lg:pr-8"></th>
        </tr></thead>
        <tbody class="divide-y divide-white/5">
        ${rows.map(r => `<tr class="${r.active ? '' : 'opacity-40'}">
          <td class="py-4 pl-4 pr-8 sm:pl-6 lg:pl-8"><strong class="font-mono text-sm/6 text-white">${esc(r.ticker)}</strong></td>
          <td class="py-4 pl-0 pr-8 text-sm/6 text-gray-400">${esc(r.asset_class || 'stock')}</td>
          <td class="py-4 pl-0 pr-8 text-sm/6 text-gray-400">${esc(r.search_name || '—')}</td>
          <td class="py-4 pl-0 pr-8">
            <label class="toggle-switch">
              <input type="checkbox" ${r.active ? 'checked' : ''}
                     onchange="toggleWatchlistActive('${esc(r.ticker)}', this.checked)" />
              <span class="slider"></span>
            </label>
          </td>
          <td class="py-4 pl-0 pr-8">
            <button class="rounded-md px-2 py-1 text-xs font-medium text-red-400 hover:bg-white/5" onclick="deleteWatchlistTicker('${esc(r.ticker)}')">✕</button>
          </td>
          <td class="py-4 pl-0 pr-4 text-right sm:pr-6 lg:pr-8">
            <div class="flex gap-1.5">
              <button class="rounded-md bg-indigo-500/10 px-2.5 py-1 text-xs font-semibold text-indigo-400 ring-1 ring-inset ring-indigo-500/20 hover:bg-indigo-500/20" onclick="runAgent('${esc(r.ticker)}')" ${r.active ? '' : 'disabled'} title="Run (skips if no flags)">▶ Run</button>
              <button class="rounded-md bg-white/5 px-2.5 py-1 text-xs font-semibold text-gray-400 ring-1 ring-inset ring-white/10 hover:bg-white/10" onclick="runAgent('${esc(r.ticker)}', true)" ${r.active ? '' : 'disabled'} title="Force run regardless of flags">⚡ Force</button>
            </div>
          </td>
        </tr>`).join('')}
        </tbody>
      </table>`;
  } catch(e) {
    body.innerHTML = '<div class="py-10 text-center text-gray-500 text-sm">⚠ Failed to load watchlist</div>';
  }
}

// Close search dropdown when clicking outside
document.addEventListener('click', (e) => {
  const search = document.getElementById('wl-search');
  const results = document.getElementById('wl-search-results');
  if (results && !search?.contains(e.target) && !results.contains(e.target)) {
    results.classList.add('hidden');
  }
});

// ─────────────────────────────────────────────
// Run Agent (SSE stream with inline progress row in Agent tab)
// ─────────────────────────────────────────────
let _agentSource = null;
let _liveAgentCounter = 0;

function runAgent(ticker, force = false) {
  // Close any previous stream
  if (_agentSource) { _agentSource.close(); _agentSource = null; }

  // Track in active runs panel
  const _uiRunId = `ui-${Date.now()}`;
  _uiRuns[_uiRunId] = { ticker, stage: 'starting', status: 'running' };
  refreshActiveRuns();

  // Switch to Agent tab
  const agentNav = document.querySelector('#sidebar-nav .nav-item[data-page="agent"]');
  if (agentNav) switchTab('agent', agentNav);

  // Ensure the history table structure exists
  const body = document.getElementById('history-body');
  let tbody = document.getElementById('history-tbody');
  if (!tbody) {
    body.innerHTML = `
      <div class="overflow-x-auto">
        <table class="w-full whitespace-nowrap text-left">
          <thead class="border-b border-white/10 text-sm/6 text-white"><tr>
            <th class="py-2 pl-4 pr-4 font-semibold sm:pl-6 lg:pl-8" style="width:36px"></th>
            <th class="py-2 pl-0 pr-8 font-semibold">ts</th>
            <th class="py-2 pl-0 pr-8 font-semibold">ticker</th>
            <th class="py-2 pl-0 pr-8 font-semibold">position_side</th>
            <th class="py-2 pl-0 pr-8 font-semibold">action</th>
            <th class="py-2 pl-0 pr-8 font-semibold">trigger_flags</th>
            <th class="py-2 pl-0 pr-8 font-semibold">executed</th>
            <th class="py-2 pl-0 pr-4 font-semibold sm:pr-6 lg:pr-8">decision_reasoning</th>
          </tr></thead>
          <tbody id="history-tbody" class="divide-y divide-white/5"></tbody>
        </table>
      </div>`;
    tbody = document.getElementById('history-tbody');
  }

  _liveAgentCounter++;
  const lid = `live-${_liveAgentCounter}`;

  const STEPS = [
    { key: 'candles',    label: 'Fetching candles' },
    { key: 'indicators', label: 'Computing indicators' },
    { key: 'x_search',   label: 'Fetching X posts' },
    { key: 'technical',  label: 'Technical analyst' },
    { key: 'social',     label: 'Social analyst' },
    { key: 'risk',       label: 'Risk analyst' },
    { key: 'trade',      label: 'Trade execution' },
  ];

  const state = {};
  STEPS.forEach(s => { state[s.key] = 'pending'; });

  function renderSteps() {
    const el = document.getElementById(`live-steps-${lid}`);
    if (!el) return;
    el.innerHTML = STEPS.map(s => {
      const st = state[s.key];
      let icon, cls;
      if (st === 'done')        { icon = '✅'; cls = 'text-green-400'; }
      else if (st === 'active') { icon = '<span class="spinner"></span>'; cls = 'text-indigo-400 font-semibold'; }
      else if (st === 'error')  { icon = '❌'; cls = 'text-red-400'; }
      else if (st === 'skipped'){ icon = '⏸'; cls = 'text-gray-500'; }
      else                      { icon = '<span class="text-gray-700">○</span>'; cls = 'text-gray-600'; }
      return `<li class="flex items-center gap-2 py-1 ${cls}">${icon} ${esc(s.label)}</li>`;
    }).join('');
  }

  // Insert live summary row + expanded detail row at top of table
  const now = new Date().toLocaleTimeString();
  const rowsHtml = `<tr id="live-row-${lid}" class="hist-row bg-indigo-500/5 cursor-pointer" onclick="toggleLiveRow('${lid}')">
    <td class="py-4 pl-4 pr-4 text-center text-gray-500 text-sm/6 sm:pl-6 lg:pl-8" id="live-chevron-${lid}" style="transform:rotate(90deg)">›</td>
    <td class="py-4 pl-0 pr-8 text-sm/6 text-gray-400 whitespace-nowrap">${now}</td>
    <td class="py-4 pl-0 pr-8 text-sm/6"><strong class="font-medium text-white">${esc(ticker)}</strong></td>
    <td class="py-4 pl-0 pr-8 text-sm/6" style="color:var(--muted)">—</td>
    <td class="py-4 pl-0 pr-8 text-sm/6" id="live-action-${lid}"><span class="spinner"></span></td>
    <td class="py-4 pl-0 pr-8 text-sm/6 text-gray-400">ui_trigger</td>
    <td class="py-4 pl-0 pr-8 text-sm/6"><span style="color:var(--muted)">—</span></td>
    <td class="py-4 pl-0 pr-4 text-sm/6 text-gray-400 sm:pr-6 lg:pr-8" id="live-reasoning-${lid}">Running agent…</td>
  </tr>
  <tr id="live-detail-${lid}" style="display:table-row">
    <td colspan="8" class="p-0 bg-white/[0.03]">
      <div class="px-4 py-3">
        <ul id="live-steps-${lid}" class="space-y-1 text-sm"></ul>
        <div id="live-result-${lid}" class="mt-3 text-sm"></div>
      </div>
    </td>
  </tr>`;
  tbody.insertAdjacentHTML('afterbegin', rowsHtml);
  renderSteps();

  // SSE step → our key mapping
  const stepMap = {
    candles_start: 'candles', candles_done: 'candles',
    indicators_start: 'indicators', indicators_done: 'indicators',
    technical_done: 'technical',
    social_agent_done: 'social',
    risk_done: 'risk',
    trade_start: 'trade', trade_done: 'trade', trade_skipped: 'trade',
  };
  const doneEvents = new Set([
    'candles_done','indicators_done',
    'technical_done','social_agent_done','risk_done',
    'decision_done','trade_done','trade_skipped',
  ]);

  _agentSource = new EventSource(`/stream/agent/${encodeURIComponent(ticker)}${force ? '?force=true' : ''}`);

  _agentSource.onmessage = (ev) => {
    try {
      const d = JSON.parse(ev.data);
      const step = d.step;
      const key = stepMap[step];

      if (key) {
        // Auto-complete earlier active steps when a later step fires
        const idx = STEPS.findIndex(s => s.key === key);
        STEPS.forEach((s, i) => { if (i < idx && state[s.key] === 'active') state[s.key] = 'done'; });
        state[key] = doneEvents.has(step) ? 'done' : 'active';
        renderSteps();
        // Update active runs panel stage
        if (_uiRuns[_uiRunId]) { _uiRuns[_uiRunId].stage = key; refreshActiveRuns(); }
      }

      // When specialists start, all four run in parallel (social_analyst calls x_search internally)
      if (step === 'specialists_start') {
        state['x_search'] = 'active';
        state['technical'] = 'active';
        state['social'] = 'active';
        state['risk'] = 'active';
        renderSteps();
      }

      // Show decision result inline
      if (step === 'decision_done' && d.result) {
        const r = d.result;
        const colorMap = { buy: 'green', sell: 'red', short: 'red', cover: 'green', hold: 'yellow' };
        const c = colorMap[r.action] || 'indigo';
        const resultEl = document.getElementById(`live-result-${lid}`);
        const reasoningEl = document.getElementById(`live-reasoning-${lid}`);
        const actionEl = document.getElementById(`live-action-${lid}`);
        if (resultEl) {
          resultEl.innerHTML = `
            <div class="rounded-md bg-${c}-400/10 px-3 py-2 text-sm text-${c}-400 ring-1 ring-inset ring-${c}-400/20">
              <span><strong>${(r.action || 'hold').toUpperCase()}</strong>
              ${r.confidence != null ? ` · ${r.confidence}% confidence` : ''}
              ${r.size_usd ? ` · $${Number(r.size_usd).toLocaleString()}` : ''}
              ${r.specialist_agreement ? ` · ${r.specialist_agreement}` : ''}</span>
            </div>
            ${r.reasoning ? `<p class="text-xs mt-1 text-gray-300 leading-relaxed">${esc(r.reasoning)}</p>` : ''}`;
        }
        if (reasoningEl) {
          reasoningEl.textContent = (r.reasoning || '').slice(0, 80) + ((r.reasoning || '').length > 80 ? '…' : '');
        }
        if (actionEl) {
          const ac = (r.action || 'hold').toLowerCase();
          actionEl.innerHTML = `<span class="${pillClass(ac)} text-xs">${esc(r.action || 'hold')}</span>`;
        }
      }

      if (step === 'guard') {
        const resultEl = document.getElementById(`live-result-${lid}`);
        if (resultEl) resultEl.innerHTML += `<div class="text-xs mt-1 text-yellow-400">⚠ ${esc(d.msg || '')}</div>`;
      }

      if (step === 'no_trigger') {
        // Orchestrator skipped — no flags fired and timer not due
        STEPS.forEach(s => { if (state[s.key] === 'pending') state[s.key] = 'skipped'; });
        renderSteps();
        const resultEl = document.getElementById(`live-result-${lid}`);
        if (resultEl) resultEl.innerHTML = `<div class="rounded-md bg-gray-400/10 px-3 py-2 text-xs text-gray-400 ring-1 ring-inset ring-gray-400/20">⏸ ${esc(d.msg || 'No flags — orchestrator skipped')}</div>`;
      }

      if (step === 'complete') {
        STEPS.forEach(s => { if (state[s.key] === 'active') state[s.key] = 'done'; });
        renderSteps();
        const row = document.getElementById(`live-row-${lid}`);
        if (row) row.classList.remove('bg-indigo-500/5');
        _agentSource.close(); _agentSource = null;
        delete _uiRuns[_uiRunId]; refreshActiveRuns();
        // Refresh history after a short delay to show the persisted DB entry
        setTimeout(loadHistory, 1500);
      }

      if (step === 'error') {
        STEPS.forEach(s => { if (state[s.key] === 'active') state[s.key] = 'error'; });
        renderSteps();
        const resultEl = document.getElementById(`live-result-${lid}`);
        if (resultEl) resultEl.innerHTML += `<div class="rounded-md bg-red-400/10 px-3 py-2 text-xs text-red-400 ring-1 ring-inset ring-red-400/20 mt-2">${esc(d.msg || 'Unknown error')}</div>`;
        _agentSource.close(); _agentSource = null;
        delete _uiRuns[_uiRunId]; refreshActiveRuns();
      }
    } catch(_) {}
  };

  _agentSource.onerror = () => {
    STEPS.forEach(s => { if (state[s.key] === 'active') state[s.key] = 'done'; });
    renderSteps();
    if (_agentSource) { _agentSource.close(); _agentSource = null; }
    delete _uiRuns[_uiRunId]; refreshActiveRuns();
  };
}

function toggleLiveRow(lid) {
  const detail = document.getElementById(`live-detail-${lid}`);
  const chevron = document.getElementById(`live-chevron-${lid}`);
  if (!detail) return;
  const isOpen = detail.style.display !== 'none';
  detail.style.display = isOpen ? 'none' : 'table-row';
  if (chevron) chevron.style.transform = isOpen ? '' : 'rotate(90deg)';
}

// ─────────────────────────────────────────────
// Candles tab
// ─────────────────────────────────────────────
let _candlesChart = null;
let _candlesSeries = null;
let _candlesVolSeries = null;
let _candlesResizeObs = null;
let _candlesTf = '1d';

const _TF_LIMITS = { '1h': 200, '4h': 100, '1d': 100, '1w': 52 };

async function loadCandles() {
  const select = document.getElementById('candles-ticker-select');
  const status = document.getElementById('candles-status');
  if (!select) return;

  // Populate ticker dropdown on first visit
  if (select.options.length === 0 || (select.options.length === 1 && select.options[0].value === '')) {
    try {
      const res  = await fetch('/api/watchlist');
      const rows = await res.json();
      const active = rows.filter(r => r.active);
      if (!active.length) {
        status.textContent = 'No active tickers in watchlist';
        return;
      }
      select.innerHTML = active.map(r =>
        `<option value="${esc(r.ticker)}">${esc(r.ticker)}</option>`
      ).join('');
    } catch (e) {
      status.textContent = '⚠ Could not load watchlist';
      return;
    }
  }

  const ticker = select.value;
  if (ticker) await _fetchAndRenderCandles(ticker, _candlesTf);
}

async function onCandlesTickerChange() {
  const ticker = document.getElementById('candles-ticker-select').value;
  if (ticker) await _fetchAndRenderCandles(ticker, _candlesTf);
}

async function onCandlesTfChange(tf, btn) {
  _candlesTf = tf;
  document.querySelectorAll('.candles-tf-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const ticker = document.getElementById('candles-ticker-select').value;
  if (ticker) await _fetchAndRenderCandles(ticker, tf);
}

async function _fetchAndRenderCandles(ticker, tf) {
  const status = document.getElementById('candles-status');
  status.textContent = 'Loading…';
  try {
    const limit = _TF_LIMITS[tf] || 100;
    const res = await fetch(`/api/candles?ticker=${encodeURIComponent(ticker)}&timeframe=${encodeURIComponent(tf)}&limit=${limit}`);
    if (!res.ok) throw new Error(await res.text());
    const data    = await res.json();
    const candles = data.candles || [];
    if (!candles.length) {
      status.textContent = 'No candle data available for this ticker/timeframe';
      document.getElementById('candles-stats').innerHTML = '';
      return;
    }
    _renderCandleChart(candles);
    _renderCandleStats(candles[candles.length - 1]);
    const last = candles[candles.length - 1];
    status.textContent = `${candles.length} candles \u00b7 last: ${fmtDate(last.ts)}`;
  } catch (e) {
    status.textContent = '\u26a0 ' + e.message;
  }
}

function _renderCandleChart(candles) {
  const container = document.getElementById('candles-chart');
  if (!container) return;

  // Tear down existing chart + observer
  if (_candlesResizeObs) { _candlesResizeObs.disconnect(); _candlesResizeObs = null; }
  if (_candlesChart)     { _candlesChart.remove();          _candlesChart     = null; }

  if (typeof LightweightCharts === 'undefined') {
    container.innerHTML = '<div class="p-6 text-sm text-gray-400">Chart library not loaded yet \u2014 please refresh the page.</div>';
    return;
  }

  _candlesChart = LightweightCharts.createChart(container, {
    layout: {
      background: { type: 'solid', color: '#131722' },
      textColor: '#9ca3af',
    },
    grid: {
      vertLines: { color: 'rgba(255,255,255,0.04)' },
      horzLines: { color: 'rgba(255,255,255,0.04)' },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
    timeScale: {
      borderColor: 'rgba(255,255,255,0.1)',
      timeVisible: true,
      secondsVisible: false,
    },
    width:  container.clientWidth,
    height: 500,
  });

  // Candlestick series (main pane)
  _candlesSeries = _candlesChart.addCandlestickSeries({
    upColor:        '#22c55e',
    downColor:      '#ef4444',
    borderVisible:  false,
    wickUpColor:    '#22c55e',
    wickDownColor:  '#ef4444',
  });

  // Volume histogram (overlay, bottom 20% of chart)
  _candlesVolSeries = _candlesChart.addHistogramSeries({
    priceFormat:  { type: 'volume' },
    priceScaleId: 'vol',
    scaleMargins: { top: 0.82, bottom: 0 },
  });
  _candlesChart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

  const ohlc = candles.map(c => ({
    time:  Math.floor(new Date(c.ts).getTime() / 1000),
    open:  c.open,
    high:  c.high,
    low:   c.low,
    close: c.close,
  }));
  const vol = candles.map(c => ({
    time:  Math.floor(new Date(c.ts).getTime() / 1000),
    value: c.volume,
    color: c.close >= c.open ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)',
  }));

  _candlesSeries.setData(ohlc);
  _candlesVolSeries.setData(vol);
  _candlesChart.timeScale().fitContent();

  // Keep chart width in sync with container
  _candlesResizeObs = new ResizeObserver(entries => {
    if (_candlesChart) {
      _candlesChart.applyOptions({ width: entries[0].contentRect.width });
    }
  });
  _candlesResizeObs.observe(container);
}

function _renderCandleStats(c) {
  const el = document.getElementById('candles-stats');
  if (!el || !c) return;
  const change    = c.close - c.open;
  const changePct = ((change / c.open) * 100).toFixed(2);
  const isPos     = change >= 0;
  const pnlCls    = isPos ? 'text-green-400' : 'text-red-400';
  const sign      = isPos ? '+' : '';
  el.innerHTML = [
    ['Open',   `$${fmt(c.open)}`],
    ['High',   `$${fmt(c.high)}`],
    ['Low',    `$${fmt(c.low)}`],
    ['Close',  `$${fmt(c.close)}`],
    ['Volume', c.volume ? Number(c.volume).toLocaleString('en-US', { maximumFractionDigits: 0 }) : '\u2014'],
    ['Change', `<span class="${pnlCls}">${sign}$${fmt(change)} (${sign}${changePct}%)</span>`],
  ].map(([label, val]) =>
    `<div class="rounded-lg bg-white/5 px-3 py-2 border border-white/10">
       <div class="text-xs text-gray-500">${label}</div>
       <div class="text-sm font-mono font-semibold text-white mt-0.5">${val}</div>
     </div>`
  ).join('');
}


// ─────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────
loadPositions();
