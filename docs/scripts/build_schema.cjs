// Documentation renderer only. No database connections or Django project generation.
// From the destination project root: node docs/scripts/build_schema.cjs
// Or from the documentation directory: node scripts/build_schema.cjs
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'MODEL_FIELD_DICTIONARY.md'), 'utf8');
const models = [];
let app, current;
for (const line of source.split(/\r?\n/)) {
  const a = line.match(/^## \d+\. (\w+)$/);
  const m = line.match(/^### (\d+)\. (\w+)$/);
  if (a) { app = a[1]; current = null; }
  if (m) { current = {number: +m[1], app, name:m[2], lines:[], fields:[]}; models.push(current); }
  else if (current && !line.startsWith('## Relationship')) current.lines.push(line);
}
assert.equal(models.length, 83);
const byName = Object.fromEntries(models.map(m => [m.name,m]));
const apps = [...new Set(models.map(m => m.app))];
function field(name,type,opts={}) { return {name,column:name,type,nullable:false,pk:false,unique:false,...opts}; }
const scalarOverrides = {
  'User.password':'varchar', 'User.last_login':'timestamptz', 'User.date_joined':'timestamptz',
  'BiometricDevice.authentication_secret_hash':'varchar', 'BiometricDevice.ip_address_last_seen':'inet',
  'BiometricTemplate.revocation_reason':'text', 'BiometricTemplate.quality_score':'integer',
  'DeviceMessage.source_ip':'inet', 'DeviceMessage.request_headers_snapshot':'jsonb',
  'PunchEvent.device_timezone':'varchar', 'PunchEvent.utc_offset_minutes':'integer',
  'PunchEvent.raw_record':'jsonb', 'AttendanceCorrection.proposed_event_at':'timestamptz',
  'AttendanceCorrection.proposed_direction':'varchar', 'AttendanceCorrection.resulting_calculation_version':'integer',
  'LeavePolicyTypeRule.carry_expiry_days':'integer', 'LeaveRequestSegment.requested_minutes':'integer',
  'PayrollPolicyVersion.paid_leave_treatment':'varchar', 'PayrollPolicyVersion.partial_leave_treatment':'varchar',
  'PayrollPolicyVersion.unpaid_leave_treatment':'varchar', 'PayrollPolicyVersion.money_rounding_mode':'varchar',
  'PayrollPolicyVersion.money_rounding_increment':'numeric(18,6)',
  'SalaryStructureComponent.rounding_mode':'varchar', 'SalaryStructureComponent.rounding_increment':'numeric(18,6)',
  'PayrollCompensationSegment.scheduled_minutes_per_day':'integer',
  'PayrollCompensationSegment.standard_minutes_in_period':'integer',
  'EmployeeLoan.terms_snapshot':'jsonb', 'EmployeeLoan.reason':'text',
  'Package.price':'numeric(18,2)', 'Package.currency':'varchar(3)',
  'AuditLog.object_public_id':'varchar', 'AuditLog.object_id':'varchar'
};
function scalarType(model,n,d) {
  if (scalarOverrides[model.name+'.'+n]) return scalarOverrides[model.name+'.'+n];
  if(n==='id') return 'bigint';
  if(n==='currency') return 'varchar(3)';
  if(n==='public_id') return 'uuid';
  if(n.endsWith('_at') || /timestamps|DateTimeField/.test(d)) return 'timestamptz';
  if(n==='revocation_reason') return 'text';
  if(/BooleanField|Django account flags/.test(d)) return 'boolean';
  if(/JSONField/.test(d)) return 'jsonb';
  if(/BinaryField/.test(d)) return 'bytea';
  if(/GenericIPAddressField/.test(d)) return 'inet';
  if(/DateField/.test(d)) return 'date';
  if(/TimeField/.test(d)) return 'time';
  if(/BigInteger/.test(d)) return 'bigint';
  if(/Integer|integers/.test(d) && !/Decimal/.test(d)) return 'integer';
  if(/Decimal/.test(d)) return /money|_amount$|_principal$|_outstanding$|_due$|^price$/.test(d+' '+n) ? 'numeric(18,2)' : 'numeric(18,6)';
  if(n==='weekday' || /^(template_size_bytes|resulting_calculation_version)$/.test(n)) return 'integer';
  if(/TextField|text fields/.test(d) && !/CharField/.test(d)) return 'text';
  if(/description|reason|_message$|_error$|^address$/.test(n)) return 'text';
  return 'varchar';
}
function parseField(model,n,d,index,names) {
  let target=null;
  const rel=d.match(/(?:FK|O2O)\s*->\s*([\w/]+)/);
  if(rel && !n.endsWith('_at')) {
    const targets=rel[1].split('/').filter(t=>t!=='DateTimeField');
    target = targets.length > 1 && targets.length===names.length ? targets[index] : targets[0];
    if(target==='self') target=model.name;
  }
  if(model.name==='BiometricTemplate' && n==='revoked_by') target='User';
  const nullable = /nullable|optional/i.test(d) || n==='effective_to' || n==='last_login';
  const result=field(n,target?'bigint':scalarType(model,n,d),{nullable,note:d});
  if(target) {
    assert(byName[target], 'Unresolved FK '+model.name+'.'+n+' -> '+target);
    result.column=n+'_id'; result.target=target;
    result.unique=/O2O/.test(d);
    result.deletion = /SET_NULL/.test(d) ? 'SET_NULL' : /CASCADE/.test(d) ? 'CASCADE':'PROTECT';
  }
  if(n==='id') {result.pk=true;result.nullable=false;}
  if(!target && /\bunique\b/.test(d) && !/non-unique/.test(d)) result.unique=true;
  return result;
}
const junctions=[];
for (const model of models) {
  model.table=model.app+'_'+model.name.toLowerCase();
  model.tenant=model.lines.some(l=>l.startsWith('Common fields: TenantOwned'));
  const commonActor=model.lines.some(l=>/^Common fields:.*(?:actor tracking|creator\/updater)/.test(l));
  model.constraints=model.lines.filter(l=>/^Constraints?:|^Relations:/.test(l)).join('\n');
  const put=f=>{const i=model.fields.findIndex(x=>x.name===f.name);if(i<0)model.fields.push(f);else model.fields[i]=f;};
  put(field('id','bigint',{pk:true,note:'Primary key; BigAutoField.'}));
  if(model.tenant) put(field('company','bigint',{column:'company_id',target:'Company',deletion:'PROTECT',note:'TenantOwned.company; mandatory tenant ownership.'}));
  if(model.tenant || model.lines.some(l=>/^Common fields: TimeStamped/.test(l))) {
    for(const n of ['created_at','updated_at']) put(field(n,'timestamptz',{note:'Inherited timestamp.'}));
  }
  if(commonActor) for(const n of ['created_by','updated_by']) put(field(n,'bigint',{column:n+'_id',target:'User',nullable:true,deletion:'SET_NULL',note:'Inherited ActorTracked field.'}));
  for(const line of model.lines) {
    const match=line.match(/^- (.+?) — (.+)$/);
    if(!match) continue;
    const names=[...match[1].matchAll(/`(\w+)`/g)].map(x=>x[1]);
    for(let i=0;i<names.length;i++) {
      const n=names[i],d=match[2];
      if(model.name==='User' && ['groups','user_permissions'].includes(n)) continue;
      if(/M2M ->/.test(d)) {
        const target=d.match(/M2M -> (\w+)/)[1];
        const jName=model.name+'_'+n;
        junctions.push({name:jName, app:model.app,number:null,table:model.table+'_'+n,tenant:false,junction:true,
          constraints:'Unique pair; both rows must belong to the same company. Tenant ownership is inherited from the source parent; this implicit junction has no company_id.',
          fields:[field('id','bigint',{pk:true}),field(model.name.toLowerCase(),'bigint',{column:model.name.toLowerCase()+'_id',target:model.name,deletion:'CASCADE'}),field(target.toLowerCase(),'bigint',{column:target.toLowerCase()+'_id',target,deletion:'CASCADE'})]});
      } else put(parseField(model,n,d,i,names));
    }
  }
  if(model.name==='PayrollSettings') model.fields.find(f=>f.name==='company').unique=true;
  assert.equal(new Set(model.fields.map(f=>f.column)).size,model.fields.length,'Duplicate fields in '+model.name);
}
assert.equal(junctions.length,5);
const tables=[...models,...junctions];
for(const j of junctions) byName[j.name]=j;
const edges=tables.flatMap(m=>m.fields.filter(f=>f.target).map(f=>({from:m.name,field:f.column,to:f.target,unique:f.unique,nullable:f.nullable,shared:f.name==='company'||['created_by','updated_by'].includes(f.name)})));
for(const m of models) {
  const expected=m.lines.filter(l=>/^- .+ — /.test(l)).flatMap(l=>[...l.split(' — ')[0].matchAll(/`(\w+)`/g)].map(x=>x[1]));
  for(const name of expected) assert(m.fields.some(f=>f.name===name)||junctions.some(j=>j.name===m.name+'_'+name)||m.name==='User'&&['groups','user_permissions'].includes(name),'Missing '+m.name+'.'+name);
}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dq=s=>'"'+String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/\r?\n/g,'\\n')+'"';
const write=(name,data)=>fs.writeFileSync(path.join(root,name),data,'utf8');
const palette=['#255f85','#7047a2','#167766','#ad5926','#8d4378','#5868a5','#226d85','#31805a','#a25063','#3f65a2','#8c671e','#656679'];

let dbml='// Proposed attendance schema. Documentation only; no database has been created.\n// All domain columns and FKs; advanced cross-row constraints are retained as notes.\n// Django auth/framework tables are intentionally external to this domain schema.\nProject attendance_management {\n  database_type: \'PostgreSQL\'\n  Note: \'83 domain models + 5 implicit M2M tables. Types and nullability are proposed; read FULL_DATABASE_SCHEMA.md for conventions.\'\n}\n\n';
for(const m of tables) {
  dbml+=`Table ${m.table} {\n`;
  for(const f of m.fields) {
    const settings=[f.pk?'pk':null,f.pk?'increment':null,f.nullable?'null':'not null',f.unique&&!f.pk?'unique':null,f.note?'note: '+dq(f.note):null].filter(Boolean);
    dbml+=`  ${f.column} ${f.type} [${settings.join(', ')}]\n`;
  }
  if(m.junction) dbml+=`  indexes {\n    (${m.fields[1].column}, ${m.fields[2].column}) [unique]\n  }\n`;
  dbml+='  Note: '+dq(m.name+' | '+(m.tenant?'Direct company ownership. ':m.junction?'Parent-owned junction. ':'Global/platform scope; AuditLog company is optional. ')+(m.constraints||''))+'\n}\n\n';
}
for(const e of edges) dbml+=`Ref: ${byName[e.from].table}.${e.field} ${e.unique?'-':'>'} ${byName[e.to].table}.id\n`;
for(const a of apps) dbml+=`\nTableGroup ${a} {\n${tables.filter(m=>m.app===a).map(m=>'  '+m.table).join('\n')}\n}\n`;
write('ATTENDANCE_SCHEMA.dbml',dbml);

let md=`# Complete attendance database schema — every field\n\nThis is the field-level schema for the proposed attendance project: **83 domain models, 5 implicit M2M junctions, ${tables.reduce((s,m)=>s+m.fields.length,0)} columns, and ${edges.length} foreign-key relationships**. No database or Django application is generated.\n\n- [Interactive diagram](ATTENDANCE_SCHEMA.html): search a table, zoom, and highlight its relationships.\n- [Full SVG diagram](ATTENDANCE_SCHEMA.svg): all table boxes contain all physical fields, types, key markers, and referenced targets.\n- [Editable DBML source](ATTENDANCE_SCHEMA.dbml): all fields and relationships for a compatible database diagram editor.\n- [Field dictionary](MODEL_FIELD_DICTIONARY.md): meaning, choice values, and detailed constraints.\n- [Database architecture](DATABASE_SCHEMA.md): business flows and tenant/index design.\n\n## Reading the schema\n\n- PK = primary key. FK = many-to-one foreign key. FK/UQ = one-to-one or unique foreign key. UQ = unique column. NULL = optional database value.\n- Django \`employee\` becomes the physical column \`employee_id\`. Vendor text fields already named \`device_user_id\` remain scalar text, not foreign keys.\n- Shared fields are expanded in every applicable table. M2M fields are represented by physical junction tables, not fictitious array columns.\n- The five implicit junctions inherit tenant ownership through their parents; they have no direct company column. Django User groups/permissions and framework infrastructure tables are outside this domain diagram.\n- PostgreSQL types are proposed mappings: text/choice CharFields use varchar unless a known width is specified; rates use numeric(18,6), monetary amounts numeric(18,2), JSONField uses jsonb, and encrypted BinaryField uses bytea. Optional text may be NULL in this proposal; blank-versus-NULL must be finalized consistently in the future Django models.\n- Ambiguous mixed descriptions were made explicit for display: actor fields ending in _by reference User, fields ending in _at are timestamps; attendance-session allocation links and ledger reversal links marked O2O/FK use a unique FK. Leave-treatment fields are choice strings, with percentages retained on the approved leave records. Open effective_to values are nullable.\n- DBML includes column PK/unique constraints, all FKs, and implicit-junction pair uniqueness. Other multi-column, conditional, range, ledger, hierarchy, and tenant consistency constraints remain explanatory notes; this file is not an executable or fully constrained migration.\n- Arrowheads run from each child FK to the referenced parent PK. Click a table to highlight connections. FK target labels remain readable even with connecting lines hidden.\n\n## App diagrams\n\n`;
md=md.replace('- [Database architecture]', '- [Device attendance policy](DEVICE_ATTENDANCE_POLICY.md): device scopes, employee/branch/company precedence, enrollment versus permission, historical decisions, and pairing across devices.\n- [Database architecture]');
for(const a of apps) md+=`- [${a} — all fields](schema_diagrams/${a}.svg)\n`;
for(const a of apps) {
  md+=`\n## ${a}\n\n`;
  for(const m of tables.filter(t=>t.app===a)) {
    md+=`### ${m.name}\n\nPhysical table: \`${m.table}\`. ${m.junction?'Implicit M2M junction.':m.tenant?'Direct tenant owner: `company_id`.':'Global/platform table; see company field if present.'}\n\n| Column | PostgreSQL type | Key | Nullable | References |\n|---|---|---|---|---|\n`;
    for(const f of m.fields) md+=`| \`${f.column}\` | \`${f.type}\` | ${f.pk?'PK':f.target?f.unique?'FK/UQ':'FK':f.unique?'UQ':'—'} | ${f.nullable?'Yes':'No'} | ${f.target?'`'+byName[f.target].table+'.id`':'—'} |\n`;
    if(m.constraints)md+='\n'+m.constraints.replace(/\n/g,'\n\n')+'\n';
  }
}
write('FULL_DATABASE_SCHEMA.md',md);

// Grid layout keeps table text separate from relation routes. All edges are retained.
function draw(selected,title,full) {
  const width=980,row=25,gap=120,margin=80,cols=full?4:Math.min(3,selected.length), positions={};
  let top=170;
  for(const a of apps) {
    const group=selected.filter(m=>m.app===a); if(!group.length)continue;
    top+=70;
    for(let i=0;i<group.length;i+=cols) {
      const batch=group.slice(i,i+cols); let maxH=0;
      batch.forEach((m,k)=>{const h=95+m.fields.length*row;positions[m.name]={x:margin+k*(width+gap),y:top,w:width,h,app:a};maxH=Math.max(maxH,h);});
      top+=maxH+100;
    }
  }
  const W=margin*2+cols*width+(cols-1)*gap,H=top+40;
  let svg=`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="diagram-title"><title id="diagram-title">${esc(title)}</title><desc>Complete table fields, types, and foreign key targets. Arrowheads point from child foreign keys to parent primary keys.</desc><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#7b8798"/></marker></defs><style>text{font-family:Consolas,monospace;fill:#23354a}.heading{font-family:Segoe UI,Arial,sans-serif}.edge{fill:none;stroke:#8b9aad;stroke-width:1.4;opacity:.18;marker-end:url(#arrow)}.edge.shared{opacity:.07}.edge.active{stroke:#db6c28;stroke-width:3;opacity:.9}.table .border{fill:white;stroke:#c8d2df;stroke-width:1.3}.table.selected .border{stroke:#dc712b;stroke-width:4}.table.dim{opacity:.22}.fieldtext{font-size:13px}.target{fill:#28648c}.nullable{fill:#7e8995}.table{cursor:pointer}</style><rect width="100%" height="100%" fill="#f3f6fa"/><text x="80" y="65" class="heading" font-size="34" font-weight="700">${esc(title)}</text><text x="80" y="104" class="heading" font-size="18">${selected.length} tables · PK / FK / UQ · ? = nullable · proposed PostgreSQL types · every inherited column expanded</text><text x="80" y="133" class="heading" font-size="16">FK labels name the parent table. Diagram arrows point to its primary key. Global Django auth infrastructure is excluded.</text><g id="relation-lines">`;
  const includedEdges=edges.filter(e=>positions[e.from]&&positions[e.to]);
  for(let i=0;i<includedEdges.length;i++) {
    const e=includedEdges[i],s=positions[e.from],t=positions[e.to];
    const fIndex=byName[e.from].fields.findIndex(f=>f.column===e.field),sy=s.y+94+fIndex*row,ty=t.y+94;
    const sx=s.x+s.w,tx=t.x+t.w,routeX=Math.max(sx,tx)+20+(i%13)*5;
    const d=e.from===e.to?`M${sx},${sy} H${routeX+10} V${ty} H${tx}`:`M${sx},${sy} H${routeX} V${ty} H${tx}`;
    svg+=`<path class="edge${e.shared?' shared':''}" data-from="${e.from}" data-to="${e.to}" data-field="${e.field}" d="${d}"><title>${esc(e.from+'.'+e.field+' → '+e.to+'.id'+(e.unique?' [unique FK]':''))}</title></path>`;
  }
  svg+='</g>';
  for(const m of selected) {
    const p=positions[m.name],color=palette[apps.indexOf(m.app)];
    svg+=`<g class="table" id="table-${m.name}" data-name="${m.name}" tabindex="0" aria-label="${esc(m.name+' table')}" transform="translate(${p.x},${p.y})"><rect class="border" rx="8" width="${p.w}" height="${p.h}"/><path d="M8 0 H${p.w-8} Q${p.w} 0 ${p.w} 8 V58 H0 V8 Q0 0 8 0" fill="${color}"/><text x="16" y="25" class="heading" style="fill:white" font-size="21" font-weight="600">${esc(m.name)}</text><text x="16" y="46" style="fill:#e7edf6" font-size="13">${esc(m.table)}</text><text x="470" y="76" font-size="11" fill="#7f8996">TYPE</text><text x="625" y="76" font-size="11">FOREIGN KEY TARGET / KEY</text>`;
    for(let i=0;i<m.fields.length;i++) {
      const f=m.fields[i],y=99+i*row, key=f.pk?'PK':f.target?f.unique?'FK/UQ':'FK':f.unique?'UQ':'';
      if(i%2===0)svg+=`<rect x="1" y="${y-15}" width="${p.w-2}" height="25" fill="#f5f8fb"/>`;
      svg+=`<g><title>${esc(f.note||f.column)}</title><text class="fieldtext" x="16" y="${y}">${esc(f.column)}${f.nullable?' ?':''}</text><text class="fieldtext nullable" x="470" y="${y}">${esc(f.type)}</text>`;
      if(f.target)svg+=`<a href="${positions[f.target]?'#table-':'../ATTENDANCE_SCHEMA.html#table-'}${f.target}"><text class="fieldtext target" x="625" y="${y}">${esc(key+' → '+f.target+'.id')}</text></a>`;
      else svg+=`<text class="fieldtext" x="625" y="${y}">${key}</text>`;
      svg+='</g>';
    }
    svg+='</g>';
  }
  svg+='</svg>';
  return {svg,positions,width:W,height:H};
}
fs.mkdirSync(path.join(root,'schema_diagrams'),{recursive:true});
const full=draw(tables,'Attendance management · complete database schema',true);
write('ATTENDANCE_SCHEMA.svg',full.svg);
for(const a of apps)write('schema_diagrams/'+a+'.svg',draw(tables.filter(t=>t.app===a),a+' · all table fields',false).svg);
const payload=JSON.stringify({positions:full.positions,edges,tableNames:tables.map(t=>t.name)}).replace(/</g,'\\u003c');
const html=`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Attendance database schema</title><style>*{box-sizing:border-box}body{margin:0;color:#23354a;background:#f3f6fa;font:14px system-ui,sans-serif}header{position:fixed;inset:0 0 auto;background:#fff;border-bottom:1px solid #d0d8e2;z-index:3;padding:14px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}h1{font-size:17px;margin:0 12px 0 0}input,select,button,a.download{padding:8px 10px;border:1px solid #becada;border-radius:5px;background:#fff;color:#234366;font:inherit}input{width:260px}button{cursor:pointer}#viewport{position:absolute;inset:108px 0 0;overflow:auto}#canvas{position:relative}#canvas>svg{transform-origin:top left;position:absolute;top:0;left:0}.hint{width:100%;font-size:12px;color:#586a7e}#status{font-size:12px;color:#60758c}.download{text-decoration:none}#canvas.focused .edge:not(.active){opacity:0!important}#canvas.hide-lines .edge{display:none}</style></head><body><header><h1>Attendance database · all fields</h1><input id="search" list="tables" placeholder="Find table, e.g. Employee" aria-label="Find table"><datalist id="tables">${tables.map(t=>`<option value="${t.name}"></option>`).join('')}</datalist><button id="go">Find</button><button id="minus" aria-label="Zoom out">−</button><button id="plus" aria-label="Zoom in">+</button><button id="fit">Fit width</button><button id="reset">Clear selection</button><label><input id="lines" type="checkbox" checked style="width:auto"> Relationship lines</label><a class="download" href="ATTENDANCE_SCHEMA.svg" download>SVG</a><a class="download" href="ATTENDANCE_SCHEMA.dbml" download>DBML</a><span id="status"></span><div class="hint">83 domain models + 5 junctions. Scroll to explore; Ctrl+wheel to zoom. Click a table to highlight connections. FK targets are clickable. All fields are visible; ? means nullable.</div></header><main id="viewport"><div id="canvas">${full.svg}</div></main><script>
const data=${payload};
const viewport=document.getElementById('viewport'),canvas=document.getElementById('canvas'),svg=canvas.querySelector('svg');
const header=document.querySelector('header');let scale=.55,selected=null;
function sizeHeader(){viewport.style.top=header.offsetHeight+'px';}
function zoom(next,anchorX=viewport.clientWidth/2,anchorY=viewport.clientHeight/2){const x=(viewport.scrollLeft+anchorX)/scale,y=(viewport.scrollTop+anchorY)/scale;scale=Math.max(.08,Math.min(2,next));canvas.style.width=(${full.width}*scale)+'px';canvas.style.height=(${full.height}*scale)+'px';svg.style.transform='scale('+scale+')';viewport.scrollLeft=x*scale-anchorX;viewport.scrollTop=y*scale-anchorY;document.getElementById('status').textContent=Math.round(scale*100)+'%'+(selected?' · '+selected:'');}
function select(name,focus){if(!data.positions[name])return;selected=name;canvas.classList.add('focused');const neighbors=new Set([name]);for(const e of data.edges){if(e.from===name)neighbors.add(e.to);if(e.to===name)neighbors.add(e.from);}svg.querySelectorAll('.table').forEach(t=>{t.classList.toggle('selected',t.dataset.name===name);t.classList.toggle('dim',!neighbors.has(t.dataset.name));});svg.querySelectorAll('.edge').forEach(e=>e.classList.toggle('active',e.dataset.from===name||e.dataset.to===name));if(focus){const p=data.positions[name];zoom(Math.min(1,(viewport.clientWidth-80)/p.w));viewport.scrollTo({left:Math.max(0,p.x*scale-40),top:Math.max(0,p.y*scale-30),behavior:'instant'});}document.getElementById('search').value=name;zoom(scale);}
document.getElementById('go').onclick=()=>{const q=document.getElementById('search').value.toLowerCase();const found=data.tableNames.find(n=>n.toLowerCase()===q)||data.tableNames.find(n=>n.toLowerCase().includes(q));if(found)select(found,true);};
document.getElementById('search').onkeydown=e=>{if(e.key==='Enter')document.getElementById('go').click();};
document.getElementById('plus').onclick=()=>zoom(scale*1.2);document.getElementById('minus').onclick=()=>zoom(scale/1.2);document.getElementById('fit').onclick=()=>zoom((viewport.clientWidth-20)/${full.width});
document.getElementById('reset').onclick=()=>{selected=null;canvas.classList.remove('focused');svg.querySelectorAll('.selected,.dim,.active').forEach(e=>e.classList.remove('selected','dim','active'));document.getElementById('search').value='';zoom(scale);};
document.getElementById('lines').onchange=e=>canvas.classList.toggle('hide-lines',!e.target.checked);
svg.addEventListener('click',e=>{const a=e.target.closest('a');if(a){e.preventDefault();select(a.getAttribute('href').replace('#table-',''),true);return;}const t=e.target.closest('.table');if(t)select(t.dataset.name,false);});
svg.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.matches('.table'))select(e.target.dataset.name,false);});
viewport.addEventListener('wheel',e=>{if(e.ctrlKey){e.preventDefault();const r=viewport.getBoundingClientRect();zoom(scale*(e.deltaY<0?1.1:1/1.1),e.clientX-r.left,e.clientY-r.top);}},{passive:false});
window.addEventListener('resize',sizeHeader);sizeHeader();zoom(.65,0,0);if(location.hash.startsWith('#table-'))select(decodeURIComponent(location.hash.slice(7)),true);
</script></body></html>`;
write('ATTENDANCE_SCHEMA.html',html);
write('schema_diagrams/SCHEMA_DATA.json',JSON.stringify({tables:tables.map(({lines,...m})=>m),edges},null,2));
console.log(JSON.stringify({models:models.length,junctions:junctions.length,apps:apps.length,columns:tables.reduce((s,t)=>s+t.fields.length,0),foreignKeys:edges.length,fullSvg:{width:full.width,height:full.height},outputs:['FULL_DATABASE_SCHEMA.md','ATTENDANCE_SCHEMA.dbml','ATTENDANCE_SCHEMA.svg','ATTENDANCE_SCHEMA.html','schema_diagrams/*.svg','schema_diagrams/SCHEMA_DATA.json']},null,2));
