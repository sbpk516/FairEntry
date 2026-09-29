(async function() {
  'use strict';
  const $ = id => document.getElementById(id), model = InvestorsModel;
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (v,d=2) => model.numeric(v) ? v.toLocaleString('en-US',{maximumFractionDigits:d}) : 'Unknown';
  const money = v => model.numeric(v) ? '$'+num(v,0) : 'Unknown';
  const link = (url,label) => /^https:\/\//.test(url || '') ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : esc(label);
  const list = rows => rows.length ? '<ul>'+rows.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>' : '<p class="investor-muted">No verified evidence available.</p>';
  let data, view='shortlist', page=0, details=[];
  const perPage=60;
  function filters() {return Object.fromEntries([...$('investor-filters').querySelectorAll('input')].map(e=>[e.name,Number(e.value)]));}
  function selected(s) {return $('investor').value === 'all' || s === $('investor').value;}
  function sourceName(id) {const s=data.sources.find(s=>s.id===id);return s ? s.organization : id;}
  function evidenceSelected(stock) {return (stock.positions||[]).some(p=>selected(p.source_id)) || (stock.research||[]).some(p=>selected(p.source_id));}
  function matches(row) {return [row.company,row.ticker,row.cusip,row.title].join(' ').toLowerCase().includes($('investor-search').value.trim().toLowerCase());}
  function openButton(row,label) {const index=details.push(row)-1;return `<button class="holding-open" data-detail="${index}">${esc(label)}</button>`;}
  function badge(status) {return `<span class="investor-status">${esc(status)}</span>`;}
  function renderSources() {
    $('source-cards').innerHTML=data.sources.map(s=>`<button data-source="${esc(s.id)}" aria-pressed="${$('investor').value===s.id}"><strong>${esc(s.person)}</strong><small>${esc(s.organization)}</small><small>${s.latest_period ? 'Holdings: '+esc(s.latest_period)+(s.historical?' · historical':'') : 'Holdings unavailable / not disclosed'}</small></button>`).join('');
    const s=data.sources.find(s=>s.id===$('investor').value);
    $('source-detail').innerHTML=s ? `<h3>${esc(s.person)} · ${esc(s.organization)}</h3><p>${esc(s.note)}</p><p>${link(s.url,'Original source')}${s.cik?' · '+link('https://www.sec.gov/edgar/browse/?CIK='+s.cik,'SEC reporting entity'):''}</p><p>Holdings: ${esc(s.holdings_status||'Not checked')} · Last successful check: ${esc(s.holdings_checked_at||'Never')}${s.entity_verified?' · SEC entity: '+esc(s.entity_verified):''}</p>${s.holdings_error?'<p class="investor-warning">'+esc(s.holdings_error)+'</p>':''}<p>Research: ${esc(s.research_status||'Not checked')}${s.research_error?' · '+esc(s.research_error):''}</p>` : '<p>Select a source to see its shortlist, complete disclosed holdings, filing coverage and research. Overlap counts distinct reporting sources, not independent proof of a thesis.</p>';
    const reports=data.sources.filter(s=>selected(s.id)).flatMap(s=>s.reports||[]);
    const periods=[...new Set(reports.map(r=>r.period))].sort().reverse();
    const old=$('holdings-period').value;
    $('holdings-period').innerHTML='<option value="latest">Latest report per source</option>'+periods.map(p=>`<option>${esc(p)}</option>`).join('');
    if(periods.includes(old))$('holdings-period').value=old;
  }
  function shortlist() {
    const f=filters();
    return data.candidates.filter(evidenceSelected).filter(matches).map(s=>({stock:s,evaluation:model.evaluate(s,f,data)}))
      .filter(x=>$('research-status').value==='all'||x.evaluation.status===$('research-status').value)
      .sort((a,b)=>a.evaluation.rank-b.evaluation.rank||b.evaluation.holders-a.evaluation.holders||a.stock.ticker.localeCompare(b.stock.ticker));
  }
  function holdings() {
    return data.sources.filter(s=>selected(s.id)).flatMap(s=>{
      const p=$('holdings-period').value;
      const r=(s.reports||[]).find(r=>r.period===(p==='latest'?s.latest_period:p));
      if(!r)return [];
      return Object.values(r.holdings).map(h=>({...h,source_id:s.id,period:r.period,filed:r.filed,published_at:r.published_at,url:r.url,
        historical:s.historical||r.period!==s.latest_period,current_coverage:s.current_coverage,revised:r.revised}));
    }).filter(matches).sort((a,b)=>(b.reported_value??-1)-(a.reported_value??-1));
  }
  function table(headers,rows) {return `<div class="investor-table-wrap"><table class="investor-table"><thead><tr>${headers.map(h=>'<th scope="col">'+esc(h)+'</th>').join('')}</tr></thead><tbody>${rows.map(c=>'<tr>'+c.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join('')}</tbody></table></div>`;}
  function render() {
    if(!data)return;
    details=[];
    const highlights=shortlist().slice(0,3);
    $('investor-highlights').innerHTML=highlights.length?highlights.map(({stock:s,evaluation:e})=>`<article class="investor-idea"><h3>${openButton(s,s.ticker+' · '+s.company)}</h3>${badge(e.status)}${list([...e.reasons.slice(0,1),...e.risks.slice(0,1)])}</article>`).join(''):'<p>No candidates match the current source and criteria.</p>';
    const recent=data.changes.filter(c=>selected(c.source_id)).slice(0,3);
    $('recent-disclosure-summary').innerHTML='<h3>Recent portfolio disclosures</h3>'+(recent.length?list(recent.map(c=>`${sourceName(c.source_id)}: reported ${c.kind} in ${c.company} · holdings ${c.period}, published ${c.filed}${c.revised?' · revised':''}`)):'<p>No comparable recent disclosures available. Source coverage is shown below.</p>');
    $('holdings-period-wrap').hidden=view!=='holdings';
    $('research-status').disabled=view!=='shortlist';
    let rows=view==='shortlist'?shortlist():view==='holdings'?holdings():view==='changes'?data.changes.filter(c=>selected(c.source_id)&&matches(c)):data.research.filter(r=>selected(r.source_id)&&matches(r));
    page=Math.min(page,Math.max(0,Math.ceil(rows.length/perPage)-1));
    const displayed=rows.slice(page*perPage,(page+1)*perPage);
    $('investor-count').textContent=`${rows.length} ${view==='shortlist'?'research candidates':view==='holdings'?'disclosed positions':view==='changes'?'reported position changes':'published research items'}${view==='holdings'?' · Financial filters and research-status selection do not hide holdings.':''}`;
    if(view==='shortlist')$('investor-results').innerHTML='<div class="investor-shortlist">'+displayed.map(({stock:s,evaluation:e})=>`<article class="investor-idea"><h3>${openButton(s,s.ticker+' · '+s.company)}</h3>${badge(e.status)}<p>${esc(e.confidence)}</p><p>${e.holders} disclosed holder(s) · ${s.research.length} research reference(s)</p>${list([...e.reasons.slice(0,2),...e.risks.slice(0,2)])}<p>${s.positions.length?'Disclosed interest is a research lead, not evidence of purchase price.':'Research idea; ownership unknown.'}</p></article>`).join('')+'</div>';
    if(view==='holdings')$('investor-results').innerHTML=table(['Company / security','Reporting source','Shares / principal','Reported value / weight','Holdings date / public date'],displayed.map(r=>[
      openButton(r,(r.ticker||'Ticker unknown')+' · '+r.company)+`<small>${esc(r.cusip)} · ${esc(r.share_class)} · ${esc(r.put_call||r.share_type)}</small>`,
      esc(sourceName(r.source_id))+`<small>${r.historical?'Historical snapshot':r.current_coverage?'Latest disclosed snapshot':'Source unavailable — saved snapshot'}</small>`,num(r.shares,0)+' '+esc(r.share_type),money(r.reported_value)+`<small>${num(r.weight_pct)}% of covered 13F value</small>`,
      esc(r.period)+'<small>Published '+esc(r.published_at)+(r.revised?' · Revised':'')+'</small>'+link(r.url,'Original filing')]));
    if(view==='changes')$('investor-results').innerHTML='<p class="investor-muted">Changes between comparable quarterly snapshots; unreviewed splits, reorganizations or disclosure changes may affect these counts. These are not known transaction dates.</p>'+table(['Reported change','Company','Source','Share count','Report / public date'],displayed.map(r=>[badge(r.kind)+(r.revised?'<small>Revised comparison; not a fresh trade</small>':''),openButton(r,r.company)+`<small>${esc(r.cusip)} · ${esc(r.put_call||r.share_class)}</small>`,esc(sourceName(r.source_id))+(r.historical?'<small>Historical</small>':''),num(r.prior_shares,0)+' → '+num(r.shares,0)+`<small>${num(r.share_change_pct)}%</small>`,esc(r.period)+'<small>'+esc(r.published_at)+'</small>'+link(r.url,'Filing')]));
    if(view==='research')$('investor-results').innerHTML=table(['Published research','Source','Published','Association'],displayed.map(r=>[link(r.url,r.title)+'<small>'+esc(r.access)+'</small>',esc(sourceName(r.source_id)),esc(r.published_at.slice(0,10)),r.tickers.length?r.tickers.map(t=>openButton(data.candidates.find(s=>s.ticker===t)||{ticker:t,company:t},t)).join(' · ')+'<small>Ownership unknown; stance not inferred from title.</small>':'Unclassified inbox — no verified company association']));
    if(!rows.length)$('investor-results').innerHTML='<div class="investor-empty">No matching evidence is available. Check source coverage, broaden the filters or search, or choose another view. Unavailable holdings are not an empty portfolio.</div>';
    $('investor-pages').innerHTML=rows.length>perPage?`<button data-page="-1" ${page===0?'disabled':''}>Previous</button><span>Page ${page+1} of ${Math.ceil(rows.length/perPage)}</span><button data-page="1" ${(page+1)*perPage>=rows.length?'disabled':''}>Next</button>`:'';
  }
  function showDetail(row) {
    const s=data.candidates.find(s=>row.ticker&&s.ticker===row.ticker)||{ticker:row.ticker,company:row.company,positions:[],research:[],history:[],metrics:{}};
    const e=model.evaluate(s,filters(),data), research=s.research||[], f=s.fairentry;
    const positions=row.security_id?[row]:(s.positions||[]);
    const metricLabels={price:'Stock price ($)',drawdown:'Decline from one-year closing high (%)',profit_margin:'Net profit margin (%)',rev_growth_qoq:'Quarterly revenue growth vs year ago (%)',debt_eq:'Debt / equity (×)',pfcf_ratio:'Price / free cash flow (×)',fwd_pe:'Forward P/E (×)',perf_year:'One-year price return (%)'};
    const fieldRows=Object.entries(s.metrics||{}).map(([k,v])=>[esc(metricLabels[k]||k),num(v.value),esc(v.source),esc(v.fetched_at)+(v.observed_at?'<small>Price observation '+esc(v.observed_at)+'</small>':''),(v.url?link(v.url,'Provider'):'Canonical FairEntry store')+'<small>'+esc(v.basis||'Provider-reported metric')+'</small>']);
    const thesis=research.filter(r=>r.thesis);
    $('investor-stock-body').innerHTML=`<h2>${esc(s.ticker||'Ticker not resolved')} · ${esc(s.company||row.company)}</h2>${badge(e.status)}<p>${esc(e.confidence)}</p>
      <div class="investor-detail-grid"><section><h3>Why it meets or misses your criteria</h3>${list(e.checks.map(c=>(c.state==='pass'?'Pass: ':c.state==='unknown'?'Unknown: ':'Miss: ')+c.explanation))}</section><section><h3>What could invalidate this research case?</h3>${list(['Profitability, revenue growth or balance-sheet strength falling below your selected thresholds.','Updated cash-flow valuation failing your criteria.','The published thesis becoming outdated or its stated assumptions failing.',...thesis.flatMap(r=>r.invalidation||[])])}<p>These are review conditions, not verified predictions.</p></section></div>
      <h3>Disclosed position details</h3>${positions.length?table(['Source / security','Shares','Reported value','Covered weight','Holdings / publication','Original source'],positions.map(p=>[esc(sourceName(p.source_id))+'<small>'+esc(p.cusip)+' · '+esc(p.share_class)+' · '+esc(p.put_call||p.share_type)+'</small>',num(p.shares,0),money(p.reported_value),num(p.weight_pct)+'%',esc(p.period)+'<small>'+esc(p.published_at||p.filed)+'</small>',link(p.url,'Filing')])):'<p>No current disclosed holding establishes ownership for this idea.</p>'}
      <p>Purchase price: unknown. Trade date: unknown. Reported value is market value at the holdings date, not acquisition cost. 13F coverage omits many international/private assets and short positions; options values reflect underlying securities, not premiums.</p>
      <h3>Business thesis and original research</h3>${research.length?research.map(r=>`<article><h4>${link(r.url,r.title)}</h4><p>${esc(sourceName(r.source_id))} · ${esc(r.published_at.slice(0,10))} · Research evidence; ownership unknown</p><p>${esc(r.thesis||'Only a public title/link was imported. No verified thesis summary is available; read the original analysis before relying on it.')}</p>${r.risks?list(r.risks):''}</article>`).join(''):'<p>No verified investor thesis is available. Holding a security does not tell us why the manager owns it.</p>'}
      <h3>Why the price fell</h3>${research.some(r=>r.price_decline_reason)?research.filter(r=>r.price_decline_reason).map(r=>'<p>'+esc(r.price_decline_reason)+' '+link(r.url,'Dated source')+'</p>').join(''):'<p>Cause unknown: no dated, sourced explanation has been attached. Price decline alone is not evidence of undervaluation.</p>'}
      <h3>FairEntry company research and valuation estimates</h3>${f?`${!model.fresh(f.as_of,data.financial_max_age_days,Date.now())?'<p class="investor-warning">This FairEntry board snapshot is stale. Its estimates and risks may no longer describe the company.</p>':''}<p>Official stock verdict: ${esc(f.verdict)} · Board snapshot ${esc(f.as_of)}. Separate from these adjustable research criteria.</p><p>Model fair-value range: ${money(f.valuation?.low)}–${money(f.valuation?.high)}; central estimate ${money(f.valuation?.base)}. Estimates are not facts or guaranteed targets.</p>${list(f.card_summary?.strongest_reasons||[])}<h4>Material company risks</h4>${list(f.card_summary?.largest_risks||[])}<p><a href="index.html?ticker=${encodeURIComponent(s.ticker)}">Open full FairEntry stock evaluation →</a></p>`:'<p>This company is outside the available FairEntry board or lacks a board evaluation. Business quality and competitive position require further research.</p>'}
      <h3>Financial facts and provenance</h3>${fieldRows.length?table(['Metric','Value','Source','Retrieved','Basis / link'],fieldRows):'<p>Financial evidence unavailable. This is not a negative finding about the company.</p>'}
      <h3>Disclosed position history</h3>${s.history?.length?table(['Source','Holdings date','Shares','Reported value','Published'],s.history.map(p=>[esc(sourceName(p.source_id)),esc(p.period),num(p.shares,0),money(p.reported_value),link(p.url,p.published_at)])):'<p>No comparable history available.</p>'}`;
    if(!$('investor-stock').open)$('investor-stock').showModal();
  }
  $('investor').addEventListener('change',()=>{page=0;renderSources();render();});
  $('source-cards').addEventListener('click',e=>{const b=e.target.closest('[data-source]');if(b){$('investor').value=b.dataset.source;page=0;renderSources();render();}});
  $('investor-filters').addEventListener('submit',e=>e.preventDefault());
  $('investor-filters').addEventListener('input',()=>{if(!$('investor-filters').checkValidity())return;page=0;try{localStorage.setItem('fairentry-investors-filters',JSON.stringify(filters()));}catch(_){}render();if($('investor-stock').open)$('investor-stock').close();});
  $('reset-investors').addEventListener('click',()=>{for(const [k,v]of Object.entries(data.defaults))$('investor-filters').elements[k].value=v;page=0;try{localStorage.removeItem('fairentry-investors-filters');}catch(_){}render();});
  for(const id of ['investor-search','research-status','holdings-period'])$(id).addEventListener('input',()=>{page=0;render();});
  document.querySelector('.investor-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-view]');if(!b)return;view=b.dataset.view;page=0;document.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));render();});
  $('investor-results').addEventListener('click',e=>{const b=e.target.closest('[data-detail]');if(b)showDetail(details[Number(b.dataset.detail)]);});
  $('investor-highlights').addEventListener('click',e=>{const b=e.target.closest('[data-detail]');if(b)showDetail(details[Number(b.dataset.detail)]);});
  $('investor-pages').addEventListener('click',e=>{const b=e.target.closest('[data-page]');if(b){page+=Number(b.dataset.page);render();}});
  $('close-investor-stock').addEventListener('click',()=>$('investor-stock').close());
  try {
    const response=await fetch('data/investors.json',{cache:'no-store'});
    if(!response.ok)throw new Error('Unavailable');
    data=await response.json();
    if(!Array.isArray(data.sources)||!Array.isArray(data.candidates))throw new Error('Invalid dataset');
    $('investor').innerHTML+=[...data.sources].map(s=>`<option value="${esc(s.id)}">${esc(s.person)} · ${esc(s.organization)}</option>`).join('');
    let saved={};try{saved=JSON.parse(localStorage.getItem('fairentry-investors-filters')||'{}');}catch(_){}
    for(const [k,v]of Object.entries(data.defaults)) {const el=$('investor-filters').elements[k];el.value=model.numeric(saved[k])?saved[k]:v;if(!el.checkValidity())el.value=v;}
    const query=new URLSearchParams(location.search);if(data.sources.some(s=>s.id===query.get('investor')))$('investor').value=query.get('investor');
    const coverage=data.sources.filter(s=>s.current_coverage).length;
    $('investors-health').innerHTML=`<p><strong>${data.candidates.length} research candidates · ${coverage} sources with recent, successfully checked holdings</strong></p><p>Snapshot ${esc(data.generated_at)} · Target source polling: every ${data.poll_minutes} minutes when scheduled processing is enabled.</p>${!model.fresh(data.generated_at,3,Date.now())?'<p class="investor-warning">Dashboard snapshot is stale; refresh before relying on source coverage.</p>':''}${coverage===0?'<p class="investor-warning">Current holdings coverage is unavailable. Research-only ideas remain available. Select a source to see its ingestion status.</p>':''}`;
    const email=data.email;
    $('investor-email').innerHTML=`<p>${email.enabled&&email.configured?'Configured; delivery results below.':'Inactive — enable INVESTORS_EMAIL_ENABLED and configure an explicit recipient plus Resend or SMTP.'}</p><p>Recipient configured: ${email.recipient_configured?'yes':'no'} · Provider: ${esc(email.transport||'not configured')}</p><p>${Object.entries(email.counts).map(([s,n])=>esc(s)+': '+n).join(' · ')||'No notification events recorded.'}</p>`;
    renderSources();render();
    if(query.get('ticker')){const stock=data.candidates.find(s=>s.ticker===query.get('ticker'));if(stock)showDetail(stock);}
  } catch(_) {$('investors-health').textContent='Investors data is unavailable. Run the Investors data build to check configured sources.';$('investor-results').textContent='No portfolio or ownership conclusions can be drawn from missing data.';}
}());
