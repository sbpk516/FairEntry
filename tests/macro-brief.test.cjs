const assert=require('node:assert/strict');
const B=require('../web/macro-brief.js');
const baseline={q25:2,q75:4,median:3,distance25:.3,distance75:.8};
function item(id,value,prior=value) {
  const h=[];
  for(let y=2016;y<=2026;y++)for(let m=1;m<=12;m++){
    if(y===2026&&m>8)break;
    h.push({date:`${y}-${String(m).padStart(2,'0')}-01`,value:y===2026?value:prior});
  }
  return {id,value,history:h,frequency:'Monthly',unit:id==='claims'?'people':'%',as_of:'2026-08-01'};
}
assert.equal(Object.keys(B.topics).length,11);
assert.equal(B.reading(item('gdp',0),baseline).label,'No growth');
assert.match(B.reading(item('gdp',0),baseline).explanation,/unchanged/);
assert.equal(B.reading(item('claims',1),baseline,'Stale observation').tone,'neutral');
assert.equal(B.reading(item('claims',null),baseline).available,false);
assert.match(B.reading(item('core_inflation',3),baseline).reference,/not a core-inflation target/);
assert.equal(B.reading(item('policy',5),baseline).label,'Context matters');
assert.match(B.reading(item('conditions',0),baseline).explanation,/match/);
assert.equal(B.movement(item('inflation',3,1)).label,'Same target gap');
assert.equal(B.movement(item('inflation',0,1)).label,'Worsening');
assert.equal(B.movement(item('claims',200,300)).label,'Improving');
assert.match(B.movement(item('claims',200,0)).detail,/200 more claims/);
assert.equal(B.movement(item('gdp',1,3)).label,'Worsening');
assert.equal(B.movement({...item('cape',40,30),unit:'×'}).label,'More expensive');
assert.equal(B.movement({...item('claims',200),history:[]}).label,'Trend unavailable');
const inputs=[item('gdp',1,3),item('unemployment',3,3),item('inflation',4,2),item('conditions',-1,-1)];
assert.equal(B.overview(inputs,'decade',()=> '').status,'Mixed conditions');
assert.match(B.overview(inputs,'decade',()=> '').headline,/Inflation is still a concern/);
assert.equal(B.overview(inputs,'decade',i=>i.id==='inflation'?'Stale':'').status,'Incomplete picture');
assert.equal(B.overview(inputs.slice(1),'decade',()=> '').status,'Incomplete picture');
const bad=[item('gdp',-1,3),item('unemployment',10,3),item('inflation',5,2),item('conditions',1,0)];
assert.equal(B.overview(bad,'decade',()=> '').status,'Several signs of strain');
assert.equal(B.overview([...inputs,item('cape',100,10),item('core_inflation',10,2)],'decade',()=> '').risks,1);
console.log('Macro briefing tests passed: plain-language boundaries, missing/stale evidence, target-gap direction, severity and no double-counted signals.');
