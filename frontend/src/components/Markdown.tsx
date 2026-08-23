import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * 统一的 Markdown 渲染（GFM：表格、删除线、任务列表等）。
 * react-markdown 默认不渲染原始 HTML，无 XSS 注入面；外链新窗口打开。
 * 流式输出时直接渲染未闭合的片段（如未闭合代码块），react-markdown 可以容错。
 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: text }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {text}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
