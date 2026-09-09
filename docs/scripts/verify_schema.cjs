// Optional documentation QA. Pass a directory containing the playwright package.
const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const {pathToFileURL}=require('url');
const root=path.resolve(__dirname,'..');
const {tables,edges}=JSON.parse(fs.readFileSync(path.join(root,'schema_diagrams','SCHEMA_DATA.json'),'utf8'));
const text=fs.readFileSync(path.join(root,'ATTENDANCE_SCHEMA.dbml'),'utf8');
assert.equal(tables.length,91);
assert.equal(new Set(tables.map(t=>t.table)).size,91);
assert.equal((text.match(/^Table /gm)||[]).length,91);
assert.equal((text.match(/^Ref:/gm)||[]).length,edges.length);
for(const t of tables){
  assert.equal(new Set(t.fields.map(f=>f.column)).size,t.fields.length);
  for(const f of t.fields.filter(f=>f.target))assert(tables.some(x=>x.name===f.target));
}
const schemaMD=fs.readFileSync(path.join(root,'FULL_DATABASE_SCHEMA.md'),'utf8');
assert.equal((schemaMD.match(/^\| `/gm)||[]).length,tables.reduce((n,t)=>n+t.fields.length,0));
for(const [model,column,type,nullable] of [
  ['CompanyAttendanceSettings','device_attendance_scope','varchar',false],
  ['Branch','device_attendance_scope_override','varchar',true],
  ['EmployeeAssignment','device_attendance_scope_override','varchar',true],
  ['DeviceEnrollment','assigned_device_authorized','boolean',false],
  ['PunchEvent','authorization_snapshot','jsonb',false]
]) {
  const f=tables.find(t=>t.name===model).fields.find(f=>f.column===column);
  assert(f,'Missing device policy field '+model+'.'+column);
  assert.equal(f.type,type);assert.equal(f.nullable,nullable);
}
assert(schemaMD.includes('DEVICE_ATTENDANCE_POLICY.md'));
if(!process.argv[2]){console.log('Structural schema checks passed.');process.exit(0);}
const {chromium}=require(path.join(process.argv[2],'playwright'));
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1550,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(pathToFileURL(path.join(root,'ATTENDANCE_SCHEMA.html')).href);
    assert.equal(await page.locator('svg .table').count(),91);
    assert.equal(await page.locator('svg .edge').count(),edges.length);
    await page.locator('#search').fill('Employee');await page.locator('#go').click();
    assert.equal(await page.locator('.table.selected').getAttribute('data-name'),'Employee');
    assert(await page.locator('.edge.active').count()>0);
    await page.screenshot({path:path.join(root,'schema_diagrams','viewer_preview.png')});
    const overflowing=await page.evaluate(()=>[...document.querySelectorAll('.table')].flatMap(t=>{
      const width=Number(t.querySelector('.border').getAttribute('width'));
      return [...t.querySelectorAll('text')].filter(el=>el.getBBox().x+el.getBBox().width>width-8).map(el=>({table:t.dataset.name,text:el.textContent}));
    }));
    assert.deepEqual(overflowing,[],'Text overflows table boxes');
    await page.locator('#lines').uncheck();assert(await page.locator('#canvas').evaluate(e=>e.classList.contains('hide-lines')));
    await page.locator('#lines').check();
    await page.locator('#search').fill('PayrollLine');await page.locator('#go').click();
    assert.equal(await page.locator('.table.selected').getAttribute('data-name'),'PayrollLine');
    await page.locator('#reset').click();assert.equal(await page.locator('.table.selected').count(),0);
    const svgFiles=fs.readdirSync(path.join(root,'schema_diagrams')).filter(f=>f.endsWith('.svg'));
    assert.equal(svgFiles.length,12);
    const xmlErrors=await page.evaluate(async files=>{
      const results=[];
      // Local file fetching is browser-policy dependent; the main SVG is already parsed in HTML.
      const doc=new DOMParser().parseFromString(document.querySelector('svg').outerHTML,'image/svg+xml');
      if(doc.querySelector('parsererror'))results.push(doc.querySelector('parsererror').textContent);
      return results;
    },svgFiles);
    assert.deepEqual(xmlErrors,[]);assert.deepEqual(errors,[]);
    console.log(JSON.stringify({tables:tables.length,fields:tables.reduce((n,t)=>n+t.fields.length,0),foreignKeys:edges.length,appDiagrams:svgFiles.length,browserErrors:errors,textOverflows:overflowing,searchAndSelection:'passed',preview:'schema_diagrams/viewer_preview.png'},null,2));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
