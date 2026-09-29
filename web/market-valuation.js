/* Standalone context panel: no connection to score or verdict calculations. */
(async function () {
  'use strict';
  const host = document.getElementById('market-valuation-cards');
  if (!host) return;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const numeric = v => typeof v === 'number' && Number.isFinite(v);
  function card(item, key) {
    const available = numeric(item.value);
    const observation = item.frequency === 'Quarterly' && item.as_of
      ? `${item.as_of.slice(0,4)} Q${Math.ceil(Number(item.as_of.slice(5,7))/3)}`
      : (item.as_of || '').slice(0,7);
    const age = item.as_of ? (Date.now() - Date.parse(item.as_of)) / 86400000 : Infinity;
    const stale = item.stale || age > item.max_age_days;
    const oldDownload = !item.retrieved_at || Date.now() - Date.parse(item.retrieved_at) > 3 * 86400000;
    const status = !available ? 'Data unavailable' : stale ? 'Stale observation' : item.error ? 'Refresh failed · saved data' : oldDownload ? 'Refresh overdue · saved data' : 'Latest published observation';
    const history = (item.history || []).filter(p => numeric(p.value));
    let chart = '';
    if (history.length > 1) {
      const values = history.map(p => p.value), low = Math.min(...values), high = Math.max(...values);
      const points = values.map((v,i) => `${(i/(values.length-1)*300).toFixed(1)},${(52-(v-low)/(high-low||1)*48).toFixed(1)}`).join(' ');
      chart = `<svg viewBox="0 0 300 56" preserveAspectRatio="none" role="img" aria-label="${esc(item.name)} history from ${esc(item.comparison_start)} to ${esc(item.as_of)}"><polyline points="${points}" fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
    }
    const links = (item.sources || []).filter(s => /^https:\/\//.test(s.url)).map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)}</a>`).join(' · ');
    return `<article class="market-valuation-card"><h3>${esc(item.name)}</h3>
      <div class="market-valuation-value">${available ? item.value.toFixed(1) + esc(item.unit) : '—'}</div>
      <small class="market-valuation-status">${status}${observation ? ' · ' + esc(observation) : ''}</small>
      <small>${esc(item.frequency)} data${item.retrieved_at ? ' · Retrieved ' + esc(item.retrieved_at.slice(0,10)) : ''}</small>
      ${window.MacroContext && document.getElementById('macro-grid') ? window.MacroContext.render({...item,id:key}) : chart}
      ${available && numeric(item.percentile) ? `<small>At or above ${item.percentile.toFixed(0)}% of observations since ${esc(item.comparison_start?.slice(0,7))} (${item.observation_count} observations). This is not a crash probability.</small>` : ''}
      <p>${esc(item.description)}</p>
      <details><summary>Formula, sources & limitations</summary><p>${esc(item.formula)}</p><p>${esc(item.limitations)}</p><p>Historical comparisons use available observations from 1997 onward and revised source data; they are not a point-in-time backtest.</p>${links}</details></article>`;
  }
  try {
    const response = await fetch('data/market-valuation.json', {cache:'no-cache'});
    if (!response.ok) throw new Error('Market data unavailable');
    const payload = await response.json();
    if (!payload.indicators?.buffett || !payload.indicators?.cape) throw new Error('Incomplete market data');
    host.innerHTML = ['buffett','cape'].map(key => card(payload.indicators[key],key)).join('');
  } catch (_) {
    host.innerHTML = '<article class="market-valuation-card"><h3>Buffett indicator</h3><div class="market-valuation-value">—</div><small>Data unavailable</small><p>US public-equity value ÷ annualized nominal GDP × 100. Compares market size with the economy.</p></article><article class="market-valuation-card"><h3>CAPE ratio (Shiller P/E)</h3><div class="market-valuation-value">—</div><small>Data unavailable</small><p>Real stock prices ÷ ten-year average real earnings. Smooths the earnings cycle.</p></article>';
  }
}());
