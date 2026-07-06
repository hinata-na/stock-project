# stock-line-bot

スイング売買の買い候補を毎晩自動抽出して LINE にプッシュ配信し、
LINE の定型コマンドで取引台帳(余力・保有)の管理と個別銘柄の売買判断ができるボット。
利用者は1〜2人の少人数を想定し、台帳・配信はユーザーごとに分離される
(設計と売買ルールの詳細は [DESIGN.md](DESIGN.md) を参照)。

## 構成(すべて無料枠)

| 役割 | 技術 |
|---|---|
| UI | LINE Messaging API(返信は無料・無制限) |
| サーバー | Python (FastAPI) + Render 無料プラン |
| LINE入力の解釈 | 定型コマンドの正規表現パーサ(commands.py、Gemini不使用) |
| 株価データ | yfinance + GitHub Actions 夜間バッチ(Phase 3 で導入) |
| ニュース | TDnet 適時開示(yanoshin WebAPI) + Gemini 感情分析(夜間バッチのみ) |
| 取引台帳 | Supabase 無料枠(Postgres、ユーザー別) |

## ロードマップ

- [x] Phase 1: LINE オウム返しボット(配管の確認)
- [x] Phase 2: Gemini で自然言語 → スクリーニング条件 JSON に変換
- [x] Phase 3: yfinance + 夜間バッチで日本株スクリーニング
- [x] Phase 4: テクニカル指標による売買シグナル + 解説文生成
- [x] Phase 5: 適時開示ニュースの感情スコアをスクリーニング項目に追加
- [x] Phase 6: 個別銘柄を名指しした「買い時・売り時・様子見」の判断
- [x] Phase 7: スイング売買ルールエンジン + バックテスト(swing_rules.py / backtest.py)
- [x] Phase 8: 夜間バッチでの買い候補抽出 + シャドーラン答え合わせ(swing_batch.py)
- [x] Phase 9: 候補カードのLINEプッシュ配信(swing_push.py)
- [x] Phase 10a/10b: LINEで報告する取引台帳(ledger.py + Supabase)と余力連動の候補選定
- [x] Phase 10c: 実保有の出口管理(建値ベースの毎晩の判定)+ 個別銘柄判断のルールエンジン化
- [x] Phase 11: 2人利用対応(台帳・候補選定・配信のユーザー別化)+
  LINE入力の定型コマンド化(自由文スクリーニングを廃止し、LINE経路からGeminiを排除)

## しくみ

```
[GitHub Actions 平日18:30 JST]
  batch.py: JPX銘柄一覧(プライム/スタンダード/グロース、約3,700銘柄) → yfinance で指標取得
    ├ ファンダメンタル: PER/PBR/配当利回り/ROE/時価総額
    ├ テクニカル(indicators.py): MA25/MA75/RSI14 → シグナル判定
    └ ニュース(news.py): TDnet適時開示 → 開示件数・日付を全銘柄に付与
      (Gemini での感情スコア化はスイング候補・追跡中銘柄のみ)
  → swing_batch.py: スイング買い候補の抽出とシャドーラン答え合わせ
    ├ 前日候補の約定・利確/損切り/時間切れを自動判定(data/candidates.csv を更新)
    ├ 地合い(日経平均のMA25)が悪い日は「候補なし」+理由を data/swing_status.json に記録
    ├ 75日高値ブレイク×出来高2倍(swing_rules.py)からユーザーごとに
    │ 本人の余力(台帳)・保有で絞って出来高倍率順に最大3銘柄。決算発表14日以内は除外
    └ 公開ファイルには全ユーザーの候補の和集合のみを記録し、
      余力・保有などの資産情報は data/swing_private.json(コミット対象外)に分離
  → swing_push.py: 候補カード(やさしい説明+専門用語の説明+注文レシピ)を
    ユーザーごとに組み立てて個別にプッシュ(Secrets 未設定の間はスキップ)
  → data/screener.csv 等をコミット & push → Render が自動再デプロイ

[ユーザーがLINEで発言(定型コマンド、Gemini不使用)]
  受け付けるのは以下のみ。解釈できない入力にはヘルプを返す(commands.py)

  「トヨタは今買い時?」「7203は売り時?」(銘柄判断)
  → screener.csv から銘柄を特定 (stock_lookup.py)
  → スイング売買ルール(swing_rules.py)で決定論的に判定:
    ├ 未保有: 買いルールの全条件チェック → 買い候補(注文レシピ付き) or
    │         様子見(満たしていない条件を列挙)
    └ 保有中(本人の台帳に登録あり): 建値ベースの出口判定
              (利確/損切りライン・20営業日の時間切れ)→ 売り時 or 様子見

  「50万入金した」「7203を1880円で100株買った」「余力いくら?」「さっきの取り消して」
  (取引台帳、Supabase設定時)
  → 発言者の台帳(user_id別)に追記し、余力・保有(平均取得単価)を復唱返信
  → 夜間バッチの候補予算が固定30万円→本人の実余力に切り替わり、
    保有銘柄は毎晩の出口判定(ライン到達・時間切れ・決算接近の警告)の対象になる
```

