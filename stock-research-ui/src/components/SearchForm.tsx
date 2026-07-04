import { useState } from 'react'

interface SearchFormProps {
  onSubmit: (tickerCode: string) => void
  loading: boolean
}

export default function SearchForm({ onSubmit, loading }: SearchFormProps) {
  const [tickerCode, setTickerCode] = useState('7203')

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (tickerCode.trim()) {
      onSubmit(tickerCode.trim())
    }
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-[24px] border border-slate-700 bg-slate-950/90 p-5 shadow-[0_18px_45px_rgba(2,6,23,0.55)] sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex-1">
          <label htmlFor="ticker" className="mb-2 block text-sm font-semibold text-slate-300">
            銘柄コードを入力してください
          </label>
          <input
            id="ticker"
            type="text"
            value={tickerCode}
            onChange={(event) => setTickerCode(event.target.value)}
            placeholder="例: 7203"
            className="w-full rounded-xl border-2 border-slate-500 bg-slate-900 px-4 py-3 text-base text-white shadow-[inset_0_2px_6px_rgba(0,0,0,0.45)] outline-none transition focus:border-sky-400 focus:bg-slate-800 focus:ring-4 focus:ring-sky-400/30"
          />
        </div>
        <button
          type="submit"
          disabled={loading}
          className="rounded-xl border border-sky-300/60 bg-gradient-to-r from-sky-500 via-cyan-500 to-blue-600 px-5 py-3 font-semibold text-white shadow-[0_10px_25px_rgba(14,165,233,0.35)] transition hover:-translate-y-0.5 hover:shadow-[0_14px_30px_rgba(14,165,233,0.45)] disabled:cursor-not-allowed disabled:from-slate-600 disabled:to-slate-600 disabled:shadow-none"
        >
          {loading ? '分析中...' : '分析する'}
        </button>
      </div>
      <p className="mt-3 text-sm text-slate-400">日本株の銘柄コードを入力すると、分析結果とチャートを表示します。</p>
    </form>
  )
}
