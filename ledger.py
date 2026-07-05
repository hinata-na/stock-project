"""取引台帳(Phase 10a)。設計は DESIGN.md 参照。

ユーザーがLINEで報告する 入金/出金/買い/売り/調整 イベントを Supabase
(無料Postgres)の ledger テーブルに追記し、余力と保有銘柄を導出する。

- 資産情報のためパブリックな本リポジトリには置かない(Supabaseに置く)
- append-only: 訂正は行の削除ではなく「取消」イベントの追記で行う
- 2人利用のため全イベントに user_id(LINEのuser_id)を付け、照会・集計は
  常に本人の行だけに絞る(取消も本人のイベントしか打ち消せない)
- SUPABASE_URL / SUPABASE_KEY が未設定なら is_configured() が False になり、
  呼び出し側(main.py / swing_batch.py)は台帳なしの動作にフォールバックする

テーブル定義(SupabaseのSQL Editorで一度実行):

    create table ledger (
      id bigint generated always as identity primary key,
      created_at timestamptz not null default now(),
      user_id text not null,       -- LINEのuser_id(ALLOWED_USER_IDSと同じ値)
      type text not null,          -- 入金/出金/買い/売り/調整/取消
      amount numeric,              -- 入金/出金/調整の金額(円)
      code text,                   -- 買い/売りの銘柄コード
      name text,                   -- 銘柄名(表示用)
      shares integer,              -- 買い/売りの株数
      price numeric,               -- 買い/売りの単価(円)
      ref_id bigint                -- 取消が打ち消す対象イベントのid
    );

既存テーブルからの移行(user_id列の追加):

    alter table ledger add column user_id text;
    update ledger set user_id = '<本人のLINE user_id>' where user_id is null;
    alter table ledger alter column user_id set not null;
"""

import os
import re

from curl_cffi import requests as cffi_requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

