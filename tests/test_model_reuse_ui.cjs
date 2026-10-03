const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/ocr-learning.js'),'utf8');
const handlers=source.slice(source.indexOf("if($('paddle-package'))"));
test('model download sends CSRF and saves the authenticated ZIP',async()=>{
  const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);};
  let sent,clicked=false,revoked=false;
  const context=vm.createContext({$,message(){},fetch:async(url,options)=>{sent={url,options};return {ok:true,blob:async()=>({type:'zip'})};},
    document:{querySelector(){return {content:'csrf-secret'};},createElement(){return {click(){clicked=true;}};}},
    URL:{createObjectURL(){return 'blob:private';},revokeObjectURL(){revoked=true;}},setTimeout(callback){callback();},api(){}});
  vm.runInContext(handlers,context);await $('paddle-package').onclick();
  assert.equal(sent.url,'/api/paddle-training/package');assert.equal(sent.options.headers['X-CSRF-Token'],'csrf-secret');
  assert.equal(clicked,true);assert.equal(revoked,true);
});
test('publishing disables duplicate clicks and reports the repository URL',async()=>{
  const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);};
  let message;
  const context=vm.createContext({$,message:text=>{message=text;},api:async(url,options)=>{assert.equal($('paddle-publish').disabled,true);assert.equal(options.method,'POST');return {url:'https://github.com/UeEmon/AI-Gate-JP-Models'};}});
  vm.runInContext(handlers,context);await $('paddle-publish').onclick();
  assert.equal($('paddle-publish').disabled,false);assert.match(message,/AI-Gate-JP-Models/);
});
