"""存储引擎单测：建表、插入、查询、metadata、过期清理、embedding 状态。"""
import json

from voicehub.storage import Storage, TranscriptLog


def test_insert_and_count(tmp_path):
    db = tmp_path / "voice_memory.db"
    s = Storage(db)
    s.insert(TranscriptLog(processed_text="你好", target_device="laptop",
                           latency_ms=120, is_routed_successfully=True))
    assert s.count() == 1
    s.close()


def test_recent_order_and_category(tmp_path):
    db = tmp_path / "v.db"
    s = Storage(db)
    s.insert(TranscriptLog(processed_text="a", target_device="desktop", category="note"))
    s.insert(TranscriptLog(processed_text="b", target_device="laptop", category="code"))
    assert [r["processed_text"] for r in s.recent(10)] == ["b", "a"]
    assert [r["processed_text"] for r in s.recent(10, category="code")] == ["b"]
    s.close()


def test_char_count_auto(tmp_path):
    db = tmp_path / "v.db"
    s = Storage(db)
    log = TranscriptLog(processed_text="hello world", target_device="desktop")
    assert log.char_count == 11
    s.insert(log)
    assert s.get(log.id)["char_count"] == 11
    s.close()


def test_metadata_roundtrip(tmp_path):
    db = tmp_path / "v.db"
    s = Storage(db)
    log = TranscriptLog(processed_text="x", target_device="tablet")
    log.set_metadata(active_window="chrome", sticky_ms=2000)
    s.insert(log)
    row = s.get(log.id)
    assert json.loads(row["metadata_json"])["active_window"] == "chrome"
    assert json.loads(row["metadata_json"])["sticky_ms"] == 2000
    s.close()


def test_prune_removes_old(tmp_path):
    db = tmp_path / "v.db"
    s = Storage(db, retention_days=90)
    s.insert(TranscriptLog(processed_text="old", target_device="desktop"))
    # 把 created_at 改成 100 天前，模拟过期记录
    with s._lock:
        s._conn.execute("UPDATE transcript_logs SET created_at = datetime('now', '-100 days')")
        s._conn.commit()
    assert s.prune() == 1
    assert s.count() == 0
    s.close()


def test_update_embedding_status(tmp_path):
    db = tmp_path / "v.db"
    s = Storage(db)
    log = TranscriptLog(processed_text="x", target_device="desktop")
    s.insert(log)
    assert s.get(log.id)["embedding_status"] == "pending"
    s.update_embedding_status(log.id, "indexed")
    assert s.get(log.id)["embedding_status"] == "indexed"
    s.close()
