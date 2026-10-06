const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const section=script.slice(script.indexOf('let streamConfig=null;'),script.indexOf("$('stream-refresh').onclick"));
function setup(hostname,value='',publicHost=''){
  const elements={'stream-host':{value,addEventListener(event,handler){this.oninput=handler;}},'stream-rtmp-url':{},'stream-rtmps-url':{}};
  const context=vm.createContext({$:id=>elements[id]??(elements[id]={}),location:{hostname},
    api:async()=>({settings:{available:true,public_host:publicHost,rtmp_port:1935,rtmps_port:1936,path:'gate-test',rtmps_enabled:true},online:true,readers:0,tracks:[]})});
  vm.runInContext(section,context);
  vm.runInContext("streamConfig={available:true,rtmp_port:1935,rtmps_port:1936,path:'gate-test',rtmps_enabled:true};renderStreamUrls()",context);
  return {elements,context};
}
test('LAN hostname initializes both stream URLs consistently',()=>{
  const {elements}=setup('192.168.3.13');
  assert.equal(elements['stream-host'].value,'192.168.3.13');
  assert.equal(elements['stream-rtmp-url'].textContent,'rtmp://192.168.3.13:1935/gate-test');
  assert.equal(elements['stream-rtmps-url'].textContent,'rtmps://192.168.3.13:1936/gate-test');
});
test('loopback is never suggested as a camera destination',()=>{
  for(const host of ['localhost','127.0.0.1','[::1]','0.0.0.0']){
    const {elements}=setup(host);
    assert.equal(elements['stream-host'].value,'');
    assert.match(elements['stream-rtmp-url'].textContent,/<MacのLAN IP>/);
  }
});
test('manual destination wins and editing updates both URLs',()=>{
  const {elements,context}=setup('proxy.example.com','192.168.3.13');
  assert.equal(elements['stream-host'].value,'192.168.3.13');
  elements['stream-host'].value='192.168.3.20';
  context.renderStreamUrls();
  assert.match(elements['stream-rtmp-url'].textContent,/192\.168\.3\.20/);
  assert.match(elements['stream-rtmps-url'].textContent,/192\.168\.3\.20/);
});
test('Mac host from API overrides localhost and proxy, preserving manual edits',async()=>{
  const {elements,context}=setup('proxy.example.com','','192.168.3.13');
  await context.loadStreams();
  assert.equal(elements['stream-host'].value,'192.168.3.13');
  assert.match(elements['stream-rtmp-url'].textContent,/192\.168\.3\.13/);
  elements['stream-host'].value='192.168.3.20';
  elements['stream-host'].oninput();
  await context.loadStreams();
  assert.equal(elements['stream-host'].value,'192.168.3.20');
});
