import type { AnalysisResponse } from '../types'

interface AnalysisResultProps {
  result: AnalysisResponse | null
}

export default function AnalysisResult({ result }: AnalysisResultProps) {
  if (!result) {
    return null
  }

  return (
    <div className="rounded-[24px] border border-slate-800 bg-slate-900/80 p-5 shadow-[0_12px_40px_rgba(2,6,23,0.35)] sm:p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-slate-400">分析結果</p>
          <h2 className="mt-1 text-xl font-semibold text-white">{result.company_name}</h2>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-800/80 px-4 py-3 text-sm text-slate-300">
          <div className="font-medium text-white">現在価値: {result.current_price.toLocaleString()} 円</div>
          <div className="mt-1">前日比: {(result.current_price - result.previous_price).toLocaleString()} 円</div>
        </div>
      </div>

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl bg-sky-50 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-sky-700">MA25</p>
          <p className="mt-1 text-lg font-bold text-sky-900">{result.technical_indicators.ma25?.toFixed(1) ?? '-'} 円</p>
        </div>
        <div className="rounded-2xl bg-emerald-50 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700">MA75</p>
          <p className="mt-1 text-lg font-bold text-emerald-900">{result.technical_indicators.ma75?.toFixed(1) ?? '-'} 円</p>
        </div>
        <div className="rounded-2xl bg-amber-50 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">RSI</p>
          <p className="mt-1 text-lg font-bold text-amber-900">{result.technical_indicators.rsi?.toFixed(1) ?? '-'} </p>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-800 bg-slate-800/60 p-4">
        <h3 className="mb-2 text-sm font-semibold text-slate-300">初心者向け解説</h3>
        <pre className="whitespace-pre-wrap text-sm leading-7 text-slate-300">{result.analysis_text}</pre>
      </div>
    </div>
  )
}
