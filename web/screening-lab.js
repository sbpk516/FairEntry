(function(root){
  'use strict';
  const numeric=v=>typeof v==='number'&&Number.isFinite(v);
  const RULES=[
    ['rev_growth_qoq','Revenue growth minimum','%', 'quality_growth','revenue_growth_min',10],
    ['gross_margin','Gross margin minimum','%', 'quality_growth','gross_margin_min',25],
    ['pb_ratio','Price / book maximum','×','deep_value','pb_max',2],
    ['ps_ratio','Price / sales maximum','×','deep_value','ps_max',2],
    ['pfcf_ratio','Positive price / free cash flow maximum','×','deep_value','pfcf_max',18],
    ['perf_year','One-year price change maximum','%','deep_value','performance_max',0],
    ['debt_eq','Debt / equity maximum','×','deep_value','debt_equity_max',2.5]
  ];
  const VERSIONS=[
    ['basic','Basic checks only','No revenue, margin, valuation, declining-price or debt screen.'],
    ['current','Current screens','Quality Growth OR Deep Value, with the current thresholds.'],
    ['no_multiples','Remove all 3 valuation limits','Keep Quality Growth OR Deep Value; Deep Value keeps the price-change and debt checks.'],
    ['no_pb_ps','P/FCF only — no P/B or P/S','Deep Value must pass P/FCF. This can reduce matches: two alternative cheapness paths are removed.'],
    ['value_no_multiples','Deep Value without valuation limits','Only its price-change and debt checks remain, after basic eligibility.']
  ];
  function settings(meta,version='basic'){
    const rules=Object.fromEntries(RULES.map(([key,,,_group,_key,fallback])=>[key,{enabled:true,value:meta[_group]?.[_key]??fallback}]));
    if(['no_multiples','value_no_multiples','basic'].includes(version))['pb_ratio','ps_ratio','pfcf_ratio'].forEach(k=>rules[k].enabled=false);
    if(version==='no_pb_ps')['pb_ratio','ps_ratio'].forEach(k=>rules[k].enabled=false);
    return {route:version==='basic'?'none':version==='value_no_multiples'?'deep_value':'either',rules};
  }
  function baseMatch(s,base){
    const m=s.metrics;
    return (!base.sector||s.sector===base.sector)&&numeric(m.market_cap)&&m.market_cap>=base.cap*1e6&&
      numeric(m.price)&&m.price>=base.price&&(base.liquidity===0||(numeric(m.avg_dollar_volume)&&m.avg_dollar_volume>=base.liquidity*1e6));
  }
  function match(s,state){
    if(state.route==='none')return true;
    const m=s.metrics,r=state.rules;
    const min=k=>!r[k].enabled||(numeric(m[k])&&m[k]>=r[k].value);
    const max=k=>!r[k].enabled||(numeric(m[k])&&m[k]<=r[k].value);
    const growth=min('rev_growth_qoq')&&(!numeric(m.gross_margin)||min('gross_margin'));
    const cheapKeys=['pb_ratio','ps_ratio','pfcf_ratio'].filter(k=>r[k].enabled);
    const cheap=!cheapKeys.length||cheapKeys.some(k=>max(k)&&(k!=='pfcf_ratio'||m[k]>0));
    const value=cheap&&max('perf_year')&&(!numeric(m.debt_eq)||max('debt_eq'));
    return state.route==='quality_growth'?growth:state.route==='deep_value'?value:growth||value;
  }
  function mount(data){
    const $=s=>document.querySelector(s),meta=data.meta,stocks=data.stocks;
    const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const num=v=>numeric(v)?v.toLocaleString(undefined,{maximumFractionDigits:2}):'—';
    let state=settings(meta),active='basic',page=1;
    $('#scope').textContent=meta.scope;
    if(!meta.available){$('#status').textContent='No screening snapshot is available yet. Run a data refresh to populate this research page.';return;}
    const age=(Date.now()-Date.parse(meta.snapshot_at))/36e5;
    $('#status').textContent='Snapshot: '+new Date(meta.snapshot_at).toLocaleString()+' · '+stocks.length+' pre-screen listings loaded.'+(age>48?' Snapshot is over 48 hours old; counts are historical, not current market counts.':'');
    $('#status').classList.toggle('warning',age>48);
    $('#sector').innerHTML='<option value="">All configured sectors</option>'+meta.sectors.map(s=>'<option>'+esc(s)+'</option>').join('');
    function resetBase(){const d=meta.defaults;$('#cap').value=d.market_cap_min_usd/1e6;$('#price').value=d.price_min_usd;$('#liquidity').value=d.avg_dollar_volume_min/1e6;$('#sector').value='';$('#search').value='';}
    resetBase();
    $('#versions').innerHTML=VERSIONS.map(([id,title,help])=>'<button class="version" type="button" data-version="'+id+'" aria-pressed="false"><strong>'+esc(title)+'</strong><span data-count="'+id+'"></span><small>'+esc(help)+'</small></button>').join('');
    function renderRules(){
      $('#route').value=state.route;
      $('#rules').innerHTML=['quality_growth','deep_value'].map((g,i)=>'<fieldset '+(state.route!=='either'&&state.route!==g?'disabled':'')+'><legend>'+(!i?'Quality Growth':'Deep Value')+'</legend>'+RULES.filter(r=>r[3]===g).map(([key,label,unit])=>'<div class="rule"><label><input type="checkbox" data-enabled="'+key+'" '+(state.rules[key].enabled?'checked':'')+'>'+esc(label)+'</label><label class="threshold"><span class="sr-only">'+esc(label)+' threshold</span><input type="number" step="any" data-value="'+key+'" value="'+state.rules[key].value+'" '+(!state.rules[key].enabled?'disabled':'')+'><span>'+unit+'</span></label></div>').join('')+'</fieldset>').join('');
    }
    function base(){return {cap:Number($('#cap').value),price:Number($('#price').value),liquidity:Number($('#liquidity').value),sector:$('#sector').value};}
    function render(){
      const valid=['cap','price','liquidity'].every(k=>$('#'+k).value!==''&&$('#'+k).validity.valid)&&RULES.every(([k,,,g])=>(state.route!=='either'&&state.route!==g)||!state.rules[k].enabled||numeric(state.rules[k].value));
      $('#invalid').hidden=valid;
      if(!valid){$('#summary').textContent='Enter valid thresholds to see results.';$('#rows').innerHTML='';$('#pagination').hidden=true;$('#versions').querySelectorAll('[data-count]').forEach(e=>e.textContent='—');return;}
      const eligible=stocks.filter(s=>baseMatch(s,base()));
      VERSIONS.forEach(([id])=>{const b=$('[data-version="'+id+'"]');b.setAttribute('aria-pressed',String(active===id));b.querySelector('[data-count]').textContent=eligible.filter(s=>match(s,settings(meta,id))).length.toLocaleString()+' listings';});
      const screened=eligible.filter(s=>match(s,state));
      const q=$('#search').value.trim().toLowerCase();
      const result=screened.filter(s=>(s.ticker+' '+s.company).toLowerCase().includes(q));
      const sort=$('#sort').value;
      result.sort((a,b)=>sort==='ticker'?a.ticker.localeCompare(b.ticker):(b.metrics[sort]??-Infinity)-(a.metrics[sort]??-Infinity)||a.ticker.localeCompare(b.ticker));
      const size=Number($('#size').value),pages=Math.max(1,Math.ceil(result.length/size));page=Math.min(page,pages);
      $('#summary').textContent=eligible.length.toLocaleString()+' pass basic checks → '+screened.length.toLocaleString()+' pass '+(active==='custom'?'your custom screen':VERSIONS.find(v=>v[0]===active)[1])+'. '+result.length.toLocaleString()+' match company search.';
      $('#active').textContent=active==='custom'?'Custom version':VERSIONS.find(v=>v[0]===active)[1];
      $('#rows').innerHTML=result.slice((page-1)*size,page*size).map(s=>{
        const m=s.metrics;
        return '<tr><th scope="row">'+esc(s.ticker)+'<small>'+esc(s.company)+'</small></th><td>'+esc(s.sector)+'</td><td>'+num(m.price)+'</td><td>'+num(m.market_cap/1e6)+'</td><td>'+num(numeric(m.avg_dollar_volume)?m.avg_dollar_volume/1e6:null)+'</td>'+['pb_ratio','ps_ratio','pfcf_ratio','rev_growth_qoq','gross_margin','perf_year','debt_eq'].map(k=>'<td>'+num(m[k])+'</td>').join('')+'<td>'+esc(s.official_verdict||'Not on official board')+'</td></tr>';
      }).join('')||'<tr><td colspan="13">No matches. Try Basic checks only, lower liquidity, or clear company search.</td></tr>';
      $('#pagination').hidden=false;$('#page').textContent='Page '+page+' of '+pages;$('#prev').disabled=page===1;$('#next').disabled=page===pages;
    }
    $('#versions').addEventListener('click',e=>{const b=e.target.closest('[data-version]');if(!b)return;active=b.dataset.version;state=settings(meta,active);page=1;renderRules();render();});
    $('#route').addEventListener('change',e=>{state.route=e.target.value;active='custom';page=1;renderRules();render();});
    $('#rules').addEventListener('change',e=>{if(e.target.dataset.enabled){state.rules[e.target.dataset.enabled].enabled=e.target.checked;active='custom';page=1;renderRules();render();}});
    $('#rules').addEventListener('input',e=>{const key=e.target.dataset.value;if(key){state.rules[key].value=e.target.value!==''&&e.target.validity.valid?Number(e.target.value):null;active='custom';page=1;render();}});
    ['cap','price','liquidity','sector','search','sort','size'].forEach(id=>$('#'+id).addEventListener('input',()=>{page=1;render();}));
    $('#reset').addEventListener('click',()=>{resetBase();state=settings(meta);active='basic';page=1;renderRules();render();});
    $('#prev').addEventListener('click',()=>{page--;render();});$('#next').addEventListener('click',()=>{page++;render();});
    renderRules();render();
  }
  const api={settings,baseMatch,match};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else {root.ScreeningLab=api;fetch('data/screening-lab.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('Snapshot unavailable');return r.json();}).then(mount).catch(()=>{document.querySelector('#status').textContent='Could not load the screening snapshot. Please retry after the next deployment.';});}
})(typeof window!=='undefined'?window:globalThis);
