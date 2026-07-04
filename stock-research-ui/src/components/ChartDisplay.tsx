import { useMemo } from 'react'
import ReactApexChart from 'react-apexcharts'
import type { ApexOptions } from 'apexcharts'
import type { ChartDataPoint } from '../types'

interface ChartDisplayProps {
  data: ChartDataPoint[]
}

export default function ChartDisplay({ data }: ChartDisplayProps) {
  const displayData = useMemo(() => [...data].slice(-60), [data])

  if (!displayData.length) {
    return null
  }

  const categories = displayData.map((item) => item.date.slice(5))

  const options: ApexOptions = {
    chart: {
      type: 'candlestick',
      toolbar: { show: false },
      zoom: { enabled: false },
      parentHeightOffset: 0,
    },
    legend: {
      show: false,
    },
    xaxis: {
      categories,
      labels: {
        rotate: -45,
        style: {
          fontSize: '12px',
        },
      },
    },
    yaxis: {
      labels: {
        formatter: (value: number) => `${value.toLocaleString()}円`,
      },
    },
    tooltip: {
      shared: false,
      custom: ({ seriesIndex, dataPointIndex, w }) => {
        const point = w.globals.initialSeries[seriesIndex].data[dataPointIndex]
        const [open, high, low, close] = point.y
        return `
          <div class="p-2 text-sm">
            <div class="font-semibold">${point.x}</div>
            <div>始値: ${open.toLocaleString()}円</div>
            <div>高値: ${high.toLocaleString()}円</div>
            <div>安値: ${low.toLocaleString()}円</div>
            <div>終値: ${close.toLocaleString()}円</div>
          </div>
        `
      },
    },
    plotOptions: {
      candlestick: {
        colors: {
          upward: '#ef4444',
          downward: '#10b981',
        },
      },
    },
    grid: {
      borderColor: '#e5e7eb',
    },
  }

  const series = [
    {
      name: 'ローソク足',
      data: displayData.map((item) => ({
        x: item.date.slice(5),
        y: [item.open, item.high, item.low, item.close],
      })),
    },
  ]

  return (
    <div className="w-full rounded-[24px] border border-slate-800 bg-slate-900/80 p-5 shadow-[0_12px_40px_rgba(2,6,23,0.35)] sm:p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-white">日足チャート</h2>
          <p className="mt-1 text-sm text-slate-400">直近 60 日分を表示</p>
        </div>
        <div className="rounded-full bg-slate-800 px-3 py-1 text-sm text-slate-300">ローソク足</div>
      </div>
      <div className="h-[420px] w-full min-w-0 overflow-hidden rounded-2xl border border-slate-800 bg-slate-950/70 p-2">
        <ReactApexChart options={options} series={series} type="candlestick" height={380} width="100%" />
      </div>
    </div>
  )
}
