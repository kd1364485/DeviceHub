"""
DeviceHub v0.2 - メインアプリケーション

Windows PC のデバイス情報を管理する Web API です。
v0.2 から PostgreSQL にデータを保存します。サーバーを再起動してもデータは残ります。

起動方法:
    uvicorn main:app --reload

動作確認:
    ブラウザで http://127.0.0.1:8000/docs を開くと、
    FastAPI が自動生成した API ドキュメント（Swagger UI）が見られます。
"""

# ============================================================================
# 設定
# ============================================================================

import os

# DB接続情報
# 環境変数 DB_URL があればそれを使う（Docker Compose で渡される）
# 無ければローカル開発用のデフォルト値を使う（WSL2 の PostgreSQL）
DB_URL = os.environ.get("DB_URL", "host=127.0.0.1 port=5432 dbname=devicehub user=postgres password=devicehub")

# APIキー（認証用。本来は環境変数で管理すべきだが、学習用に固定値）
API_KEY = "my-secret-key"


# ============================================================================
# インポート
# ============================================================================

from datetime import datetime

# psycopg: PostgreSQL に接続するための Python ドライバ
# pip install "psycopg[binary]" でインストール済み
import psycopg

# dict_row: 検索結果を「タプル」ではなく「辞書」で受け取るためのヘルパー
# これを使うと row[0] ではなく row["id"] のようにカラム名でアクセスできる
from psycopg.rows import dict_row

# psycopg.errors: DB固有のエラー（制約違反等）を判別するため
# UniqueViolation     → UNIQUE制約違反（hostname重複）
# ForeignKeyViolation → FOREIGN KEY制約違反（存在しないuser_id）
from psycopg.errors import UniqueViolation, ForeignKeyViolation

# FastAPI: Web API フレームワーク
from fastapi import FastAPI, HTTPException, Response, status, Header

# Pydantic: リクエスト/レスポンスのデータ形（スキーマ）を定義
from pydantic import BaseModel, Field

from typing import Optional


# ============================================================================
# データモデル（Pydantic BaseModel）
# ============================================================================
# v0.1 と同じ役割だが、cpu フィールドを追加した（DBに cpu カラムがあるため）


class UserCreate(BaseModel):
    """ユーザー作成時のリクエストボディ。"""
    name: str = Field(..., min_length=1, max_length=100, description="ユーザー名")


class UserResponse(BaseModel):
    """ユーザー作成後のレスポンスボディ。"""
    id: int
    name: str
    created_at: datetime


class DeviceCreate(BaseModel):
    """デバイス登録時のリクエストボディ。

    v0.2 から cpu フィールドを追加しました。
    cpu は任意（DB で NULL可）なのでデフォルト None にします。
    """
    user_id: int = Field(..., description="このデバイスを所有するユーザーの ID")
    hostname: str = Field(..., min_length=1, max_length=255, description="PC のホスト名")
    os: str = Field(..., max_length=100, description="OS 名（例: Windows 11）")
    cpu: str | None = Field(None, max_length=100, description="CPU 名（例: Core i7）。省略可")
    gpu: str = Field(..., max_length=100, description="GPU 名（例: RTX 3060）")
    memory_gb: int = Field(..., gt=0, description="メモリ容量（GB）。0 より大きい値")


class DevicePatch(BaseModel):
    """デバイス部分更新時のリクエストボディ。全フィールドが任意。"""
    user_id: Optional[int] = None
    hostname: Optional[str] = None
    os: Optional[str] = None
    cpu: Optional[str] = None
    gpu: Optional[str] = None
    memory_gb: Optional[int] = None


class DeviceResponse(BaseModel):
    """デバイス取得時のレスポンスボディ。cpu を追加。"""
    id: int
    user_id: int
    hostname: str
    os: str
    cpu: str | None       # DB では NULL可なので、None が返る可能性がある
    gpu: str
    memory_gb: int
    created_at: datetime


# ============================================================================
# DB接続ヘルパー
# ============================================================================
# 各エンドポイントで DB に接続して操作を行います。
# v0.1 では devices = {} という辞書を使っていた部分が、ここから DB に変わります。


def get_db():
    """DB接続を作成して返す。

    使い方:
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
        finally:
            conn.close()    # ← 忘れると接続が漏洩する

    autocommit=True にしているので、各 SQL文が即座にDBに反映されます。
    （トランザクションを明示的に管理する必要がない。v0.3以降で必要になったら変える）
    """
    conn = psycopg.connect(DB_URL)
    conn.autocommit = True
    return conn


