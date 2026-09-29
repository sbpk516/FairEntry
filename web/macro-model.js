/* Descriptive comparisons, never a recession or market-return model. */
(function(root) {
  'use strict';
  const numeric = v => typeof v === 'number' && Number.isFinite(v);
  const clean = item => (item.history || []).filter(p => numeric(p.value) && Number.isFinite(Date.parse(p.date))).slice().sort((a,b)=>a.date.localeCompare(b.date));
  function quantile(values, q) {
    if (!values.length) return null;
    const sorted = values.slice().sort((a,b)=>a-b), pos = (sorted.length-1)*q;
    const lo = Math.floor(pos), hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi]-sorted[lo])*(pos-lo);
  }
  function yearsBefore(date, years) {
    const d = new Date(date+'T00:00:00Z'), month = d.getUTCMonth();
    d.setUTCFullYear(d.getUTCFullYear()-years);
    if (d.getUTCMonth() !== month) d.setUTCDate(0);
    return d.toISOString().slice(0,10);
  }
  function benchmark(item, mode='decade') {
    const h = clean(item), latest = h.at(-1);
    if (!latest) return null;
    const endYear = mode === 'prepandemic' ? 2019 : Number(latest.date.slice(0,4))-1;
    const start = `${endYear-9}-01-01`, end = `${endYear}-12-31`;
    const points = h.filter(p=>p.date>=start && p.date<=end);
    const gapLimit = {Daily:10,Weekly:15,Monthly:45,Quarterly:100}[item.frequency] || 100;
    // Refuse a misleading "10-year" reference when only a short cache is present.
    if (points.length<20 || Date.parse(points[0].date)-Date.parse(start)>gapLimit*86400000 || Date.parse(end)-Date.parse(points.at(-1).date)>gapLimit*86400000) return null;
    const v=points.map(p=>p.value), deviations=v.map(x=>Math.abs(x-2));
    return {start,end,first:points[0].date,last:points.at(-1).date,n:v.length,
      mean:v.reduce((s,x)=>s+x,0)/v.length,median:quantile(v,.5),q25:quantile(v,.25),q75:quantile(v,.75),
      min:Math.min(...v),max:Math.max(...v),
      percentile:100*v.filter(x=>x<=latest.value).length/v.length,
      distance25:quantile(deviations,.25),distance75:quantile(deviations,.75)};
  }
  function trend(item, years) {
    const h=clean(item), latest=h.at(-1);
    if (!latest) return null;
    const target=yearsBefore(latest.date,years), prior=h.filter(p=>p.date<=target).at(-1);
    const tolerance={Daily:10,Weekly:15,Monthly:45,Quarterly:100}[item.frequency]||100;
    if (!prior || Date.parse(target)-Date.parse(prior.date)>tolerance*86400000) return null;
    const change=latest.value-prior.value;
    return {years,prior,latest,change,relative:prior.value>0 ? change/prior.value*100 : null,
      direction:Math.abs(change)<1e-8?'Unchanged':change>0?'↑ Increasing':'↓ Decreasing'};
  }
  function assessment(item,b) {
    const v=item.value;
    if (!numeric(v)) return {tone:'neutral',label:'Unavailable',explanation:'No valid observation.',rules:[]};
    const lower=['claims','unemployment'].includes(item.id);
    if (['buffett','cape'].includes(item.id)) {
      if (!b) return {tone:'neutral',label:'Valuation reference unavailable',explanation:'A complete comparison period is required.',rules:[]};
      return {tone:v<=b.q25?'good':v<=b.q75?'watch':'risk',label:v<=b.q25?'Lower historical valuation':v<=b.q75?'Within typical valuation range':'Elevated historical valuation',
        explanation:'Lower multiples imply a lower price relative to the denominator, not guaranteed value. Historical valuation ranges are not fair-value estimates or crash probabilities.',
        rules:[['good','Lower historical quarter',`≤ ${b.q25}`],['watch','Middle 50% of history',`> ${b.q25} to ${b.q75}`],['risk','Upper historical quarter',`> ${b.q75}`]]};
    }
    if (lower) {
      if (!b) return {tone:'neutral',label:'Reference unavailable',explanation:'A full comparison period is required before assigning a historical color.',rules:[]};
      return {tone:v<=b.q25?'good':v<=b.q75?'watch':'risk',label:v<=b.q25?'Lower historical labor stress':v<=b.q75?'Within typical historical range':'Elevated historical labor stress',
        explanation:'Lower generally suggests less labor-market stress. The reference includes different economic cycles; it is not an ideal or a recession threshold.',
        rules:[['good','Lower stress',`≤ ${b.q25}`],['watch','Middle 50% of history',`> ${b.q25} to ${b.q75}`],['risk','Upper historical quarter',`> ${b.q75}`]]};
    }
    if (item.id==='gdp') return {tone:v<0?'risk':!b?'neutral':v<b.q25?'watch':'good',label:v<0?'Output contracting':!b?'Output expanding':v<b.q25?'Growth below typical range':'Output expanding',
      explanation:'Zero separates contraction from expansion. Faster growth is not always sustainable; a negative quarter alone does not establish a recession.',
      rules:[['risk','Contraction','< 0%'],['watch','Slower expansion',b?`0% to below ${Math.max(0,b.q25).toFixed(2)}%`:'Historical range unavailable'],['good','Expansion at/above lower quartile',b?`≥ ${Math.max(0,b.q25).toFixed(2)}%`:'Historical range unavailable']]};
    if (['inflation','core_inflation'].includes(item.id)) {
      const d=Math.abs(v-2);
      return {tone:!b?'neutral':d<=b.distance25?'good':d<=b.distance75?'watch':'risk',
        label:!b?'Compare with 2% reference':d<=b.distance25?'Relatively close to 2%':d<=b.distance75?'Moderate historical gap from 2%':'Large historical gap from 2%',
        explanation:(item.id==='inflation'?'2% is the Fed’s longer-run headline PCE target.':'2% is a contextual reference for core PCE, not a separate official core target.')+' Colors compare the absolute gap with historical gaps; these bands are not Fed tolerance ranges.',
        rules:b?[['good','Smallest quarter of historical gaps',`Gap ≤ ${b.distance25.toFixed(2)} pp`],['watch','Middle half of historical gaps',`Gap > ${b.distance25.toFixed(2)} to ${b.distance75.toFixed(2)} pp`],['risk','Largest quarter of historical gaps',`Gap > ${b.distance75.toFixed(2)} pp`]]:[]};
    }
    if (item.rule==='curve') return {tone:v<0?'risk':v===0?'watch':'good',label:v<0?'Inverted yield curve':v===0?'Flat yield curve':'Not inverted',
      explanation:'Zero is the inversion boundary. Greater depth quantifies inversion, not crash odds; a return above zero does not prove that risk has passed.',
      rules:[['risk','Inverted','< 0 pp'],['watch','Flat','= 0 pp'],['good','Positive slope','> 0 pp']]};
    if (item.id==='conditions') return {tone:v>0?'risk':v===0?'watch':'good',label:v>0?'Tighter financial conditions':v===0?'At index historical average':'Looser financial conditions',
      explanation:'Chicago Fed defines zero as the index’s historical average, with a standard deviation of one. Looser conditions can support financing but also risk-taking.',
      rules:[['good','Looser than index average','< 0'],['watch','Index average','= 0'],['risk','Tighter than index average','> 0']]};
    return {tone:'neutral',label:'Policy context · no good/bad cutoff',explanation:'The policy rate has no fixed ideal. Restrictiveness depends on inflation, expected real rates and an uncertain neutral rate; historical rank alone cannot settle it.',rules:[['neutral','Historical comparison only','No fixed healthy range']]};
  }
  function directionMeaning(item,t) {
    if (!t) return 'Comparison unavailable';
    if (Math.abs(t.change)<1e-8) return 'Same endpoint value; the path may have varied.';
    if (['claims','unemployment'].includes(item.id)) return t.change>0?'More labor stress than at the earlier endpoint.':'Less labor stress than at the earlier endpoint.';
    if (['inflation','core_inflation'].includes(item.id)) {
      const gap=Math.abs(t.latest.value-2)-Math.abs(t.prior.value-2);
      return Math.abs(gap)<1e-8?'Same distance from 2%.':gap>0?'Farther from the 2% reference.':'Closer to the 2% reference.';
    }
    if (item.id==='gdp') return t.change>0?'Faster output growth than at the earlier endpoint.':'Slower output growth than at the earlier endpoint.';
    if (item.id==='conditions') return t.change>0?'Financial conditions tightened over this interval.':'Financial conditions loosened over this interval.';
    if (item.rule==='curve') return t.change>0?'Spread widened; this alone does not resolve recession risk.':'Spread narrowed; assess whether it crossed zero.';
    if (['buffett','cape'].includes(item.id)) return t.change>0?'Valuation increased relative to its denominator.':'Valuation decreased relative to its denominator.';
    return t.change>0?'Higher overnight borrowing rate; economic effect depends on context.':'Lower overnight borrowing rate; economic effect depends on context.';
  }
  const api={numeric,clean,quantile,yearsBefore,benchmark,trend,assessment,directionMeaning};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.MacroModel=api;
}(typeof globalThis==='undefined'?this:globalThis));
