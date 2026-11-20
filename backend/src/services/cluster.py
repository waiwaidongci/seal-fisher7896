"""漏封扎堆：最优连续段与固定漏封总数下的精确概率。

记 n=件数、K=漏封总数、x_i∈{0,1} 为逐件判定（1=漏封）。
全线漏封率为 K/n，段 [i,j] 的「实际漏封 − 预计件数」为
    Σx − (j−i+1)·K/n = (n·Σx − (j−i+1)·K)/n
分子 w 为整数，最大化真实差值等价于最大化 w；同分按起点最早、再按段最短。

概率口径：固定 n、K，全部 C(n,K) 种漏封摆法等权，求任一段的 w 达到
观测最优 w* 的摆法占比（达到即计入，含并列）。用前缀和
    P_t = n·k_t − t·K
上的动态规划统计「所有段 w 均 < w*」的摆法数，再以总数相减取补集。
全程整数运算，结果为精确分数，不使用浮点或随机抽样。

恢复口径：DP 按前缀位置 t=1..n 逐层推进，每层状态可序列化为检查点；
计算中断后从最后记下的 t 与状态层续跑，更正只影响之后新建的计算。
"""
from math import comb

MAX_PIECES=160


def normalize_judgments(raw):
    """校验并归一化逐件判定，返回 0/1 列表；非法输入抛 ValueError。"""
    if not isinstance(raw,list) or not raw:
        raise ValueError('逐件判定不能为空')
    if len(raw)>MAX_PIECES:
        raise ValueError(f'一条产线至多{MAX_PIECES}件，当前{len(raw)}件，超容量拒绝')
    out=[]
    for idx,v in enumerate(raw,start=1):
        if isinstance(v,bool):
            bit=1 if v else 0
        elif isinstance(v,int) and v in (0,1):
            bit=v
        else:
            raise ValueError(f'第{idx}件判定必须为0(正常)或1(漏封)')
        out.append(bit)
    return out


def best_segment(bits):
    """返回 (起点序号, 终点序号, 整数分子w)，序号从1开始；含并列裁决。"""
    n=len(bits);K=sum(bits)
    best_w=None;best=None
    cur_w=None;cur_start=0
    for t,x in enumerate(bits):
        v=(n-K) if x==1 else -K
        # 空后缀视为负无穷：首件必然开段；之后仅在严格更优时重启，
        # 平局保留更早的起点；全局同分再按起点最早、段最短裁决。
        if cur_w is None or v>cur_w+v:
            cur_w,cur_start=v,t
        else:
            cur_w=cur_w+v
        if best_w is None or cur_w>best_w:
            best_w=cur_w;best=(cur_start,t)
        elif cur_w==best_w:
            ci,cj=cur_start,t;bi,bj=best
            if ci<bi or (ci==bi and (cj-ci)<(bj-bi)):
                best=(ci,cj)
    i,j=best
    return i+1,j+1,best_w


def initial_checkpoint():
    """t=0 的初始状态层：尚未放置任何件，漏封数与最小前缀和均为 0。"""
    return {'t':0,'layer':[[0,0,1]]}


def load_layer(checkpoint):
    t=checkpoint['t']
    layer={(k,m):cnt for k,m,cnt in checkpoint['layer']}
    return t,layer


def dump_layer(t,layer):
    return {'t':t,'layer':[[k,m,cnt] for (k,m),cnt in sorted(layer.items())]}


def step_layer(layer,t,n,K,limit):
    """从前缀位置 t-1 的状态层推进到 t。

    状态 (k,m)：已用漏封数 k、P_0..P_t 中的最小前缀和 m，
    并要求每个 P_t < m+limit（即任意段差都 < limit，不含等于，
    因为达到阈值的并列摆法要计入事件）。
    """
    nxt={}
    for (k,m),cnt in layer.items():
        p0=n*k-(t-1)*K
        # 追加正常件：P 下降 K
        p=p0-K;nm=min(m,p)
        if p<nm+limit:
            nxt[(k,nm)]=nxt.get((k,nm),0)+cnt
        # 追加漏封件：P 上升 n-K
        if k+1<=K:
            p=p0+(n-K);nm=min(m,p)
            if p<nm+limit:
                nxt[(k+1,nm)]=nxt.get((k+1,nm),0)+cnt
    return nxt


def segment_summary(bits):
    n=len(bits);K=sum(bits)
    start,end,w=best_segment(bits)
    length=end-start+1
    return {'start':start,'end':end,'length':length,
            'segment_misses':sum(bits[start-1:end]),
            'expected_misses':length*K/n,
            'difference_numerator':w,'difference':w/n}


def build_result(bits,below):
    """由逐件判定与「未达阈值」摆法数组装最终结果（精确分数）。

    组合计数可达数十位，超出 JavaScript 安全整数范围，因此所有大整数
    计数与精确分数的分子分母一律以字符串输出，保证前端逐位精确显示；
    另附 decimal 供比例展示，small 业务量保持数值类型。
    """
    n=len(bits);K=sum(bits)
    total=comb(n,K)
    favorable=total-below
    def gcd(a,b):
        while b:a,b=b,a%b
        return a
    g=gcd(favorable,total)
    return {
        'total_pieces':n,
        'miss_total':K,
        'line_miss_rate':K/n,
        'segment':segment_summary(bits),
        'threshold_numerator':segment_summary(bits)['difference_numerator'],
        'arrangements_total':str(total),
        'arrangements_below':str(below),
        'arrangements_favorable':str(favorable),
        'probability':{'numerator':str(favorable//g),
                      'denominator':str(total//g),
                      'decimal':favorable/total},
        'tie_included':True,
    }
