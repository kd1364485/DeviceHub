"""
DeviceHub v0.1 - メインアプリケーション

Windows PC のデバイス情報を管理する Web API です。
このバージョンでは DB は使わず、Python の辞書（メモリ上）でデータを保持します。

起動方法:
    uvicorn main:app --reload

--reload を付けると、コードを書き換えた時に自動で再起動してくれるので開発中に便利です。

動作確認:
    ブラウザで http://127.0.0.1:8000/docs を開くと、
    FastAPI が自動生成した API ドキュメント（Swagger UI）が見られます。
"""

API_KEY = "my-secret-key"

# ============================================================================
# インポート
# ============================================================================

from datetime import datetime, timezone

# FastAPI: アプリ本体を作るためのクラス
from fastapi import FastAPI, HTTPException, Response, status

# BaseModel: リクエスト/レスポンスのデータ形（スキーマ）を定義するためのクラス
#            Pydantic が自動でバリデーション（入力チェック）をしてくれる
from pydantic import BaseModel, Field


from typing import Optional

from fastapi import Header


# ============================================================================
# データモデル（Pydantic BaseModel）
# ============================================================================
# 「この API はどんなデータを受け取り、どんなデータを返すか」を定義します。
# FastAPI はこの定義を読んで、自動で:
#   - 入力チェック（必須フィールドが無いと 400 Bad Request）
#   - 自動ドキュメント生成（/docs）
#   - 型変換
# を行ってくれます。


class UserCreate(BaseModel):
    """ユーザー作成時のリクエストボディ。

    POST /users でクライアントから送られてくる JSON の形です。
    """

    name: str = Field(..., min_length=1, max_length=100, description="ユーザー名")
    # ... は「必須」を意味します。省略すると 422 Unprocessable Entity になります。


class UserResponse(BaseModel):
    """ユーザー作成後のレスポンスボディ。

    サーバーがクライアントに返す JSON の形です。
    id と created_at はサーバー側で自動採番・自動生成するので、
    リクエスト時には不要ですが、レスポンスには含めます。
    """

    id: int
    name: str
    created_at: datetime


class DeviceCreate(BaseModel):
    """デバイス登録時のリクエストボディ。

    POST /devices でクライアントから送られてくる JSON の形です。
    例:
        {
          "user_id": 1,
          "hostname": "DESKTOP-ABC",
          "os": "Windows 11",
          "gpu": "RTX 3060",
          "memory_gb": 16
        }
    """

    user_id: int = Field(..., description="このデバイスを所有するユーザーの ID")
    hostname: str = Field(..., min_length=1, max_length=255, description="PC のホスト名")
    os: str = Field(..., max_length=100, description="OS 名（例: Windows 11）")
    gpu: str = Field(..., max_length=100, description="GPU 名（例: RTX 3060）")
    memory_gb: int = Field(..., gt=0, description="メモリ容量（GB）。0 より大きい値")


class DevicePatch(BaseModel):
    """デバイス部分更新時のリクエストボディ.
    全フィールドが任意(optional)なので、送られた項目だけ更新される
    送られなかった項目はNoneになり、Noneのものはスキップ
    """

    user_id: Optional[int] = None
    hostname: Optional[str] = None
    os: Optional[str] = None
    gpu: Optional[str] = None
    memory_gb: Optional[int] = None

class DeviceResponse(BaseModel):
    """デバイス取得時のレスポンスボディ。

    サーバーがクライアントに返す JSON の形です。
    id と created_at はサーバー側で自動生成します。
    """

    id: int
    user_id: int
    hostname: str
    os: str
    gpu: str
    memory_gb: int
    created_at: datetime


# ============================================================================
# インメモリデータストア
# ============================================================================
# 今回は DB を使わず、Python の辞書でデータを保持します。
# サーバーを再起動するとデータは消えます（v0.2 で PostgreSQL に移行予定）。
#
# 辞書のキーに ID（int）を使い、値に dict を格納します。
# 例: devices = { 1: {"id": 1, "hostname": "DESKTOP-ABC", ...}, ... }

