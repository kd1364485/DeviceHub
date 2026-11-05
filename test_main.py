"""
DeviceHub のテストコード

pytest を使って API の動作をテストします。
GitHub Actions で push 時に自動実行されます。

実行方法:
    python -m pytest test_main.py -v

テストの基本構造:
    def test_〇〇():
        # 準備（Arrange）
        ...
        # 実行（Act）
        ...
        # 検証（Assert）
        ...
"""

# ============================================================================
# インポート
# ============================================================================

# TestClient: FastAPI アプリを「本物のサーバーを立ち上げずに」テストするツール
# 内部的には httpx を使って、アプリに直接リクエストを送る
from fastapi.testclient import TestClient

# main.py から app をインポート（テスト対象）
from main import app


# ============================================================================
# テストクライアントの準備
# ============================================================================

# TestClient を作る
# これを使うと client.get("/health") のように API を呼び出せる
client = TestClient(app)

# 正しい API キーのヘッダー（main.py の API_KEY と同じ値）
AUTH = {"Authorization": "Bearer my-secret-key"}


# ============================================================================
# テスト: GET /health（DB不要）
# ============================================================================

def test_health_check():
    """ヘルスチェックエンドポイントが正常に応答するかテスト。

    期待する結果:
        - ステータスコード: 200
        - レスポンス: {"status": "ok"}
    """
    # Act: /health に GET リクエストを送る
    response = client.get("/health")

    # Assert: ステータスコードが 200 であること
    assert response.status_code == 200

    # Assert: レスポンスボディが {"status": "ok"} であること
    assert response.json() == {"status": "ok"}


# ============================================================================
# テスト: POST /devices 認証なし（DB不要、認証チェックが先に走る）
# ============================================================================

def test_create_device_without_auth():
    """Authorization ヘッダーなしで POST /devices を呼ぶと 401 になるかテスト。

    期待する結果:
        - ステータスコード: 401
        - レスポンス: {"detail": "Authorization headerがありません"}
    """
    # Act: Authorization ヘッダーなしで POST
    response = client.post(
        "/devices",
        json={
            "user_id": 1,
            "hostname": "test-pc",
            "os": "Windows 11",
            "gpu": "RTX 3060",
            "memory_gb": 16,
        },
    )

    # Assert: 401 Unauthorized であること
    assert response.status_code == 401

    # Assert: エラーメッセージが正しいこと
    assert response.json() == {"detail": "Authorization headerがありません"}


def test_create_device_with_wrong_auth():
    """間違った API キーで POST /devices を呼ぶと 401 になるかテスト。

    期待する結果:
        - ステータスコード: 401
        - レスポンス: {"detail": "APIキーが間違っています"}
    """
    # Act: 間違った API キーで POST
    response = client.post(
        "/devices",
        headers={"Authorization": "Bearer wrong-key"},
        json={
            "user_id": 1,
            "hostname": "test-pc",
            "os": "Windows 11",
            "gpu": "RTX 3060",
            "memory_gb": 16,
        },
    )

    # Assert: 401 Unauthorized であること
    assert response.status_code == 401

    # Assert: エラーメッセージが正しいこと
    assert response.json() == {"detail": "APIキーが間違っています"}


def test_malformed_auth_header():
    """"Bearer <キー>" の形でないヘッダーは 401 になるかテスト（以前は 500 だった）。"""
    response = client.post(
        "/devices",
        headers={"Authorization": "my-secret-key"},   # "Bearer " が無い
        json=VALID_DEVICE,
    )
    assert response.status_code == 401


# ============================================================================
# テスト: 変更系のエンドポイントはすべて認証が必要（DB不要、認証チェックが先に走る）
# ============================================================================
# 以前は POST /devices だけに認証があり、PUT / PATCH / DELETE / POST /users は
# 誰でも呼べた。全部 401 になることを確かめる。

VALID_DEVICE = {
    "user_id": 1,
    "hostname": "test-pc",
    "os": "Windows 11",
    "gpu": "RTX 3060",
    "memory_gb": 16,
}


def test_create_user_without_auth():
    response = client.post("/users", json={"name": "test-user"})
    assert response.status_code == 401


def test_update_device_without_auth():
    response = client.put("/devices/1", json=VALID_DEVICE)
    assert response.status_code == 401


def test_patch_device_without_auth():
    response = client.patch("/devices/1", json={"memory_gb": 32})
    assert response.status_code == 401


def test_delete_device_without_auth():
    response = client.delete("/devices/1")
    assert response.status_code == 401


def test_delete_device_with_wrong_auth():
    response = client.delete("/devices/1", headers={"Authorization": "Bearer wrong-key"})
    assert response.status_code == 401


# ============================================================================
# テスト: バリデーションエラー（DB不要、Pydanticが先にチェック）
# ============================================================================

def test_create_device_invalid_memory():
    """memory_gb が 0 以下の場合は 422 になるかテスト。

    DeviceCreate で memory_gb: int = Field(..., gt=0) と定義しているので、
    0 以下の値はバリデーションエラーになるはず。

    期待する結果:
        - ステータスコード: 422
    """
    response = client.post(
        "/devices",
        headers={"Authorization": "Bearer my-secret-key"},
        json={
            "user_id": 1,
            "hostname": "test-pc",
            "os": "Windows 11",
            "gpu": "RTX 3060",
            "memory_gb": 0,  # 0 は gt=0 で弾かれる
        },
    )

    # Assert: 422 Unprocessable Entity であること
    assert response.status_code == 422


