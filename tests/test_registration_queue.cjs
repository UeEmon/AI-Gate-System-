const {test}=require('node:test');
const assert=require('node:assert/strict');
const {RegistrationQueue}=require('../static/registration-queue.js');
const row=(cursor,key,vehicle_type='car')=>({cursor,draft:{key,vehicle_type,observation_id:String(cursor)}});
test('continuous batches deduplicate numbers, preserve review selection and ignore repeated cursors',()=>{
  const queue=new RegistrationQueue();queue.accept([row(1,'a'),row(2,'b')]);
  queue.items.get('a').selected=true;
  queue.accept([row(2,'b'),row(3,'a'),{cursor:4,draft:null}]);
  assert.equal(queue.items.size,2);assert.equal(queue.items.get('a').reads,2);
  assert.equal(queue.selected().length,1);assert.equal(queue.skipped,1);assert.equal(queue.cursor,4);
});
test('conflicting vehicle types cannot be batch registered',()=>{
  const queue=new RegistrationQueue();queue.accept([row(1,'a')]);queue.items.get('a').selected=true;
  queue.accept([row(2,'a','truck')]);
  assert.equal(queue.items.get('a').conflict,true);assert.equal(queue.selected().length,0);
});
test('full queue pauses before advancing past the next unprocessed vehicle',()=>{
  const queue=new RegistrationQueue(2);
  assert.equal(queue.accept([row(1,'a'),row(2,'b'),row(3,'c')]),false);
  assert.equal(queue.cursor,2);queue.items.delete('a');
  assert.equal(queue.accept([row(3,'c')]),true);assert.equal(queue.items.has('c'),true);
});
