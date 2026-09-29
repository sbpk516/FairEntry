/* Shared historical evidence UI for every chart on the macro page. */
(function() {
  'use strict';
  const M=window.MacroModel, registry=new Map();
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=(v,item)=>M.numeric(v)?item.unit==='people'?Math.round(v).toLocaleString('en-US'):v.toFixed(2):'—';
  const unit=item=>item.unit==='people'?'claims per week (4-week average)':item.unit;
  const deltaUnit=item=>item.unit==='people'?'claims':item.unit==='index'?'index points':item.unit==='×'?'multiple points':'pp';
  const signed=(v,item)=>(v>0?'+':'')+fmt(v,item);
  const mode=()=>document.getElementById('macro-reference')?.value||'decade';
  const horizon=()=>document.getElementById('macro-horizon')?.value||'10';
  function warning(item) {
    if(!M.numeric(item.value))return 'Data unavailable';
    const d=Date.parse(item.freshness_date||item.as_of||'');
    if(item.stale||item.status==='stale'||!Number.isFinite(d)||(Date.now()-d)/86400000>item.max_age_days)return 'Stale observation';
    if(item.error)return 'Refresh failed · saved data';
    if(!item.retrieved_at||Date.now()-Date.parse(item.retrieved_at)>3*86400000)return 'Refresh overdue · saved data';
    return '';
  }
  function chart(item,b) {
    const all=M.clean(item), end=all.at(-1)?.date;
    if(!end)return '<p>No history available.</p>';
    const start=horizon()==='all'?all[0].date:M.yearsBefore(end,Number(horizon()));
    const h=all.filter(p=>p.date>=start);
    if(h.length<2)return '<p>Not enough observations to chart.</p>';
    const target=['inflation','core_inflation'].includes(item.id)?2:['gdp','conditions','curve2','curve3'].includes(item.id)?0:b?.median;
    const vals=h.map(p=>p.value).concat(b?[b.q25,b.q75]:[]).concat(M.numeric(target)?[target]:[]);
    let lo=Math.min(...vals),hi=Math.max(...vals); const pad=(hi-lo||1)*.08;lo-=pad;hi+=pad;
    if(['claims','unemployment','buffett','cape'].includes(item.id))lo=Math.max(0,lo);
    const x=d=>62+(Date.parse(d)-Date.parse(h[0].date))/(Date.parse(end)-Date.parse(h[0].date)||1)*424;
    const y=v=>168-(v-lo)/(hi-lo)*150;
    const points=h.map(p=>`${x(p.date).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
    const axisValue=v=>item.unit==='people'&&Math.abs(v)>=1000000?(v/1000000).toFixed(1)+'m':item.unit==='people'&&Math.abs(v)>=1000?(v/1000).toFixed(0)+'k':fmt(v,item);
    const ticks=[lo,(lo+hi)/2,hi].map(v=>`<text x="56" y="${y(v)+4}" text-anchor="end">${esc(axisValue(v))}</text><line class="macro-axis" x1="62" x2="486" y1="${y(v)}" y2="${y(v)}"/>`).join('');
    const band=b?`<rect class="macro-band" x="62" width="424" y="${y(b.q75)}" height="${Math.max(0,y(b.q25)-y(b.q75))}"/>`:'';
    const line=M.numeric(target)?`<line class="macro-reference-line" x1="62" x2="486" y1="${y(target)}" y2="${y(target)}"/>`:'';
    const targetLabel=['inflation','core_inflation'].includes(item.id)?'2% reference':['gdp','conditions','curve2','curve3'].includes(item.id)?'zero boundary':'reference median';
    return `<svg class="macro-history" viewBox="0 0 500 198" role="img" aria-label="${esc(item.name)} from ${h[0].date} to ${end}; ${h.length} observations. Exact values in the evidence table.">${band}${ticks}${line}<polyline points="${points}" fill="none" stroke="var(--accent)" stroke-width="2"/><circle cx="${x(end)}" cy="${y(h.at(-1).value)}" r="3" fill="var(--accent)"/><text x="62" y="190">${h[0].date}</text><text x="486" y="190" text-anchor="end">${end}</text></svg><small>Axis: ${esc(unit(item))}. ${b?'Shaded area: reference middle 50%. ':''}${M.numeric(target)?`Dashed line: ${targetLabel} (${fmt(target,item)} ${esc(item.unit)}).`:''} Full observed range retained, including extreme values.</small>${horizon()!=='all'&&all[0].date>start?'<small>History is shorter than the selected window.</small>':''}`;
  }
  function evidenceTable(item,offset=0) {
    const h=M.clean(item).reverse(),rows=h.slice(offset,offset+60);
    return `<p>Observations ${h.length?offset+1:0}–${Math.min(offset+60,h.length)} of ${h.length}. Latest first; includes all downloaded history, independent of chart window.</p><div class="macro-table-scroll"><table><thead><tr><th>Measured period</th><th>Value (${esc(item.unit)})</th></tr></thead><tbody>${rows.map(p=>`<tr><td>${esc(p.date)}</td><td>${fmt(p.value,item)}</td></tr>`).join('')}</tbody></table></div><div class="macro-data-controls"><button type="button" data-page="${Math.max(0,offset-60)}" ${offset===0?'disabled':''}>Newer observations</button><button type="button" data-page="${offset+60}" ${offset+60>=h.length?'disabled':''}>Older observations</button><button type="button" data-csv>Download all observations (CSV)</button></div>`;
  }
  function render(item) {
    registry.set(item.id,item);
    const b=M.benchmark(item,mode()),a=M.assessment(item,b),w=warning(item);
    const delta=b&&M.numeric(item.value)?item.value-b.median:null;
    const gap=['inflation','core_inflation'].includes(item.id)?`Gap from 2%: ${signed(item.value-2,item)} pp (${item.value>=2?'above':'below'}).`:['curve2','curve3'].includes(item.id)?`Spread: ${signed(item.value,item)} pp = ${signed(item.value*100,item)} basis points.`:item.id==='conditions'?`Distance from index average: ${signed(item.value,item)} index points.`:'';
    const trends=[1,2,5,10].map(years=>{const t=M.trend(item,years);return `<tr><th>${years} year${years>1?'s':''}</th><td>${t?`<strong>${esc(t.direction)}</strong><br>${signed(t.change,item)} ${deltaUnit(item)}${item.unit==='people'&&M.numeric(t.relative)?` (${signed(t.relative,{unit:'%'})}%)`:''}<small>${fmt(t.prior.value,item)} → ${fmt(t.latest.value,item)} ${esc(item.unit)}<br>${t.prior.date} → ${t.latest.date}<br>${esc(M.directionMeaning(item,t))}</small>`:'Insufficient comparable history'}</td></tr>`;}).join('');
    const ruleText=a.rules.map(([tone,label,value])=>{const readable=value.replace(/-?\d+\.\d{3,}/g,n=>Number(n).toFixed(2));return `<tr><td><span class="macro-badge ${tone}">${esc(label)}</span></td><td>${esc(readable)} ${['claims','unemployment','buffett','cape'].includes(item.id)?esc(item.unit):''}</td></tr>`;}).join('');
    const source=(item.sources||[{label:`FRED · ${item.series}`,url:item.source_url}]).filter(s=>/^https:\/\//.test(s.url||'')).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)}</a>`).join(' · ');
    return `<div class="macro-context" data-context="${esc(item.id)}"><div class="macro-badge ${w?'neutral':a.tone}">${esc(w||a.label)}</div>${w?`<small>At the saved observation: ${esc(a.label)}. Colors withheld for current conditions.</small>`:''}<p>${esc(a.explanation)}</p>${gap&&M.numeric(item.value)?`<p class="macro-gap">${esc(gap)}</p>`:''}${b?`<div class="macro-benchmark"><strong>Compared with ${b.start.slice(0,4)}–${b.end.slice(0,4)}</strong><p>Median ${fmt(b.median,item)} ${esc(item.unit)} · Average ${fmt(b.mean,item)} ${esc(item.unit)}<br>Typical middle 50%: <strong>${fmt(b.q25,item)}–${fmt(b.q75,item)} ${esc(item.unit)}</strong></p><p>Latest is <strong>${signed(delta,item)} ${deltaUnit(item)}</strong> from the median${item.unit==='people'&&b.median>0?` (${signed(delta/b.median*100,{unit:'%'})}%)`:''}; at or above <strong>${b.percentile.toFixed(1)}%</strong> of reference observations.</p><small>Historical rank, not a probability or a “percent good/bad” score.</small></div>`:'<p>Complete historical reference unavailable; no substitute benchmark is invented.</p>'}${chart(item,b)}<details open class="macro-trends"><summary>Long-term direction · 1, 2, 5 & 10 years</summary><table><tbody>${trends}</tbody></table><small>Endpoint comparisons, not a fitted trend or a claim of uninterrupted movement. Dates are measured periods; comparisons end at this series’ latest available observation.</small></details><details><summary>Why this color? Reference values & evidence</summary><table><thead><tr><th>Interpretation</th><th>Exact display rule</th></tr></thead><tbody>${ruleText||'<tr><td colspan="2">No complete reference available</td></tr>'}</tbody></table><p>Historical bands are FairEntry descriptive conventions using the 25th and 75th percentiles, not validated crisis thresholds. Green means favorable on this particular dimension, not an all-clear for the economy or stocks.</p>${b?`<p>Reference: ${b.first} through ${b.last}, ${b.n.toLocaleString()} observations at source frequency. Minimum ${fmt(b.min,item)}, maximum ${fmt(b.max,item)} ${esc(item.unit)}. Quantiles use linear interpolation; percentile is the share of reference values ≤ latest. No observations or crisis outliers are excluded within the selected reference period.</p>`:''}<p>${source}</p>${['inflation','core_inflation','unemployment','policy'].includes(item.id)?'<p><a href="https://www.federalreserve.gov/monetarypolicy/monetary-policy-what-are-its-goals-how-does-it-work.htm" target="_blank" rel="noopener noreferrer">Federal Reserve: 2% PCE objective and no fixed employment goal</a></p>':''}${item.id==='conditions'?'<p><a href="https://www.chicagofed.org/research/data/nfci/current-data" target="_blank" rel="noopener noreferrer">Chicago Fed: NFCI definitions and data</a></p>':''}${item.id==='claims'?'<p>Claims are insurance applications, not all unemployed people. Counts are not adjusted for changes in labor-force size or eligibility, which limits comparisons across decades.</p>':''}<p>${esc(item.formula||(['inflation','core_inflation'].includes(item.id)?'PCE index ÷ same-month index one year earlier, minus 1, multiplied by 100.':'Source values, without additional scaling. Claims are expressed in full counts, not thousands.'))}</p><small>Retrieved ${esc(item.retrieved_at||'never')}. Revised history; not the values necessarily known at the time.</small></details><details class="macro-observations"><summary>Inspect underlying data & download CSV</summary><div class="macro-evidence-rows">${evidenceTable(item)}</div></details></div>`;
  }
  document.addEventListener('change',e=>{
    if(!['macro-reference','macro-horizon'].includes(e.target.id))return;
    document.querySelectorAll('[data-context]').forEach(node=>{const item=registry.get(node.dataset.context);if(item)node.outerHTML=render(item);});
    document.dispatchEvent(new Event('macro-comparison-change'));
  });
  document.addEventListener('click',e=>{
    const button=e.target.closest('[data-page],[data-csv]'),context=button?.closest('[data-context]');
    if(!context)return;
    const item=registry.get(context.dataset.context);
    if(button.hasAttribute('data-page'))context.querySelector('.macro-evidence-rows').innerHTML=evidenceTable(item,Number(button.dataset.page));
    else {
      const text=`observation_date,value,unit\r\n`+M.clean(item).map(p=>`${p.date},${p.value},${item.unit}`).join('\r\n');
      const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');
      a.href=url;a.download=`fairentry-${item.id}-history.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  });
  window.MacroContext={render,warning,mode,chart};
}());
