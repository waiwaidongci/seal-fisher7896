import os,json,logging
from pathlib import Path
from redis import Redis
from sqlalchemy import select
from src.config.database import Session
from src.models.run import Run
from src.models.seal_cluster import ClusterRun
from src.models import user,study,observation,review,audit
from src.services.algorithm import calculate
from src.services import cluster
from src.utils.serializers import checksum
redis=Redis.from_url(os.environ['REDIS_URL'])
ARTIFACT_DIR=Path(os.environ.get('ARTIFACT_DIR','/app/artifacts'))
ARTIFACT_DIR.mkdir(parents=True,exist_ok=True)
ANALYSIS_QUEUE='analysis-queue'
CLUSTER_QUEUE='cluster-queue'

def recover():
    # Recover jobs interrupted after dequeue. A project runs exactly one worker.
    with Session() as db:
        pending=list(db.scalars(select(Run).where(Run.status.in_(['queued','running']))))
        for r in pending: redis.rpush(ANALYSIS_QUEUE,r.id)
        pending_cluster=list(db.scalars(select(ClusterRun).where(ClusterRun.status.in_(['queued','running']))))
        for job in pending_cluster: redis.rpush(CLUSTER_QUEUE,job.id)

def run_analysis(id,db):
    r=db.get(Run,id)
    if not r or r.status in ('completed','failed'): return
    r.status='running';db.commit()
    try:
        if checksum(r.snapshot)!=r.checksum: raise ValueError('输入快照校验失败')
        result=calculate(r.snapshot,r.parameters)
        result['input_sha256']=r.checksum
        r.result=result;r.status='completed'
        Path(ARTIFACT_DIR/f'run-{id}.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
    except Exception as e:
        logging.exception('analysis failed');r.status='failed';r.error=str(e)
    db.commit()

def run_cluster(id,db):
    """漏封扎堆精确计数；每层提交一次检查点，中断后从已记下的进度续跑。"""
    job=db.get(ClusterRun,id)
    if not job or job.status in ('completed','failed'): return
    if checksum({'version':job.line_version,'judgments':job.snapshot})!=job.checksum:
        job.status='failed';job.error='输入快照校验失败';db.commit();return
    bits=list(job.snapshot);n=job.total_pieces;K=job.miss_total
    # 最优段与阈值在冻结快照上一次性确定，与计数进度无关
    seg=cluster.segment_summary(bits)
    limit=seg['difference_numerator']
    job.status='running'
    if job.progress is None:
        checkpoint=cluster.initial_checkpoint()
        job.progress=checkpoint
        db.commit()
    # K 为 0 或 n 时摆法唯一，无需逐层 DP；未达阈值摆法数为 0
    trivial=(K==0 or K==n)
    try:
        if trivial:
            below=0
        else:
            while job.progress['t']<n:
                t,layer=cluster.load_layer(job.progress)
                layer=cluster.step_layer(layer,t+1,n,K,limit)
                t+=1
                job.progress=cluster.dump_layer(t,layer)
                # 每层落库：计算中断后从该计数进度续跑，未算完不写 result
                db.commit()
            below=sum(cnt for (k,m),cnt in cluster.load_layer(job.progress)[1].items() if k==K)
        result=cluster.build_result(bits,below)
        result['input_sha256']=job.checksum
        result['line_version']=job.line_version
        job.result=result;job.status='completed';job.progress=None;job.error=''
        Path(ARTIFACT_DIR/f'cluster-{id}.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
    except Exception as e:
        logging.exception('cluster counting failed');job.status='failed';job.error=str(e)
    db.commit()

def main():
    recover()
    while True:
        Path('/tmp/worker-heartbeat').touch()
        # 同时监听两条队列；超时后循环继续，保持单一 worker 与心跳
        queued=redis.blpop([CLUSTER_QUEUE,ANALYSIS_QUEUE],timeout=2)
        if not queued: continue
        queue_name,payload=queued
        id=int(payload)
        with Session() as db:
            if queue_name==CLUSTER_QUEUE.encode():
                run_cluster(id,db)
            else:
                run_analysis(id,db)

if __name__=='__main__':
    main()
