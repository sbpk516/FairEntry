const assert = require('node:assert/strict');
const M = require('../web/macro-model.js');
const history=[];
for(let year=1990;year<=2026;year++)for(let month=1;month<=12;month++){
  if(year===2026&&month>7)break;
  history.push({date:`${year}-${String(month).padStart(2,'0')}-01`,value:200000+(year-2010)*1000+month*100});
}
const claims={id:'claims',frequency:'Monthly',unit:'people',history,value:history.at(-1).value};
const b=M.benchmark(claims);
assert.equal(b.start,'2016-01-01');assert.equal(b.end,'2025-12-31');assert.equal(b.n,120);
assert.equal(M.benchmark(claims,'prepandemic').start,'2010-01-01');
assert.equal(M.trend(claims,2).prior.date,'2024-07-01');
assert.equal(M.trend(claims,2).change,2000);
assert.ok(M.trend(claims,2).relative>0);
assert.equal(M.trend({...claims,history:history.slice(-12)},2),null);
assert.equal(M.benchmark({...claims,history:history.slice(-24)}),null);
assert.equal(M.quantile([1,2,3,4],.25),1.75);
assert.equal(M.yearsBefore('2024-02-29',1),'2023-02-28');
assert.equal(M.assessment({...claims,value:b.q25},b).tone,'good');
assert.equal(M.assessment({...claims,value:b.q75},b).tone,'watch');
assert.equal(M.assessment({...claims,value:b.q75+1},b).tone,'risk');
assert.equal(M.assessment({...claims,value:null},b).tone,'neutral');
assert.equal(M.assessment({...claims,id:'policy'},b).tone,'neutral');
assert.equal(M.assessment({...claims,id:'gdp',value:-.1},b).tone,'risk');
assert.equal(M.assessment({...claims,id:'curve2',rule:'curve',value:0},b).tone,'watch');
assert.equal(M.assessment({...claims,id:'conditions',value:-.1},b).tone,'good');
for(const id of ['buffett','cape']) assert.equal(M.assessment({...claims,id,value:b.q75+1},b).tone,'risk');
const inflation={id:'inflation',value:3};
assert.equal(M.assessment(inflation,{distance25:.25,distance75:.75}).tone,'risk');
assert.match(M.directionMeaning(inflation,{change:-2,prior:{value:1},latest:{value:-1}}),/Farther/);
assert.match(M.directionMeaning(claims,M.trend(claims,2)),/More labor stress/);
const absent=structuredClone(claims); absent.history=history.filter(p=>p.date<'2023-01-01'||p.date>'2024-08-01');
assert.equal(M.trend(absent,2),null); // don't bridge a long missing-data gap
console.log('Macro comparison tests passed: reference windows, quantiles, units, trend dates, missing history and all indicator color rules.');
