const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
function setup(confirmed){
  const calls=[];let resets=0,loads=0;
  const ctx=vm.createContext({confirm:()=>confirmed,$:()=>({value:'v1'}),
    api:async(...args)=>calls.push(args),resetVehicle:()=>resets++,loadVehicles:async()=>loads++,message(){}});
  vm.runInContext(source.slice(source.indexOf('async function deleteVehicle(')),ctx);
  return {ctx,calls,get resets(){return resets;},get loads(){return loads;}};
}
test('cancel keeps registration; confirm deletes only selected vehicle and refreshes',async()=>{
  const canceled=setup(false);await canceled.ctx.deleteVehicle({id:'v1',plate:'品川 330 さ 1234'});assert.equal(canceled.calls.length,0);
  const confirmed=setup(true);await confirmed.ctx.deleteVehicle({id:'v1',plate:'品川 330 さ 1234'});
  assert.equal(confirmed.calls[0][0],'/api/vehicles/v1');assert.equal(confirmed.calls[0][1].method,'DELETE');
  assert.equal(confirmed.resets,1);assert.equal(confirmed.loads,1);
});
