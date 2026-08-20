"""SQLite 存储引擎：持久化转写日志（transcript_logs），面向 Agent 记忆预留。

对应 PRD §4 的表结构与索引。多线程场景（热键 / 剪贴板 / Web 各线程）下使用
单连接 + 锁，开启 WAL 提升并发读。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    """UTC 时间 ISO 字符串（与 SQLite CURRENT_TIMESTAMP 同格式）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@dataclass
class TranscriptLog:
    """一条转写日志记录，字段对应 PRD §4 表结构。"""

    processed_text: str
    target_device: str = "desktop"
    raw_text: str | None = None
    char_count: int | None = None
    latency_ms: int = 0
    category: str = "general"
    is_routed_successfully: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=_now_iso)
    embedding_status: str = "pending"
    metadata_json: str | None = None

    def __post_init__(self) -> None:
        if self.char_count is None:
            self.char_count = len(self.processed_text)

    @property
    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_json) if self.metadata_json else {}

    def set_metadata(self, **kwargs: Any) -> None:
        """合并写入扩展元数据（如当前活跃窗口、粘滞等待时长）。"""
        meta = self.metadata
        meta.update(kwargs)
        self.metadata_json = json.dumps(meta, ensure_ascii=False)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcript_logs (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    target_device TEXT NOT NULL,
    raw_text TEXT,
    processed_text TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    category TEXT DEFAULT 'general',
    is_routed_successfully INTEGER NOT NULL,
    embedding_status TEXT DEFAULT 'pending',
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_created_at ON transcript_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_embedding_status ON transcript_logs(embedding_status);
"""


class Storage:
    """线程安全的 SQLite 封装（单连接 + 锁）。"""

    def __init__(self, db_path: str | Path = "voice_memory.db", retention_days: int = 90) -> None:
        self.db_path = str(db_path)
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert(self, log: TranscriptLog) -> None:
        """写入一条转写日志。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO transcript_logs "
                "(id, created_at, target_device, raw_text, processed_text, char_count, "
                " latency_ms, category, is_routed_successfully, embedding_status, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (log.id, log.created_at, log.target_device, log.raw_text, log.processed_text,
                 log.char_count, log.latency_ms, log.category,
                 1 if log.is_routed_successfully else 0, log.embedding_status, log.metadata_json),
            )
            self._conn.commit()

    def recent(self, limit: int = 50, category: str | None = None) -> list[dict[str, Any]]:
        """按时间倒序取最近记录，可选按分类过滤。"""
        sql = "SELECT * FROM transcript_logs"
        params: list[Any] = []
        if category:
            sql += " WHERE category = ?"
            params.append(category)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get(self, log_id: str) -> dict[str, Any] | None:
        """按 id 取单条记录。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM transcript_logs WHERE id = ?", (log_id,)
            ).fetchone()
        return dict(row) if row else None

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM transcript_logs").fetchone()
        return int(row["c"])

    def prune(self) -> int:
        """删除超过 retention_days 的旧记录，返回删除条数。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM transcript_logs WHERE created_at < datetime('now', ?)",
                (f"-{self.retention_days} days",),
            )
            self._conn.commit()
        return cur.rowcount

    def update_embedding_status(self, log_id: str, status: str) -> None:
        """更新向量化状态（'pending' -> 'indexed'），为 Agent 记忆预留。"""
        with self._lock:
            self._conn.execute(
                "UPDATE transcript_logs SET embedding_status = ? WHERE id = ?",
                (status, log_id),
            )
            self._conn.commit()
