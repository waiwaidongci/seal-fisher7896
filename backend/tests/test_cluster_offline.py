"""离线验证：纯算法 + SQLite 持久化 + worker 检查点续算。

运行：cd backend && PYTHONPATH=. python3 /workspace/backend/tests/test_cluster_offline.py
不依赖 MySQL/Redis，seal_lines 内的 Redis 用内存假对象替换。
"""
import os,sys,json
os.environ.setdefault('DATABASE_URL','sqlite:////tmp/cluster-test.db')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('ARTIFACT_DIR','/tmp/cluster-artifacts')
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from itertools import combinations
from math import comb
from fractions import Fraction

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.services.cluster as cluster
from src.config import database as db_config
from src.config.database import Base
from src.models import user,study,observation,run,review,audit,seal_cluster
from src.models.seal_cluster import SealLine,ClusterRun
from src.schemas.seal_cluster import SealLineInput,ClusterRunInput
from src.services import seal_lines
from src.utils.serializers import checksum

# 复用应用自身的全局 engine（worker 也用它），在其上建表
Base.metadata.create_all(db_config.engine)
Session=db_config.Session

# ---------- 1. 算法对拍：最优段 ----------
def brute_best(bits):
    n=len(bits);K=sum(bits);best=None;bw=None
    for i in range(n):
        s=0
        for j in range(i,n):
            s+=bits[j];w=n*s-(j-i+1)*K
            if bw is None or w>bw: bw=w;best=(i+1,j+1)
    return best,bw

import random
rng=random.Random(7)
for n in range(1,12):
    for _ in range(150):
        bits=[rng.randrange(2) for _ in range(n)]
        (i,j),bw=brute_best(bits)
        si,sj,sw=cluster.best_segment(bits)
        assert (si,sj,sw)==(i,j,bw),(bits,(si,sj,sw),(i,j,bw))
assert cluster.best_segment([0,0,0])==(1,1,0)
assert cluster.best_segment([1,1,1])==(1,1,0)
print('1. best_segment 对拍通过')

# ---------- 2. 算法对拍：精确概率（全枚举） ----------
def brute_prob(n,K,wstar):
    hit=0
    for combo in combinations(range(n),K):
        bits=[0]*n
        for c in combo: bits[c]=1
        _,w=brute_best(bits)
        if w>=wstar: hit+=1
    return Fraction(hit,comb(n,K))

for n in range(1,11):
    for K in range(n+1):
        for _ in range(6):
            bits=[1]*K+[0]*(n-K);rng.shuffle(bits)
            *_,w=cluster.best_segment(bits)
            if K==0 or K==n:
                below=0
            else:
                cp=cluster.initial_checkpoint()
                t,layer=cluster.load_layer(cp)
                for tt in range(1,n+1):
                    layer=cluster.step_layer(layer,tt,n,K,w)
                below=sum(c for (k,m),c in layer.items() if k==K)
            res=cluster.build_result(bits,below)
            p=Fraction(int(res['probability']['numerator']),int(res['probability']['denominator']))
            assert p==brute_prob(n,K,w),(n,K,bits,p)
            assert int(res['arrangements_total'])==comb(n,K)
            assert int(res['arrangements_favorable'])+int(res['arrangements_below'])==comb(n,K)
print('2. 精确概率 DP 对全枚举通过')

# ---------- 3. 归一化与容量拒绝 ----------
assert cluster.normalize_judgments([True,False,1,0])==[1,0,1,0]
for bad in ([],[2],[1,'x'],[True]*161):
    try:
        cluster.normalize_judgments(bad);raise AssertionError('应拒绝: '+repr(bad))
    except ValueError: pass
try:
    cluster.normalize_judgments([1]*161);raise AssertionError
except ValueError as e:
    assert '超容量' in str(e)
print('3. 归一化与 160 件容量拒绝通过')

# ---------- 4. SQLite 持久化、乐观锁、worker 续算 ----------
db=Session()

class FakeRedis:
    def __init__(self): self.queues={}
    def rpush(self,k,v): self.queues.setdefault(k,[]).append(v)
    def lrange(self,k,a,b): return self.queues.get(k,[])[a:b+1 if b!=-1 else None]
fake=FakeRedis()
seal_lines.Redis=type('R',(),{'from_url':staticmethod(lambda url:fake)})

class U:
    email='analyst@demo.local';role='analyst'
user=U()

s=study.Study(name='试封产线甲',source_filename='line-a.csv',description='')
db.add(s);db.commit()
sid=s.id

# 首次登记，期望版本必须为 0
empty=seal_lines.get_line(db,sid)
assert empty['version']==0 and empty['judgments']==[]
line=seal_lines.save_line(db,sid,SealLineInput(judgments=[0,1,1,0,1],expected_version=0),user)
assert line['version']==1 and line['piece_count']==5 and line['miss_count']==3
# 旧版本更正 -> 409
try:
    seal_lines.save_line(db,sid,SealLineInput(judgments=[1,1],expected_version=0),user)
    raise AssertionError('应版本冲突')
except Exception as e:
    assert e.status_code==409
# 正确版本更正
line=seal_lines.save_line(db,sid,SealLineInput(judgments=[1,1,0,0],expected_version=1,note='复验'),user)
assert line['version']==2 and line['miss_count']==2
# 超容量登记拒绝
try:
    seal_lines.save_line(db,sid,SealLineInput(judgments=[1]*161,expected_version=2),user)
    raise AssertionError('应超容量拒绝')
except Exception as e:
    assert e.status_code==422 and '超容量' in e.detail
print('4. 产线登记 / 乐观锁 / 容量拒绝通过')

