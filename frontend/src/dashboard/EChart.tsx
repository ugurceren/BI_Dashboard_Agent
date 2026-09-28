// İnce ECharts sarmalayıcı: tree-shaken çekirdek, ResizeObserver, font yüklenince yeniden çizim.
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import {
  BarChart, LineChart, PieChart, ScatterChart, HeatmapChart, FunnelChart, GaugeChart, TreemapChart,
} from "echarts/charts";
import {
  GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, TitleComponent,
  GraphicComponent, MarkLineComponent, AxisPointerComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsCoreOption } from "echarts/core";

echarts.use([
  BarChart, LineChart, PieChart, ScatterChart, HeatmapChart, FunnelChart, GaugeChart, TreemapChart,
  GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, TitleComponent,
  GraphicComponent, MarkLineComponent, AxisPointerComponent, CanvasRenderer,
]);

export type EOption = EChartsCoreOption;

let fontsReady: Promise<unknown> | null = null;
function whenFontsReady(): Promise<unknown> {
  if (!fontsReady) fontsReady = typeof document !== "undefined" && document.fonts ? document.fonts.ready : Promise.resolve();
  return fontsReady;
}

export function EChart({ option, className, onSize, onClick }: {
  option: EOption;
  className?: string;
  /** öğeye tıklama (çapraz filtre) */
  onClick?: (params: { name?: string; seriesName?: string; dataIndex?: number; componentType?: string }) => void;
  /** boyut değişince (px) — seçenek boyuta göre değişecekse */
  onSize?: (w: number, h: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const optRef = useRef(option);
  optRef.current = option;
  const sizeCb = useRef(onSize);
  sizeCb.current = onSize;
  const clickCb = useRef(onClick);
  clickCb.current = onClick;

  useEffect(() => {
    const el = ref.current!;
    const c = echarts.init(el, undefined, { renderer: "canvas" });
    chart.current = c;
    c.setOption(optRef.current, true);
    c.on("click", (p: unknown) => clickCb.current?.(p as { name?: string }));
    // Boyutu hemen bildir: rAF gizli/arka plandaki sekmelerde çalışmaz, seçenekler varsayılan boyutta kalırdı.
    if (el.clientWidth > 0 && el.clientHeight > 0) sizeCb.current?.(el.clientWidth, el.clientHeight);
    const ro = new ResizeObserver((entries) => {
      const r = entries[entries.length - 1].contentRect;
      if (r.width > 0 && r.height > 0 && !c.isDisposed()) {
        c.resize();
        sizeCb.current?.(r.width, r.height);
      }
    });
    ro.observe(el);
    let alive = true;
    whenFontsReady().then(() => {
      if (alive && !c.isDisposed()) c.setOption(optRef.current, true);
    });
    return () => {
      alive = false;
      ro.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    const c = chart.current;
    if (c && !c.isDisposed()) c.setOption(option, true);
  }, [option]);

  return <div ref={ref} className={(className ?? "db-echart") + (onClick ? " is-clickable" : "")} />;
}
