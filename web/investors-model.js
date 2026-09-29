/* One evaluation function drives both filtering and explanations. */
(function(root) {
  'use strict';
  const numeric = v => typeof v === 'number' && Number.isFinite(v);
  function fresh(timestamp, days, now) {
    const stamp = Date.parse(timestamp || '');
    return Number.isFinite(stamp) && stamp <= now && now - stamp <= days * 86400000;
  }
  function evaluate(stock, filters, context, now = Date.now()) {
    const checks = [], metrics = stock.metrics || {};
    const specs = [
      ['drawdown','Price below its one-year closing high','drawdown','≥','%'],
      ['profit_margin','Net profit margin','margin','≥','%'],
      ['rev_growth_qoq','Latest-quarter revenue growth vs year ago','growth','≥','%'],
      ['debt_eq','Debt / equity','debt','≤','×'],
      ['pfcf_ratio','Price / free cash flow','pfcf','≤','×']
    ];
    for (const [metric,label,filter,operator,unit] of specs) {
      const m = metrics[metric] || {}, threshold = filters[filter];
      const usable = numeric(m.value) && fresh(m.fetched_at, context.financial_max_age_days, now) && (!m.observed_at || fresh(m.observed_at,context.financial_max_age_days,now));
      const valid = usable && (metric === 'pfcf_ratio' ? m.value > 0 : metric === 'debt_eq' ? m.value >= 0 : true);
      const pass = valid && (operator === '≥' ? m.value >= threshold : m.value <= threshold);
      checks.push({id:metric,label,actual:usable ? m.value : null,reported:m.value,threshold,operator,unit,
        state:!usable ? 'unknown' : pass ? 'pass' : 'fail',
        explanation:!usable ? `${label}: missing or older than ${context.financial_max_age_days} days.` :
          `${label}: ${m.value.toFixed(2)}${unit}; requires ${operator} ${threshold}${unit}${!valid ? ' and a positive cash-flow multiple / nonnegative equity ratio' : ''}.`, source:m.source, date:m.fetched_at});
    }
    const positions = (stock.positions || []).filter(p => p.current_coverage &&
      fresh(p.retrieved_at,3,now) && fresh(p.period,context.holdings_max_age_days,now));
    const research = (stock.research || []).filter(p => fresh(p.published_at,context.research_max_age_days,now));
    const holders = new Set(positions.map(p => p.source_id)).size;
    const weights = positions.map(p => p.weight_pct).filter(numeric);
    const weight = weights.length ? Math.max(...weights) : null;
    if (filters.overlap > 0) checks.push({id:'overlap',state:holders >= filters.overlap ? 'pass' : 'fail',
      explanation:`Disclosed holders: ${holders}; requires ≥ ${filters.overlap}. Reports may cover different quarters.`});
    if (filters.weight > 0) checks.push({id:'weight',state:weight === null ? 'unknown' : weight >= filters.weight ? 'pass' : 'fail',
      explanation:`Largest disclosed portfolio weight: ${weight === null ? 'unknown' : weight.toFixed(2)+'%'}; requires ≥ ${filters.weight}%. Denominator is covered 13F value, not total wealth.`});
    if (!positions.length && !research.length) checks.push({id:'evidence',state:'unknown',explanation:'No fresh disclosed holding or dated company research. Historical positions do not establish current ownership.'});
    const missing = checks.filter(c => c.state === 'unknown');
    const failed = checks.filter(c => c.state === 'fail');
    const hard = failed.some(c => ['profit_margin','debt_eq','pfcf_ratio'].includes(c.id));
    const status = missing.length ? 'Insufficient data' : hard ? 'Avoid under current criteria' : failed.length ? 'Watch' : 'Consider for research';
    return {status, checks, holders, weight, evidence:positions.length + research.length,
      confidence:missing.length ? 'Low — incomplete or stale evidence' : 'Screening evidence available; thesis still requires review',
      reasons: checks.filter(c => c.state === 'pass').map(c => c.explanation),
      risks: checks.filter(c => c.state !== 'pass').sort((a,b) =>
        Number(b.state === 'fail' && ['profit_margin','debt_eq','pfcf_ratio'].includes(b.id)) -
        Number(a.state === 'fail' && ['profit_margin','debt_eq','pfcf_ratio'].includes(a.id))).map(c => c.explanation),
      rank: {'Consider for research':0,'Watch':1,'Insufficient data':2,'Avoid under current criteria':3}[status]};
  }
  const api = {evaluate,fresh,numeric};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InvestorsModel = api;
}(typeof globalThis === 'undefined' ? this : globalThis));
