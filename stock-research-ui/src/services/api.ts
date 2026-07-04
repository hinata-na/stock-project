import axios from 'axios'
import type { AnalysisResponse } from '../types'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000',
  timeout: 120000,
})

export async function analyzeStock(tickerCode: string): Promise<AnalysisResponse> {
  const response = await api.post<AnalysisResponse>('/analyze', {
    ticker_code: tickerCode,
  })
  return response.data
}
