# stock-line-bot

LINE から自然言語で日本株のスクリーニング・売買判断の材料を得るボット。

## 構成(すべて無料枠)

| 役割 | 技術 |
|---|---|
| UI | LINE Messaging API(返信は無料・無制限) |
| サーバー | Python (FastAPI) + Render 無料プラン |
| 自然言語の解釈 | Gemini API 無料枠(Phase 2 で導入) |
| 株価データ | yfinance + GitHub Actions 夜間バッチ(Phase 3 で導入) |

## ロードマップ

- [x] Phase 1: LINE オウム返しボット(配管の確認)
- [x] Phase 2: Gemini で自然言語 → スクリーニング条件 JSON に変換
- [x] Phase 3: yfinance + 夜間バッチで日本株スクリーニング
- [x] Phase 4: テクニカル指標による売買シグナル + 解説文生成

## しくみ

```
[GitHub Actions 平日18:30 JST]
  batch.py: JPX銘柄一覧(プライム) → yfinance で指標取得
    ├ ファンダメンタル: PER/PBR/配当利回り/ROE/時価総額
    └ テクニカル(indicators.py): MA25/MA75/RSI14 → シグナル判定
  → data/screener.csv をコミット & push → Render が自動再デプロイ

[ユーザーがLINEで発言]
  「PER15倍以下で配当3%以上、ゴールデンクロスの自動車株」
  → Gemini が条件JSONに変換 (screening.py: parse_screening_conditions)
  → data/screener.csv を pandas でフィルタ (screener.py)
  → 時価総額上位10件 + Gemini による初心者向け解説文 (screening.py: generate_commentary) を返信
```

- 取得指標: PER / PBR / 配当利回り / ROE / 時価総額 / 東証33業種
- シグナル判定: ゴールデンクロス / デッドクロス(MA25とMA75のクロス)、
  売られすぎ / 買われすぎ(RSI14が30未満 / 70超)、それ以外は中立
- 1件のLINE返信につき Gemini 呼び出しは2回(条件解析 + 解説文生成)。
  解説文はヒット銘柄にシグナルが1件も無ければ生成をスキップする
- 初回はデータがないため、GitHub Actions の `nightly-batch` を手動実行
  (Actions タブ > nightly-batch > Run workflow)するか、
  ローカルで `python batch.py` を実行して CSV をコミットする

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

## 注意事項

- 売買判断の提示は不特定多数に公開すると投資助言業(金商法)に抵触し得る。
  自分用・少人数用に留めること。
- yfinance は非公式ライブラリのため、将来壊れた場合は J-Quants API
  (無料プランは12週遅延)への乗り換えを検討する。
