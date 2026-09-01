import { useEffect, useRef } from 'react';
import { CrosshairMode, LineStyle } from 'lightweight-charts';

// The chart's normal crosshair (matches useChart's initial options).
export const CROSSHAIR_DEFAULT = {
  mode: CrosshairMode.Normal,
  vertLine: { color: '#605f68', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#2c2a33', visible: true, labelVisible: true },
  horzLine: { color: '#605f68', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#2c2a33', visible: true, labelVisible: true },
};

// While picking a replay start point (TradingView-style): the crosshair
// becomes a bold accent-colored bar-snapping vertical line, the horizontal
// line disappears, and the next chart click reports the clicked bar's logical
// index through onPick. Leaving select mode restores the normal crosshair.
export function useReplaySelect(chart, active, onPick) {
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

  useEffect(() => {
    if (!active || !chart) return undefined;
    chart.applyOptions({
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { color: '#4d8eff', width: 2, style: LineStyle.Solid, labelBackgroundColor: '#4d8eff', visible: true, labelVisible: true },
        horzLine: { visible: false, labelVisible: false },
      },
    });
    const handler = (param) => {
      if (!param.point) return;
      const logical = chart.timeScale().coordinateToLogical(param.point.x);
      if (logical == null) return;
      pickRef.current(Math.round(logical));
    };
    chart.subscribeClick(handler);
    return () => {
      chart.unsubscribeClick(handler);
      chart.applyOptions({ crosshair: CROSSHAIR_DEFAULT });
    };
  }, [chart, active]);
}
