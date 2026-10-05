'use strict';
async function loadSpeedExperiment(){
  const report=await api('/api/system/speed-experiment');
  const running=report.state==='running';
  document.getElementById('speed-start').disabled=running;
  document.getElementById('speed-cancel').disabled=!running;
  document.getElementById('speed-report').textContent=JSON.stringify(report,null,2);
  const body=document.getElementById('speed-results');
  body.replaceChildren();
  for(const result of report.results||[]){
    const row=document.createElement('tr');
    const accuracy=result.accuracy;
    for(const value of [result.label+(result.stage==='threads'?' / '+result.threads+' threads':''),
      result.state,result.ocr_calls??'—',result.fps?.toFixed(2)??'—',
      result.mean_ms?.toFixed(1)??'—',result.p95_ms?.toFixed(1)??'—',
      pct(accuracy?.continuity_detection_recall),pct(accuracy?.exact_plate_accuracy),
      result.accuracy_preserved==null?'未判定':result.accuracy_preserved?'維持':'低下']){
      row.appendChild(cell(value));
    }
    body.appendChild(row);
  }
}
const speedForm=document.getElementById('speed-experiment-form');
if(speedForm){
  speedForm.onsubmit=async event=>{
    event.preventDefault();
    const data=new FormData();
    const video=document.getElementById('speed-video').files[0];
    if(!video)return;
    data.append('video',video);
    const truth=document.getElementById('speed-truth').files[0];
    if(truth)data.append('truth',truth);
    data.append('limit',document.getElementById('speed-limit').value);
    data.append('repeats',document.getElementById('speed-repeats').value);
    document.getElementById('speed-start').disabled=true;
    try{
      await api('/api/system/speed-experiment',{method:'POST',body:data});
      await loadSpeedExperiment();
      message('段階比較を開始しました。CPU推論では長時間かかる場合があります。');
    }catch(error){
      document.getElementById('speed-start').disabled=false;
      message(error.message);
    }
  };
  document.getElementById('speed-cancel').onclick=async()=>{
    try{await api('/api/system/speed-experiment/cancel',{method:'POST'});await loadSpeedExperiment();}
    catch(error){message(error.message);}
  };
  setInterval(()=>{
    if(!document.getElementById('system-page').hidden)loadSpeedExperiment().catch(()=>{});
  },3000);
}
