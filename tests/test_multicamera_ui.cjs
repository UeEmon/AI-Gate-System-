const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');

function controls(kind,activeIds,selected,maxConcurrent=4){
  const elements=new Map();
  const $=id=>{if(!elements.has(id))elements.set(id,{disabled:false});return elements.get(id);};
  const state={activeIds,maxConcurrent,selected,busy:false};
  const document={querySelector:()=>({value:kind})};
  const context=vm.createContext({$,state,document});
  const start=source.indexOf('function setControls()');
  const end=source.indexOf('for(const input',start);
  vm.runInContext(source.slice(start,end),context);
  context.setControls();
  return $;
}

test('another camera can start while selected camera can stop',()=>{
  const $=controls('camera',['camera-a'],'camera-a');
  assert.equal($('start').disabled,false);
  assert.equal($('stop').disabled,false);
  assert.equal($('delete-history').disabled,true);
});

test('file stays exclusive and camera limit disables start',()=>{
  assert.equal(controls('file',['camera-a'],'camera-a')('start').disabled,true);
  assert.equal(controls('camera',['a','b'],'a',2)('start').disabled,true);
  assert.equal(controls('camera',['a'],'completed',2)('stop').disabled,true);
});
