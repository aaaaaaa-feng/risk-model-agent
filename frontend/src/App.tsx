import { lazy, Suspense } from "react";

const AppShell = lazy(() =>
  import("./app/AppShell").then((module) => ({ default: module.AppShell })),
);

/** 应用入口只负责组合和顶层加载边界。 */
export function App() {
  return (
    <Suspense fallback={<main className="loading-panel">正在加载本地建模工作台…</main>}>
      <AppShell />
    </Suspense>
  );
}
