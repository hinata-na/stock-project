"""ローカル動作確認用CLI。LINEを経由せず generate_reply() を直接呼び出す。

LINEアプリで送るのと同じ入力に対して、同じ応答がここで確認できる。
擬似ユーザーIDは CLI_USER_ID(未設定なら ALLOWED_USER_IDS の先頭、
それも無ければ "local")。2人分の台帳分離の確認に切り替えて使う。
"""

import os

from dotenv import load_dotenv

load_dotenv()

from main import generate_reply  # noqa: E402 (load_dotenv を先に実行する必要がある)


def _user_id() -> str:
    if os.environ.get("CLI_USER_ID"):
        return os.environ["CLI_USER_ID"]
    allowed = [u.strip() for u in os.environ.get("ALLOWED_USER_IDS", "").split(",") if u.strip()]
    return allowed[0] if allowed else "local"


def main() -> None:
    user_id = _user_id()
    print(f"株スクリーニングBot ローカル検証CLI(user_id: {user_id}、終了: Ctrl+C)")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        print()
        print(generate_reply(text, user_id))


if __name__ == "__main__":
    main()
