"""Start the app: python -m todo_app [--port 8000] [--db data/todo.db]."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import webbrowser
from pathlib import Path

from .server import make_server
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="todo_app", description="日誌つきカレンダーTODOアプリ")
    parser.add_argument("--port", type=int, default=8000, help="待ち受けポート（既定: 8000）")
    parser.add_argument(
        "--db",
        default=os.environ.get("TODO_DB", str(Path("data") / "todo.db")),
        help="SQLiteファイルの場所（既定: 環境変数 TODO_DB か data/todo.db）",
    )
    parser.add_argument("--no-browser", action="store_true", help="起動時にブラウザを開かない")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("ポートは 0〜65535 で指定してください")

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    try:
        server = make_server(store, port=args.port)
    except OSError as exc:
        print(f"ポート {args.port} で起動できません: {exc}", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"起動しました: {url}  （データ: {db_path.resolve()}）  Ctrl+C で終了")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
