const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/ocr-learning.js'),'utf8');
function setup(){
  const elements=new Map();const images=[];
  const $=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,hidden:false,getContext(){return {drawImage(){}};}});return elements.get(id);};
  const context=vm.createContext({$,message(){},Image:function(){this.width=160;this.height=80;images.push(this);},
    registrationDraft:{observation_id:'source',has_image:true,plate_candidates:[{
      fields:{region:'品川',category:'330',kana:'さ',serial:'1234'}}]}});
  $('registration-candidate').value='0';
  vm.runInContext(source.slice(0,source.indexOf('for(const name of learningFields){\n  for(const part')),context);
  return {$,images,context};
}
test('requires displayed image and explicit confirmation; split change clears confirmation',()=>{
  const {$,images,context}=setup();context.prepareLearning();
  assert.equal($('region').value,'品川');
  assert.equal($('category').value,'330');
  assert.equal($('kana').value,'さ');
  assert.equal($('serial').value,'1234');
  assert.throws(()=>context.learningPayload(),/確認/);
  images[0].onload();$('learning-confirm').checked=true;
  const payload=context.learningPayload();assert.equal(payload.observation_id,'source');assert.equal(payload.candidate_index,0);assert.deepEqual(JSON.parse(JSON.stringify(payload.fields.category.box)),[.55,0,1,.45]);
  $('learning-category-x1').value='50';context.drawLearning();assert.throws(()=>context.learningPayload(),/確認/);
});
test('saved corrections override detected values',()=>{
  const {$,context}=setup();
  context.prepareLearning({region:{text:'横浜',box:[0,0,.6,.45]},
    category:{text:'500',box:[.6,0,1,.45]},kana:{text:'あ',box:[0,.45,.2,1]},
    serial:{text:'5678',box:[.2,.45,1,1]}});
  assert.equal($('region').value,'横浜');
  assert.equal($('category').value,'500');
  assert.equal($('kana').value,'あ');
  assert.equal($('serial').value,'5678');
});
test('late image cannot attach to a new candidate or cleared draft',()=>{
  const {$,images,context}=setup();context.prepareLearning();context.resetLearning();
  images[0].onload();assert.equal($('learning-review').hidden,true);
  $('learning-confirm').checked=true;assert.throws(()=>context.learningPayload(),/確認/);
});
