---
id: P-0001
type: project
status: validated
scope: project:todo-diary
tags: [python, sqlite, stdlib, web, calendar, journal, windows]
stack: [python]
importance: high
evidence: [todo-diary/runs/20261029-1913-todo-diary#V1, todo-diary/runs/20261029-1913-todo-diary#V12]
applied_in: [20261029-1913-todo-diary]
created: 2026-10-29
updated: 2026-10-29
---
# todo-diary の技術と規約

## 適用条件
このプロジェクトのすべての変更。

## 学んだ行動
- 実行時依存ゼロ（Python標準ライブラリ: http.server + sqlite3、素のHTML/CSS/JS）。この開発PCには Node.js と gh がない。依存を足す前に必要性を確認する。
- 検証: `.venv\Scripts\python.exe` で ruff / mypy --strict（todo_app のみ）/ unittest。gate は cmd.exe 経由で実行される。
- 入力検証は todo_app/validation.py に集約。日付は [0-9] の正規表現＋fromisoformat、年は1900〜2999。
- API のエラーは日本語メッセージの JSON `{"error": ...}`、4xx。500は出さない（ファズテストで確認）。
- 画面描画は textContent のみ。カレンダーのマス計算はサーバー側。
- ブラウザ確認では DB を一時ファイルにする（--db か TODO_DB）。data/ はコミットしない。

## 根拠・証拠
初回runの gate で全 verification pass。

## 例外・注意点
GitHub公開は gh 未導入のため未実施（初回run時点）。

## 元の実行記録
todo-diary/runs/20261029-1913-todo-diary
