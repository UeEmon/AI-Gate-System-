'use strict';
const learningFields=['region','category','kana','serial'];
let learningImage=null, learningGeneration=0;
function resetLearning(){
  learningGeneration++;learningImage=null;
  $('learning-review').hidden=true;
  $('learning-confirm').checked=false;
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
function learningPayload(requireConfirmation=true){
  if(!learningImage||!registrationDraft)throw new Error('学習用画像を読み込めません。画像の保存と候補の範囲を確認してください。');
  if(requireConfirmation&&!$('learning-confirm').checked)throw new Error('4項目それぞれの学習画像と正解を確認してください。');
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
    message('確認したナンバーと画像を保存しました。');
    await refreshLearning();
  }catch(error){message(error.message);}
};
let learningRefreshBusy=false;
async function refreshLearning(){
  if(learningRefreshBusy)return;
  learningRefreshBusy=true;
  try{
    const data=await api('/api/ocr-learning');
    $('learning-status').textContent='保存済み '+data.count+'件 / 運用方式: Lipla-jp';
    const training=await api('/api/paddle-training');
    $('paddle-training-status').textContent='PaddleOCR追加学習: '+({idle:'待機',running:'学習中',completed:'完了（未適用）',failed:'失敗',interrupted:'中断',insufficient_data:'データ不足',unavailable:'学習環境未準備'}[training.state]||training.state)+(training.error?' / '+training.error:'');
    $('paddle-retry').hidden=training.state!=='failed'||!!training.retry_queued;
    const stage={detector:'プレート検出器',recognizer:'PaddleOCR',validation:'評価',export:'推論形式への変換'}[training.stage]||training.stage;
    const counts=training.report||{};
    $('paddle-training-details').textContent=[stage?'工程: '+stage:'',counts.det_train!==undefined?'検出器 学習 '+counts.det_train+'件 / 評価 '+counts.det_val+'件':'',training.exit_code!==undefined?'終了コード: '+training.exit_code:''].filter(Boolean).join(' / ');
    $('paddle-training-log').textContent=training.recent_log||'ログはまだありません。';
    const comparison=training.comparison||{state:'idle'};
    $('paddle-compare').disabled=training.state!=='completed'||comparison.state==='running';
    $('paddle-compare-status').textContent='同一画像での比較: '+({idle:'未実施',running:'実行中',completed:'完了',failed:'失敗',interrupted:'中断'}[comparison.state]||comparison.state)+(comparison.error?' / '+comparison.error:'');
    const target=$('paddle-comparison');target.replaceChildren();
    const report=comparison.report;
    if(report&&comparison.dataset===training.dataset){
      const caption=document.createElement('p');caption.textContent='手動確認済み '+report.evaluated+'件（除外 '+report.skipped+'件） / IoU 0.50 / 車両画像からの処理時間';target.append(caption);
      const table=document.createElement('table');
      const head=document.createElement('tr');
      for(const title of ['方式','検出率','ナンバー完全一致率','平均遅延 (ms)','95%遅延 (ms)','処理速度 (FPS)']){const cell=document.createElement('th');cell.textContent=title;head.append(cell);}table.append(head);
      for(const [title,key] of [['Lipla-jp','lipla'],['学習済みPaddleOCR','paddle']]){
        const metrics=report[key];if(!metrics)continue;
        const row=document.createElement('tr');
        for(const value of [title,(metrics.plate_recall_at_iou_50*100).toFixed(1)+'%',(metrics.exact_plate_accuracy*100).toFixed(1)+'%',metrics.average_latency_ms.toFixed(1),metrics.p95_latency_ms.toFixed(1),metrics.throughput_fps.toFixed(1)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}table.append(row);
      }target.append(table);
      const paired=document.createElement('p');paired.textContent='両方正解 '+report.paired.both_correct+'件 / PaddleOCRのみ正解 '+report.paired.paddle_only+'件 / Lipla-jpのみ正解 '+report.paired.lipla_only+'件';target.append(paired);
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
$('learning-refresh').onclick=()=>refreshLearning().catch(e=>message(e.message));
$('paddle-retry').onclick=async()=>{try{await api('/api/paddle-training/retry',{method:'POST'});$('paddle-retry').hidden=true;message('再学習を予約しました。約30秒後に状態を確認してください。');}catch(e){message(e.message);}};
$('paddle-compare').onclick=async()=>{try{await api('/api/paddle-training/compare',{method:'POST'});message('比較を予約しました。');await refreshLearning();}catch(e){message(e.message);}};
refreshLearning().catch(e=>message(e.message));
setInterval(()=>refreshLearning().catch(()=>{}),10000);
