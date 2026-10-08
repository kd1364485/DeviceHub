# DeviceHub

Windows PC のデバイス情報（ホスト名・OS・CPU・GPU・メモリ）を、C++ 製エージェントが収集し、FastAPI バックエンド経由で PostgreSQL に登録・管理するシステムです。

クライアント（C++ / WinHTTP）→ API サーバー（FastAPI）→ DB（PostgreSQL）→ インフラ（Docker / AWS）→ CI（GitHub Actions）まで、Web システムの一通りの層を自分で実装した学習プロジェクトです。

## 構成図

```
Windows PC                          サーバー側
┌──────────────────┐   HTTP/JSON   ┌─────────────────────────┐
│ agent.exe        │ ─────────────▶│ FastAPI (uvicorn :8000) │
│ ・Windows API で │◀───────────── │  ・REST API             │
│   PC情報を収集    │   201/200/404 │  ・APIキー認証           │
│ ・WinHTTP で送信 │               └───────────┬─────────────┘
└──────────────────┘                           │ psycopg
                                               ▼
                                     ┌──────────────────────┐
                                     │ PostgreSQL :5432     │
                                     │  users / devices /   │
                                     │  heartbeats          │
                                     └──────────────────────┘
```

### AWS 構成（本番デプロイ）

```
Windows PC (agent.exe)
      │
      │ HTTP over Internet
      ▼
┌─────────────────────────────────┐
│ VPC                             │
│  ┌───────────┐   ┌────────────┐ │
│  │ EC2       │──▶│ RDS        │ │
│  │ FastAPI   │   │ PostgreSQL │ │
│  └───────────┘   └────────────┘ │
│  Security Group で FW 制御       │
└─────────────────────────────────┘
```

## 技術スタックと選定理由

| 技術 | 用途 | 選定理由 |
|------|------|----------|
| C++ / WinHTTP / Windows API | デバイスエージェント | Windows に標準搭載の API のみで完結させ、追加ライブラリ不要の単一 exe にしたかったため。OS レベルの情報取得（レジストリ・メモリ情報）が直接できる |
| Python / FastAPI | バックエンド API | 型ヒントベースのバリデーション（Pydantic）と Swagger UI の自動生成により、少ないコードで仕様が明確な API を作れるため |
| PostgreSQL / psycopg3 | 永続化 | リレーショナル設計（FK・UNIQUE・CHECK 制約）を業務レベルで扱う練習のため。ORM を使わず生 SQL で書き、制約違反を例外としてハンドリングする設計にした |
| Docker / Docker Compose | 開発・本番環境 | `docker compose up` 一発で API + DB を再現できるようにし、「自分の PC でしか動かない」状態を解消するため |
| AWS（EC2 / RDS / VPC / Security Group） | 本番ホスティング | ローカル PC を切っても動く環境にするため。RDS にすることで DB の運用（バックアップ・パッチ適用）をマネージドに任せる構成を経験した |
| GitHub Actions | CI | push / PR 時に pytest を自動実行し、PostgreSQL サービスコンテナで実 DB を使ったテストを回すため |

## API

Swagger UI で `/docs` から対話的に確認できます。

| メソッド | パス | 説明 | 認証 |
|---------|------|------|------|
| POST | `/users` | ユーザー作成 | 不要 |
| POST | `/devices` | デバイス登録 | APIキー必須 |
| GET | `/devices` | デバイス一覧（`?os=` `?gpu=` で絞り込み可） | 不要 |
| GET | `/devices/{id}` | 個別取得 | 不要 |
| PUT | `/devices/{id}` | 全体更新 | 不要 |
| PATCH | `/devices/{id}` | 部分更新 | 不要 |
| DELETE | `/devices/{id}` | 削除 | 不要 |
| GET | `/health` | ヘルスチェック | 不要 |

### エージェントの動作フロー

```
agent.exe 起動
   │
   ├─ Windows API で hostname / OS / CPU / GPU / RAM を収集
   │
   ├─ GET /devices で自分の hostname が登録済みか確認
   │
   ├─ 未登録 → POST /users → POST /devices (Authorization: Bearer <key>)
   │
   └─ 登録済み → PUT /devices/{id} で最新情報に更新
   （同じ PC を何度起動しても重複登録されない）
```

## データベース設計

```
users                    devices                  heartbeats
┌────────────┐           ┌──────────────────┐     ┌────────────┐
│ id      PK │───┐       │ id           PK  │     │ id      PK │
│ name       │   └──────▶│ user_id      FK  │────▶│ device_id FK│
│ created_at │           │ hostname  UNIQUE │     │ created_at │
└────────────┘           │ os               │     └────────────┘
                         │ cpu (NULL可)      │
                         │ gpu               │
                         │ memory_gb CHECK   │
                         │ created_at        │
                         └──────────────────┘
```

設計上のポイント：

- `hostname` に UNIQUE 制約を持たせ、重複登録を DB 側で保証（アプリ側で事前チェックしない）
- `user_id` / `device_id` は FOREIGN KEY で親テーブルの存在を保証
- `memory_gb` は CHECK 制約で `> 0` を強制
- 制約違反は psycopg の `UniqueViolation` / `ForeignKeyViolation` を捕捉し、それぞれ 400 / 404 の HTTP エラーに変換

## Docker 構成

`docker-compose.yml` で 2 サービスを定義しています。

| サービス | イメージ | 役割 |
|----------|----------|------|
| `backend` | Dockerfile からビルド（python:3.12-slim） | FastAPI + uvicorn |
| `db` | postgres:16 | PostgreSQL |

