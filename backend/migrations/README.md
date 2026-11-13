# migrations

本项目的表结构由 SQLAlchemy 2.0 的 `Base.metadata.create_all()` 在 backend 启动时创建
（`src/config/app.py::_bootstrap_database`），因此**容器启动即建表**，不需要额外的迁移步骤。

`0001_initial.sql` 是同一份结构的可读 DDL 存档，供 DBA 审阅、或在只读副本上手工建表使用。
`0002_identity_links.sql` 是身份关联（匿名 → 账号）能力的增量 DDL 存档：新增 `identity_links`
台账，给 `session_paths` 加 `source_keys` / `identity_merged` 溯源列，给 `drop_reports` 加
`identity_snapshot`，并把 `event_logs` / `session_paths` 的 `user_key` 放宽到 64。
若后续要做真正的版本化迁移，建议引入 Alembic 并把这里的 DDL 作为 baseline。
