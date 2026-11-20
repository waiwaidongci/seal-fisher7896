"""HTTP 端到端：登录鉴权 + 漏封扎堆接口 + 原 Fisher 工艺比较不回归。

运行：cd backend && PYTHONPATH=. python3 tests/test_cluster_api.py
SQLite + 内存假 Redis，不依赖 MySQL/Redis 容器。
"""
import os,sys,types,time
os.environ.setdefault('DATABASE_URL','sqlite:////tmp/cluster-api.db')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('ARTIFACT_DIR','/tmp/cluster-api-artifacts')
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 在任何应用模块 import 前，用内存假对象替换 redis 驱动
class FakeRedisClient:
    def __init__(self): self.qs={};self.counters={}
    def rpush(self,k,v): self.qs.setdefault(k,[]).append(v)
    def blpop(self,keys,timeout=0):
        for k in keys:
            q=self.qs.setdefault(k,[])
            if q:return (k.encode() if isinstance(k,str) else k,q.pop(0))
        time.sleep(timeout or 0);return None
    def incr(self,k): self.counters[k]=self.counters.get(k,0)+1;return self.counters[k]
    def expire(self,k,sec): return True
    def ping(self): return True
    @staticmethod
    def from_url(url): return FakeRedisClient()
fake_mod=types.ModuleType('redis');fake_mod.Redis=FakeRedisClient
sys.modules['redis']=fake_mod

from fastapi.testclient import TestClient
from src.config.database import Base,engine,Session
from src.models import user,study,observation,run,review,audit,seal_cluster
from src.models.user import User
from src.models.study import Study
from src.utils.security import hash_password

if os.path.exists('/tmp/cluster-api.db'): os.remove('/tmp/cluster-api.db')
Base.metadata.create_all(engine)
with Session() as db:
    for role in ('admin','analyst','viewer'):
        db.add(User(email=role+'@demo.local',role=role,password_hash=hash_password('Research2026!')))
    db.add(Study(name='接口测试产线',source_filename='line.csv',description=''))
    db.commit();sid=db.scalar(__import__('sqlalchemy').select(Study).where(Study.name=='接口测试产线')).id

from src.main import app
from src import worker
client=TestClient(app)

def token(role):
    r=client.post('/auth/login',json={'email':role+'@demo.local','password':'Research2026!'})
    assert r.status_code==200,r.text
    return r.json()['token']
def auth(role): return {'Authorization':'Bearer '+token(role)}

# 1. 未登录拒绝
assert client.get(f'/studies/{sid}/seal-line').status_code==401

# 2. viewer 只读，写入被拒
r=client.put(f'/studies/{sid}/seal-line',headers=auth('viewer'),
             json={'judgments':[1,0,1],'expected_version':0})
assert r.status_code==403,r.text

# 3. analyst 首次登记
r=client.put(f'/studies/{sid}/seal-line',headers=auth('analyst'),
             json={'judgments':[1,1,0,0,1],'expected_version':0})
assert r.status_code==200,r.text
body=r.json();assert body['version']==1 and body['piece_count']==5 and body['miss_count']==3 and body['capacity']==160

# 4. 乐观锁：旧版本冲突
r=client.put(f'/studies/{sid}/seal-line',headers=auth('analyst'),
             json={'judgments':[1,1,1],'expected_version':0})
assert r.status_code==409 and '版本冲突' in r.json()['detail']

# 5. 非法判定与超容量拒绝（422，且带明确原因）
r=client.put(f'/studies/{sid}/seal-line',headers=auth('analyst'),
             json={'judgments':[1,2],'expected_version':1})
assert r.status_code==422 and '0(正常)或1(漏封)' in r.json()['detail']
r=client.put(f'/studies/{sid}/seal-line',headers=auth('analyst'),
             json={'judgments':[1]*161,'expected_version':1})
assert r.status_code==422 and '超容量' in r.json()['detail']

