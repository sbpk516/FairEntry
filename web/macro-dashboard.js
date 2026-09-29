(async function() {
  'use strict';
  const M=window.MacroModel,B=window.MacroBrief,C=window.MacroContext;
  const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let items=[],category='All indicators',returnFocus=null;
  const groups=['All indicators','Jobs & growth','Prices & rates','Financial conditions','Market valuations'];
  const displayValue=i=>B.number(i.value,i.unit==='people'?0:i.unit==='index'||i.rule==='curve'?2:1);
  const displayUnit=i=>i.unit==='people'?'claims / week':i.unit==='% YoY'?'% per year':i.unit==='pp'?'percentage points':i.unit==='index'?'index':i.unit;
  const date=i=>!i.as_of?'Date unavailable':i.frequency==='Quarterly'?`Q${Math.ceil(Number(i.as_of.slice(5,7))/3)} ${i.as_of.slice(0,4)}`:new Date(i.as_of+'T00:00:00Z').toLocaleDateString('en-US',{month:'short',...(i.frequency==='Monthly'?{}:{day:'numeric'}),year:'numeric',timeZone:'UTC'});
  const story=i=>B.reading(i,M.benchmark(i,C.mode()),C.warning(i));
  const badge=(label,tone)=>`<span class="brief-badge ${tone}"><span aria-hidden="true"></span>${esc(label)}</span>`;
  function brief() {
    const o=B.overview(items,C.mode(),C.warning);
    const tones={gdp:'Growth',unemployment:'Jobs',inflation:'Prices',conditions:'Financing'};
    $('economic-brief').innerHTML=`<div class="brief-main-story"><p class="brief-eyebrow">THE SHORT VERSION</p>${badge(o.status,o.pillars.some(p=>!p.available)?'neutral':o.risks>=3?'risk':o.status==='Mostly supportive readings'?'good':'watch')}<h2>${esc(o.headline)}</h2><p>${esc(o.detail)}</p><a class="brief-cta" href="#indicators">Explore the evidence <span aria-hidden="true">↓</span></a><small>Based on the latest available periods, which differ by indicator.</small></div><div class="brief-pillar-panel"><h3>Four parts of the picture</h3>${o.pillars.map(p=>`<button class="brief-pillar" data-open="${p.item?.id||''}" ${!p.item?'disabled':''}><span class="pillar-dot ${p.tone}" aria-hidden="true"></span><span><small>${esc(tones[p.item?.id]||p.title)}</small><strong>${esc(p.label||'Unavailable')}</strong></span><span class="pillar-value">${p.item?displayValue(p.item):'—'}<small>${p.item?esc(displayUnit(p.item)):''}</small></span><span aria-hidden="true">↗</span></button>`).join('')}<p>Different signals. No single crash score.</p></div>`;
    const choices=['inflation','gdp','claims'].map(id=>items.find(i=>i.id===id)).filter(Boolean);
    $('brief-takeaways').innerHTML=choices.map((i,n)=>{
      const s=story(i),t=B.movement(i),heading=!s.available?'A reading needs an update':i.id==='inflation'?(i.value>2?'Keep an eye on inflation':'Keep price stability in view'):i.id==='gdp'?(t.trend&&t.trend.change<0?'Growth has lost some pace':'Check the pace of growth'):s.tone==='good'?'A reassuring jobs signal':'Watch new unemployment claims';
      return `<button class="takeaway" data-open="${i.id}"><span class="takeaway-number">0${n+1}</span><div><span class="takeaway-label ${s.tone}">${!s.available?'DATA GAP':s.tone==='risk'?'A CONCERN':s.tone==='good'?'A SUPPORTIVE SIGNAL':'WORTH WATCHING'}</span><h3>${esc(heading)}</h3><p>${esc(s.explanation)}</p></div><span class="takeaway-arrow" aria-hidden="true">↗</span></button>`;
    }).join('');
  }
  function cards() {
    const selected=items.filter(i=>category==='All indicators'||B.topics[i.id]?.[1]===category);
    $('brief-result-count').textContent=`Showing ${selected.length} of ${items.length} indicators`;
    $('macro-grid').innerHTML=selected.map(i=>{
      const s=story(i),t=B.movement(i),b=M.benchmark(i,C.mode()),w=C.warning(i);
      return `<article class="brief-indicator" id="indicator-${i.id}"><div class="indicator-top"><span class="brief-eyebrow">${esc(s.group)}</span>${badge(s.label,s.tone)}</div><h3>${esc(s.title)}</h3><p class="indicator-description">${esc(s.definition)}</p><div class="indicator-reading"><strong>${displayValue(i)}</strong><span>${esc(displayUnit(i))}</span></div><p class="indicator-date">${esc(date(i))} · ${esc(i.name)}</p><p class="indicator-meaning">${esc(s.explanation)}</p><div class="brief-chart">${C.chart(i,b)}</div><div class="indicator-comparison"><span>COMPARE WITH</span><p>${esc(s.reference)}</p><small>${b?`Historical range uses ${b.start.slice(0,4)}–${b.end.slice(0,4)}.`:'A full historical reference is unavailable.'}</small></div><div class="indicator-trend"><span class="trend-arrow ${w?'neutral':t.tone}" aria-hidden="true">${t.trend?.change>0?'↗':t.trend?.change<0?'↘':'→'}</span><div><strong>${esc(w?'Historical direction only':t.label)} <span>over 2 years</span></strong><p>${esc(t.detail)}</p></div></div><button class="indicator-detail" type="button" data-open="${i.id}">Why this status? <span>History, sources & data <span aria-hidden="true">↗</span></span></button></article>`;
    }).join('')||'<p class="brief-empty">No indicators are available for this topic.</p>';
    $('brief-categories').innerHTML=groups.map(g=>`<button type="button" data-category="${esc(g)}" aria-pressed="${category===g}">${esc(g)} <span>${items.filter(i=>g==='All indicators'||B.topics[i.id]?.[1]===g).length}</span></button>`).join('');
  }
  function detail(id) {
    const i=items.find(i=>i.id===id);if(!i)return;
    const s=story(i);
    $('macro-detail-content').innerHTML=`<h2 id="macro-detail-title">${esc(s.title)}</h2><p class="detail-subtitle">${esc(i.name)} · ${esc(date(i))}</p><div class="indicator-reading"><strong>${displayValue(i)}</strong><span>${esc(displayUnit(i))}</span></div><p>${esc(i.description)}</p>${C.render(i)}${i.limitations?`<h3>Limitations of this measure</h3><p>${esc(i.limitations)}</p>`:''}${i.rule==='curve'?`<p>${i.last_inverted?'Last negative observation in downloaded history: '+esc(i.last_inverted):'No negative observation in downloaded history.'} Returning above zero does not establish that recession risk has passed.</p>`:''}`;
    // The chart is visible first; the longer tables are available on demand.
    $('macro-detail-content').querySelector('.macro-trends')?.removeAttribute('open');
    returnFocus=document.activeElement;$('macro-detail').showModal();
  }
  document.addEventListener('click',e=>{
    const opener=e.target.closest('[data-open]');if(opener)detail(opener.dataset.open);
    const filter=e.target.closest('[data-category]');if(filter){category=filter.dataset.category;cards();document.querySelector(`[data-category="${category}"]`)?.focus();}
  });
  document.addEventListener('macro-comparison-change',()=>{brief();cards();});
  ['open-method','footer-method'].forEach(id=>$(id).addEventListener('click',()=>{$('macro-method').showModal();}));
  $('close-detail').addEventListener('click',()=>$('macro-detail').close());
  $('close-method').addEventListener('click',()=>$('macro-method').close());
  $('macro-detail').addEventListener('close',()=>{returnFocus?.focus();});
  for(const id of ['macro-detail','macro-method'])$(id).addEventListener('click',e=>{if(e.target===$(id)){const r=$(id).getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$(id).close();}});
  const responses=await Promise.allSettled(['macro','market-valuation'].map(async name=>{const r=await fetch(`data/${name}.json`,{cache:'no-cache'});if(!r.ok)throw new Error(name);return r.json();}));
  const macro=responses[0].status==='fulfilled'?responses[0].value:null,valuation=responses[1].status==='fulfilled'?responses[1].value:null;
  items=[...(Array.isArray(macro?.indicators)?macro.indicators:[]),...Object.entries(valuation?.indicators||{}).map(([id,i])=>({...i,id}))];
  // Keep all expected measures visible even when an entire source is unavailable.
  for(const [id,[title]] of Object.entries(B.topics))if(!items.some(i=>i.id===id))items.push({id,name:title,value:null,history:[],unit:'',status:'unavailable'});
  const good=items.filter(i=>!C.warning(i)).length;
  $('brief-updated').innerHTML=`<span class="update-dot ${good===11?'good':'watch'}" aria-hidden="true"></span><strong>${good} of 11 readings up to date</strong><small>Latest measured periods appear on each card</small>`;
  brief();cards();
}());
