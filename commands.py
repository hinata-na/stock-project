"""LINE入力の定型コマンドパーサ(Gemini不使用・決定論的)。

Gemini無料枠の縮小と2人利用への移行に伴い、LINEの入力は自由文ではなく
以下の定型コマンドに限定する。解釈できない入力にはヘルプを返す。

- 台帳報告: 入金/出金(「50万入金した」)、買い/売り(「7203を1880円で100株買った」)、
  調整(「余力を52万円に修正」)、取消(「さっきの取り消して」)
- 余力照会: 「余力いくら?」「保有見せて」
- 銘柄判断: 銘柄コードまたは screener.csv の銘柄名を含む発言(「トヨタは買い時?」)
"""

import re
from dataclasses import dataclass

# 「50万」「52.5万円」「500000円」を円に正規化する
_AMOUNT_PATTERN = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(万円|万|円)")
_SHARES_PATTERN = re.compile(r"([0-9][0-9,]*)\s*株")
_PRICE_PATTERN = re.compile(r"([0-9][0-9,.]*)\s*円")
# \b は日本語(も単語文字扱い)との間で効かないため lookaround で区切る。
# 「1880円」「1000株」のような数値表記を銘柄コードと誤認しないよう 円/株 も除外
_CODE_PATTERN = re.compile(r"(?<![0-9A-Za-z])([0-9][0-9A-Z]{3})(?![0-9A-Za-z円株])")

HELP_TEXT = """使い方(定型コマンド):
・銘柄の判断: 「トヨタは買い時?」「7203は売り時?」
・買い報告: 「7203を1880円で100株買った」
・売り報告: 「7203を1950円で100株売った」
・入金/出金: 「50万入金した」「10万円出金した」
・余力の修正: 「余力を52万円に修正」
・照会: 「余力いくら?」「保有見せて」
・直前の取消: 「さっきの取り消して」"""


@dataclass
class Command:
    kind: str  # 入金/出金/買い/売り/調整/取消/余力照会/銘柄判断/不明
    amount: float | None = None   # 入金/出金/調整の金額(円)
    company: str | None = None    # 買い/売り/銘柄判断の銘柄名またはコード
    shares: int | None = None     # 買い/売りの株数
    price: float | None = None    # 買い/売りの単価(円)


def _parse_amount(text: str) -> float | None:
    m = _AMOUNT_PATTERN.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return value * 10000 if "万" in m.group(2) else value


def _extract_company(text: str) -> str | None:
    """発言から銘柄コードまたは銘柄名らしき部分を取り出す。"""
    m = _CODE_PATTERN.search(text)
    if m:
        return m.group(1)
    # 「トヨタを…」「トヨタは買い時?」のような助詞の手前を銘柄名とみなす
    for particle in ("を", "は", "って", "、"):
        if particle in text:
            text = text.split(particle, 1)[0]
    return text.strip() or None


def parse_command(text: str) -> Command:
    text = text.strip()

    if "取り消" in text or "取消" in text:
        return Command(kind="取消")

    # 買い/売り: 株数と単価の両方があるものだけ報告とみなす
    # (「トヨタは買い時?」のような判断の質問と区別するため)
    shares_m = _SHARES_PATTERN.search(text)
    if shares_m and ("買" in text or "売" in text):
        price_m = _PRICE_PATTERN.search(text)
        if price_m:
            price = float(price_m.group(1).replace(",", ""))
            return Command(
                kind="買い" if "買" in text else "売り",
                company=_extract_company(text),
                shares=int(shares_m.group(1).replace(",", "")),
                price=price,
            )

    amount = _parse_amount(text)

    if "余力" in text and amount is not None and any(w in text for w in ("修正", "調整", "変更", "にして")):
        return Command(kind="調整", amount=amount)

    if "入金" in text:
        return Command(kind="入金", amount=amount)
    if "出金" in text:
        return Command(kind="出金", amount=amount)

    if any(w in text for w in ("余力", "保有", "ポジション", "資産")):
        return Command(kind="余力照会")

    # 上記以外は銘柄判断の質問として解釈を試みる。
    # 銘柄コード形式か、screener.csv の銘柄名に一致する場合のみ成立
    company = _extract_company(text)
    if company:
        if _CODE_PATTERN.fullmatch(company):
            return Command(kind="銘柄判断", company=company)
        from stock_lookup import resolve_company

        if resolve_company(company):
            return Command(kind="銘柄判断", company=company)

    return Command(kind="不明")
