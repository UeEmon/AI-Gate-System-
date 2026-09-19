const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');

function setup(confirmed=true){
  const elements=new Map(),calls=[],messages=[];
  const $=id=>{if(!elements.has(id))elements.set(id,{checked:true,value:'',disabled:false});return elements.get(id);};
  const api=async(url,options={})=>{
    calls.push([url,options]);
    if(url==='/api/history/summary')return {processing:2,recognition:3,vehicles_retained:4,alerts_retained:5,learning_samples_retained:6};
    return {deleted:{processing:2,recognition:3},file_errors:[]};
  };
  const state={active:null,selected:'old',page:2,busy:false};
  const context=vm.createContext({$,api,state,confirm:()=>confirmed,message:x=>messages.push(x),
    refresh:async()=>{},setControls(){}});
  const start=source.indexOf('async function deleteSelectedHistory()');
  const end=source.indexOf('refresh();setInterval',start);
  vm.runInContext(source.slice(start,end),context);
  return {context,$,calls,messages,state};
}

test('bulk deletion confirms exact counts and sends selected scopes',async()=>{
  const env=setup();await env.context.deleteSelectedHistory();
  assert.equal(env.calls[0][0],'/api/history/summary');assert.equal(env.calls[1][0],'/api/history');
  assert.deepEqual(JSON.parse(env.calls[1][1].body),{scopes:['processing','recognition'],confirmation:'DELETE HISTORY'});
  assert.equal(env.state.selected,null);assert.equal(env.state.page,1);assert.match(env.messages.at(-1),/処理履歴 2件、認識履歴 3件/);
});

test('no selection and canceled confirmation do not delete',async()=>{
  const empty=setup();empty.$('delete-processing-history').checked=false;empty.$('delete-recognition-history').checked=false;
  await empty.context.deleteSelectedHistory();assert.equal(empty.calls.length,0);assert.match(empty.messages[0],/選択/);
  const canceled=setup(false);await canceled.context.deleteSelectedHistory();assert.equal(canceled.calls.length,1);
});
