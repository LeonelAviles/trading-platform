import { useEffect, useRef, useState } from 'react';
import { useChart, formatVol } from './useChart';
import { intervalToSeconds } from '../drawing/geometry';
import DrawingOverlay from '../drawing/DrawingOverlay';
import DomHeatmapLayer from '../orderflow/DomHeatmapLayer';
import useOrderFlowData from '../orderflow/useOrderFlowData';
import FootprintLayer from './layers/FootprintLayer';
import DeltaBubblesLayer from './layers/DeltaBubblesLayer';
import ProfileLayer from './layers/ProfileLayer';

// The chart shell shared by /review/:backtestId and /chart/:symbol: one
// lightweight-charts instance, the legend, the drawing overlay and the
// order-flow canvas layers. Bars are painted by the page through `onReady`'s
// api (pages own loading/replay); `bars` here feeds the legend and layers.
export default function ChartView({
  symbol, interval, bars, settings, onReady, onView,
  layers = {}, layerSettings = {}, tickSize = 0.25,
  footprints = {}, liveFootprint = null, bubbleTrades = [], profileBins = null, clockTime = null,
  widenBubbles = false,
  drawing, trades = [], revealTime = null,
  children, className = '',
}) {
  const areaRef = useRef(null);
  const innerRef = useRef(null);
  const heatmapOn = !!layers.heatmap;
  const { api, tick, hoverTime } = useChart(areaRef, innerRef, settings, { transparent: heatmapOn });
  const [footprintNarrow, setFootprintNarrow] = useState(false);

  useEffect(() => { if (api) onReady?.(api); }, [api, onReady]);

  const chart = api?.chart || null;
  const series = api?.candleSeries || null;
  const intervalSeconds = intervalToSeconds(interval);

  // Visible window for the heatmap fetch — recomputed off `tick`.
  const visibleRange = chart ? chart.timeScale().getVisibleRange() : null;
  const chartHeight = areaRef.current?.clientHeight;
  const topPrice = series && chartHeight ? series.coordinateToPrice(0) : null;
  const bottomPrice = series && chartHeight ? series.coordinateToPrice(chartHeight) : null;
  const priceRange = topPrice != null && bottomPrice != null
    ? { min: Number(Math.min(topPrice, bottomPrice).toFixed(4)), max: Number(Math.max(topPrice, bottomPrice).toFixed(4)) }
    : null;
  const { heatmapData, heatmapLoading, tooWideForHeatmap } = useOrderFlowData(symbol, heatmapOn, visibleRange, priceRange);
  void tick;
  const vFrom = visibleRange?.from ?? null;
  const vTo = visibleRange?.to ?? null;
  useEffect(() => { onView?.(vFrom != null && vTo != null ? { from: vFrom, to: vTo } : null); }, [onView, vFrom, vTo]);

  const hoverIdx = hoverTime != null ? bars.findIndex((b) => b.time === hoverTime) : -1;
  const legendIdx = hoverIdx >= 0 ? hoverIdx : bars.length - 1;
  const legendBar = legendIdx >= 0 ? bars[legendIdx] : null;
  const legendPrev = legendIdx > 0 ? bars[legendIdx - 1] : null;
  const legendBase = legendPrev ? legendPrev.close : legendBar?.open;
  const legendChange = legendBar ? legendBar.close - legendBase : 0;
  const legendChangePct = legendBase ? (legendChange / legendBase) * 100 : 0;
  const legendSign = legendChange >= 0 ? 'pos' : 'neg';

  return (
    <div className={`chart-area ${heatmapOn ? 'order-flow-active' : ''} ${className}`} ref={areaRef}>
      <div className="chart-inner" ref={innerRef} />
      {heatmapOn && (
        <DomHeatmapLayer chart={chart} series={series} heatmapData={heatmapData} bars={bars} intervalSeconds={intervalSeconds} maxTime={clockTime} />
      )}
      {heatmapOn && tooWideForHeatmap && <div className="order-flow-hint">Zoom in to see order flow</div>}
      {heatmapOn && heatmapLoading && !heatmapData && !tooWideForHeatmap && <div className="order-flow-hint">Loading liquidity…</div>}
      {heatmapOn && heatmapData?.buckets?.length > 0 && (
        <div className="order-flow-scale" aria-label="Liquidity intensity scale"><span>Liquidity</span><i /><small>low</small><small>high</small></div>
      )}
      {layers.profile && profileBins && (
        <ProfileLayer chart={chart} series={series} bins={profileBins} tickSize={tickSize} width={layerSettings.profileWidth} />
      )}
      {layers.footprint && (
        <FootprintLayer
          chart={chart} series={series} bars={bars} footprints={footprints} liveFootprint={liveFootprint}
          tickSize={tickSize} settings={layerSettings} onTooNarrow={setFootprintNarrow}
        />
      )}
      {layers.footprint && footprintNarrow && <div className="order-flow-hint subtle">Zoom in for footprint</div>}
      {layers.bubbles && (
        <DeltaBubblesLayer
          chart={chart} series={series} bars={bars} intervalSeconds={intervalSeconds} trades={bubbleTrades}
          settings={layerSettings} clockTime={clockTime} widenWithZoom={widenBubbles}
        />
      )}
      {legendBar && (
        <div className="chart-legend">
          <div className="legend-top">
            <span className="legend-symbol">{symbol}</span>
            <span className="legend-price">{legendBar.close.toFixed(2)}</span>
            <span className={`legend-change ${legendSign}`}>
              {legendChange >= 0 ? '+' : ''}{legendChange.toFixed(2)} ({legendChange >= 0 ? '+' : ''}{legendChangePct.toFixed(2)}%)
            </span>
          </div>
          <div className="legend-ohlc">
            <span>O <b className={legendSign}>{legendBar.open.toFixed(2)}</b></span>
            <span>H <b className={legendSign}>{legendBar.high.toFixed(2)}</b></span>
            <span>L <b className={legendSign}>{legendBar.low.toFixed(2)}</b></span>
            <span>C <b className={legendSign}>{legendBar.close.toFixed(2)}</b></span>
            {legendBar.volume != null && <span>Vol <b>{formatVol(legendBar.volume)}</b></span>}
            {legendBar.delta != null && legendBar.hasDelta !== false && <span>Δ <b className={legendBar.delta >= 0 ? 'pos' : 'neg'}>{Math.round(legendBar.delta)}</b></span>}
          </div>
        </div>
      )}
      {drawing && (
        <DrawingOverlay
          chart={chart} series={series}
          shapes={drawing.shapes} setShapes={drawing.setShapes}
          activeTool={drawing.activeTool} setActiveTool={drawing.setActiveTool}
          selectedId={drawing.selectedId} setSelectedId={drawing.setSelectedId}
          trades={trades} revealTime={revealTime} bars={bars} intervalSeconds={intervalSeconds}
        />
      )}
      {children}
    </div>
  );
}
