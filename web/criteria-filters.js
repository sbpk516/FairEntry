(function(root){
  'use strict';
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  function presentationGroup(f){
    if(['Universe','Candidate screens'].includes(f.group))return 'Screening filters';
    if(['category_quality','category_survival','category_growth','price_to_fair','method_count','roic','obv','veto'].includes(f.id))return 'Buy filters';
    if(f.id.startsWith('distance_'))return 'Optional technical filters';
    return 'Advanced filters';
  }
  function matches(stock, state, catalog){
    const fields=catalog.fields||[], data=stock.filter_values||{};
    const defaults=Object.fromEntries(fields.filter(f=>f.type==='threshold').map(f=>[f.metric,f.default]));
    const threshold=k=>finite(state['screen_'+k])?state['screen_'+k]:defaults[k];
    if(state.screen){
      const n=k=>finite(data[k]);
      const growth=n('rev_growth_qoq')&&data.rev_growth_qoq>=threshold('rev_growth_qoq')&&(!n('gross_margin')||data.gross_margin>=threshold('gross_margin'));
      const cheap=(n('pb_ratio')&&data.pb_ratio<=threshold('pb_ratio'))||(n('ps_ratio')&&data.ps_ratio<=threshold('ps_ratio'))||(n('pfcf_ratio')&&data.pfcf_ratio>0&&data.pfcf_ratio<=threshold('pfcf_ratio'));
      const value=cheap&&n('perf_year')&&data.perf_year<=threshold('perf_year')&&(!n('debt_eq')||data.debt_eq<=threshold('debt_eq'));
      if(!(state.screen==='either'?(growth||value):state.screen==='quality_growth'?growth:value))return false;
    }
    return fields.every(f=>{
      const s=state[f.id], v=data[f.id];
      if(s==null||s===''||f.type==='threshold'||f.id==='screen')return true;
      if(f.type==='choice')return Array.isArray(v)?v.includes(s):v===s;
      if(s.op==='missing')return !finite(v);
      if(s.op==='known')return finite(v);
      if(!finite(v)||!finite(s.value))return false;
      return s.op==='min'?v>=s.value:v<=s.value;
    });
  }
  function mount(el,catalog,onChange,onPreset){
    if(!catalog||!catalog.fields){el.textContent='Criteria filters become available after the next data build.';return {matches:()=>true,updateCount:()=>{}};}
    const defaultPreset=catalog.presets.find(p=>p.id==='buy')||catalog.presets[0];
    let state=JSON.parse(JSON.stringify(defaultPreset.values)),preset=defaultPreset.id;
    const fields=catalog.fields.filter(f=>!f.id.startsWith('distance_ema_')).map(f=>({...f,group:presentationGroup(f)}));
    const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const groups=['Screening filters','Buy filters','Optional technical filters','Advanced filters'];
    const descriptions={
      'Screening filters':'Choose which companies qualify for your candidate list. Passing a screen does not mean Buy.',
      'Buy filters':'Choose the business scores, fair value, ROIC, weekly volume and hard-veto checks to apply. Ignore this filter skips a check; it does not change the official rating.',
      'Optional technical filters':'Moving-average proximity is optional, not a Buy requirement.',
      'Advanced filters':'Individual factors, information-only indicators, extra risk checks and official-rating filters.'
    };
    el.innerHTML='<details class="criteria-panel"><summary class="criteria-summary"><span id="criteria-title">Screening &amp; Buy filters</span><span data-preset-label></span><span data-badge></span><span class="criteria-edit"><span class="criteria-open-label">Edit filters</span><span class="criteria-close-label">Hide filters</span></span></summary><div class="criteria-body">'+
      '<p class="criteria-intro">Choose a starting point, then refine it. <b>Filters change the list, not recommendations.</b> <a href="criteria.html">Read the current rules ↗</a></p>'+
      '<div class="criteria-toolbar"><label>Start with a preset<select data-preset>'+catalog.presets.map(p=>'<option value="'+escape(p.id)+'">'+escape(p.label)+'</option>').join('')+'<option value="custom" disabled>Custom selection</option></select></label><label>Find a criterion<input type="search" data-search placeholder="Try ROIC, margin, dilution…"></label><button type="button" data-clear>Show all candidates</button><button type="button" data-reset>Restore default filters</button></div>'+
      '<p class="criteria-scope">'+escape(catalog.scope)+'</p><p class="criteria-scope">Minimum and maximum limits exclude unavailable values. “Value available” shows stocks with a reported number; “Value unavailable” shows stocks without one. Stocks must match every enabled filter.</p><div data-chips class="criteria-chips"></div><p data-results aria-live="polite"></p><p data-no-fields hidden>No matching criteria. Try another search term.</p>'+
      '<nav class="criteria-jumps" aria-label="Filter sections"><a href="#screening-filters">Screening filters ↓</a><a href="#buy-filters">Buy filters ↓</a></nav><div class="criteria-sections">'+
      groups.map((g,i)=>(i<2?'<section class="criteria-group criteria-primary" id="'+(i===0?'screening-filters':'buy-filters')+'"><h3>':'<details class="criteria-group"><summary>')+escape(g)+' <span data-group-count="'+escape(g)+'"></span>'+(i<2?'</h3>':'</summary>')+'<p class="criteria-group-description">'+escape(descriptions[g])+'</p><div class="criteria-grid">'+fields.filter(f=>f.group===g).map(f=>{
        const id='cf-'+f.id;
        let input;
        if(f.type==='choice')input='<select id="'+id+'" data-choice="'+f.id+'"><option value="">Ignore this filter</option>'+f.options.map(o=>'<option value="'+escape(o[0])+'">'+escape(o[1])+'</option>').join('')+'</select>';
        else if(f.type==='threshold')input='<input id="'+id+'" data-threshold="'+f.id+'" type="number" step="any" placeholder="'+f.default+'">';
        else input='<div class="criteria-number"><select id="'+id+'" data-op="'+f.id+'"><option value="">Ignore this filter</option><option value="min">At least ≥</option><option value="max">At most ≤</option><option value="known">Value available</option><option value="missing">Value unavailable</option></select><input type="number" step="any" data-number="'+f.id+'" aria-label="'+escape(f.label)+' threshold" placeholder="'+(f.default??'Value')+'" disabled></div>';
        return '<div class="criteria-field" data-field="'+f.id+'"><label for="'+id+'">'+escape(f.label)+' <small>'+escape(f.unit||'')+'</small></label>'+input+'<small id="'+id+'-help">'+(f.default!=null?'Production default: '+escape(f.default)+' '+escape(f.unit||'')+'. ':'')+escape(f.help||'')+'</small></div>';
      }).join('')+'</div>'+(i<2?'</section>':'</details>')).join('')+'</div></div></details>';
    const q=s=>el.querySelector(s);
    function clearSearch(){q('[data-search]').value='';el.querySelectorAll('[data-field],.criteria-group').forEach(r=>r.hidden=false);q('[data-no-fields]').hidden=true;}
    function active(f){return state[f.id]!=null&&!(f.type==='threshold'&&!state.screen);}
    function sync(){
      q('[data-preset]').value=preset;
      q('[data-preset-label]').textContent=(catalog.presets.find(p=>p.id===preset)||{label:'Custom filters'}).label;
      fields.forEach(f=>{
        const s=state[f.id], row=q('[data-field="'+f.id+'"]');
        row.classList.toggle('is-active',active(f));
        if(f.type==='choice')row.querySelector('select').value=s||'';
        else if(f.type==='threshold'){const input=row.querySelector('input');if(document.activeElement!==input)input.value=s??'';input.disabled=!state.screen;}
        else {row.querySelector('select').value=s?.op||'';const input=row.querySelector('input');if(document.activeElement!==input)input.value=s?.value??'';input.disabled=!s||['known','missing'].includes(s.op);}
        row.querySelectorAll('input,select').forEach(input=>input.setAttribute('aria-describedby','cf-'+f.id+'-help'));
      });
      const selected=fields.filter(active);
      q('[data-badge]').textContent=selected.length+' active';
      groups.forEach(g=>{const count=selected.filter(f=>f.group===g).length;q('[data-group-count="'+g+'"]').textContent=count?'· '+count+' active':'';});
      q('[data-chips]').innerHTML=selected.map(f=>{
        const s=state[f.id];
        const value=f.type==='choice'?(f.options.find(o=>o[0]===s)||[])[1]:f.type==='threshold'?s:s.op==='min'?'≥ '+(s.value??'enter value'):s.op==='max'?'≤ '+(s.value??'enter value'):s.op==='known'?'Value available':'Value unavailable';
        return '<button type="button" data-remove="'+f.id+'" aria-label="Remove '+escape(f.label)+' filter">'+escape(f.label)+': '+escape(value)+' ×</button>';
      }).join('');
    }
    function changed(){preset='custom';sync();onChange();}
    el.addEventListener('change',e=>{
      const t=e.target, d=t.dataset;
      if(d.preset!==undefined){preset=t.value;state=JSON.parse(JSON.stringify(catalog.presets.find(p=>p.id===preset).values));clearSearch();onPreset();sync();onChange();}
      else if(d.choice){if(t.value)state[d.choice]=t.value;else delete state[d.choice];changed();}
      else if(d.op){const f=fields.find(f=>f.id===d.op);if(t.value)state[d.op]={op:t.value,value:state[d.op]?.value??f.default??0};else delete state[d.op];changed();}
    });
    el.addEventListener('input',e=>{
      const t=e.target,d=t.dataset;
      if(d.number&&state[d.number]){state[d.number].value=t.value!==''&&t.validity.valid?Number(t.value):null;changed();}
      else if(d.threshold){if(t.value!==''&&t.validity.valid)state[d.threshold]=Number(t.value);else delete state[d.threshold];changed();}
    });
    el.addEventListener('click',e=>{
      const t=e.target.closest('button');if(!t)return;
      if(t.dataset.remove){delete state[t.dataset.remove];changed();}
      else if(t.hasAttribute('data-clear')||t.hasAttribute('data-reset')){state={};preset='all';if(t.hasAttribute('data-reset')){state=JSON.parse(JSON.stringify(defaultPreset.values));preset=defaultPreset.id;}clearSearch();onPreset();sync();onChange();}
    });
    q('[data-search]').addEventListener('input',e=>{
      const term=e.target.value.trim().toLowerCase();let found=0;
      fields.forEach(f=>{const row=q('[data-field="'+f.id+'"]');row.hidden=!!term&&!`${f.label} ${f.help}`.toLowerCase().includes(term)&&f.group.toLowerCase()!==term;if(!row.hidden)found++;});
      el.querySelectorAll('.criteria-group').forEach(g=>{g.hidden=![...g.querySelectorAll('[data-field]')].some(r=>!r.hidden);if(term&&!g.hidden&&g.tagName==='DETAILS')g.open=true;});
      q('[data-no-fields]').hidden=found>0;
    });
    sync();
    return {clear:()=>{state={};preset='all';sync();},matches:s=>matches(s,state,catalog),updateCount:(shown,total,emerging)=>{
      q('[data-results]').textContent=emerging?'Emerging is a separate research dataset. Criteria evidence is not exported there; active criteria may return no matches.':shown+' of '+total+' published candidates match all current filters.'+(shown===0?' No matches. Remove a filter or choose Show all candidates to broaden the list.':'');
    }};
  }
  const api={matches,mount,presentationGroup};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.FairEntryFilters=api;
})(typeof window!=='undefined'?window:globalThis);
