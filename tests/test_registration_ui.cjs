// Exercise the real registration handlers with a small DOM stub; no camera hardware.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const handlers=script.slice(script.indexOf('let registrationDraft='),script.indexOf("$('vehicle-reset').onclick"));
function setup(api,extras={}){
  const elements=new Map();
  const $=id=>{
    if(!elements.has(id))elements.set(id,{value:'',hidden:false,checked:false,
      replaceChildren(...children){this.children=children;this.value=children[0]?.value??'';},
      removeAttribute(key){delete this[key];},scrollIntoView(){},focus(){},
      reset(){for(const name of ['region','category','kana','serial','vehicle-label'])$(name).value='';$('watch').checked=false;}});
    return elements.get(id);
  };
  const context=vm.createContext({$,api,message(){},date:x=>x,pct:x=>typeof x==='number'?Math.round(x*100)+'%':'—',
    Option:function(text,value){this.text=text;this.value=value;},...extras});
  vm.runInContext(handlers,context);
  return {context,$};
}
const draft={observation_id:'a',processed_at:'2026-09-19',frame_index:3,vehicle_type:'truck',confidence:.9,has_image:true,
  plate_candidates:[{text:'品川300あ1234',confidence:.85,fields:{region:'品川',category:'300',kana:'あ',serial:'1234'}},
    {text:'品川300あ5678',confidence:.5,fields:{region:'品川',category:'300',kana:'あ',serial:'5678'}}]};
test('import clears edit identity, fills fields, shows source and allows candidate selection',async()=>{
  const calls=[];const {context,$}=setup(async url=>{calls.push(url);return draft;});
  $('vehicle-id').value='existing';$('watch').checked=true;
  await context.importRegistration('a');
  assert.equal($('vehicle-id').value,'');assert.equal($('registered-type').value,'truck');
  assert.equal($('serial').value,'1234');assert.equal($('watch').checked,false);
  assert.equal($('registration-image').src,'/api/observations/a/image');
  assert.deepEqual(calls,['/api/observations/a/registration']);
  $('registration-candidate').value='1';context.chooseRegistrationCandidate();
  assert.equal($('serial').value,'5678');assert.match($('registration-confidence').textContent,/信頼度が低い/);
});
test('shows strong and review-only kei plate hints',async()=>{
  const {context,$}=setup(async()=>({...draft,vehicle_type:'kei',plate_candidates:[
    {...draft.plate_candidates[0],kei_strength:'strong'},
    {...draft.plate_candidates[1],kei_strength:'review'}]}));
  await context.importRegistration('kei');
  assert.equal($('registered-type').value,'kei');
  assert.match($('registration-confidence').textContent,/軽自動車プレートとして検出/);
  $('registration-candidate').value='1';context.chooseRegistrationCandidate();
  assert.match($('registration-confidence').textContent,/車種を確認/);
});
test('learning form initialization sees the selected OCR values',async()=>{
  let observed;
  const holder={};
  const {context,$}=setup(async()=>draft,{prepareLearning(){
    observed=['region','category','kana','serial'].map(id=>holder.$(id).value);
  }});
  holder.$=$;
  await context.importRegistration('a');
  assert.deepEqual(observed,['品川','300','あ','1234']);
  $('registration-candidate').value='1';context.chooseRegistrationCandidate();
  assert.deepEqual(observed,['品川','300','あ','5678']);
});
test('unreadable input clears old plate and remains editable',async()=>{
  const {context,$}=setup(async()=>({...draft,has_image:false,plate_candidates:[]}));
  $('serial').value='old';await context.importRegistration('unread');
  assert.equal($('serial').value,'');assert.equal($('registration-image').hidden,true);
  assert.equal($('registration-candidate').disabled,true);
  assert.match($('registration-confidence').textContent,/手入力/);
});
test('late response cannot overwrite a newer selected observation or a cleared draft',async()=>{
  const pending={};const {context,$}=setup(url=>new Promise(resolve=>pending[url]=resolve));
  const first=context.importRegistration('first');const second=context.importRegistration('second');
  pending['/api/observations/second/registration']({...draft,vehicle_type:'bus'});await second;
  pending['/api/observations/first/registration'](draft);await first;
  assert.equal($('registered-type').value,'bus');
  const third=context.importRegistration('third');context.resetVehicle();
  pending['/api/observations/third/registration'](draft);await third;
  assert.equal($('registration-review').hidden,true);assert.equal($('serial').value,'');
});