- `pgdata` ボリュームで DB データを永続化
- `schema.sql` を `/docker-entrypoint-initdb.d/` にマウントし、初回起動時にテーブルを自動作成
- `backend` の `DB_URL` は `host=db` — Compose のサービス名でコンテナ間通信

## AWS 構成

- **EC2**: FastAPI をホスティング。ローカル PC をシャットダウンしても API が動き続ける
- **RDS（PostgreSQL）**: マネージド DB。ローカルの PostgreSQL コンテナと同じ `schema.sql` を適用
- **VPC / Security Group**: EC2 は 8000 番（API）を公開、RDS は EC2 の SG からの接続のみ許可し、DB をインターネットに露出しない構成
- **IAM**: 最小権限の考え方に沿って、普段使いのユーザーを root と分離

エージェント側は `API_HOST` を EC2 の IP に変えるだけで、同一コードがローカル／AWS 両方で動きます。

## CI/CD

`.github/workflows/test.yml` で push / PR 時に自動テストを実行しています。

```
push / PR to main
   │
   ▼
actions/checkout
   │
   ▼
setup-python 3.12
   │
   ▼
pip install -r requirements.txt
   │
   ▼
PostgreSQL 16 サービスコンテナ起動（ヘルスチェック付き）
   │
   ▼
schema.sql でテーブル作成
   │
   ▼
pytest test_main.py -v   ← 実 DB への書き込みを含むテスト
```

テストは認証エラー（401）・バリデーションエラー（422）・404 に加え、ユーザー作成 → デバイス登録 → 一覧取得という実 DB を使った結合テストまでカバーしています。

## 苦労した点と解決策

### 1. Docker コンテナから PostgreSQL に接続できない

`backend` コンテナの `DB_URL` を `host=127.0.0.1` にしていたため接続に失敗しました。

- **原因**: コンテナ内の `localhost` はホストではなくコンテナ自身を指す
- **解決**: Compose のコンテナネットワーク上でサービス名 `host=db` を使う。環境変数で `DB_URL` を切り替える設計にして、ローカル開発（WSL2 直起動）と Docker で同じコードを使い回せるようにした

### 2. Windows バージョンが正しく取れない

`GetVersionEx` はマニフェストの互換性設定により Windows 10 の値を返すことがあり、Windows 11 と区別できませんでした。

- **解決**: `ntdll.dll` の `RtlGetVersion` を `GetProcAddress` で動的に取得して呼び出し、ビルド番号（>= 22000 なら Windows 11）で判定

### 3. WinHTTP が localhost に接続できない

デフォルト設定（`WINHTTP_ACCESS_TYPE_DEFAULT_PROXY`）だと Windows のプロキシ設定が使われ、プロキシ環境では `127.0.0.1` への接続すら失敗します。

- **解決**: `WINHTTP_ACCESS_TYPE_NO_PROXY` を指定して常に直接接続するようにした

### 4. C++ に標準 JSON ライブラリがない

追加ライブラリを入れず単一 exe に収めたかったため、JSON を手組み立てしています。

- **解決**: `"` と `\` をエスケープする `json_escape` を実装してリクエストボディを生成。レスポンスは `"hostname":"<自PC名>"` の直前の `"id":` を探す簡易パーサーで対応（入力が FastAPI の固定フォーマットに限られるため許容と判断）

### 5. v0.1（メモリ内辞書）から PostgreSQL への移行

v0.1 では Python の辞書で保持し、重複チェック・存在チェックを `for` ループと `if` で自前実装していました。

- **解決**: UNIQUE / FOREIGN KEY / CHECK 制約にロジックを委譲し、アプリ側は psycopg の例外を HTTP エラーに写像するだけに簡素化。サーバー再起動でデータが消える問題も解消

## 今後の改善点（残課題）

- `heartbeats` テーブルは作成済みだが、エージェント側の定期 heartbeat 送信と `POST /devices/heartbeat` エンドポイントは未実装
- API キーがコード内の固定値 → 環境変数・シークレット管理への移行
- エージェント ⇔ EC2 間が HTTP 平文 → HTTPS 化（ALB + ACM、またはリバースプロキシ）
- デプロイは手作業 → GitHub Actions からの自動デプロイ（build → push → deploy）への拡張

## セットアップ

```bash
# Docker で全部起動（API + PostgreSQL）
docker compose up --build

# API ドキュメント
# http://127.0.0.1:8000/docs

# テスト（要 PostgreSQL）
python -m pytest test_main.py -v

# エージェントのビルド（MSYS2 UCRT64）
g++ -static agent/agent.cpp -o agent/agent.exe -lwinhttp
```

## このプロジェクトで学んだこと

- **ネットワーク**: DNS → TCP → TLS → HTTP の階層を意識しながら、WinHTTP で生の HTTP リクエストを組み立てる経験
- **API 設計**: 200/201/400/401/404/422 の使い分け、PUT（全体更新）と PATCH（部分更新）の違いを実装レベルで理解
- **DB**: 制約を「DB の仕事」として設計に組み込むことでアプリコードが単純になることを実感
- **Docker**: コンテナ・ネットワーク・ボリューム・環境変数による設定の外部化
- **AWS**: EC2 / RDS / VPC / Security Group / IAM を組み合わせた最小構成の本番環境
- **CI**: 実 DB を使うテストを GitHub Actions のサービスコンテナで自動化
