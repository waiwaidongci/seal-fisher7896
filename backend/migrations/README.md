# Schema 版本说明

## v2（漏封扎堆功能）
新增两张表，均由 SQLAlchemy 模型在启动时经 `Base.metadata.create_all` 自动创建，
不破坏既有表，无需手工迁移：

- `seal_lines`：一条产线按试封序号的逐件判定（`judgments` 为 0/1 数组，
  1=漏封），含乐观锁 `version` 与 `updated_at`；与 `studies` 一对一。
- `cluster_runs`：漏封扎堆精确计算。创建时冻结 `line_version`、判定快照与
  `checksum`；`progress` 存逐层 DP 检查点 `{t,layer}`，完成后置空；
  `result` 仅在计数完成后写入，未算完不出现概率。

升级前请备份命名卷 `db-data`。旧版本应用不会读取这两张表，可共存。

## v1（初始）
由 SQLAlchemy 模型定义并在启动时创建；本地小型应用不执行破坏性迁移。
修改 schema 前备份 `db-data`。
