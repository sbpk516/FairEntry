(async function() {
  'use strict';
  const summary=document.getElementById('macro-summary'),grid=document.getElementById('macro-grid');
  if(!summary)return;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=item=>typeof item.value==='number'?item.unit==='people'?Math.round(item.value).toLocaleString('en-US'):item.value.toFixed(2):'—';
  const period=item=>!item.as_of?'Unavailable':item.frequency==='Quarterly'?`${item.as_of.slice(0,4)} Q${Math.ceil(Number(item.as_of.slice(5,7))/3)}`:item.frequency==='Monthly'?item.as_of.slice(0,7):item.as_of;
  try {
    const response=await fetch('data/macro.json',{cache:'no-cache'});
    if(!response.ok)throw new Error('Unavailable');
    const data=await response.json(),items=data.indicators;
    if(!Array.isArray(items)||!items.length)throw new Error('Empty');
    function overview() {
      summary.innerHTML='<div class="macro-summary-grid">'+['gdp','unemployment','inflation','conditions'].map(id=>{
        const item=items.find(i=>i.id===id);if(!item)return '';
        const b=MacroModel.benchmark(item,MacroContext.mode()),a=MacroModel.assessment(item,b),w=MacroContext.warning(item),t=MacroModel.trend(item,2);
        return `<div class="macro-summary-item"><small>${esc(item.group)}</small><strong class="macro-badge ${w?'neutral':a.tone}">${esc(w||a.label)}</strong><small>${fmt(item)} ${esc(item.unit)} · ${period(item)}</small><small>2 years: ${esc(t?.direction||'Unavailable')}</small></div>`;
      }).join('')+'</div><p>Level and direction are separate: an indicator can remain historically low while getting worse. Colors describe the measured dimension, not stock-market crash risk.</p>';
    }
    overview();document.addEventListener('macro-comparison-change',overview);
    if(!grid)return;
    grid.innerHTML=items.map(item=>`<article class="macro-card"><span class="macro-group">${esc(item.group)}</span><h2>${esc(item.name)}</h2><div class="macro-value">${fmt(item)}<span>${esc(item.unit==='people'?'claims / week':item.unit)}</span></div><small>${esc(item.frequency)} · Observation ${esc(period(item))}${item.id==='claims'?' · Four-week moving average':''}</small><p>${esc(item.description)}</p>${MacroContext.render(item)}${item.rule==='curve'?`<small>${item.inverted_since?'Negative on consecutive available observations since '+esc(item.inverted_since):item.last_inverted?'Last negative observation: '+esc(item.last_inverted):'No negative observation in downloaded history.'}</small>`:''}</article>`).join('');
  } catch(e) {
    summary.textContent='Macro data is unavailable. Economic conditions cannot be assessed from this snapshot.';
    if(grid)grid.textContent='The next successful data build will populate the historical comparisons.';
  }
}());
