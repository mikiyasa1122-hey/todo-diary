# カレンダーTODO（日誌つき）

カレンダーでタスクを管理し、毎日の日誌を書けるTODOアプリです。
Python の標準ライブラリだけで動くので、追加のインストールは要りません。データは手元の SQLite ファイルに保存されます。

## できること

- **タスク**: 日付を選んで追加、完了チェック、編集（タイトル・期日・メモ）、削除
- **カレンダー**: 月表示（日曜始まり）。日ごとに「未完了」「完了」「日誌あり」「期限切れ」のバッジを表示。前月・次月・今日へ移動
- **期限切れ一覧**: 期日を過ぎた未完了タスクをまとめて表示し、クリックでその日へ移動
- **日誌**: 1日1件。保存（ボタンか Ctrl+S）で更新、空で保存すると削除。未来の日付には書けません。保存せずに日付を移ろうとすると確認が出ます

## 起動

Python 3.10 以上が必要です。

```bash
python -m todo_app
```

ブラウザで `http://127.0.0.1:8000/` が開きます。終了は Ctrl+C。

| オプション | 説明 |
|---|---|
| `--port 8000` | 待ち受けポート |
| `--db data/todo.db` | データファイルの場所（環境変数 `TODO_DB` でも指定可） |
| `--no-browser` | 起動時にブラウザを開かない |

データは既定で `data/todo.db` に保存されます。バックアップはこのファイル（と同じ場所の `-wal` `-shm`）をコピーしてください。

## 安全のための仕様

自分のPCで1人で使う前提です。

- `127.0.0.1` でのみ待ち受け、同じネットワークの他の端末からは見えません
- 他のWebサイトからの書き込み（CSRF・DNSリバインディング）を Host / Origin / Content-Type の検査で拒否します
- 画面はすべて `textContent` で描画し、HTMLを解釈しません（XSS対策）。CSPで外部スクリプトも禁止しています

## 開発

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

| ファイル | 役割 |
|---|---|
| `todo_app/validation.py` | 入力チェック（日付・タイトル・本文・真偽値） |
| `todo_app/store.py` | SQLite への保存、月カレンダーの集計 |
| `todo_app/server.py` | HTTP API と静的ファイル配信 |
| `todo_app/static/` | 画面（HTML / CSS / JS） |
| `tests/` | 単体・API・不正入力・セキュリティ・画面の静的チェック |

### API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/today` | サーバー側の今日の日付 |
| GET | `/api/calendar?month=YYYY-MM` | 6週分のマスと日ごとの件数 |
| GET | `/api/tasks?from=YYYY-MM-DD&to=YYYY-MM-DD` | 期間内のタスク（最大366日） |
| POST | `/api/tasks` | `{title, due_date, notes?}` で追加 |
| GET | `/api/tasks/overdue` | 期限切れの未完了タスク |
| GET / PATCH / DELETE | `/api/tasks/{id}` | 取得・部分更新（`title` `due_date` `notes` `done`）・削除 |
| GET | `/api/journal?from=&to=` | 日誌がある日付の一覧 |
| GET / PUT | `/api/journal/{YYYY-MM-DD}` | 日誌の取得・保存（`{content}`、空なら削除） |