users: dict[int, dict] = {}
devices: dict[int, dict] = {}

# 採番用カウンター。新規登録ごとに +1 します。
# 本来は DB の AUTO INCREMENT がやってくれることです。
_next_user_id = 1
_next_device_id = 1


# ============================================================================
# FastAPI アプリケーション
# ============================================================================

app = FastAPI(
    title="DeviceHub",
    version="0.1.0",
    description="Windows PC のデバイス情報を管理する API（DB なし・メモリ上）",
)


# ============================================================================
# エンドポイント（API の入口）
# ============================================================================
# 各エンドポイントは HTTP メソッド（GET / POST / PUT / DELETE）と URL パスの組み合わせで
# 定義します。FastAPI はデコレータ（@app.get など）でこれを指定します。


# ----------------------------------------------------------------------------
# POST /users - ユーザー作成
# ----------------------------------------------------------------------------
@app.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="ユーザーを作成する",
)
def create_user(user: UserCreate):
    """新しいユーザーを作成します。

    引数 user には、リクエストボディの JSON が Pydantic によって
    UserCreate オブジェクトに変換されて渡ってきます。
    （FastAPI が自動で JSON パース + バリデーション を行う）

    Returns:
        UserResponse: 作成されたユーザー情報（id, name, created_at を含む）

    Status Code:
        201 Created - リソースが正常に作成された
    """
    global _next_user_id

    # サーバー側で自動生成する値を追加
    user_data = {
        "id": _next_user_id,
        "name": user.name,
        "created_at": datetime.now(timezone.utc),
    }

    # メモリに保存
    users[_next_user_id] = user_data

    # 次の ID のためにカウンターを進める
    _next_user_id += 1

    # レスポンスを返す（FastAPI が UserResponse に変換して JSON にして返す）
    return user_data


# ----------------------------------------------------------------------------
# POST /devices - デバイス登録
# ----------------------------------------------------------------------------
@app.post(
    "/devices",
    response_model=DeviceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="デバイスを登録する",
)
def create_device(device: DeviceCreate,
authorization: str|None = Header(None)):
    """新しいデバイスを登録します。

    指定された user_id が存在しない場合は 404 Not Found を返します。
    これは「存在しないユーザーのデバイスは登録できない」というルールです。

    Args:
        device: リクエストボディ（user_id, hostname, os, gpu, memory_gb）

    Returns:
        DeviceResponse: 登録されたデバイス情報

    Status Code:
        201 Created - デバイスが正常に登録された
        404 Not Found  - 指定された user_id が存在しない
    """


    #ステップ１Headerが無い、または形式が違う
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization headerがありません"
        )

    #ステップ２"Bearer my-secret-key"から"my-secret-key"だけを取り出す
    #authorization = "Bearer my-secret-key"
    #split(" ") → ["Bearer", "my-secret-key"]
    #[1] → "my-secret-key"
    token = authorization.split(" ")[1]

    #3ステップ：トークンが正しいか確認
    if token != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="APIキーが間違っています"
        )

    #ここまで来たら認証成功

    #POST /devices で、同じ hostname のデバイスが既に存在する場合、登録を拒否する
    for d in devices.values():
        if d["hostname"] == device.hostname:
            raise HTTPException(
                status_code = status.HTTP_400_BAD_REQUEST,
                detail=f"hostname={device.hostname} のデバイスは既に存在します"
            )




    global _next_device_id

    # --- 存在チェック: 指定されたユーザーは存在するか？ ---
    if device.user_id not in users:
        # HTTPException を raise すると、FastAPI が自動でエラーレスポンスを作ってくれる
        # detail に渡した文字列が、レスポンスの JSON の "detail" フィールドに入る
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user_id={device.user_id} のユーザーは存在しません",
        )

    # --- デバイスデータを組み立てて保存 ---
    device_data = {
        "id": _next_device_id,
        "user_id": device.user_id,
        "hostname": device.hostname,
        "os": device.os,
        "gpu": device.gpu,
        "memory_gb": device.memory_gb,
        "created_at": datetime.now(timezone.utc),
    }

    devices[_next_device_id] = device_data
    _next_device_id += 1

    return device_data