EVENT_TYPES = ("入金", "出金", "買い", "売り", "調整", "取消")


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def fetch_events(user_id: str) -> list[dict]:
    """指定ユーザーの全イベントをid昇順で返す。"""
    resp = cffi_requests.get(
        f"{SUPABASE_URL}/rest/v1/ledger",
        params={"select": "*", "order": "id.asc", "user_id": f"eq.{user_id}"},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def add_event(
    type_: str,
    user_id: str,
    amount: float | None = None,
    code: str | None = None,
    name: str | None = None,
    shares: int | None = None,
    price: float | None = None,
    ref_id: int | None = None,
) -> dict:
    """イベントを1件追記し、追記された行を返す。"""
    assert type_ in EVENT_TYPES, type_
    assert user_id, "user_id は必須(2人利用のため全イベントに付与する)"
    resp = cffi_requests.post(
        f"{SUPABASE_URL}/rest/v1/ledger",
        json={
            "type": type_,
            "user_id": user_id,
            "amount": amount,
            "code": code,
            "name": name,
            "shares": shares,
            "price": price,
            "ref_id": ref_id,
        },
        headers={**_headers(), "Prefer": "return=representation"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()[0]


def compute_state(events: list[dict]) -> dict:
    """イベント列から現在の余力と保有銘柄を導出する(純粋関数)。

    返り値: {"cash": float, "positions": {code: {"name", "shares", "avg_price", "opened_date"}}}
    opened_date はポジションが0株→保有になった買いイベントの登録日(YYYY-MM-DD)。
    実際の約定日ではなく登録日ベースの近似(通常は当日夜に登録される想定)で、
    時間切れ(20営業日)の起点に使う。
    """
    cancelled = {e["ref_id"] for e in events if e["type"] == "取消" and e.get("ref_id")}
    cash = 0.0
    positions: dict[str, dict] = {}

    for e in sorted(events, key=lambda e: e["id"]):
        if e["type"] == "取消" or e["id"] in cancelled:
            continue
        amount = float(e["amount"] or 0)
        if e["type"] == "入金":
            cash += amount
        elif e["type"] == "出金":
            cash -= amount
        elif e["type"] == "調整":
            cash += amount  # 符号付きの差分(絶対額指定はhandle側で差分に変換する)
        elif e["type"] in ("買い", "売り"):
            shares = int(e["shares"] or 0)
            price = float(e["price"] or 0)
            code = e["code"] or ""
            if e["type"] == "買い":
                cash -= shares * price
                pos = positions.setdefault(
                    code,
                    {
                        "name": e.get("name") or code,
                        "shares": 0,
                        "avg_price": 0.0,
                        "opened_date": (e.get("created_at") or "")[:10],
                    },
                )
                total = pos["shares"] + shares
                if total > 0:
                    pos["avg_price"] = (pos["shares"] * pos["avg_price"] + shares * price) / total
                pos["shares"] = total
            else:
                cash += shares * price
                pos = positions.get(code)
                if pos:
                    pos["shares"] -= shares
                    if pos["shares"] <= 0:
                        del positions[code]

    return {"cash": round(cash), "positions": positions}


def current_state(user_id: str) -> dict:
    return compute_state(fetch_events(user_id))


def format_state(state: dict) -> str:
    """LINE返信用に余力と保有を整形する。"""
    lines = [f"余力: {state['cash'] / 10000:,.1f}万円"]
    if state["positions"]:
        lines.append("保有:")
        for code, pos in state["positions"].items():
            lines.append(
                f"・{pos['name']}({code}) {pos['shares']}株 @ {pos['avg_price']:,.1f}円"
            )
    else:
        lines.append("保有: なし")
    return "\n".join(lines)


# 「100株」「1,880円」のような表記は完全に規則的なので、Gemini の抽出漏れは
# 正規表現で補完する(flash-lite が ledger_shares を出力しない事象を観測したため)
_SHARES_PATTERN = re.compile(r"([0-9][0-9,]*)\s*株")
_PRICE_PATTERN = re.compile(r"([0-9][0-9,.]*)\s*円")


def _fill_from_text(conditions, user_text: str):
    """Gemini が取り漏らした株数・単価を発言テキストから補完する。"""
    if conditions.ledger_shares is None:
        m = _SHARES_PATTERN.search(user_text)
        if m:
            conditions.ledger_shares = float(m.group(1).replace(",", ""))
    if conditions.ledger_price is None:
        m = _PRICE_PATTERN.search(user_text)
        if m:
            conditions.ledger_price = float(m.group(1).replace(",", ""))
    return conditions


def handle_ledger_event(conditions, user_id: str, user_text: str = "") -> str:
    """LINEで解析済みの台帳イベントを処理し、返信文を返す。

    conditions は screening.ScreeningConditions(ledger_* フィールド付き)。
    user_id は発言者(LINEのuser_id)。台帳の読み書きは本人の行だけに閉じる。
    user_text は補完用の元発言(株数・単価の正規表現フォールバック)。
    """
    if conditions.ledger_event in ("買い", "売り") and user_text:
        conditions = _fill_from_text(conditions, user_text)
    if not is_configured():
        return (
            "台帳(Supabase)が未設定のため、この機能はまだ使えません。\n"
            "READMEの「取引台帳のセットアップ」を参照してください。"
        )

    ev = conditions.ledger_event

    if ev == "余力照会":
        return format_state(current_state(user_id))

    if ev == "取消":
        events = fetch_events(user_id)
        cancelled = {e["ref_id"] for e in events if e["type"] == "取消" and e.get("ref_id")}
        active = [e for e in events if e["type"] != "取消" and e["id"] not in cancelled]
        if not active:
            return "取り消せるイベントがありません。"
        last = active[-1]
        add_event("取消", user_id, ref_id=last["id"])
        detail = f"{last['type']} " + (
            f"{last['name']}({last['code']}) {last['shares']}株 @ {last['price']}円"
            if last["type"] in ("買い", "売り")
            else f"{float(last['amount'] or 0) / 10000:,.1f}万円"
        )
        return f"直前のイベントを取り消しました: {detail}\n\n{format_state(current_state(user_id))}"

    if ev in ("入金", "出金"):
        if not conditions.ledger_amount:
            return f"{ev}額が読み取れませんでした。「50万円{ev}した」のように金額を含めてください。"
        add_event(ev, user_id, amount=conditions.ledger_amount)
        return f"{ev} {conditions.ledger_amount / 10000:,.1f}万円 を記録しました。\n\n{format_state(current_state(user_id))}"

    if ev == "調整":
        if conditions.ledger_amount is None:
            return "修正後の余力額が読み取れませんでした。「余力を52万円に修正」のように指定してください。"
        current = current_state(user_id)
        diff = conditions.ledger_amount - current["cash"]
        add_event("調整", user_id, amount=diff)
        return (
            f"余力を {conditions.ledger_amount / 10000:,.1f}万円 に修正しました"
            f"(調整額 {diff / 10000:+,.1f}万円)。\n\n{format_state(current_state(user_id))}"
        )

    if ev in ("買い", "売り"):
        from stock_lookup import resolve_company

        if not conditions.company_name:
            return f"銘柄が読み取れませんでした。「7203を1880円で100株{ev[0]}った」のように銘柄・単価・株数を含めてください。"
        if not conditions.ledger_shares or not conditions.ledger_price:
            return f"株数または単価が読み取れませんでした。「1880円で100株」のように両方を含めてください。"
        matches = resolve_company(conditions.company_name)
        if not matches:
            return f"「{conditions.company_name}」に該当する銘柄が見つかりませんでした。"
        if len(matches) > 1:
            names = "、".join(f"{m['name']}({m['code']})" for m in matches[:5])
            return f"候補が複数あります。銘柄コードで指定してください: {names}"
        stock = matches[0]
        shares = int(conditions.ledger_shares)
        add_event(
            ev,
            user_id,
            code=stock["code"],
            name=stock["name"],
            shares=shares,
            price=conditions.ledger_price,
        )
        total = shares * conditions.ledger_price
        return (
            f"{ev}: {stock['name']}({stock['code']}) "
            f"{shares}株 @ {conditions.ledger_price:,.1f}円"
            f"(約{total / 10000:,.1f}万円)を記録しました。\n\n{format_state(current_state(user_id))}"
        )

    return "台帳イベントを解釈できませんでした。"
