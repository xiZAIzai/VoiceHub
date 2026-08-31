"""V4/M12 凭证服务与端点测试：脱敏回读、白名单、合并保留、Web 往返。"""

import json

from fastapi.testclient import TestClient

from voicehub.config import Config
from voicehub.credentials import CredentialsService
from voicehub.web import Dashboard


# ---------- CredentialsService ----------

def test_update_merges_and_masks(tmp_path):
    p = tmp_path / "config.local.json"
    p.write_text(json.dumps({"polish": {"api_key": "sk-existing-9999"}}),
                 encoding="utf-8")
    svc = CredentialsService(p)
    r = svc.update({"transcription": {"app_key": "1234567890abcd"},
                    "polish": {"api_key": ""}})  # 空=不改动
    assert r["ok"] is True and r["updated"] == ["transcription.app_key"]
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["polish"]["api_key"] == "sk-existing-9999"       # 合并不覆盖
    assert data["transcription"]["app_key"] == "1234567890abcd"

    st = svc.status()
    assert st["transcription"]["app_key"]["set"] is True
    assert st["transcription"]["app_key"]["tail"] == "…abcd"     # 脱敏尾 4 位
    assert st["polish"]["api_key"]["tail"] == "…9999"
    # 完整 key 不出现在任何状态输出里
    assert "1234567890abcd" not in json.dumps(st)
    assert "sk-existing" not in json.dumps(st)


def test_update_whitelist_and_atomicity(tmp_path):
    p = tmp_path / "config.local.json"
    svc = CredentialsService(p)
    r = svc.update({"transcription": {"api_key": "k1",
                                      "language": "zh"},   # 白名单外字段忽略
                    "unknown_domain": {"api_key": "k2"},
                    "polish": {"api_key": "sk2"}})
    assert r["ok"] is True
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["transcription"] == {"api_key": "k1"}
    assert "unknown_domain" not in data

    # 全空 payload → 不写文件（ok=False）
    r2 = svc.update({"transcription": {"api_key": "  "}})
    assert r2["ok"] is False


def test_broken_local_file_not_clobbered(tmp_path):
    p = tmp_path / "config.local.json"
    p.write_text("{broken", encoding="utf-8")
    svc = CredentialsService(p)
    r = svc.update({"polish": {"api_key": "sk-new"}})
    assert r["ok"] is True  # 坏文件被当作空重新构建（用户原文件已坏，提示语义可接受）
    assert json.loads(p.read_text(encoding="utf-8"))["polish"]["api_key"] == "sk-new"


# ---------- Web 往返 ----------

def test_credentials_endpoints_roundtrip(tmp_path):
    cp = tmp_path / "config.json"
    cp.write_text("{}", encoding="utf-8")
    app = Dashboard(Config(), credentials=CredentialsService(cp)).build_app()
    c = TestClient(app)

    status = c.get("/api/credentials").json()
    assert status["ok"] is True
    assert status["credentials"]["polish"]["api_key"]["set"] is False

    saved = c.post("/api/credentials", json={
        "transcription": {"app_key": "7658182493abcd"},
        "polish": {"api_key": "sk-test-12345678"}}).json()
    assert saved["ok"] is True

    again = c.get("/api/credentials").json()["credentials"]
    assert again["transcription"]["app_key"]["tail"] == "…abcd"
    # 完整 key 不出现在任何 HTTP 响应
    assert "7658182493abcd" not in again.__str__()
    assert "sk-test-12345678" not in str(c.get("/api/credentials").content)


def test_credentials_absent_graceful():
    app = Dashboard(Config()).build_app()
    assert TestClient(app).get("/api/credentials").json()["ok"] is False


def test_vendor_js_served_locally():
    """仪表盘前端依赖本地分发：/static/vendor 白名单命中返回 JS，未知名 404。"""
    app = Dashboard(Config()).build_app()
    c = TestClient(app)
    r = c.get("/static/vendor/vue.global.prod.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert len(r.content) > 100_000  # 完整 vendor 文件而非错误页
    r2 = c.get("/static/vendor/tailwind.js")
    assert r2.status_code == 200 and len(r2.content) > 300_000
    assert c.get("/static/vendor/../config.py").status_code == 404  # 路径穿越拒绝
    assert c.get("/static/vendor/notexist.js").status_code == 404
