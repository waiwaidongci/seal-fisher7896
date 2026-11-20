from sqlalchemy import Column,Integer,String,JSON,Text,ForeignKey
from src.config.database import Base

class SealLine(Base):
    """一条产线按试封序号登记的逐件判定。

    judgments 按下标对应试封序号 1..n，元素 1=漏封、0=正常封。
    version 随每次更正自增，供并发更正的乐观锁与开跑冻结使用。
    """
    __tablename__='seal_lines'
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),unique=True,nullable=False)
    judgments=Column(JSON,nullable=False)
    version=Column(Integer,default=1,nullable=False)
    updated_at=Column(Text,nullable=False)

class ClusterRun(Base):
    """漏封扎堆精确概率计算；输入在创建时按当时版本冻结。"""
    __tablename__='cluster_runs'
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    name=Column(String(120),nullable=False)
    status=Column(String(20),default='queued',nullable=False)
    line_version=Column(Integer,nullable=False)
    total_pieces=Column(Integer,nullable=False)
    miss_total=Column(Integer,nullable=False)
    snapshot=Column(JSON,nullable=False)
    checksum=Column(String(64),nullable=False)
    # 计数进度（可恢复）：{t,layer:[[k,m,count],...]}；完成后清空
    progress=Column(JSON,nullable=True)
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    created_at=Column(String(40),nullable=False)
