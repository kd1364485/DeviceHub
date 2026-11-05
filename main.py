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

# APIキー（認証用）
# 環境変数 API_KEY があればそれを使う。無ければ学習用の固定値（本番では必ず環境変数で渡す）
API_KEY = os.environ.get("API_KEY", "my-secret-key")

# 最後の heartbeat からこの秒数以内なら ONLINE、超えたら OFFLINE
# エージェントは 30 秒ごとに送るので、1 回取りこぼしても ONLINE のままになる長さにしている
ONLINE_THRESHOLD_SECONDS = int(os.environ.get("ONLINE_THRESHOLD_SECONDS", "60"))


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
from fastapi import Depends, FastAPI, HTTPException, Response, status, Header

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
    """デバイス取得時のレスポンスボディ。cpu を追加。

    v1.0 から、最後の heartbeat の時刻と、そこから判定した状態（ONLINE / OFFLINE）を返す。
    """
    id: int
    user_id: int
    hostname: str
    os: str
    cpu: str | None       # DB では NULL可なので、None が返る可能性がある
    gpu: str
    memory_gb: int
    created_at: datetime
    last_heartbeat_at: datetime | None   # 一度も heartbeat が無ければ None
    status: str                          # "ONLINE" または "OFFLINE"


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


# デバイスを、最後の heartbeat と状態つきで取り出す SELECT。
# 1 つ目の %s は ONLINE とみなす秒数（ONLINE_THRESHOLD_SECONDS）。
#
# LEFT JOIN LATERAL: デバイス 1 行ごとに、そのデバイスの heartbeat の最新時刻を求める。
#   heartbeat が 1 件も無いデバイスも残す（LEFT JOIN）。その場合 last_heartbeat_at は NULL → OFFLINE。
# 判定は DB の NOW() と比べる。アプリと DB で時計やタイムゾーンがずれても、判定がぶれない。
DEVICE_SELECT = """
    SELECT d.id, d.user_id, d.hostname, d.os, d.cpu, d.gpu, d.memory_gb, d.created_at,
           hb.last_heartbeat_at,
           CASE WHEN hb.last_heartbeat_at >= NOW() - make_interval(secs => %s)
                THEN 'ONLINE' ELSE 'OFFLINE' END AS status
    FROM devices d
    LEFT JOIN LATERAL (
        SELECT MAX(created_at) AS last_heartbeat_at
        FROM heartbeats
        WHERE device_id = d.id
    ) hb ON true
"""


def select_device(cur, device_id: int):
    """1 台のデバイスを、状態つきで取り出す。無ければ None。"""
    cur.execute(DEVICE_SELECT + " WHERE d.id = %s", (ONLINE_THRESHOLD_SECONDS, device_id))
    return cur.fetchone()


# ============================================================================
# 認証ヘルパー
# ============================================================================
# データを変更するエンドポイント（POST / PUT / PATCH / DELETE）すべてで使う。
# 以前は POST /devices の中だけに認証チェックがあり、PUT / PATCH / DELETE は
# 誰でも呼べてしまっていた。Depends にして、付け忘れを防ぐ。


def require_api_key(authorization: str | None = Header(None)) -> None:
    """Authorization: Bearer <APIキー> を確認する。違えば 401 にする。"""
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization headerがありません",
        )

    # "Bearer <キー>" の形でなければ 401（以前は split(" ")[1] で 500 になっていた）
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or token != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="APIキーが間違っています",
        )


# ============================================================================
# FastAPI アプリケーション
# ============================================================================

