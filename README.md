# stock-line-bot

LINE から自然言語で日本株のスクリーニング・売買判断の材料を得るボット。
Phase 7 以降はスイング売買の買い候補を毎晩自動抽出して LINE にプッシュ配信する
(設計と売買ルールの詳細は [DESIGN.md](DESIGN.md) を参照)。

## 構成(すべて無料枠)

| 役割 | 技術 |
|---|---|
| UI | LINE Messaging API(返信は無料・無制限) |
| サーバー | Python (FastAPI) + Render 無料プラン |
| 自然言語の解釈 | Gemini API 無料枠(Phase 2 で導入) |
| 株価データ | yfinance + GitHub Actions 夜間バッチ(Phase 3 で導入) |
| ニュース | TDnet 適時開示(yanoshin WebAPI) + Gemini 感情分析(Phase 5 で導入) |

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
- [ ] Phase 10: 保有ポジションの登録と出口管理(保存先の設計が未決着)

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
    └ 75日高値ブレイク×出来高2倍(swing_rules.py)から出来高倍率順に最大3銘柄。
      決算発表14日以内は除外
  → swing_push.py: 候補カード(やさしい説明+専門用語の説明+注文レシピ)を
    LINE にプッシュ(Secrets 未設定の間はスキップ)
  → data/screener.csv 等をコミット & push → Render が自動再デプロイ

[ユーザーがLINEで発言]
  「好材料が出ている配当4%以上の株」
  → Gemini が条件JSONに変換 (screening.py: parse_screening_conditions)
  → data/screener.csv を pandas でフィルタ (screener.py)
  → 上位10件 + Gemini による初心者向け解説文 (screening.py: generate_commentary) を返信

[個別銘柄を名指しした場合]
  「トヨタは今買い時?」
  → Gemini が銘柄名を認識 (company_name) → screener.csv から銘柄を特定 (stock_lookup.py)
  → ファンダメンタル(業種平均とのPER/PBR比較) + テクニカル + ニュース感情 +
    チャート形状(直近20日レンジ内の位置・MA25の傾き・陽線陰線日数を都度取得)を集約
  → Gemini が「買い時/売り時/様子見」+ 理由を生成して返信
```

- 取得指標: PER / PBR / 配当利回り / ROE / 時価総額 / 東証33業種
- シグナル判定: ゴールデンクロス / デッドクロス(MA25とMA75のクロス)、
  売られすぎ / 買われすぎ(RSI14が30未満 / 70超)、それ以外は中立
- ニュース(数値特徴量化): 直近7日の TDnet 適時開示から `news_count` / `news_latest` を
  全銘柄に付与(Gemini不要)。「最近開示があった」等でスクリーニングできる
  - `news_sentiment` / `news_label`(-1〜1 の感情スコア)は**スイング候補・追跡中銘柄のみ**
    Gemini で採点する(下記の無料枠制約のため)。「好材料」でのスクリーニングは
    感情スコアが付いた銘柄に限られる
- Gemini の無料枠は **20リクエスト/日(モデル毎、2026-07時点)**。かつては flash-lite の
  日次上限が大きかったが縮小されたため、呼び出しを次の通り最小化している:
  - 夜間バッチ: 感情採点の最大1回のみ(候補カードの説明文はテンプレート生成でGemini不使用)
  - LINE返信: 1件につき2回(条件解析 + 解説文生成 or 売買判断)→ 1日9件程度まで
- Gemini のモデルは flash-lite を使用(screening.py: GEMINI_MODEL)
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

### 1.5. Gemini API キーの取得(Phase 2)

1. [Google AI Studio](https://aistudio.google.com/apikey) にGoogleアカウントでログイン
2. 「Create API key」でキーを発行(無料枠の範囲で利用)
3. 控えたキーを `.env` の `GEMINI_API_KEY` に設定

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
   - `GEMINI_API_KEY`
   - `ALLOWED_USER_IDS`(任意。下記「限定公開にする」を参照。未設定なら誰でも利用可)
4. デプロイ完了後の URL(例: `https://stock-line-bot.onrender.com`)を控える

### 4. Webhook の接続

1. LINE Developers > Messaging API設定 > Webhook URL に
   `https://<RenderのURL>/callback` を設定
2. 「検証」ボタンで成功を確認
3. QR コードから友だち追加し、何か送信 → 返信が来れば成功

### 限定公開にする(推奨)

不特定多数が使えると Gemini の無料枠を消費されたり、投資助言的な内容が
意図せず公開されるリスクがあるため、自分・少人数だけに絞ることを推奨する。

1. アカウントを非公開のまま運用する(認証リクエストをしない、QRコード/IDを共有しない)
2. 上記の手順3で一度Botに何か送信すると、Renderの **Logs** タブに
   `user_id: U1234...` という行が出るので、そのIDを控える
3. Render の環境変数 `ALLOWED_USER_IDS` にそのIDを設定(複数人の場合はカンマ区切り)
4. 保存すると自動で再デプロイされ、以降は許可したIDのみ応答が返るようになる
   (許可外のユーザーには「現在このBotは限定公開です。」とだけ返す)

### 4.5. スイング候補のLINEプッシュ配信を有効にする(任意)

夜間バッチが GitHub Actions 上で動くため、リポジトリの Secrets に以下を追加すると
毎晩の候補カードがプッシュ配信される(未設定の間は配信だけスキップされ、他は正常動作):

1. GitHub リポジトリ > Settings > Secrets and variables > Actions > New repository secret
2. `LINE_CHANNEL_ACCESS_TOKEN`: チャネルアクセストークン(長期)
3. `ALLOWED_USER_IDS`: 配信先の user_id(複数はカンマ区切り。取得方法は「限定公開にする」参照)

LINE Push API の無料枠は月200通。1晩1通×宛先数なので個人利用なら枠内に収まる。

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

### スクリーニング機能のローカル確認

```powershell
# 全銘柄だと数分かかるので、動作確認だけなら --limit で絞る
.venv\Scripts\python batch.py --limit 50

.venv\Scripts\python -c "from screening import ScreeningConditions; from screener import run_screening; print(run_screening(ScreeningConditions(per_max=15))[0])"
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

LINEアプリを使わずに、実際のGemini APIとscreener.csvを使った応答をその場で確認できる。

```powershell
.venv\Scripts\python cli.py
> PER15倍以下で配当利回り3%以上の建設株
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
