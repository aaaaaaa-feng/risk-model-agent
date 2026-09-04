import type { BinSpec } from "../types";

export type EditableNumericBinSpec = {
  kind: "numeric";
  edges: number[];
  business_exception?: string;
};

export type EditableCategoricalBinSpec = {
  kind: "categorical";
  groups: string[][];
  rare_values: string[];
  business_exception?: string;
};

export type EditableBinSpec = EditableNumericBinSpec | EditableCategoricalBinSpec;

export interface ManualBinSpecError {
  code:
    | "empty"
    | "syntax"
    | "root"
    | "field"
    | "kind"
    | "numeric_edges"
    | "categorical_groups"
    | "categorical_overlap"
    | "business_exception";
  message: string;
  line?: number;
  column?: number;
  path?: string;
}

export type ManualBinSpecResult =
  { ok: true; value: EditableBinSpec } | { ok: false; error: ManualBinSpecError };

export type ManualBinDraftsResult =
  | { ok: true; value: Record<string, EditableBinSpec> }
  | { ok: false; column: string; error: ManualBinSpecError };

export function updateManualVisualErrors(
  current: Record<string, string>,
  key: string,
  error?: string,
): Record<string, string> {
  const next = { ...current };
  if (error) next[key] = error;
  else delete next[key];
  return next;
}