app = FastAPI(
    title="DeviceHub",
    version="1.0.0",
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
    dependencies=[Depends(require_api_key)],
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
    dependencies=[Depends(require_api_key)],
)
def create_device(device: DeviceCreate):
    """新しいデバイスを登録します。

    v0.1 との違い:
    - for ループで hostname 重複チェック → DB の UNIQUE 制約が自動チェック
    - if user_id not in users → DB の FOREIGN KEY 制約が自動チェック
    - _next_device_id += 1 → SERIAL が自動採番
    - これらのチェックが DB 側に移ったので、コードがシンプルになった
    - 認証は require_api_key（dependencies）で、関数に入る前に確認する
    """
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
                    RETURNING id
                    """,
                    (device.user_id, device.hostname, device.os, device.cpu, device.gpu, device.memory_gb),
                )
                new_id = cur.fetchone()["id"]
                # 状態（last_heartbeat_at / status）つきで返すため、改めて取り出す
                return select_device(cur, new_id)

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
            # 基本のクエリ（状態つき。1 つ目のパラメータは ONLINE とみなす秒数）
            query = DEVICE_SELECT
            params = [ONLINE_THRESHOLD_SECONDS]
            conditions = []

            # クエリパラメータで絞り込み条件を動的に組み立てる
            # v0.1 では Python の if でフィルタしていたが、
            # v0.2 では WHERE 句で DB 側にフィルタさせる（DBの方が速い）
            if os is not None:
                conditions.append("d.os = %s")
                params.append(os)

            if gpu is not None:
                conditions.append("d.gpu = %s")
                params.append(gpu)

            # 条件があれば WHERE 句を追加
            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            # ORDER BY で ID 順に並べる（v0.1 の sorted() と同じ）
            query += " ORDER BY d.id"

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
            row = select_device(cur, device_id)

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
    dependencies=[Depends(require_api_key)],
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
                    """,
                    (device.user_id, device.hostname, device.os, device.cpu, device.gpu, device.memory_gb, device_id),
                )
                return select_device(cur, device_id)

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
    dependencies=[Depends(require_api_key)],
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
                return select_device(cur, device_id)

            # 動的に組み立てた UPDATE 文を実行
            # 例: UPDATE devices SET hostname = %s, memory_gb = %s WHERE id = %s
            values.append(device_id)
            query = f"""
                UPDATE devices
                SET {', '.join(fields)}
                WHERE id = %s
            """

            try:
                cur.execute(query, values)
                return select_device(cur, device_id)

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
    dependencies=[Depends(require_api_key)],
)
def delete_device(device_id: int):
    """デバイスを削除します。

    v0.1 との違い:
    - del devices[device_id] → DELETE FROM devices WHERE id = ...
    - RETURNING id で削除された行を確認（無ければ404）
    """
    conn = get_db()
    try:
        # heartbeats は devices を参照している（FOREIGN KEY）。先に heartbeat を消さないと、
        # heartbeat が 1 件でもあるデバイスは消せない（ForeignKeyViolation）。
        # 2 つの DELETE を 1 つのトランザクションにまとめ、途中で失敗したら両方取り消す
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("DELETE FROM heartbeats WHERE device_id = %s", (device_id,))
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
# POST /devices/{device_id}/heartbeat - 生存通知
# ----------------------------------------------------------------------------
@app.post(
    "/devices/{device_id}/heartbeat",
    response_model=DeviceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="デバイスの生存通知（heartbeat）を記録する",
    dependencies=[Depends(require_api_key)],
)
def send_heartbeat(device_id: int):
    """エージェントが定期的に呼ぶ。heartbeats に 1 行追加し、状態つきのデバイスを返す。

    最後の heartbeat から ONLINE_THRESHOLD_SECONDS 秒以内なら ONLINE、超えたら OFFLINE と判定する。
    判定は保存せず、取り出すたびに計算する（「OFFLINE になった」ことを誰かが書き込む必要がない）。
    """
    conn = get_db()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            try:
                cur.execute("INSERT INTO heartbeats (device_id) VALUES (%s)", (device_id,))
            except ForeignKeyViolation:
                # device_id が存在しない（FOREIGN KEY制約違反）
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"device_id={device_id} のデバイスは存在しません",
                )
            return select_device(cur, device_id)
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# GET /health - ヘルスチェック
# ----------------------------------------------------------------------------
@app.get("/health", summary="ヘルスチェック")
def health_check():
    """サーバーが正常に動作しているか確認します。"""
    return {"status": "ok"}