# ----------------------------------------------------------------------------
# GET /devices - デバイス一覧取得
# ----------------------------------------------------------------------------
@app.get(
    "/devices",
    response_model=list[DeviceResponse],
    summary="デバイス一覧を取得する",
)
def list_devices(os: str | None = None,gpu: str | None = None):
    """登録されている全デバイスの一覧を取得します。

    Returns:
        list[DeviceResponse]: デバイスのリスト（空の場合は空配列 []）

    Status Code:
        200 OK - 正常に取得できた
    """
    result = [devices[key] for key in sorted(devices.keys())]

    if os is not None:
        result = [device for device in result if device["os"] == os]
    if gpu is not None:
        result = [device for device in result if device["gpu"] == gpu]

    return result


# ----------------------------------------------------------------------------
# GET /devices/{device_id} - 個別デバイス取得
# ----------------------------------------------------------------------------
@app.get(
    "/devices/{device_id}",
    response_model=DeviceResponse,
    summary="指定 ID のデバイスを取得する",
)
def get_device(device_id: int):
    """指定された ID のデバイス情報を取得します。

    Args:
        device_id: URL パスの一部として渡されるデバイス ID
                   例: GET /devices/1 → device_id=1

    Returns:
        DeviceResponse: デバイス情報

    Status Code:
        200 OK       - デバイスが見つかった
        404 Not Found - デバイスが存在しない（例: GET /devices/9999）

    ---
    【重要な判断ポイント】

    GET /devices/9999 のように、存在しない ID を指定された場合、
    どう返すべきか？

        200 OK  → ❌ データが無いのに「成功」はおかしい
        400 Bad Request → ❌ リクエストの形式は正しい（整数だし）
        404 Not Found   → ✅ 「リソースが見つからない」が正しい

    したがって、ここでは 404 を返します。
    """
    # 辞書にその ID が存在するか確認
    if device_id not in devices:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device_id={device_id} のデバイスは存在しません",
        )

    return devices[device_id]


# ============================================================================
# ヘルスチェックエンドポイント（おまけ）
# ============================================================================
# サーバーが生きているか確認するためのエンドポイント。
# AWS にデプロイした後、ロードバランサーのヘルスチェック等で使います。


@app.get("/health", summary="ヘルスチェック")
def health_check():
    """サーバーが正常に動作しているか確認します。"""
    return {"status": "ok"}



@app.put("/devices/{device_id}")
def update_device(device_id: int, device: DeviceCreate):
    if device_id not in devices:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device_id={device_id} のデバイスは存在しません"
        )

    # id と created_at は既存のものを保持、それ以外は新しい値で上書き
    devices[device_id] = {
        "id": device_id,
        "user_id": device.user_id,
        "hostname": device.hostname,
        "os": device.os,
        "gpu": device.gpu,
        "memory_gb": device.memory_gb,
        "created_at": devices[device_id]["created_at"],
    }
    return devices[device_id]

@app.patch("/devices/{device_id}")
def partial_update_device(device_id: int, device: DevicePatch):
    if device_id not in devices:
        raise HTTPException(
            status_code= status.HTTP_404_NOT_FOUND,
            detail = f"device_id={device_id} のデバイスは存在しません"
        )
    
    # デバイス情報を更新
    for key, value in device.dict().items():
        if value is not None:
            devices[device_id][key] = value
    
    return devices[device_id]


@app.delete("/devices/{device_id}")
def delete_device(device_id: int):
    if device_id not in devices:

        raise HTTPException(
            status_code= status.HTTP_404_NOT_FOUND,
            detail = f"device_id={device_id} のデバイスは存在しません"
        )
    
    del devices[device_id]
    return Response(status_code=status.HTTP_204_NO_CONTENT)