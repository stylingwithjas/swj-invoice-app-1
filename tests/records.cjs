const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const c={};vm.createContext(c);vm.runInContext(fs.readFileSync('assets/records.js','utf8'),c);
const rows=Array.from({length:1201},(_,id)=>({id,amount:1}));
(async()=>{
 let offsets=[];
 const factory=()=>({range:async(start,end)=>{offsets.push(start);return{data:rows.slice(start,Math.min(end+1,start+127)),count:rows.length};}});
 const result=await c.SWJRecords.all(factory,{key:'id'});assert.equal(result.complete,true);assert.equal(result.data.length,1201);assert.equal(result.data.reduce((s,r)=>s+r.amount,0),1201);assert.deepEqual(offsets.slice(0,3),[0,127,254]);
 for(const response of [{data:rows.slice(0,5),count:null},{data:[],count:10},{data:[{id:1},{id:1}],count:2},{data:[{}],count:1}]){
  const r=await c.SWJRecords.all(()=>({range:async()=>response}),{key:'id'});assert.equal(r.complete,false);assert.equal(r.data,null);
 }
 let page=0;let r=await c.SWJRecords.all(()=>({range:async()=>++page===1?{data:[{id:1}],count:2}:{data:[{id:2}],count:3}}),{key:'id'});assert.equal(r.data,null);
 page=0;r=await c.SWJRecords.all(()=>({range:async()=>++page===1?{data:[{id:1}],count:2}:{error:{message:'fixture page failure'}}}),{key:'id'});assert.equal(r.data,null);assert.match(r.error.message,/fixture/);
 r=await c.SWJRecords.all(factory,{key:'id',maxPages:1});assert.equal(r.data,null);assert.match(r.error.message,/safety/);
 r=await c.SWJRecords.all(()=>({range:async()=>({data:[],count:0})}),{key:'id'});assert.equal(r.complete,true);assert.equal(r.data.length,0);
 console.log('PASS pagination: 1,201 rows despite server cap, exact counts, missing/changed counts, duplicate keys, partial-page failures and safety bound');
})().catch(error=>{console.error(error);process.exitCode=1;});
