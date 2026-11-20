from fastapi import HTTPException
from sqlalchemy import select
from src.models.study import Study
from src.models.seal_cluster import SealLine,ClusterRun
from src.schemas.seal_cluster import MAX_PIECES
from src.services import cluster
from src.services.audits import log
from src.utils.serializers import record,now,checksum
import os
from redis import Redis


def require_study(db,study_id):
    s=db.get(Study,study_id)
    if not s: raise HTTPException(404,'业务批次不存在')
    return s


def get_line(db,study_id):
    require_study(db,study_id)
    line=db.scalar(select(SealLine).where(SealLine.study_id==study_id))
    if not line:
        return {'study_id':study_id,'judgments':[],'version':0,
                'piece_count':0,'miss_count':0,'capacity':MAX_PIECES,'updated_at':None}
    data=record(line)
    data['piece_count']=len(line.judgments)
    data['miss_count']=sum(line.judgments)
    data['capacity']=MAX_PIECES
    return data


def save_line(db,study_id,data,user):
    require_study(db,study_id)
    try:
        judgments=cluster.normalize_judgments(data.judgments)
    except (ValueError,TypeError) as e:
        raise HTTPException(422,str(e))
    line=db.scalar(select(SealLine).where(SealLine.study_id==study_id))
    if not line:
        if data.expected_version!=0:
            raise HTTPException(409,'版本冲突：该产线尚无记录，期望版本应为0')
        line=SealLine(study_id=study_id,judgments=judgments,version=1,updated_at=now())
        db.add(line)
    else:
        if data.expected_version!=line.version:
            raise HTTPException(409,f'版本冲突：记录已到第{line.version}版，请刷新后基于最新版本更正')
        line.judgments=judgments
        line.version+=1
        line.updated_at=now()
    db.flush()
    log(db,user,'seal_line.save',f'{study_id}/v{line.version}/{len(judgments)}件'
        +(f'/{data.note}' if data.note else ''))
    db.commit()
    return get_line(db,study_id)


def create_run(db,data,user):
    require_study(db,data.study_id)
    line=db.scalar(select(SealLine).where(SealLine.study_id==data.study_id))
    if not line or not line.judgments:
        raise HTTPException(422,'先按试封序号录入该产线的逐件判定')
    if len(line.judgments)>MAX_PIECES:
        raise HTTPException(422,f'一条产线至多{MAX_PIECES}件，超容量拒绝')
    # 开跑只取同一版记录：冻结当前判定与版本，之后的更正不影响本次计算
    snapshot=list(line.judgments)
    run=ClusterRun(study_id=data.study_id,name=data.name,status='queued',
                   line_version=line.version,total_pieces=len(snapshot),
                   miss_total=sum(snapshot),snapshot=snapshot,
                   checksum=checksum({'version':line.version,'judgments':snapshot}),
                   progress=None,result=None,error='',created_at=now())
    db.add(run);db.flush()
    log(db,user,'cluster_run.queue',f'{run.id}/study {data.study_id}/v{line.version}')
    db.commit()
    Redis.from_url(os.environ['REDIS_URL']).rpush('cluster-queue',run.id)
    return _attach(db,run)


def _attach(db,run):
    data=record(run)
    # 检查点状态层可能很大，仅对外暴露已推进到的件数进度，不下发整层
    data['progress_t']=run.progress['t'] if run.progress else (
        run.total_pieces if run.status=='completed' else 0)
    data.pop('progress',None)
    return data


def get_run(db,run_id):
    run=db.get(ClusterRun,run_id)
    if not run: raise HTTPException(404,'扎堆计算不存在')
    return _attach(db,run)


def list_runs(db,study_id=None):
    stmt=select(ClusterRun).order_by(ClusterRun.id.desc())
    if study_id is not None:
        stmt=stmt.where(ClusterRun.study_id==study_id)
    return [_attach(db,r) for r in db.scalars(stmt)]
