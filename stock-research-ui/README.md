# React + TypeScript + Vite

Stock Research のフロントエンドです。Vite + React + TypeScript で構築されています。

## ローカル起動

```bash
cd /d/Documents/stock-project/stock-research-ui
npm install
npm run dev -- --host 0.0.0.0
```

起動後は次の URL で確認できます。

- http://localhost:5173/

## ローカル停止

起動中のターミナルで `Ctrl + C` を押してください。

## バックエンドとの接続

フロントエンドはバックエンド API の URL を環境変数 `VITE_API_BASE_URL` で参照します。

```bash
cp .env.example .env
```

`.env` では次のように設定します。

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## ビルド

```bash
npm run build
```