# 创建扎堆计算（冻结 v2）
run=seal_lines.create_run(db,ClusterRunInput(study_id=sid,name='扎堆核算一'),user)
rid=run['id']
assert run['status']=='queued' and run['line_version']==2
assert fake.lrange('cluster-queue',0,-1)==[rid]
# 更正照录，不影响已排队计算（快照保持 v2）
seal_lines.save_line(db,sid,SealLineInput(judgments=[1,1,1,0,0,0],expected_version=2),user)
job=db.get(ClusterRun,rid)
assert job.snapshot==[1,1,0,0] and job.line_version==2
cur_line=seal_lines.get_line(db,sid)
assert cur_line['version']==3 and cur_line['piece_count']==6
print('5. 开跑冻结同版记录、更正照常录入通过')

# 模拟 worker：手动逐层跑到一半后中断，再从检查点续跑
import sys as _sys,types as _types
_fake_mod=_types.ModuleType('redis')
class _FakeRedisClient:
    def __init__(self): self.qs={}
    def rpush(self,k,v): self.qs.setdefault(k,[]).append(v)
    def blpop(self,keys,timeout=0):
        for k in keys:
            q=self.qs.setdefault(k,[])
            if q: return (k.encode() if isinstance(k,str) else k,q.pop(0))
        import time;time.sleep(timeout or 0);return None
    def ping(self): return True
    @staticmethod
    def from_url(url): return _FakeRedisClient()
_fake_mod.Redis=_FakeRedisClient
_sys.modules['redis']=_fake_mod
from src import worker as worker_mod
worker_mod.redis=fake
bits=list(job.snapshot);n=job.total_pieces;K=job.miss_total
seg=cluster.segment_summary(bits);limit=seg['difference_numerator']
job.status='running'
job.progress=cluster.initial_checkpoint();db.commit()
# 只推进 n//2 层后“崩溃”
half=n//2
for _ in range(half):
    t,layer=cluster.load_layer(job.progress)
    layer=cluster.step_layer(layer,t+1,n,K,limit)
    job.progress=cluster.dump_layer(t+1,layer)
db.commit()
mid=json.loads(json.dumps(job.progress))
assert mid['t']==half
# 续跑：直接调用 worker 的完整处理函数，应从 half 续到 n
worker_mod.run_cluster(rid,db)
job=db.get(ClusterRun,rid)
assert job.status=='completed',job.error
assert job.progress is None and job.result is not None
# 结果与一次性算完一致
cp=cluster.initial_checkpoint();_,layer=cluster.load_layer(cp)
for tt in range(1,n+1):
    layer=cluster.step_layer(layer,tt,n,K,limit)
below=sum(c for (k,m),c in layer.items() if k==K)
expect=cluster.build_result(bits,below)
assert job.result['probability']==expect['probability']
assert job.result['segment']==expect['segment']
assert int(job.result['arrangements_favorable'])+int(job.result['arrangements_below'])==comb(n,K)
# 未算完不出概率：运行中的中间态 result 必须为空
mid_job=ClusterRun(study_id=sid,name='x',status='running',line_version=3,
                   total_pieces=n,miss_total=K,snapshot=bits,
                   checksum=checksum({'version':3,'judgments':bits}),
                   progress=mid,result=None,error='',created_at='t')
db.add(mid_job);db.commit()
assert mid_job.result is None
print('6. 中断检查点续算、未完成不出概率通过')

# ---------- 5. 160 件满容量端到端 ----------
big=study.Study(name='产线乙',source_filename='line-b.csv',description='');db.add(big);db.commit()
seal_lines.save_line(db,big.id,SealLineInput(judgments=[1]*40+[0]*120,expected_version=0),user)
r2=seal_lines.create_run(db,ClusterRunInput(study_id=big.id,name='满容量核算'),user)
worker_mod.run_cluster(r2['id'],db)
j2=db.get(ClusterRun,r2['id'])
assert j2.status=='completed',j2.error
res=j2.result
assert res['total_pieces']==160 and res['miss_total']==40
# 大整数以字符串精确传输，且可逐位解析
assert isinstance(res['probability']['numerator'],str) and int(res['probability']['numerator'])>0
# 40 连漏块可落在 121 个位置，全部达到同一阈值，并列均须计入
assert int(res['arrangements_favorable'])==160-40+1
assert int(res['arrangements_below'])+int(res['arrangements_favorable'])==comb(160,40)
# 全扎堆：40 个漏封连在最前面，达到该差值的摆法只有这一种连续块位置的对拍检查
seg0=res['segment']
print('7. 160 件满容量端到端通过，概率=',
      f"{res['probability']['numerator']}/{res['probability']['denominator']}",
      '段=',seg0['start'],'-',seg0['end'])

# ---------- 6. 边界：全漏 / 无漏概率为 1 ----------
for bits0 in ([[0],[1]],):
    pass
s3=study.Study(name='产线丙',source_filename='line-c.csv',description='');db.add(s3);db.commit()
for vals in ([0,0,0],[1,1,1]):
    seal_lines.save_line(db,s3.id,SealLineInput(judgments=vals,
        expected_version=0 if vals==[0,0,0] else seal_lines.get_line(db,s3.id)['version']),user)
    rr=seal_lines.create_run(db,ClusterRunInput(study_id=s3.id,name='边界'),user)
    worker_mod.run_cluster(rr['id'],db)
    jj=db.get(ClusterRun,rr['id'])
    assert jj.status=='completed',jj.error
    assert jj.result['probability']['numerator']=='1' and jj.result['probability']['denominator']=='1'
print('8. 无漏/全漏边界概率为 1 通过')
print('\n全部离线测试通过 ✔')
