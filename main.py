import os

from dotenv import load_dotenv
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

from screener import run_screening
from screening import format_conditions, generate_commentary, parse_screening_conditions

load_dotenv()

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

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


def generate_reply(user_text: str) -> str:
    """ユーザーの発言から返信文を作る。

    Gemini で自然言語をスクリーニング条件に変換し、
    夜間バッチで生成済みの銘柄データをフィルタして結果を返す。
    """
    try:
        conditions = parse_screening_conditions(user_text)
    except Exception:
        return "条件の解析に失敗しました。時間をおいてもう一度お試しください。"

    summary = format_conditions(conditions)
    if not summary:
        return (
            "条件を認識できませんでした。\n"
            "例:「PER15倍以下で配当利回り3%以上の自動車株」"
        )

    result_text, rows = run_screening(conditions)
    reply = f"■認識した条件\n{summary}\n\n■結果\n{result_text}"

    if rows:
        try:
            commentary = generate_commentary(rows)
        except Exception:
            commentary = ""
        if commentary:
            reply += f"\n\n■AIによる解説\n{commentary}"

    return reply


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    reply = generate_reply(event.message.text)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)],
            )
        )