const COMMON_FIELDS = new Set(["kind", "business_exception"]);
const NUMERIC_FIELDS = new Set([...COMMON_FIELDS, "edges"]);
const CATEGORICAL_FIELDS = new Set([...COMMON_FIELDS, "groups", "rare_values"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function lineColumnAt(source: string, offset: number) {
  const safeOffset = Math.max(0, Math.min(offset, source.length));
  const before = source.slice(0, safeOffset);
  const lines = before.split(/\r?\n/);
  return { line: lines.length, column: (lines.at(-1)?.length || 0) + 1 };
}

class JsonLocationError extends Error {
  constructor(readonly offset: number) {
    super("JSON_LOCATION_ERROR");
  }
}

/**
 * 部分新版 V8 只返回一段英文上下文，不再提供 position。这个扫描器不负责生成
 * 数据，只在原生解析失败后走一遍 JSON 文法，稳定找出第一个无效字符的位置。
 */
function scanJsonErrorOffset(source: string): number | undefined {
  let cursor = 0;
  const fail = (offset = cursor): never => {
    throw new JsonLocationError(offset);
  };
  const whitespace = () => {
    while (/\s/.test(source[cursor] || "")) cursor += 1;
  };
  const string = () => {
    if (source[cursor] !== '"') fail();
    cursor += 1;
    while (cursor < source.length) {
      const character = source[cursor];
      if (character === '"') {
        cursor += 1;
        return;
      }
      if (character === "\\") {
        cursor += 1;
        const escaped = source[cursor];
        if (!escaped || !'"\\/bfnrtu'.includes(escaped)) fail();
        if (escaped === "u") {
          const digits = source.slice(cursor + 1, cursor + 5);
          if (!/^[0-9a-fA-F]{4}$/.test(digits)) fail(cursor + 1);
          cursor += 4;
        }
      } else if (character.charCodeAt(0) < 0x20) {
        fail();
      }
      cursor += 1;
    }
    fail(source.length);
  };
  const value = (): void => {
    whitespace();
    const character = source[cursor];
    if (character === '"') {
      string();
      return;
    }
    if (character === "{") {
      cursor += 1;
      whitespace();
      if (source[cursor] === "}") {
        cursor += 1;
        return;
      }
      while (cursor < source.length) {
        whitespace();
        string();
        whitespace();
        if (source[cursor] !== ":") fail();
        cursor += 1;
        value();
        whitespace();
        if (source[cursor] === "}") {
          cursor += 1;
          return;
        }
        if (source[cursor] !== ",") fail();
        cursor += 1;
        whitespace();
        if (source[cursor] === "}") fail();
      }
      fail(source.length);
    }
    if (character === "[") {
      cursor += 1;
      whitespace();
      if (source[cursor] === "]") {
        cursor += 1;
        return;
      }
      while (cursor < source.length) {
        value();
        whitespace();
        if (source[cursor] === "]") {
          cursor += 1;
          return;
        }
        if (source[cursor] !== ",") fail();
        cursor += 1;
        whitespace();
        if (source[cursor] === "]") fail();
      }
      fail(source.length);
    }
    for (const literal of ["true", "false", "null"]) {
      if (source.startsWith(literal, cursor)) {
        cursor += literal.length;
        return;
      }
    }
    const number = source.slice(cursor).match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    if (!number) return fail();
    cursor += number[0].length;
  };

  try {
    whitespace();
    value();
    whitespace();
    if (cursor !== source.length) fail();
  } catch (error) {
    if (error instanceof JsonLocationError) return error.offset;
  }
  return undefined;
}

function jsonErrorLocation(source: string, error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  const explicit = message.match(/line\s+(\d+)\s+column\s+(\d+)/i);
  if (explicit) return { line: Number(explicit[1]), column: Number(explicit[2]) };
  const position = message.match(/(?:position|at)\s+(\d+)/i);
  if (position) return lineColumnAt(source, Number(position[1]));
  const scannedOffset = scanJsonErrorOffset(source);
  return scannedOffset === undefined ? {} : lineColumnAt(source, scannedOffset);
}

function fieldLocation(source: string, field: string) {
  const offset = source.indexOf(`"${field}"`);
  return offset >= 0 ? lineColumnAt(source, offset) : {};
}

function problem(
  code: ManualBinSpecError["code"],
  message: string,
  options: Pick<ManualBinSpecError, "line" | "column" | "path"> = {},
): ManualBinSpecResult {
  const locatedMessage =
    options.line && !message.startsWith("第 ")
      ? `${describeLocation(options)}：${message}`
      : message;
  return { ok: false, error: { code, message: locatedMessage, ...options } };
}

function describeLocation(location: { line?: number; column?: number }) {
  if (location.line && location.column)
    return `第 ${location.line} 行、第 ${location.column} 列附近`;
  if (location.line) return `第 ${location.line} 行附近`;
  return "输入内容中";
}

/**
 * 解析并校验提交给 Worker 的人工分箱规则。
 *
 * 这里故意采用与后端相同或更严格的正向契约，避免把未知字段、重复切点或
 * 重叠类别一直带到提交阶段后，才由通用接口错误兜底。
 */
export function parseManualBinSpec(source: string): ManualBinSpecResult {
  if (!source.trim()) return problem("empty", "人工分箱规则不能为空。", { line: 1, column: 1 });

  let value: unknown;
  try {
    value = JSON.parse(source) as unknown;
  } catch (error) {
    const location = jsonErrorLocation(source, error);
    return problem(
      "syntax",
      `${describeLocation(location)}的 JSON 格式不正确，请检查逗号、引号和括号是否完整。`,
      location,
    );
  }

  if (!isRecord(value)) {
    return problem("root", "人工分箱规则必须是一个 JSON 对象。", {
      line: 1,
      column: 1,
      path: "$",
    });
  }

  if (value.kind !== "numeric" && value.kind !== "categorical") {
    return problem("kind", '字段 kind 只能填写 "numeric" 或 "categorical"。', {
      ...fieldLocation(source, "kind"),
      path: "kind",
    });
  }

  const allowedFields = value.kind === "numeric" ? NUMERIC_FIELDS : CATEGORICAL_FIELDS;
  const unknownField = Object.keys(value).find((field) => !allowedFields.has(field));
  if (unknownField) {
    return problem("field", "检测到不属于当前分箱规则的字段，请删除后再提交。", {
      ...fieldLocation(source, unknownField),
      path: unknownField,
    });
  }

  if (
    value.business_exception !== undefined &&
    (typeof value.business_exception !== "string" ||
      (value.business_exception.trim().length > 0 && value.business_exception.trim().length < 8))
  ) {
    return problem("business_exception", "非单调业务说明如填写，至少需要 8 个字符。", {
      ...fieldLocation(source, "business_exception"),
      path: "business_exception",
    });
  }
  const businessException =
    typeof value.business_exception === "string" && value.business_exception.trim()
      ? value.business_exception.trim()
      : undefined;

  if (value.kind === "numeric") {
    if (!Array.isArray(value.edges)) {
      return problem("numeric_edges", "数值型分箱的 edges 必须是切点数组。", {
        ...fieldLocation(source, "edges"),
        path: "edges",
      });
    }
    const edges = value.edges;
    if (!edges.every((edge) => typeof edge === "number" && Number.isFinite(edge))) {
      return problem("numeric_edges", "每个数值切点都必须是有限数字。", {
        ...fieldLocation(source, "edges"),
        path: "edges",
      });
    }
    if (edges.some((edge, index) => index > 0 && edge <= edges[index - 1])) {
      return problem("numeric_edges", "数值切点必须严格递增，且不能重复。", {
        ...fieldLocation(source, "edges"),
        path: "edges",
      });
    }
    return {
      ok: true,
      value: {
        kind: "numeric",
        edges: [...edges],
        ...(businessException && { business_exception: businessException }),
      },
    };
  }

  if (
    !Array.isArray(value.groups) ||
    !value.groups.every(
      (group) =>
        Array.isArray(group) &&
        group.length > 0 &&
        group.every((category) => typeof category === "string" && category.trim().length > 0),
    )
  ) {
    return problem("categorical_groups", "类别型分箱的 groups 必须由非空字符串数组组成。", {
      ...fieldLocation(source, "groups"),
      path: "groups",
    });
  }
  if (
    value.rare_values !== undefined &&
    (!Array.isArray(value.rare_values) ||
      !value.rare_values.every(
        (category) => typeof category === "string" && category.trim().length > 0,
      ))
  ) {
    return problem("categorical_groups", "rare_values 必须是非空字符串数组。", {
      ...fieldLocation(source, "rare_values"),
      path: "rare_values",
    });
  }

  const groups = value.groups as string[][];
  const rareValues = (value.rare_values || []) as string[];
  const categories = [...groups.flat(), ...rareValues];
  if (new Set(categories).size !== categories.length) {
    return problem("categorical_overlap", "同一个类别不能同时出现在多个分组或稀有值列表中。", {
      ...fieldLocation(source, "groups"),
      path: "groups",
    });
  }
  return {
    ok: true,
    value: {
      kind: "categorical",
      groups: groups.map((group) => [...group]),
      rare_values: [...rareValues],
      ...(businessException && { business_exception: businessException }),
    },
  };
}

export function serializeManualBinSpec(spec: EditableBinSpec) {
  return JSON.stringify(spec, null, 2);
}

export function createManualBinDrafts(specs: Record<string, BinSpec>) {
  return Object.fromEntries(
    Object.entries(specs).map(([column, spec]) => [
      column,
      serializeManualBinSpec(editableBinSpec(spec)),
    ]),
  );
}

export function updateManualBinDraft(
  drafts: Record<string, string>,
  column: string,
  value: string,
) {
  return { ...drafts, [column]: value };
}

export function parseManualBinDrafts(
  drafts: Record<string, string>,
  columns: string[],
): ManualBinDraftsResult {
  const values: Record<string, EditableBinSpec> = {};
  for (const column of columns) {
    const parsed = parseManualBinSpec(drafts[column] || "");
    if (!parsed.ok) return { ok: false, column, error: parsed.error };
    values[column] = parsed.value;
  }
  return { ok: true, value: values };
}

export function editableBinSpec(spec: BinSpec | undefined): EditableBinSpec {
  if (!spec) return { kind: "numeric", edges: [] };
  const businessException = spec.business_exception?.trim();
  return spec.kind === "numeric"
    ? {
        kind: "numeric",
        edges: [...(spec.edges || [])],
        ...(businessException && { business_exception: businessException }),
      }
    : {
        kind: "categorical",
        groups: (spec.groups || []).map((group) => [...group]),
        rare_values: [...(spec.rare_values || [])],
        ...(businessException && { business_exception: businessException }),
      };
}
