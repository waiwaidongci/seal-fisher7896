from fastapi import APIRouter,Depends
from src.config.database import get_db
from src.middlewares.auth import current_user,writer
from src.schemas.seal_cluster import SealLineInput,ClusterRunInput
from src.services import seal_lines
router=APIRouter()

@router.get('/studies/{id}/seal-line')
def get_line(id:int,db=Depends(get_db),user=Depends(current_user)):
    return seal_lines.get_line(db,id)

@router.put('/studies/{id}/seal-line')
def save_line(id:int,data:SealLineInput,db=Depends(get_db),user=Depends(writer)):
    return seal_lines.save_line(db,id,data,user)

@router.get('/cluster-runs')
def list_cluster_runs(study_id:int|None=None,db=Depends(get_db),user=Depends(current_user)):
    return seal_lines.list_runs(db,study_id)

@router.post('/cluster-runs')
def create_cluster_run(data:ClusterRunInput,db=Depends(get_db),user=Depends(writer)):
    return seal_lines.create_run(db,data,user)

@router.get('/cluster-runs/{run_id}')
def get_cluster_run(run_id:int,db=Depends(get_db),user=Depends(current_user)):
    return seal_lines.get_run(db,run_id)
