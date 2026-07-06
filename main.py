import os

from dotenv import load_dotenv

load_dotenv()  # ledger.py 等が import 時に環境変数を読むため、import より前に呼ぶ

from fastapi import FastAPI, HTTPException, Request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from commands import HELP_TEXT, parse_command
from ledger import handle_ledger_event
from stock_lookup import judge_timing

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

# フェイルクローズ: 未設定(空)の場合は誰にも応答しない。
# 初回セットアップ時は一度話しかけてログに出る自分の user_id を設定する(README参照)
ALLOWED_USER_IDS = {
    uid.strip() for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",") if uid.strip()
}

app = FastAPI()
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


@app.get("/")
def health():
    """死活監視用。cron-job.org からの定期 ping で Render のスリープを防ぐ。"""
    return {"status": "ok"}


@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"


def generate_reply(user_text: str, user_id: str) -> str:
    """ユーザーの発言から返信文を作る。

    定型コマンド(commands.parse_command、Gemini不使用)だけを受け付け、
    解釈できない入力にはヘルプを返す。
    台帳・個別銘柄判断は発言者(user_id)ごとに分離される。
    """
    cmd = parse_command(user_text)

    if cmd.kind == "不明":
        return HELP_TEXT

    if cmd.kind == "銘柄判断":
        try:
            return judge_timing(cmd.company, user_id)
        except Exception:
            return "判断の生成に失敗しました。時間をおいてもう一度お試しください。"

    try:
        return handle_ledger_event(cmd, user_id)
    except Exception:
        return "台帳の処理に失敗しました。時間をおいてもう一度お試しください。"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    print(f"user_id: {user_id}")  # 初回セットアップ時、自分のIDをRenderのログから確認するため

    if not ALLOWED_USER_IDS:
        reply = "現在このBotは利用者が設定されていません(ALLOWED_USER_IDS を設定してください)。"
    elif user_id not in ALLOWED_USER_IDS:
        reply = "現在このBotは限定公開です。"
    else:
        reply = generate_reply(event.message.text, user_id)

    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)],
            )
        )
