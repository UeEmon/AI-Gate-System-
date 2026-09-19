const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');

function setup(){
  const elements=new Map(),calls=[],messages=[];
  const $=id=>{if(!elements.has(id))elements.set(id,{disabled:false,textContent:'',replaceChildren(){},append(){}});return elements.get(id);};
  let reads=0;
  const api=async(url,options={})=>{
    calls.push([url,options]);
    if(url==='/api/alerts'){
      reads++;
      return {items:[],total:12,unread:reads===1?3:0,hidden:2,email_configured:false,s3_configured:false};
    }
    if(url==='/api/alerts/ack-all')return {changed:3};
    throw new Error('unexpected '+url);
  };
  const context=vm.createContext({$,api,message:value=>messages.push(value),vehicleNames:{},deliveryNames:{}});
  const start=source.indexOf('async function renderAlerts()');
  const end=source.indexOf('loadVehicles().catch',start);
  vm.runInContext(source.slice(start,end),context);
  return {context,$,calls,messages};
}

test('alert summary shows all unread and hidden counts',async()=>{
  const env=setup();await env.context.renderAlerts();
  assert.equal(env.$('unread-count').textContent,'3件 未確認');
  assert.equal(env.$('alert-hidden-count').textContent,'直近10件を表示・ほか2件は非表示');
  assert.equal(env.$('ack-all-alerts').disabled,false);
});

test('bulk acknowledge calls endpoint and refreshes the summary',async()=>{
  const env=setup();await env.context.renderAlerts();await env.$('ack-all-alerts').onclick();
  assert.deepEqual(env.calls.map(call=>call[0]),['/api/alerts','/api/alerts/ack-all','/api/alerts']);
  assert.equal(env.calls[1][1].method,'POST');
  assert.equal(env.$('unread-count').textContent,'0件 未確認');
  assert.equal(env.$('ack-all-alerts').disabled,true);
  assert.equal(env.messages.at(-1),'3件の通知を確認済みにしました。');
});