def test_create_device_missing_hostname():
    """hostname が欠けている場合は 422 になるかテスト。

    期待する結果:
        - ステータスコード: 422
    """
    response = client.post(
        "/devices",
        headers={"Authorization": "Bearer my-secret-key"},
        json={
            "user_id": 1,
            # hostname が欠けている
            "os": "Windows 11",
            "gpu": "RTX 3060",
            "memory_gb": 16,
        },
    )

    # Assert: 422 Unprocessable Entity であること
    assert response.status_code == 422


# ============================================================================
# テスト: DB が必要なテスト（GitHub Actions で PostgreSQL サービスを使う）
# ============================================================================

def test_create_user_and_get_devices():
    """ユーザー作成 → デバイス登録 → デバイス一覧取得の流れをテスト。

    このテストは DB が必要なので、ローカルで実行する場合は
    PostgreSQL が起動している必要がある。
    GitHub Actions では PostgreSQL サービスコンテナを使う。

    期待する結果:
        1. POST /users → 201
        2. POST /devices（認証あり）→ 201
        3. GET /devices → 200 で1件以上
    """
    # Step 1: ユーザー作成（認証あり）
    response = client.post("/users", headers=AUTH, json={"name": "test-user"})
    assert response.status_code == 201
    user = response.json()
    assert user["name"] == "test-user"
    user_id = user["id"]

    # Step 2: デバイス登録（認証あり）
    response = client.post(
        "/devices",
        headers={"Authorization": "Bearer my-secret-key"},
        json={
            "user_id": user_id,
            "hostname": "ci-test-pc",
            "os": "Windows 11",
            "cpu": "Core i7",
            "gpu": "RTX 3060",
            "memory_gb": 16,
        },
    )
    assert response.status_code == 201
    device = response.json()
    assert device["hostname"] == "ci-test-pc"

    # Step 3: デバイス一覧取得
    response = client.get("/devices")
    assert response.status_code == 200
    devices = response.json()
    assert len(devices) >= 1

    # Step 4: 認証ありなら、更新・部分更新・削除ができる
    device_id = device["id"]
    response = client.put(
        f"/devices/{device_id}",
        headers=AUTH,
        json={**VALID_DEVICE, "user_id": user_id, "hostname": "ci-test-pc", "memory_gb": 32},
    )
    assert response.status_code == 200
    assert response.json()["memory_gb"] == 32

    response = client.patch(f"/devices/{device_id}", headers=AUTH, json={"gpu": "RTX 4060"})
    assert response.status_code == 200
    assert response.json()["gpu"] == "RTX 4060"

    response = client.delete(f"/devices/{device_id}", headers=AUTH)
    assert response.status_code == 204
    assert client.get(f"/devices/{device_id}").status_code == 404


def test_get_device_not_found():
    """存在しないデバイス ID を取得すると 404 になるかテスト。

    期待する結果:
        - ステータスコード: 404
    """
    # 9999 は存在しないはずの ID
    response = client.get("/devices/9999")

    # Assert: 404 Not Found であること
    assert response.status_code == 404


# ============================================================================
# テスト: heartbeat と ONLINE / OFFLINE
# ============================================================================

def test_heartbeat_without_auth():
    """heartbeat も変更系なので、キーなしは 401（DB不要）。"""
    response = client.post("/devices/1/heartbeat")
    assert response.status_code == 401


def test_heartbeat_unknown_device():
    """存在しないデバイスへの heartbeat は 404。"""
    response = client.post("/devices/999999/heartbeat", headers=AUTH)
    assert response.status_code == 404


def test_heartbeat_online_offline(monkeypatch):
    """登録直後は OFFLINE → heartbeat で ONLINE → 時間切れで OFFLINE → heartbeat があっても削除できる。"""
    import main

    user_id = client.post("/users", headers=AUTH, json={"name": "hb-user"}).json()["id"]
    response = client.post(
        "/devices",
        headers=AUTH,
        json={**VALID_DEVICE, "user_id": user_id, "hostname": "hb-test-pc"},
    )
    assert response.status_code == 201
    device = response.json()
    device_id = device["id"]

    # 一度も heartbeat が無いので OFFLINE
    assert device["last_heartbeat_at"] is None
    assert device["status"] == "OFFLINE"

    # heartbeat を送ると ONLINE。一覧と個別取得でも同じ状態が見える
    response = client.post(f"/devices/{device_id}/heartbeat", headers=AUTH)
    assert response.status_code == 201
    assert response.json()["status"] == "ONLINE"
    assert response.json()["last_heartbeat_at"] is not None
    assert client.get(f"/devices/{device_id}").json()["status"] == "ONLINE"
    listed = [d for d in client.get("/devices").json() if d["id"] == device_id]
    assert listed[0]["status"] == "ONLINE"

    # 判定の秒数を 0 にすると、最後の heartbeat は「古い」扱いになり OFFLINE
    # （60 秒待つ代わりに、しきい値を変えて時間切れを再現する）
    monkeypatch.setattr(main, "ONLINE_THRESHOLD_SECONDS", 0)
    assert client.get(f"/devices/{device_id}").json()["status"] == "OFFLINE"

    # heartbeat の行があっても、デバイスを削除できる（heartbeat も一緒に消える）
    response = client.delete(f"/devices/{device_id}", headers=AUTH)
    assert response.status_code == 204
    assert client.get(f"/devices/{device_id}").status_code == 404
