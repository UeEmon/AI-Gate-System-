'use strict';
const learningFields=['region','category','kana','serial'];
let learningImage=null, learningGeneration=0;
function resetLearning(){
  learningGeneration++;learningImage=null;
  $('learning-review').hidden=true;
  $('learning-confirm').checked=false;$('learning-with-registration').checked=false;
}
function fieldBox(name){return ['x1','y1','x2','y2'].map(part=>Number($('learning-'+name+'-'+part).value)/100);}
function drawLearning(){
  $('learning-confirm').checked=false;
  if(!learningImage)return;
  for(const name of learningFields){
    const [x1,y1,x2,y2]=fieldBox(name);
    const canvas=$('learning-'+name+'-image');
    canvas.width=Math.max(1,Math.round((x2-x1)*learningImage.width));
    canvas.height=Math.max(1,Math.round((y2-y1)*learningImage.height));
    if(x2>x1&&y2>y1&&x1>=0&&y1>=0&&x2<=1&&y2<=1)
      canvas.getContext('2d').drawImage(learningImage,Math.round(x1*learningImage.width),Math.round(y1*learningImage.height),
        Math.round(x2*learningImage.width)-Math.round(x1*learningImage.width),Math.round(y2*learningImage.height)-Math.round(y1*learningImage.height),0,0,canvas.width,canvas.height);
  }
}
function prepareLearning(savedFields=null){
  resetLearning();
  if(!registrationDraft?.has_image||!registrationDraft.plate_candidates.length)return;
  const candidate=registrationDraft.plate_candidates[Number($('registration-candidate').value)];
  const detected=candidate?.fields||{};
  const defaults={region:[0,0,.55,.45],category:[.55,0,1,.45],kana:[0,.45,.2,1],serial:[.2,.45,1,1]};
  for(const name of learningFields){
    const box=savedFields?.[name]?.box||defaults[name];
    ['x1','y1','x2','y2'].forEach((part,i)=>$('learning-'+name+'-'+part).value=String(box[i]*100));
    // Registration fields are also the learning answers. OCR values are the
    // initial before-correction data; saved reviews take precedence.
    $(name).value=savedFields?.[name]?.text??detected[name]??$(name).value;
  }
  const generation=learningGeneration;
  const img=new Image();
  img.onload=()=>{if(generation!==learningGeneration)return;learningImage=img;$('learning-review').hidden=false;$('learning-full-image').src=img.src;drawLearning();};
  img.onerror=()=>{if(generation===learningGeneration)message('この候補は学習画像として取得できません。画像保存設定と候補枠を確認してください。');};
  img.src='/api/ocr-learning/preview/'+encodeURIComponent(registrationDraft.observation_id)+'/'+Number($('registration-candidate').value);
}
function learningPayload(){
  if(!learningImage||!registrationDraft||!$('learning-confirm').checked)throw new Error('4項目それぞれの学習画像と正解を確認してください。');
  const fields={};
  for(const name of learningFields)fields[name]={text:$(name).value,box:fieldBox(name)};
  return {observation_id:registrationDraft.observation_id,candidate_index:Number($('registration-candidate').value),fields,confirmed:true};
}
for(const name of learningFields){
  for(const part of ['x1','y1','x2','y2'])$('learning-'+name+'-'+part).oninput=drawLearning;
  $(name).addEventListener('input',()=>{$('learning-confirm').checked=false;});
}
$('learning-save').onclick=async()=>{
  try{
    const payload={learning:learningPayload()};
    for(const id of ['region','category','kana','serial'])payload[id]=$(id).value;
    await api('/api/ocr-learning/samples',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    message('修正内容と画像を学習データとして保存しました。モデルへの反映には再学習が必要です。');
    await refreshLearning();
  }catch(error){message(error.message);}
};
async function activateLearning(id){
  try{await api('/api/ocr-learning/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});await refreshLearning();message('モデルを選択しました。認識処理を停止して再開すると反映されます。');}catch(error){message(error.message);}
}
let learningRefreshBusy=false;
async function refreshLearning(){
  if(learningRefreshBusy)return;
  learningRefreshBusy=true;
  try{
    const data=await api('/api/ocr-learning');
    $('learning-status').textContent='保存済み '+data.count+'件 / 使用モデル: '+(data.active||'標準OCR');
    $('learning-train').disabled=data.runs.some(r=>r.status==='running');
    $('learning-runs').replaceChildren();
    const names={running:'学習中',completed:'完了',failed:'失敗',interrupted:'中断'};
    for(const run of data.runs){
      const row=document.createElement('div');
      const label=document.createElement('p');
      label.textContent=date(run.created_at)+' · '+(names[run.status]||run.status)+(run.error?' · '+run.error:'');
      if(run.report){const r=run.report;if(r.backend==='fast-plate-ocr'){const c=r.candidate||{};label.textContent+=' · FastPlateOCR日本向け · 評価 '+(c.plates||0)+'枚 · CER '+((c.cer||0)*100).toFixed(1)+'% · 完全一致 '+((c.plate_accuracy||0)*100).toFixed(1)+'% · '+(r.eligible?'適用可能':'検証未達');}else if(r.baseline&&r.candidate){label.textContent+=' · 比較元: '+(r.baseline_model||'標準OCR')+' · 評価 '+r.validation_lines+'画像 · 文字誤り率 '+(r.baseline.cer*100).toFixed(1)+'% → '+(r.candidate.cer*100).toFixed(1)+'% · 項目・行一致率 '+(r.baseline.line_accuracy*100).toFixed(1)+'% → '+(r.candidate.line_accuracy*100).toFixed(1)+'% · '+(r.eligible?'適用可能':'改善基準に未達');}}
      row.append(label);
      if(run.report?.eligible){const button=document.createElement('button');button.textContent='このモデルを適用';button.disabled=data.active===run.id;button.onclick=()=>activateLearning(run.id);row.append(button);}
      $('learning-runs').append(row);
    }
    $('learning-samples').replaceChildren();
    for(const sample of data.samples){
      const row=document.createElement('div');const label=document.createElement('span');
      label.textContent=sample.top_text+' / '+sample.bottom_text+' （元のOCR: '+sample.original_text+'） ';
      const button=document.createElement('button');button.textContent='学習対象から削除';
      button.onclick=async()=>{try{await api('/api/ocr-learning/samples/'+sample.id,{method:'DELETE'});await refreshLearning();}catch(error){message(error.message);}};
      const edit=document.createElement('button');edit.textContent='正解・画像範囲を編集';
      edit.disabled=!sample.has_observation;
      if(!sample.has_observation)edit.title='元の認識履歴は削除されています。学習データ自体は保持されています。';
      edit.onclick=async()=>{
        await importRegistration(sample.observation_id);
        if(registrationDraft?.observation_id!==sample.observation_id)return;
        $('registration-candidate').value=String(sample.candidate_index);
        const values=sample.plate_key.split('|');learningFields.forEach((name,i)=>$(name).value=values[i]);
        prepareLearning(sample.fields);
      };
      row.append(label,edit,button);$('learning-samples').append(row);
    }
  }finally{learningRefreshBusy=false;}
}
$('learning-train').onclick=async()=>{
  $('learning-train').disabled=true;
  try{await api('/api/ocr-learning/train',{method:'POST'});message('再学習を開始しました。状態は自動更新されます。');}catch(error){message(error.message);}finally{await refreshLearning().catch(e=>message(e.message));}
};
$('learning-reset').onclick=()=>activateLearning(null);
$('learning-refresh').onclick=()=>refreshLearning().catch(e=>message(e.message));
refreshLearning().catch(e=>message(e.message));
setInterval(()=>refreshLearning().catch(()=>{}),10000);
