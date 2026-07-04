export interface ChartDataPoint {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  ma25?: number | null
  ma75?: number | null
}

export interface TechnicalIndicators {
  ma25?: number | null
  ma75?: number | null
  rsi?: number | null
}

export interface AnalysisResponse {
  ticker_code: string
  company_name: string
  current_price: number
  previous_price: number
  analysis_text: string
  chart_data: ChartDataPoint[]
  technical_indicators: TechnicalIndicators
}
