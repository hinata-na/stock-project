import { useMemo, useState } from 'react'
import './App.css'
import SearchForm from './components/SearchForm'
import ChartDisplay from './components/ChartDisplay'
import AnalysisResult from './components/AnalysisResult'
import { analyzeStock } from './services/api'
import type { AnalysisResponse } from './types'

function App() {
  const [result, setResult] = useState<AnalysisResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleAnalyze = async (tickerCode: string) => {
    setLoading(true)
    setError(null)

    try {
      const data = await analyzeStock(tickerCode)
      console.log('API response:', data)
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : '分析に失敗しました。')
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  const summary = useMemo(() => {
    if (!result) return null
    return {
      priceChange: result.current_price - result.previous_price,
      priceTrend: result.current_price >= result.previous_price ? '上昇' : '下落',
    }
  }, [result])

  return (
    <main className="min-h-screen px-4 py-6 text-slate-100 sm:px-6 lg:px-8 lg:py-8">
      <div className="mx-auto flex max-w-7xl flex-col gap-5">
        <header className="overflow-hidden rounded-[28px] border border-slate-800/80 bg-slate-900/80 p-7 shadow-[0_20px_60px_rgba(2,6,23,0.45)] backdrop-blur">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.3em] text-sky-400">Stock Research</p>
              <h1 className="mt-2 text-3xl font-bold tracking-tight text-white sm:text-4xl">銘柄分析ダッシュボード</h1>
              <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-300 sm:text-base">
                銘柄コードを入力すると、チャートと初心者向けの分析をすぐ確認できます。
              </p>
            </div>
          </div>
        </header>

        <SearchForm onSubmit={handleAnalyze} loading={loading} />

        {error && (
          <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        )}

        {summary && (
          <div className="grid gap-4 md:grid-cols-3">
            <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4 shadow-sm">
              <p className="text-sm text-slate-400">銘柄コード</p>
              <p className="mt-1 text-lg font-semibold text-white">{result?.ticker_code}</p>
            </div>
            <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4 shadow-sm">
              <p className="text-sm text-slate-400">価格の傾向</p>
              <p className="mt-1 text-lg font-semibold text-white">{summary.priceTrend}</p>
            </div>
            <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4 shadow-sm">
              <p className="text-sm text-slate-400">前日比</p>
              <p className="mt-1 text-lg font-semibold text-white">{summary.priceChange.toLocaleString()} 円</p>
            </div>
          </div>
        )}

        <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
          <ChartDisplay data={result?.chart_data ?? []} />
          <AnalysisResult result={result} />
        </div>
      </div>
    </main>
  )
}

export default App
