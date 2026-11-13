-- funnelpath 0002：匿名 → 账号身份关联（跨身份轨迹合成）
-- 引擎自动建表：SQLAlchemy Base.metadata.create_all()
-- 本文件是与 src/models/*.py 对应的可读 DDL 存档。

-- 关联台账：匿名标识与账号的绑定、冲突与中断状态
CREATE TABLE IF NOT EXISTS identity_links (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  anonymous_key   VARCHAR(64)  NOT NULL,
  account_key     VARCHAR(64)  NOT NULL,
  status          VARCHAR(16)  NOT NULL DEFAULT 'active',
  delivery_count  INT          NOT NULL DEFAULT 1,
  conflict_with   VARCHAR(64)  NULL,
  conflict_detail JSON         NOT NULL,
  note            VARCHAR(240) NOT NULL DEFAULT '',
  first_seen_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
  last_delivery_at DATETIME    DEFAULT CURRENT_TIMESTAMP,
  created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_identity_pair (anonymous_key, account_key),
  KEY ix_identity_anon (anonymous_key),
  KEY ix_identity_account (account_key),
  KEY ix_identity_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- user_key 放宽到 64，容纳 anon: 前缀的合成标识
ALTER TABLE event_logs MODIFY COLUMN user_key VARCHAR(64) NOT NULL;
ALTER TABLE session_paths MODIFY COLUMN user_key VARCHAR(64) NOT NULL;

-- 会话路径的身份合成溯源
ALTER TABLE session_paths
  ADD COLUMN source_keys JSON NOT NULL AFTER is_converted;
ALTER TABLE session_paths
  ADD COLUMN identity_merged TINYINT(1) NOT NULL DEFAULT 0 AFTER source_keys;

-- 报告固化生成时那次运行的身份快照，保证结果页 / 路径 / 导出同源
ALTER TABLE drop_reports
  ADD COLUMN identity_snapshot JSON NOT NULL AFTER segment_gap;
