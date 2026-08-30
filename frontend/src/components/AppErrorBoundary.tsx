import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  failed: boolean;
}

/** 最后一道界面容错：不把 JS 异常、堆栈或白屏直接暴露给用户。 */
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(_: Error, __: ErrorInfo) {
    // 运行时会由打包容器记录异常；页面不展示原始错误或堆栈。
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="app-error-boundary" role="alert">
        <span>LOCAL WORKBENCH</span>
        <h1>页面暂时无法继续显示</h1>
        <p>项目数据仍保存在本机。请重新加载；如果仍然出现，请重启应用。</p>
        <button type="button" onClick={() => window.location.reload()} title="重新加载本地应用">
          重新加载
        </button>
      </main>
    );
  }
}
