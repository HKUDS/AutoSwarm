import test from 'node:test';
import assert from 'node:assert/strict';
import { turns, combine, windowTurns } from '../renderer/streams.mjs';
import { processView } from '../renderer/stream-ui.mjs';
import { setLanguage } from '../renderer/i18n.mjs';
setLanguage('zh');  // These assertions read Desk's Chinese source text.
test('deltas merge, completion replaces, and tool IDs are scoped to turns',()=>{
  const result=turns([{kind:'tool',id:'x',title:'read',output:'a',status:'in_progress'},{kind:'tool',id:'x',output:'b',outputDelta:true},{kind:'tool',id:'x',output:'ab',status:'completed'},{kind:'message',id:'m',text:'partial'},{kind:'message',id:'m',text:'final',replace:true},{kind:'turn'},{kind:'tool',id:'x',title:'second'}]);
  assert.equal(result.length,2); assert.equal(result[0].items[0].output,'ab'); assert.equal(result[0].items[1].text,'final'); assert.equal(result[0].items[1].final,true); assert.equal(result[1].items[0].title,'second');
});
test('late OpenCode part metadata relabels a prior delta',()=>{
  const result=turns([{kind:'message',id:'x',text:'abc'},{kind:'thought',id:'x',text:'abcdef',replace:true}]);
  assert.equal(result[0].items.length,1);assert.equal(result[0].items[0].kind,'thought');assert.equal(result[0].items[0].text,'abcdef');
});
test('history pages deduplicate and truncation resets',()=>{
  const latest={before:100,size:300,events:[{seq:'100:0',kind:'message',text:'b'}]};
  const combined=combine(latest,{before:0,size:300,events:[{seq:'0:0',kind:'message',text:'a'}]},true);
  assert.equal(combine(combined,latest).events.length,2);
  assert.equal(combine(combined,{before:0,size:10,events:[]}).events.length,0);
});
test('multiple blocks on the same native line preserve numeric order',()=>{
  const events=Array.from({length:12},(_,i)=>({seq:`10:${i}`,kind:'message',text:String(i)}));
  const merged=combine({before:0,size:100,events:[]},{before:0,size:100,events});
  assert.deepEqual(merged.events.map(e=>e.text),events.map(e=>e.text));
});
test('process windows are bounded and every item remains reachable',()=>{
  const groups=[{items:Array.from({length:161},(_,i)=>({text:String(i)}))}];
  assert.equal(windowTurns(groups).flatMap(g=>g.items).length,80);
  assert.equal(windowTurns(groups,80).flatMap(g=>g.items)[0].text,'1');
  assert.equal(windowTurns(groups,160).flatMap(g=>g.items)[0].text,'0');
});
test('a returned call does not imply an invented protocol boundary or running state',()=>{
  const html=processView({id:'c',result:{outcome:'completed'}},{events:[{kind:'notice',seq:'0:0',raw:{unknown:true}}]});
  assert.match(html,/调用已结束/);assert.doesNotMatch(html,/进行中|本轮结束/);
});
