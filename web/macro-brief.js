/* Plain-language descriptions derived from the existing, disclosed rules. */
(function(root) {
  'use strict';
  const M=typeof module!=='undefined'&&module.exports?require('./macro-model.js'):root.MacroModel;
  const topics={
    gdp:['Economic growth','Jobs & growth','How quickly the economy is growing.'],
    unemployment:['People out of work','Jobs & growth','The share of the labor force looking for a job.'],
    claims:['New unemployment claims','Jobs & growth','New insurance claims per week, averaged over four weeks.'],
    inflation:['Price increases','Prices & rates','How much everyday prices rose over the past year.'],
    core_inflation:['Underlying inflation','Prices & rates','Price increases with food and energy taken out.'],
    policy:['Overnight borrowing rate','Prices & rates','The short-term rate that influences other borrowing costs.'],
    curve2:['Bond market signal · 2-year','Financial conditions','The gap between 10-year and 2-year Treasury yields.'],
    curve3:['Bond market signal · 3-month','Financial conditions','The gap between 10-year and 3-month Treasury yields.'],
    conditions:['Ease of financing','Financial conditions','How tight or loose financial conditions are.'],
    buffett:['Market size vs. the economy','Market valuations','The Buffett indicator: public equity value compared with GDP.'],
    cape:['Stock prices vs. long-term earnings','Market valuations','CAPE: stock prices compared with ten years of real earnings.']
  };
  const number=(v,d=1)=>M.numeric(v)?v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
  function reading(item,b,warning='') {
    const [title,group,definition]=topics[item.id]||[item.name,'Other',item.description];
    const a=M.assessment(item,b),v=item.value;
    let label=a.label,explanation=a.explanation,reference=b?`${number(b.q25)}–${number(b.q75)} ${item.unit} is the typical historical range.`:'Historical comparison unavailable.';
    if(warning||!M.numeric(v))return {title,group,definition,tone:'neutral',label:warning||'Data unavailable',explanation:'We do not have a fresh reading to judge current conditions.',reference,available:false};
    if(item.id==='gdp') {
      label=v<0?'Shrinking':v===0?'No growth':a.tone==='watch'?'Growing slowly':'Growing';
      explanation=v<0?'The economy produced less than in the previous quarter.':v===0?'Output was unchanged from the previous quarter.':a.tone==='watch'?'The economy is still expanding, but below its typical pace.':!b?'The economy is expanding; the historical comparison is incomplete.':'The economy is expanding at or above its lower historical growth range.';
      reference=`0% separates growth from contraction.${b?` Typical growth: ${number(b.q25)}–${number(b.q75)}%.`:''}`;
    } else if(['claims','unemployment'].includes(item.id)) {
      label=a.tone==='good'?'Low labor stress':a.tone==='risk'?'Elevated labor stress':a.tone==='neutral'?'Needs context':'Within usual range';
      explanation=item.id==='claims'?(a.tone==='good'?'Relatively few new unemployment claims compared with history.':a.tone==='risk'?'New unemployment claims are above the typical historical range.':'New unemployment claims are within the typical historical range.'):(a.tone==='risk'?'Unemployment is above its typical historical range.':a.tone==='good'?'Unemployment is low relative to this reference period.':'Unemployment is within its typical historical range.');
      if(!b)explanation='The latest reading is available, but its historical comparison is incomplete.';
      if(b)reference=`Typical range: ${number(b.q25,item.id==='claims'?0:1)}–${number(b.q75,item.id==='claims'?0:1)}${item.id==='claims'?' claims per week':'%'}.`;
    } else if(['inflation','core_inflation'].includes(item.id)) {
      label=v>2?'Above 2%':v<2?'Below 2%':'At 2%';
      explanation=v>2?`Prices are rising faster than the ${item.id==='inflation'?'Fed’s longer-term target':'2% reference used here'}.`:v<0?'Prices fell over the past year; falling prices can also signal weak demand.':v<2?'Prices are rising more slowly than the 2% reference.':'The annual inflation reading matches the 2% reference.';
      reference=`${number(Math.abs(v-2),2)} percentage points ${v>=2?'above':'below'} ${item.id==='inflation'?'the Fed’s 2% target':'2% (context, not a core-inflation target)'}.`;
    } else if(item.id==='policy') {
      label='Context matters';explanation='This rate affects borrowing costs. Its level alone cannot tell us whether policy is helping or hurting the economy.';
      reference=b?`Historical midpoint: ${number(b.median,2)}%. There is no fixed ideal rate.`:'There is no fixed ideal interest rate.';
    } else if(item.rule==='curve') {
      label=v<0?'Warning signal':v===0?'At the boundary':'Not inverted';
      explanation=v<0?'Short-term yields exceed long-term yields—a recession warning, not a countdown.':v===0?'Short- and long-term yields are equal. The curve is at its inversion boundary.':'This curve is not inverted. That does not rule out a recession.';
      reference=`${number(Math.abs(v),2)} percentage points ${v>=0?'above':'below'} the zero inversion boundary.`;
    } else if(item.id==='conditions') {
      label=v>0?'Financing is tighter':v<0?'Financing is easier':'Near the index average';
      explanation=v>0?'Financial conditions are tighter than the index’s historical average.':v===0?'Financial conditions match the index’s historical average.':'Financial conditions are looser than the index’s historical average. This can also encourage risk-taking.';
      reference=`Zero is the index’s historical average; latest ${number(v,2)}.`;
    } else if(['buffett','cape'].includes(item.id)) {
      label=a.tone==='risk'?'Expensive vs. history':a.tone==='good'?'Lower valuation':'Typical valuation';
      explanation=a.tone==='risk'?'Investors are paying more relative to the denominator than in most reference-period observations.':'Valuation is not elevated relative to the selected historical comparison.';
      if(!b){label='Needs context';explanation='Not enough history to make the reference comparison.';}
      if(b)reference=`Historical midpoint: ${number(b.median)}${item.unit}. High valuation does not tell us when prices will fall.`;
    }
    return {title,group,definition,tone:a.tone,label,explanation,reference,available:true};
  }
  function movement(item,years=2) {
    const t=M.trend(item,years);
    if(!t)return {tone:'neutral',label:'Trend unavailable',detail:'Not enough comparable history.',trend:null};
    if(Math.abs(t.change)<1e-8)return {tone:'neutral',label:'Unchanged',detail:`Same reading as ${years} years earlier.`,trend:t};
    let favorable=null;
    if(['unemployment','claims','conditions','buffett','cape'].includes(item.id))favorable=t.change<0;
    if(item.id==='gdp')favorable=t.change>0;
    if(['inflation','core_inflation'].includes(item.id)) {
      const gapChange=Math.abs(t.latest.value-2)-Math.abs(t.prior.value-2);
      if(Math.abs(gapChange)<1e-8)return {tone:'neutral',label:'Same target gap',detail:`The reading changed, but its distance from 2% is unchanged over ${years} years.`,trend:t};
      favorable=gapChange<0;
    }
    const label=['buffett','cape'].includes(item.id)?(favorable?'Less expensive':'More expensive'):favorable===null?(t.change>0?'Rising':'Falling'):favorable?'Improving':'Worsening';
    const delta=item.unit==='people'?`${M.numeric(t.relative)?number(Math.abs(t.relative),1)+'%':number(Math.abs(t.change),0)} ${t.change>0?'more':'fewer'} claims`: `${number(Math.abs(t.change),2)} ${item.unit==='index'?'index points':item.unit==='×'?'multiple points':'percentage points'} ${t.change>0?'higher':'lower'}`;
    return {tone:favorable===null?'neutral':favorable?'good':'risk',label,detail:`${delta} than ${years} years earlier.`,trend:t};
  }
  function overview(items,mode,warning) {
    const ids=['gdp','unemployment','inflation','conditions'];
    const pillars=ids.map(id=>{const item=items.find(i=>i.id===id);return item?{item,...reading(item,M.benchmark(item,mode),warning(item))}: {title:id,tone:'neutral',available:false};});
    const fresh=pillars.filter(p=>p.available),risks=fresh.filter(p=>p.tone==='risk').length;
    const status=fresh.length<4?'Incomplete picture':risks>=3?'Several signs of strain':fresh.every(p=>p.tone==='good')?'Mostly supportive readings':'Mixed conditions';
    const g=pillars[0],i=pillars[2];
    const headline=fresh.length<4?'Some readings need an update.':g.item.value>0&&i.item.value>2?'The economy is growing. Inflation is still a concern.':g.item.value<0?'The economy shrank in the latest quarter.':'The signals need to be read together.';
    const detail=fresh.length<4?'Some core indicators are missing or stale. Open the individual readings before judging current conditions.':
      i.item.value>2?`Inflation is ${number(i.item.value-2)} percentage points above the Fed’s 2% target.${pillars[1].tone!=='risk'&&pillars[3].tone!=='risk'?' Jobs and financing are not showing the same adverse readings.':''}`:
      g.item.value<0?'One negative quarter does not establish a recession. The jobs and financing readings help put it in context.':'Read growth, jobs, prices and financing together. No single indicator tells the whole story.';
    return {status,headline,detail,pillars,risks};
  }
  const api={topics,number,reading,movement,overview};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.MacroBrief=api;
}(typeof globalThis==='undefined'?this:globalThis));