- 取得指標: PER / PBR / 配当利回り / ROE / 時価総額 / 東証33業種
- シグナル判定: ゴールデンクロス / デッドクロス(MA25とMA75のクロス)、
  売られすぎ / 買われすぎ(RSI14が30未満 / 70超)、それ以外は中立
- ニュース(数値特徴量化): 直近7日の TDnet 適時開示から `news_count` / `news_latest` を
  全銘柄に付与(Gemini不要)
  - `news_sentiment` / `news_label`(-1〜1 の感情スコア)は**スイング候補・追跡中銘柄のみ**
    Gemini で採点し、候補カードの材料注記に使う(下記の無料枠制約のため)
- Gemini の利用は**夜間バッチの感情採点の最大1回/日のみ**(モデルは flash-lite、
  news.py: GEMINI_MODEL)。無料枠は 20リクエスト/日(モデル毎、2026-07時点)。
  LINE の応答は定型コマンドパーサとルールエンジンのみで動き、Gemini を使わない
  (かつての自由文スクリーニングは無料枠縮小と2人利用への移行に伴い廃止した)
- 初回はデータがないため、GitHub Actions の `nightly-batch` を手動実行
  (Actions タブ > nightly-batch > Run workflow)するか、
  ローカルで `python batch.py` を実行して CSV をコミットする
- 全市場・約3,700銘柄の取得には**90分前後**かかる(Yahoo側のレート制限を
  避けるため銘柄ごとに間隔を空けて順次取得しているため)。平日毎日実行すると
  月2,000分を超える可能性があるため、**リポジトリはパブリック**にして
  GitHub Actions無料枠(パブリックは無制限)を使う前提としている

## セットアップ手順

### 1. LINE 公式アカウントの作成

