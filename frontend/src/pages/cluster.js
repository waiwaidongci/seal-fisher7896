import {store,load} from '../stores/research.js';
import {studies,clusterRuns} from '../api/research.js';
import {studySelect} from '../components/common/study-select.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {bindForm} from '../hooks/forms.js';
import {esc,fmt} from '../utils/format.js';

const CAP=160;

// 把逐件判定解析为 0/1 数组；只接受 0/1，按录入顺序即试封序号 1..n
function parseJudgments(text){
  const tokens=text.split(/[\s,，;；]+/).filter(Boolean);
  const bits=[];
  tokens.forEach((tok,i)=>{
    if(!/^[01]$/.test(tok))throw new Error(`第${i+1}个判定「${tok}」非法：只允许 0(正常) 或 1(漏封)`);
    bits.push(Number(tok));
  });
  return bits;
}

function pieceGrid(bits){
  if(!bits.length)return '<p class="method-note">尚未录入逐件判定。</p>';
  return `<div class="piece-grid" role="list" aria-label="逐件判定，红色为漏封">${bits.map((b,i)=>`<span class="piece ${b?'miss':'ok'}" title="序号 ${i+1} · ${b?'漏封':'正常'}">${i+1}</span>`).join('')}</div>`;
}

function resultPanel(r){
  if(!r||!r.id)
    return `<p class="method-note">未找到该扎堆计算，可从上方列表选择。</p>`;
  if(r.status==='failed')
    return `<div class="error">计算被拒绝 / 失败：${esc(r.error||'未知原因')}。请修正后基于当前版本重新开跑。</div>`;
  if(r.status!=='completed'||!r.result)
    return `<p class="method-note">${status(r.status)} 计数进度 ${r.progress_t||0}/${r.total_pieces||'—'} 件。计算中断会从已记下的计数进度续跑；未算完不输出概率，此页每2秒刷新。</p>`;
  const s=r.result.segment,p=r.result.probability;
  return `<section class="result-intro">
      <strong>漏封最扎堆连续段：序号 ${s.start}–${s.end}（共 ${s.length} 件）</strong>
      <p>该段实际漏封 ${s.segment_misses} 件，按全线漏封率 ${(r.result.line_miss_rate*100).toFixed(3)}% 预计 ${s.expected_misses.toFixed(4)} 件，实际比预计多 ${s.difference.toFixed(4)} 件（整数分子 ${s.difference_numerator}）。同分按起点最早、再取最短裁决。</p>
    </section>
    <h2>精确概率（固定漏封总数，全部摆法等权，达到该差值即计入，含并列）</h2>
    ${table(['项目','精确值'],[
      ['达标摆法数（精确）',r.result.arrangements_favorable],
      ['未达标摆法数（精确）',r.result.arrangements_below],
      ['全部摆法数 C(n,K)（精确）',r.result.arrangements_total],
      ['精确分数',`${p.numerator} / ${p.denominator}`],
      ['小数（仅供参考）',fmt(p.decimal)],
      ['输入版本 / SHA256',`第${r.line_version}版 · ${r.checksum.slice(0,16)}…`],
    ])}
    <p class="method-note">大整数超出浏览器安全范围，分子分母与摆法数均按字符串逐位精确显示；分数已约分。</p>`;
}

export async function render(){
  const writable=localStorage.getItem('research-role')!=='viewer';
  const line=await studies.sealLine(store.studyId).catch(()=>null);
  const bits=line?.judgments||[];
  const miss=bits.reduce((a,b)=>a+b,0);
  const runsHere=store.clusterRuns.filter(r=>r.study_id===store.studyId);
  return `<div class="eyebrow">02B / SEAL CLUSTER</div>
  <h1>产线漏封扎堆分析</h1>
  <p>按试封序号录入一条产线的逐件判定（1=漏封，0=正常），至多 ${CAP} 件，超容量拒绝。开跑只冻结当前同一版记录；之后的更正照常录入，不影响已排队的计算。</p>
  <div class="toolbar">${studySelect()}</div>

  ${writable?`<form id="line-form" class="worksheet">
    <label>逐件判定（按序号顺序，空格/逗号分隔 0 与 1）
      <textarea name="judgments" rows="3" placeholder="0 1 1 0 1 0 …" required>${esc(bits.join(' '))}</textarea>
    </label>
    <div class="inline-form">
      <label>当前记录版本（更正须带回，乐观锁）<input name="expected_version" type="number" min="0" step="1" value="${line?.version??0}" required></label>
      <label>更正说明（可选）<input name="note" maxlength="200" placeholder="如：第7件复验改判"></label>
    </div>
    <p class="capacity">已录入 <b>${bits.length}</b> / ${CAP} 件 · 漏封 <b>${miss}</b> 件 · 当前第 <b>${line?.version??0}</b> 版${bits.length>CAP?' · <span style="color:oklch(45% .16 25)">超容量将被拒绝</span>':''}</p>
    <button type="submit">${line&&line.version?'更正并升版保存':'保存逐件判定'}</button>
  </form>`:`<p class="method-note">观察员只读；分析师与负责人可录入和更正。</p>`}

  <h2>逐件判定 <small>序号即试封顺序</small></h2>
  ${pieceGrid(bits)}

  ${writable?`<form id="cluster-run-form" class="worksheet">
    <label>本次扎堆研究名称<input name="name" required minlength="2" value="产线扎堆核算 · ${esc(store.studies.find(s=>s.id===store.studyId)?.name||'')}"></label>
    <p>提交即冻结第 ${line?.version??0} 版的 ${bits.length} 件判定并生成 SHA256，后台精确计数（无抽样）。</p>
    <button type="submit" ${bits.length===0||bits.length>CAP?'disabled':''}>冻结同版记录并开跑 →</button>
  </form>`:''}

  <h2>扎堆计算记录</h2>
  ${table(['编号 / 名称','状态','输入版本','件数/漏封','计数进度','段序号范围','精确概率'],runsHere.map(r=>[
    {html:`<a href="#cluster/${r.id}">#${r.id} ${esc(r.name)}</a>`},
    {html:status(r.status)},
    '第'+r.line_version+'版',
    `${r.total_pieces} / ${r.miss_total}`,
    `${r.progress_t??0}/${r.total_pieces}`,
    r.result?`${r.result.segment.start}–${r.result.segment.end}`:(r.status==='failed'?'—':'待算'),
    r.result?`${r.result.probability.numerator}/${r.result.probability.denominator}`:(r.status==='failed'?esc(r.error):'未完成'),
  ]))}
  ${location.hash.startsWith('#cluster/')?resultPanel(store.clusterRuns.find(x=>x.id===Number(location.hash.split('/')[1]))||{}):''}`;
}

export function bind(refresh){
  const sel=document.getElementById('study-select');
  if(sel)sel.onchange=()=>{store.studyId=+sel.value;refresh();};
  bindForm('line-form',async data=>{
    const bits=parseJudgments(data.judgments);
    if(bits.length===0)throw new Error('至少录入1件判定');
    if(bits.length>CAP)throw new Error(`一条产线至多${CAP}件，当前${bits.length}件，超容量拒绝`);
    await studies.saveSealLine(store.studyId,{judgments:bits,expected_version:Number(data.expected_version),note:data.note||''});
    await load();refresh();
  });
  bindForm('cluster-run-form',async data=>{
    const r=await clusterRuns.create({study_id:store.studyId,name:data.name});
    await load();location.hash='cluster/'+r.id;refresh();
  });
}