# 6. 正确更正到 v2
r=client.put(f'/studies/{sid}/seal-line',headers=auth('analyst'),
             json={'judgments':[1,1,0,0],'expected_version':1,'note':'复验改判'})
assert r.status_code==200 and r.json()['version']==2

# 7. 开跑（冻结 v2），viewer 不可发起
r=client.post('/cluster-runs',headers=auth('viewer'),json={'study_id':sid,'name':'扎堆接口核算'})
assert r.status_code==403
r=client.post('/cluster-runs',headers=auth('analyst'),json={'study_id':sid,'name':'扎堆接口核算'})
assert r.status_code==200,r.text
run_id=r.json()['id'];assert r.json()['line_version']==2 and r.json()['status']=='queued'
assert 'progress' not in r.json()  # 检查点层不下发

# 8. 更正照常录入到 v3，已排队计算仍冻结 v2
client.put(f'/studies/{sid}/seal-line',headers=auth('analyst'),
           json={'judgments':[1,1,1,0,0,0],'expected_version':2})
r=client.get(f'/cluster-runs/{run_id}',headers=auth('viewer'))
assert r.json()['snapshot']==[1,1,0,0] and r.json()['line_version']==2

# 9. worker 实算（精确），viewer 可读取结果
worker.run_cluster(run_id,Session())
r=client.get(f'/cluster-runs/{run_id}',headers=auth('viewer'))
job=r.json();assert job['status']=='completed',job.get('error')
res=job['result'];seg=res['segment']
assert seg['start']==1 and seg['end']==2  # [1,1,0,0] 最扎堆段为前两件
# 概率字段为精确字符串
assert isinstance(res['probability']['numerator'],str) and isinstance(res['arrangements_total'],str)
assert res['tie_included'] is True
print('扎堆接口链路通过：段',seg['start'],'-',seg['end'],
      '精确分数',res['probability']['numerator']+'/'+res['probability']['denominator'])

# 10. 列表接口带 study 过滤
r=client.get('/cluster-runs?study_id='+str(sid),headers=auth('admin'))
assert any(x['id']==run_id for x in r.json())

# 11. 原 Fisher 工艺比较不回归：录入 A/B 两组并跑 analysis 队列
client.post(f'/studies/{sid}/observations',headers=auth('analyst'),
            json={'source_key':'G-A','values':{'group':'A','success':87,'failure':13}})
client.post(f'/studies/{sid}/observations',headers=auth('analyst'),
            json={'source_key':'G-B','values':{'group':'B','success':96,'failure':4}})
r=client.post('/runs',headers=auth('analyst'),json={'study_id':sid,'name':'Fisher回归','parameters':{'alpha':0.05}})
assert r.status_code==200,r.text
fid=r.json()['id']
worker.run_analysis(fid,Session())
r=client.get(f'/runs/{fid}',headers=auth('analyst'))
fr=r.json();assert fr['status']=='completed',fr.get('error')
assert 'Fisher双侧p' in fr['result']['metrics']
print('原 Fisher 工艺比较回归通过：p =',fr['result']['metrics']['Fisher双侧p'])

# 12. 满容量 160 件端到端 + 拒绝原因展示路径
with Session() as db:
    db.add(Study(name='满容量产线',source_filename='big.csv',description=''));db.commit()
    sid2=db.scalar(__import__('sqlalchemy').select(Study).where(Study.name=='满容量产线')).id
r=client.put(f'/studies/{sid2}/seal-line',headers=auth('admin'),
             json={'judgments':[1]*40+[0]*120,'expected_version':0})
assert r.status_code==200 and r.json()['piece_count']==160
r=client.post('/cluster-runs',headers=auth('admin'),json={'study_id':sid2,'name':'满容量'})
cid=r.json()['id'];worker.run_cluster(cid,Session())
r=client.get(f'/cluster-runs/{cid}',headers=auth('admin'))
assert r.json()['status']=='completed'
assert int(r.json()['result']['arrangements_favorable'])==121  # 并列全计入
print('160 件满容量端到端通过')
print('\n全部 HTTP 接口测试通过 ✔')
