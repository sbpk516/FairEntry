const assert=require('node:assert/strict');
const {matches}=require('../web/criteria-filters.js');
const fields=[{id:'screen',type:'choice'},...Object.entries({rev_growth_qoq:10,gross_margin:25,pb_ratio:2,ps_ratio:2,pfcf_ratio:18,perf_year:0,debt_eq:2.5}).map(([metric,value])=>({id:'screen_'+metric,metric,type:'threshold',default:value})),{id:'price',type:'number'},{id:'verdict',type:'choice'},{id:'obv',type:'choice'}];
const cat={fields};
const stock=data=>({filter_values:data});
assert(matches(stock({price:100}),{},cat));
assert(!matches(stock({}),{price:{op:'min',value:0}},cat));
assert(matches(stock({}),{price:{op:'missing'}},cat));
assert(!matches(stock({price:0}),{price:{op:'missing'}},cat));
assert(matches(stock({price:100}),{price:{op:'max',value:100}},cat));
assert(matches(stock({rev_growth_qoq:10}),{screen:'quality_growth'},cat));
assert(!matches(stock({rev_growth_qoq:10,gross_margin:24}),{screen:'quality_growth'},cat));
assert(matches(stock({pb_ratio:1,ps_ratio:99,perf_year:-10}),{screen:'deep_value'},cat));
assert(!matches(stock({pfcf_ratio:-1,perf_year:-10}),{screen:'deep_value'},cat));
assert(!matches(stock({pb_ratio:1,perf_year:-10,debt_eq:3}),{screen:'deep_value'},cat));
assert(matches(stock({pb_ratio:1,perf_year:-10,debt_eq:3}),{screen:'deep_value',screen_debt_eq:4},cat));
assert(!matches(stock({}),{screen:'either'},cat));
const original=stock({verdict:'Watch',obv:'yes'});
assert(!matches(original,{verdict:'Buy'},cat));
assert(matches(original,{obv:'yes'},cat));
assert.equal(original.filter_values.verdict,'Watch');
console.log('Criteria filter logic: 14 checks passed');
const {presentationGroup}=require('../web/criteria-filters.js');
assert.equal(presentationGroup({id:'market_cap',group:'Universe'}),'Screening filters');
assert.equal(presentationGroup({id:'screen',group:'Candidate screens'}),'Screening filters');
for(const id of ['category_quality','category_survival','category_growth','price_to_fair','method_count','roic','obv','veto'])assert.equal(presentationGroup({id}),'Buy filters');
assert.equal(presentationGroup({id:'distance_sma_50week'}),'Optional technical filters');
assert.equal(presentationGroup({id:'factor_roe'}),'Advanced filters');
const source=require('node:fs').readFileSync(require('node:path').join(__dirname,'../web/criteria-filters.js'),'utf8');
assert(source.includes('<details class="criteria-panel">'));
assert(!source.includes('<details class="criteria-panel" open'));
assert(!source.includes('Any / off'));
assert(source.includes('Value unavailable'));
assert(source.includes('Value available'));
assert(source.includes('<section class="criteria-group criteria-primary"'));
console.log('Screening and Buy visibility checks passed');
// Regression: switching rating tabs must release both the Buy verdict and Buy gates.
const {mount}=require('../web/criteria-filters.js');
function fakeElement(){
  const children=new Map();
  return {dataset:{},classList:{toggle(){}},setAttribute(){},addEventListener(){},
    querySelector(key){if(!children.has(key))children.set(key,fakeElement());return children.get(key);},
    querySelectorAll(){return [];}};
}
global.document={activeElement:null};
const root=fakeElement();
const testCatalog={fields:[
  {id:'verdict',type:'choice',options:[['Buy','Buy'],['Watch','Watch'],['Avoid','Avoid']]},
  {id:'obv',type:'choice',options:[['yes','Confirmed'],['no','Not confirmed']]}
],presets:[{id:'all',label:'All published candidates',values:{}},
  {id:'buy',label:'Current Buy rules',values:{verdict:'Buy',obv:'yes'}}]};
let highlighted;
const controller=mount(root,testCatalog,()=>{},()=>{},v=>highlighted=v);
const sample=['Buy','Watch','Avoid'].map(verdict=>stock({verdict,obv:verdict==='Buy'?'yes':'no'}));
assert.equal(highlighted,'Buy');
assert.deepEqual(sample.filter(controller.matches).map(s=>s.filter_values.verdict),['Buy']);
controller.selectVerdict();
assert.equal(highlighted,'all');
assert.equal(root.querySelector('[data-preset]').value,'all');
assert.deepEqual(sample.filter(controller.matches).map(s=>s.filter_values.verdict),['Buy','Watch','Avoid']);
// Execute the actual dashboard click handler for Buy -> All -> Watch -> Avoid -> All.
const fs=require('node:fs'),vm=require('node:vm');
const index=fs.readFileSync(require('node:path').join(__dirname,'../web/index.html'),'utf8');
const handler=index.split("$('#vfilter').addEventListener('click',")[1].split("$('#wmafilter').addEventListener")[0].trim().replace(/\);$/,'');
let selected='all',rendered=[];
const context={CRITERIA_FILTERS:controller,$:()=>({classList:{remove(){}}}),
  renderBoard(){rendered=sample.filter(controller.matches).filter(s=>selected==='all'||s.filter_values.verdict===selected).map(s=>s.filter_values.verdict);}};
const click=vm.runInNewContext('('+handler+')',context);
for(const rating of ['Buy','all','Watch','Avoid','all']){
  const button={dataset:{v:rating},classList:{add(){selected=rating;}}};
  click({target:{closest:()=>button}});
  assert.deepEqual(rendered,rating==='all'?['Buy','Watch','Avoid']:[rating]);
}
console.log('Rating navigation regression checks passed');
