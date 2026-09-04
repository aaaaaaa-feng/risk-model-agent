import { describe, expect, it } from "vitest";
import {
  createManualBinDrafts,
  editableBinSpec,
  parseManualBinDrafts,
  parseManualBinSpec,
  serializeManualBinSpec,
  updateManualBinDraft,
  updateManualVisualErrors,
} from "@/features/runs/lib/binning";

describe("人工分箱规则", () => {
  it("解析合法的数值切点并只保留受支持字段", () => {
    const result = parseManualBinSpec(`{
  "kind": "numeric",
  "edges": [10, 20.5, 30],
  "business_exception": "业务确认允许保留该趋势"
}`);

    expect(result).toEqual({
      ok: true,
      value: {
        kind: "numeric",
        edges: [10, 20.5, 30],
        business_exception: "业务确认允许保留该趋势",
      },
    });
  });

  it("将 JSON 语法错误转换为中文行列提示，不泄露浏览器原始异常", () => {
    const source = `{
  "kind": "numeric",
  "edges": [10, 20,]
}`;
    const result = parseManualBinSpec(source);

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.code).toBe("syntax");
    expect(result.error.line).toBe(3);
    expect(result.error.column).toBeGreaterThan(1);
    expect(result.error.message).toContain("第 3 行");
    expect(result.error.message).toContain("JSON 格式不正确");
    expect(result.error.message).not.toMatch(/Unexpected|SyntaxError|position/i);
    expect(source).toContain('"edges": [10, 20,]');
  });

  it.each([
    [`{"kind":"numeric","edges":[2,1]}`, "numeric_edges", "严格递增"],
    [`{"kind":"numeric","edges":[1,1]}`, "numeric_edges", "不能重复"],
    [`{"kind":"numeric","edges":["1"]}`, "numeric_edges", "有限数字"],
    [`{"kind":"numeric","edges":[],"script":"x"}`, "field", "不属于"],
  ])("拒绝不符合数值分箱契约的规则", (source, code, message) => {
    const result = parseManualBinSpec(source);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.code).toBe(code);
    expect(result.error.message).toContain(message);
    expect(result.error.line).toBe(1);
    expect(result.error.column).toBeGreaterThan(0);
  });

  it("拒绝跨类别组和稀有值的重复类别", () => {
    const result = parseManualBinSpec(
      JSON.stringify({
        kind: "categorical",
        groups: [["A", "B"], ["C"]],
        rare_values: ["A"],
      }),
    );

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.code).toBe("categorical_overlap");
    expect(result.error.message).toContain("不能同时出现");
  });

  it("拒绝空类别组和过短的非单调业务说明", () => {
    const emptyGroup = parseManualBinSpec(
      JSON.stringify({ kind: "categorical", groups: [[]], rare_values: [] }),
    );
    const shortReason = parseManualBinSpec(
      JSON.stringify({ kind: "numeric", edges: [1], business_exception: "太短" }),
    );

    expect(emptyGroup.ok).toBe(false);
    expect(shortReason.ok).toBe(false);
    if (!shortReason.ok) expect(shortReason.error.message).toContain("至少需要 8 个字符");
  });

  it("可视化编辑和高级 JSON 使用同一份可往返规则", () => {
    const editable = editableBinSpec({
      kind: "categorical",
      groups: [["A"], ["B", "C"]],
      rare_values: ["OTHER"],
      business_exception: "这是经过确认的业务例外",
    });
    const result = parseManualBinSpec(serializeManualBinSpec(editable));

    expect(result).toEqual({ ok: true, value: editable });
  });

  it("切换字段时保留独立草稿，并一次解析全部已修改字段", () => {
    const drafts = createManualBinDrafts({
      age: { kind: "numeric", edges: [25, 35] },
      city: { kind: "categorical", groups: [["上海"], ["北京"]], rare_values: [] },
    });
    const changedAge = updateManualBinDraft(drafts, "age", '{"kind":"numeric","edges":[24,36]}');
    const changedBoth = updateManualBinDraft(
      changedAge,
      "city",
      '{"kind":"categorical","groups":[["上海","杭州"],["北京"]],"rare_values":[]}',
    );

    expect(changedBoth.age).toContain("24,36");
    expect(parseManualBinDrafts(changedBoth, ["age", "city"])).toEqual({
      ok: true,
      value: {
        age: { kind: "numeric", edges: [24, 36] },
        city: {
          kind: "categorical",
          groups: [["上海", "杭州"], ["北京"]],
          rare_values: [],
        },
      },
    });
  });

  it("多字段提交遇错时定位字段且不改写原草稿", () => {
    const drafts = {
      age: '{"kind":"numeric","edges":[35,25]}',
      city: '{"kind":"categorical","groups":[["上海"]],"rare_values":[]}',
    };
    const parsed = parseManualBinDrafts(drafts, ["age", "city"]);

    expect(parsed.ok).toBe(false);
    if (parsed.ok) return;
    expect(parsed.column).toBe("age");
    expect(parsed.error.message).toContain("严格递增");
    expect(drafts.age).toContain("35,25");
  });

  it("修正另一个控件不会清除仍然存在的可视化输入错误", () => {
    const withFirstError = updateManualVisualErrors({}, "edge-0", "第一个切点无效");
    const withTwoErrors = updateManualVisualErrors(withFirstError, "edge-1", "第二个切点无效");
    const firstStillBlocked = updateManualVisualErrors(withTwoErrors, "edge-1");

    expect(firstStillBlocked).toEqual({ "edge-0": "第一个切点无效" });
    expect(updateManualVisualErrors(firstStillBlocked, "edge-0")).toEqual({});
  });
});

describe("人工分箱提交边界", () => {
  const sources = import.meta.glob(
    "./features/runs/ui/{DecisionWorkbench,ManualBinningEditor}.tsx",
    {
      eager: true,
      query: "?raw",
      import: "default",
    },
  ) as Record<string, string>;

  it("提交层不再直接 JSON.parse，并提供可访问的原位错误", () => {
    const workbench = sources["./features/runs/ui/DecisionWorkbench.tsx"];
    const editor = sources["./features/runs/ui/ManualBinningEditor.tsx"];

    expect(workbench).toContain("parseManualBinDrafts(manualDrafts, manualDirtyColumns)");
    expect(workbench).not.toContain("JSON.parse(manualSpec)");
    expect(workbench).toContain("manualDirtyColumns");
    expect(workbench).toContain("createManualBinDrafts(specs)");
    expect(workbench).toContain("initializedDecision.current === decisionKey");
    expect(editor).toContain('role="alert"');
    expect(editor).toContain("manual-bin-spec-error");
    expect(editor).toContain("可视化调整规则");
    expect(editor).not.toContain("setVisualErrors({})");
    expect(editor).toContain("disabled={hasVisualErrors}");
  });
});