1. [LINE Developers](https://developers.line.biz/ja/) にLINEアカウントでログイン
2. プロバイダーを新規作成(名前は任意。例: `stock-bot`)
3. 「Messaging API チャネル」を作成
   - ※ 2024年以降は先に [LINE公式アカウント](https://entry.line.biz/) を作成し、
     設定画面から Messaging API を有効化する流れになる場合あり
4. 控えるもの:
   - **チャネルシークレット**(チャネル基本設定タブ)
   - **チャネルアクセストークン(長期)**(Messaging API設定タブで発行)
5. Messaging API設定タブで以下を設定:
   - 応答メッセージ: **オフ**(自動応答が二重に飛ぶのを防ぐ)
   - Webhook の利用: **オン**

### 1.5. Gemini API キーの取得(夜間バッチの感情採点用)

1. [Google AI Studio](https://aistudio.google.com/apikey) にGoogleアカウントでログイン
2. 「Create API key」でキーを発行(無料枠の範囲で利用)
3. GitHub Actions の Secrets `GEMINI_API_KEY` に設定(ローカルで `batch.py` を
   動かす場合は `.env` にも)。**Renderには不要**(LINE応答はGeminiを使わない)

### 2. GitHub にプッシュ

```
GitHub で空のプライベートリポジトリを作成し、このフォルダを push
```

### 3. Render にデプロイ

1. [Render](https://render.com/) に GitHub アカウントでサインアップ(カード登録不要)
2. New > **Web Service** > GitHub リポジトリを選択
   (`render.yaml` を自動認識。認識されない場合は Blueprint として作成)
3. 環境変数を設定:
   - `LINE_CHANNEL_SECRET`
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `ALLOWED_USER_IDS`(**必須**。下記「利用者を設定する」を参照。
     未設定の間は誰にも応答しない=フェイルクローズ)
4. デプロイ完了後の URL(例: `https://stock-line-bot.onrender.com`)を控える

### 4. Webhook の接続

1. LINE Developers > Messaging API設定 > Webhook URL に
   `https://<RenderのURL>/callback` を設定
2. 「検証」ボタンで成功を確認
3. QR コードから友だち追加し、何か送信 → 返信が来れば成功

### 利用者を設定する(必須)

投資助言的な内容が不特定多数に公開されるのを防ぐため、`ALLOWED_USER_IDS` が
未設定の間は誰にも機能を開放しない(フェイルクローズ)。台帳・配信も
このIDごとに分離されるため、利用者(1〜2人)のIDを必ず設定する。

1. アカウントを非公開のまま運用する(認証リクエストをしない、QRコード/IDを共有しない)
2. 上記の手順3で一度Botに何か送信すると、Renderの **Logs** タブに
   `user_id: U1234...` という行が出るので、そのIDを控える
3. Render の環境変数 `ALLOWED_USER_IDS` にそのIDを設定(2人ならカンマ区切り)
4. 保存すると自動で再デプロイされ、以降は許可したIDのみ応答が返るようになる
   (許可外のユーザーには「現在このBotは限定公開です。」とだけ返す)

### 4.5. スイング候補のLINEプッシュ配信を有効にする(任意)

夜間バッチが GitHub Actions 上で動くため、リポジトリの Secrets に以下を追加すると
毎晩の候補カードがプッシュ配信される(未設定の間は配信だけスキップされ、他は正常動作):

1. GitHub リポジトリ > Settings > Secrets and variables > Actions > New repository secret
2. `LINE_CHANNEL_ACCESS_TOKEN`: チャネルアクセストークン(長期)
3. `ALLOWED_USER_IDS`: 配信先の user_id(複数はカンマ区切り。取得方法は「利用者を設定する」参照)

配信本文はユーザーごとに組み立てられ、本人の余力・保有は本人にしか送られない。
LINE Push API の無料枠は月200通。平日1晩1通×2人でも月45通前後で枠内に収まる。

### 4.6. 取引台帳のセットアップ(任意、Phase 10)

LINEで「50万入金した」「7203を1880円で100株買った」と報告すると余力・保有を記録し、
スイング候補の予算が固定30万円から**本人の実際の余力**に切り替わる。
台帳は発言者の user_id ごとに分離され、2人で使ってもお互いの資産は混ざらない。
未設定の間は台帳機能だけが案内メッセージになり、他は固定予算で動く。

1. [Supabase](https://supabase.com/) にサインアップし、無料プロジェクトを作成
   (リージョンは Tokyo 推奨。無料枠: DB 500MB。1週間アクセスがないと一時停止するが、
   平日毎晩のバッチが読むため実質発動しない)
2. SQL Editor で ledger テーブルを作成(定義は `ledger.py` の docstring のSQLをコピー。
   user_id 列なしの旧テーブルからの移行SQLも同 docstring に記載)
3. Project Settings > API から以下を控える:
   - Project URL → 環境変数 `SUPABASE_URL`
   - `service_role` キー → 環境変数 `SUPABASE_KEY`(**secretキーなので絶対に公開しない**)
4. 上記2つを Render の環境変数と GitHub Actions の Secrets の両方に設定
5. ローカルの `.env` にも書けば `cli.py` から動作確認できる:
   `python cli.py` → 「50万入金した」→「余力いくら?」
   (擬似ユーザーは環境変数 `CLI_USER_ID` で切り替え。未設定なら
   `ALLOWED_USER_IDS` の先頭、それも無ければ `local`)

### 5. スリープ対策(任意)

Render 無料プランは 15 分間アクセスがないとスリープし、復帰に約1分かかる。
[cron-job.org](https://cron-job.org/)(無料)で `https://<RenderのURL>/` に
10 分間隔の GET を設定すると回避できる。

## ローカルでの動作確認

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # 値を記入
.venv\Scripts\uvicorn main:app --reload
# → http://127.0.0.1:8000/ で {"status":"ok"} が返れば OK
```

LINE からの Webhook をローカルで受けたい場合は ngrok 等でトンネルする
(通常は Render に直接デプロイして確認すれば十分)。

### 夜間バッチのローカル確認

```powershell
# 全銘柄だと90分前後かかるので、動作確認だけなら --limit で絞る
.venv\Scripts\python batch.py --limit 50
```

### スイング機能のローカル確認

```powershell
# 売買ルールの単体テスト(pytest 不要)
.venv\Scripts\python test_swing_rules.py

# バックテスト(初回は fetch で3年分の日足を data/backtest_cache/ に取得、約20分)
.venv\Scripts\python backtest.py fetch
.venv\Scripts\python backtest.py run

# 配信文の確認(LINEに送らず表示。夜間バッチ実行後に使う)
.venv\Scripts\python swing_push.py --dry-run
```

### LINEを経由しない対話確認(cli.py)

LINEアプリを使わずに、定型コマンドの応答をその場で確認できる(Gemini不使用)。

```powershell
.venv\Scripts\python cli.py
> トヨタは今買い時?
> 50万入金した
> 余力いくら?
```

## 注意事項

- 売買判断の提示は不特定多数に公開すると投資助言業(金商法)に抵触し得る。
  自分用・少人数用に留めること。
- 本リポジトリはあくまで技術学習・個人利用を目的としたサンプル実装であり、
  投資助言を目的としたものではない。
- 本コードをフォーク・改変して第三者に公開・提供する場合、投資助言業
  (金商法)等の規制対象になり得るかは利用者自身の責任で確認すること。
- yfinance は非公式ライブラリのため、将来壊れた場合は J-Quants API
  (無料プランは12週遅延)への乗り換えを検討する。
