from pydantic import BaseModel,Field

MAX_PIECES=160

class SealLineInput(BaseModel):
    # 逐件判定，按试封序号顺序；True/1 表示漏封，False/0 表示正常
    judgments:list=Field(min_length=1)
    expected_version:int=Field(ge=0)
    note:str=Field(default='',max_length=200)

class ClusterRunInput(BaseModel):
    study_id:int=Field(gt=0)
    name:str=Field(min_length=2,max_length=120)