# ============================================================================
# FastAPI アプリケーション
# ============================================================================

app = FastAPI(
    title="DeviceHub",
    version="0.2.0",
    description="Windows PC のデバイス情報を管理する API（PostgreSQL 使用）",
)


# ============================================================================
# エンドポイント（API の入口）
# ============================================================================


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

    v0.1 との違い:
    - _next_user_id += 1 → SERIAL が自動採番
    - datetime.now() → DEFAULT NOW() が自動生成
    - users[id] = data → INSERT INTO users
    """
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # INSERT でデータを作り、RETURNING で作られた行を取り出す
            # %s はパラメータ（後で user.name に置き換わる）
            # SQLインジェクション対策: %s を使うと安全に値が埋め込まれる
            cur.execute(
                "INSERT INTO users (name) VALUES (%s) RETURNING id, name, created_at",
                (user.name,),
            )
            row = cur.fetchone()
            # row は {"id": 1, "name": "seiya", "created_at": ...} の辞書
            return row
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# POST /devices - デバイス登録
# ----------------------------------------------------------------------------
@app.post(
    "/devices",
    response_model=DeviceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="デバイスを登録する",
)
def create_device(
    device: DeviceCreate,
    authorization: str | None = Header(None),
):
    """新しいデバイスを登録します。

    v0.1 との違い:
    - for ループで hostname 重複チェック → DB の UNIQUE 制約が自動チェック
    - if user_id not in users → DB の FOREIGN KEY 制約が自動チェック
    - _next_device_id += 1 → SERIAL が自動採番
    - これらのチェックが DB 側に移ったので、コードがシンプルになった
    """
    # --- 認証チェック（v0.1 と同じ） ---
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization headerがありません",
        )

    token = authorization.split(" ")[1]

    if token != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="APIキーが間違っています",
        )

    # --- DB にデバイスを登録 ---
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            try:
                # INSERT を実行。RETURNING で作成された行を取り出す
                # cpu は None の可能性があるが、そのまま渡せば DB に NULL として入る
                cur.execute(
                    """
                    INSERT INTO devices (user_id, hostname, os, cpu, gpu, memory_gb)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, user_id, hostname, os, cpu, gpu, memory_gb, created_at
                    """,
                    (device.user_id, device.hostname, device.os, device.cpu, device.gpu, device.memory_gb),
                )
                row = cur.fetchone()
                return row

            except UniqueViolation:
                # hostname が重複した場合（UNIQUE制約違反）
                # v0.1 では for ループで自分でチェックしていた部分
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"hostname={device.hostname} のデバイスは既に存在します",
                )

            except ForeignKeyViolation:
                # user_id が存在しない場合（FOREIGN KEY制約違反）
                # v0.1 では if user_id not in users で自分でチェックしていた部分
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"user_id={device.user_id} のユーザーは存在しません",
                )
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# GET /devices - デバイス一覧取得
# ----------------------------------------------------------------------------
@app.get(
    "/devices",
    response_model=list[DeviceResponse],
    summary="デバイス一覧を取得する",
)
def list_devices(os: str | None = None, gpu: str | None = None):
    """登録されている全デバイスの一覧を取得します。

    v0.1 との違い:
    - [devices[key] for key in sorted(devices.keys())] → SELECT ... ORDER BY id
    - if os is not None: で Python 側で絞り込み → WHERE 句で DB 側で絞り込み
    """
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # 基本のクエリ
            query = "SELECT id, user_id, hostname, os, cpu, gpu, memory_gb, created_at FROM devices"
            params = []
            conditions = []

            # クエリパラメータで絞り込み条件を動的に組み立てる
            # v0.1 では Python の if でフィルタしていたが、
            # v0.2 では WHERE 句で DB 側にフィルタさせる（DBの方が速い）
            if os is not None:
                conditions.append("os = %s")
                params.append(os)

            if gpu is not None:
                conditions.append("gpu = %s")
                params.append(gpu)

            # 条件があれば WHERE 句を追加
            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            # ORDER BY で ID 順に並べる（v0.1 の sorted() と同じ）
            query += " ORDER BY id"

            cur.execute(query, params)
            rows = cur.fetchall()
            return rows
    finally:
        conn.close()


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

    v0.1 との違い:
    - if device_id not in devices → SELECT で1件探して、無ければ404
    - return devices[device_id] → SELECT の結果をそのまま返す
    """
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, user_id, hostname, os, cpu, gpu, memory_gb, created_at FROM devices WHERE id = %s",
                (device_id,),
            )
            row = cur.fetchone()

            # 見つからなければ404（v0.1 と同じ判断）
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"device_id={device_id} のデバイスは存在しません",
                )

            return row
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# PUT /devices/{device_id} - デバイス全体更新
# ----------------------------------------------------------------------------
@app.put(
    "/devices/{device_id}",
    response_model=DeviceResponse,
    summary="デバイスを全体更新する",
)
def update_device(device_id: int, device: DeviceCreate):
    """デバイス情報を全体更新します。

    v0.1 との違い:
    - devices[device_id] = {...} → UPDATE ... SET ... WHERE id = ...
    - created_at の保持は DB が自動でやってくれる（UPDATE では created_at を変更しない）
    """
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # まず存在確認
            cur.execute("SELECT id FROM devices WHERE id = %s", (device_id,))
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"device_id={device_id} のデバイスは存在しません",
                )

            try:
                # UPDATE を実行。created_at は更新しない（DB が保持）
                cur.execute(
                    """
                    UPDATE devices
                    SET user_id = %s, hostname = %s, os = %s, cpu = %s, gpu = %s, memory_gb = %s
                    WHERE id = %s
                    RETURNING id, user_id, hostname, os, cpu, gpu, memory_gb, created_at
                    """,
                    (device.user_id, device.hostname, device.os, device.cpu, device.gpu, device.memory_gb, device_id),
                )
                row = cur.fetchone()
                return row

            except UniqueViolation:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"hostname={device.hostname} のデバイスは既に存在します",
                )

            except ForeignKeyViolation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"user_id={device.user_id} のユーザーは存在しません",
                )
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# PATCH /devices/{device_id} - デバイス部分更新
# ----------------------------------------------------------------------------
@app.patch(
    "/devices/{device_id}",
    response_model=DeviceResponse,
    summary="デバイスを部分更新する",
)
def partial_update_device(device_id: int, device: DevicePatch):
    """デバイス情報を部分更新します。送られたフィールドだけ更新します。

    v0.1 との違い:
    - for key, value in device.dict().items() → 動的に UPDATE文を組み立てる
    - 辞書に直接代入 → SQL の SET で更新
    """
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            # 存在確認
            cur.execute("SELECT id FROM devices WHERE id = %s", (device_id,))
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"device_id={device_id} のデバイスは存在しません",
                )

            # 更新するフィールドを動的に組み立てる
            # device.dict() で {"user_id": None, "hostname": "PC-B", ...} の辞書が取れる
            # None でないものだけ更新対象にする
            fields = []
            values = []
            for key, value in device.dict().items():
                if value is not None:
                    fields.append(f"{key} = %s")
                    values.append(value)

            if not fields:
                # 更新するフィールドが無い場合は現在のデータをそのまま返す
                cur.execute(
                    "SELECT id, user_id, hostname, os, cpu, gpu, memory_gb, created_at FROM devices WHERE id = %s",
                    (device_id,),
                )
                return cur.fetchone()

            # 動的に組み立てた UPDATE 文を実行
            # 例: UPDATE devices SET hostname = %s, memory_gb = %s WHERE id = %s
            values.append(device_id)
            query = f"""
                UPDATE devices
                SET {', '.join(fields)}
                WHERE id = %s
                RETURNING id, user_id, hostname, os, cpu, gpu, memory_gb, created_at
            """

            try:
                cur.execute(query, values)
                row = cur.fetchone()
                return row

            except UniqueViolation:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"hostname={device.hostname} のデバイスは既に存在します",
                )

            except ForeignKeyViolation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"user_id={device.user_id} のユーザーは存在しません",
                )
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# DELETE /devices/{device_id} - デバイス削除
# ----------------------------------------------------------------------------
@app.delete(
    "/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="デバイスを削除する",
)
def delete_device(device_id: int):
    """デバイスを削除します。

    v0.1 との違い:
    - del devices[device_id] → DELETE FROM devices WHERE id = ...
    - RETURNING id で削除された行を確認（無ければ404）
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # DELETE しつつ、削除された行の id を取得
            # 削除対象が存在しない場合は RETURNING で何も返らない
            cur.execute("DELETE FROM devices WHERE id = %s RETURNING id", (device_id,))
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"device_id={device_id} のデバイスは存在しません",
                )
    finally:
        conn.close()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------
# GET /health - ヘルスチェック
# ----------------------------------------------------------------------------
@app.get("/health", summary="ヘルスチェック")
def health_check():
    """サーバーが正常に動作しているか確認します。"""
    return {"status": "ok"}
