"""ローカル動作確認用CLI。LINEを経由せず generate_reply() を直接呼び出す。

LINEアプリで送るのと同じ入力に対して、同じ応答がここで確認できる。
"""

from dotenv import load_dotenv

load_dotenv()

from main import generate_reply  # noqa: E402 (load_dotenv を先に実行する必要がある)


def main() -> None:
    print("株スクリーニングBot ローカル検証CLI(終了: Ctrl+C)")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        print()
        print(generate_reply(text))


if __name__ == "__main__":
    main()
