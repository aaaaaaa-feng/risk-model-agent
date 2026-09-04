import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import {
  parseManualBinSpec,
  serializeManualBinSpec,
  updateManualVisualErrors,
  type EditableBinSpec,
  type EditableCategoricalBinSpec,
  type EditableNumericBinSpec,
  type ManualBinSpecError,
} from "../lib/binning";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Textarea } from "@/shared/ui/textarea";

interface Props {
  value: string;
  onChange: (value: string) => void;
  submitError?: ManualBinSpecError | null;
  onVisualErrorChange: (error: string | null) => void;
}

function nextEdge(edges: number[]) {
  if (!edges.length) return 0;
  const last = edges[edges.length - 1];
  const previous = edges.at(-2);
  const step =
    previous === undefined ? Math.max(Math.abs(last) * 0.1, 1) : Math.max(last - previous, 1);
  return Number((last + step).toPrecision(12));
}

function NumericEdgeField({
  value,
  index,
  previous,
  next,
  onCommit,
  onRemove,
  errorKey,
  reportError,
  hasVisualErrors,
}: {
  value: number;
  index: number;
  previous?: number;
  next?: number;
  onCommit: (value: number) => void;
  onRemove: () => void;
  errorKey: string;
  reportError: (key: string, error?: string) => void;
  hasVisualErrors: boolean;
}) {
  const [draft, setDraft] = useState(String(value));
  const [error, setError] = useState("");

  useEffect(() => setDraft(String(value)), [value]);

  const commit = () => {
    const parsed = Number(draft);
    if (!draft.trim() || !Number.isFinite(parsed)) {
      setError("请输入有效数字");
      reportError(errorKey, "存在未填写或无效的数值切点，请先修正。");
      return;
    }
    if ((previous !== undefined && parsed <= previous) || (next !== undefined && parsed >= next)) {
      setError("切点必须位于相邻切点之间");
      reportError(errorKey, "数值切点必须严格递增，请先修正。");
      return;
    }
    setError("");
    reportError(errorKey);
    if (parsed !== value) onCommit(parsed);
  };

  return (
    <div className="grid grid-cols-[72px_minmax(0,1fr)_auto] items-start gap-2">
      <span className="pt-2 text-[11px] font-semibold text-[var(--muted)]">切点 {index + 1}</span>
      <div>
        <Input
          type="number"
          step="any"
          value={draft}
          aria-invalid={Boolean(error)}
          aria-label={`第 ${index + 1} 个数值切点`}
          onChange={(event) => {
            setDraft(event.target.value);
            setError("");
            reportError(errorKey);
          }}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
          }}
        />
        {error && <small className="mt-1 block text-[var(--red-text)]">{error}</small>}
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={onRemove}
        disabled={hasVisualErrors}
        aria-label={`删除切点 ${index + 1}`}
        title={hasVisualErrors ? "请先修正当前输入错误" : `删除切点 ${index + 1}`}
      >
        删除
      </Button>
    </div>
  );
}

function NumericRuleEditor({
  spec,
  onCommit,
  reportError,
  hasVisualErrors,
}: {
  spec: EditableNumericBinSpec;
  onCommit: (spec: EditableNumericBinSpec) => void;
  reportError: (key: string, error?: string) => void;
  hasVisualErrors: boolean;
}) {
  return (
    <div className="grid gap-2" aria-label="数值切点编辑器">
      {spec.edges.length ? (
        spec.edges.map((edge, index) => (
          <NumericEdgeField
            key={index}
            value={edge}
            index={index}
            previous={spec.edges[index - 1]}
            next={spec.edges[index + 1]}
            onCommit={(nextValue) => {
              const edges = [...spec.edges];
              edges[index] = nextValue;
              onCommit({ ...spec, edges });
            }}
            onRemove={() =>
              onCommit({ ...spec, edges: spec.edges.filter((_, edgeIndex) => edgeIndex !== index) })
            }
            errorKey={`edge-${index}`}
            reportError={reportError}
            hasVisualErrors={hasVisualErrors}
          />
        ))
      ) : (
        <p className="m-0 text-[11px] text-[var(--muted)]">
          当前没有切点，所有非缺失值会落入同一箱。
        </p>
      )}
      <div>
        <Button
          size="sm"
          variant="outline"
          disabled={hasVisualErrors}
          title={hasVisualErrors ? "请先修正当前输入错误" : "在末尾添加一个数值切点"}
          onClick={() => onCommit({ ...spec, edges: [...spec.edges, nextEdge(spec.edges)] })}
        >
          添加切点
        </Button>
      </div>
    </div>
  );
}

function ValueList({
  title,
  values,
  allValues,
  onChange,
  onRemoveList,
  errorKey,
  reportError,
  hasVisualErrors,
}: {
  title: string;
  values: string[];
  allValues: ReadonlySet<string>;
  onChange: (values: string[]) => void;
  onRemoveList?: () => void;
  errorKey: string;
  reportError: (key: string, error?: string) => void;
  hasVisualErrors: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  const add = () => {
    const category = draft.trim();
    if (!category) {
      setError("请输入类别值");
      reportError(errorKey, `请先修正${title}的类别输入。`);
      return;
    }
    if (allValues.has(category)) {
      setError("该类别已在其他分组或稀有值中");
      reportError(errorKey, `请先修正${title}中重复的类别。`);
      return;
    }
    onChange([...values, category]);
    setDraft("");
    setError("");
    reportError(errorKey);
  };

  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--ground)] p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <strong className="text-[11px]">{title}</strong>
        {onRemoveList && (
          <Button
            size="sm"
            variant="outline"
            onClick={onRemoveList}
            disabled={hasVisualErrors}
            aria-label={`删除${title}`}
            title={hasVisualErrors ? "请先修正当前输入错误" : `删除${title}`}
          >
            删除组
          </Button>
        )}
      </div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {values.map((category, index) => (
          <span
            key={`${category}-${index}`}
            className="inline-flex items-center gap-1 rounded-full border border-[var(--line-strong)] bg-[var(--paper)] px-2 py-1 text-[10px]"
          >
            {category}
            <button
              type="button"
              className="font-bold text-[var(--muted)] hover:text-[var(--red-text)]"
              disabled={hasVisualErrors}
              aria-label={`从${title}移除 ${category}`}
              title={hasVisualErrors ? "请先修正当前输入错误" : `从${title}移除 ${category}`}
              onClick={() => {
                if (values.length === 1 && onRemoveList) onRemoveList();
                else onChange(values.filter((_, valueIndex) => valueIndex !== index));
              }}
            >
              ×
            </button>
          </span>
        ))}
        {!values.length && <small className="text-[var(--muted)]">暂无类别</small>}
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
        <Input
          value={draft}
          aria-invalid={Boolean(error)}
          aria-label={`添加到${title}`}
          placeholder="输入一个类别值"
          onChange={(event) => {
            setDraft(event.target.value);
            setError("");
            reportError(errorKey);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
        />
        <Button
          size="sm"
          variant="outline"
          onClick={add}
          disabled={hasVisualErrors}
          title={hasVisualErrors ? "请先修正当前输入错误" : `添加到${title}`}
        >
          添加
        </Button>
      </div>
      {error && <small className="mt-1 block text-[var(--red-text)]">{error}</small>}
    </div>
  );
}

function CategoricalRuleEditor({
  spec,
  onCommit,
  reportError,
  hasVisualErrors,
}: {
  spec: EditableCategoricalBinSpec;
  onCommit: (spec: EditableCategoricalBinSpec) => void;
  reportError: (key: string, error?: string) => void;
  hasVisualErrors: boolean;
}) {
  const allValues = new Set([...spec.groups.flat(), ...spec.rare_values]);
  const [newGroupValue, setNewGroupValue] = useState("");
  const [newGroupError, setNewGroupError] = useState("");
  const addGroup = () => {
    const category = newGroupValue.trim();
    if (!category) {
      setNewGroupError("请先填写新组的第一个类别");
      reportError("new-group", "请先修正新类别组的输入。");
      return;
    }
    if (allValues.has(category)) {
      setNewGroupError("该类别已在其他分组或稀有值中");
      reportError("new-group", "请先修正新类别组中重复的类别。");
      return;
    }
    onCommit({ ...spec, groups: [...spec.groups, [category]] });
    setNewGroupValue("");
    setNewGroupError("");
    reportError("new-group");
  };

  return (
    <div className="grid gap-2" aria-label="类别分组编辑器">
      {spec.groups.map((group, index) => (
        <ValueList
          key={index}
          title={`类别组 ${index + 1}`}
          values={group}
          allValues={allValues}
          errorKey={`group-${index}`}
          reportError={reportError}
          hasVisualErrors={hasVisualErrors}
          onChange={(values) => {
            const groups = spec.groups.map((item, groupIndex) =>
              groupIndex === index ? values : item,
            );
            onCommit({ ...spec, groups });
          }}
          onRemoveList={() =>
            onCommit({
              ...spec,
              groups: spec.groups.filter((_, groupIndex) => groupIndex !== index),
            })
          }
        />
      ))}
      <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
        <Input
          value={newGroupValue}
          aria-invalid={Boolean(newGroupError)}
          aria-label="新类别组的第一个类别"
          placeholder="输入新组的第一个类别"
          onChange={(event) => {
            setNewGroupValue(event.target.value);
            setNewGroupError("");
            reportError("new-group");
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addGroup();
            }
          }}
        />
        <Button
          size="sm"
          variant="outline"
          onClick={addGroup}
          disabled={hasVisualErrors}
          title={hasVisualErrors ? "请先修正当前输入错误" : "新增一个类别组"}
        >
          新增类别组
        </Button>
      </div>
      {newGroupError && <small className="text-[var(--red-text)]">{newGroupError}</small>}
      <ValueList
        title="稀有值"
        values={spec.rare_values}
        allValues={allValues}
        errorKey="rare-values"
        reportError={reportError}
        hasVisualErrors={hasVisualErrors}
        onChange={(rareValues) => onCommit({ ...spec, rare_values: rareValues })}
      />
    </div>
  );
}

function BusinessExceptionField({
  value,
  onCommit,
  reportError,
}: {
  value?: string;
  onCommit: (value?: string) => void;
  reportError: (key: string, error?: string) => void;
}) {
  const [draft, setDraft] = useState(value || "");
  const [error, setError] = useState("");
  useEffect(() => setDraft(value || ""), [value]);

  const commit = () => {
    const normalized = draft.trim();
    if (normalized && normalized.length < 8) {
      setError("如需接受非单调方案，请填写至少 8 个字符的业务说明");
      reportError("business-exception", "非单调业务说明不足 8 个字符，请先补充。");
      return;
    }
    setError("");
    reportError("business-exception");
    if (normalized !== (value || "").trim()) onCommit(normalized || undefined);
  };

  return (
    <label className="grid gap-1 text-[11px] font-semibold">
      非单调业务说明（可选）
      <Textarea
        value={draft}
        rows={2}
        aria-invalid={Boolean(error)}
        placeholder="只有接受非单调方案时才需要填写，至少 8 个字符"
        onChange={(event) => {
          setDraft(event.target.value);
          setError("");
          reportError("business-exception");
        }}
        onBlur={commit}
      />
      {error && <small className="font-normal text-[var(--red-text)]">{error}</small>}
    </label>
  );
}

export function ManualBinningEditor({ value, onChange, submitError, onVisualErrorChange }: Props) {
  const [jsonOpen, setJsonOpen] = useState(false);
  const [visualErrors, setVisualErrors] = useState<Record<string, string>>({});
  const parsed = useMemo(() => parseManualBinSpec(value), [value]);
  const visualError = Object.values(visualErrors)[0] || null;

  useEffect(() => {
    if (submitError) setJsonOpen(true);
  }, [submitError]);

  useLayoutEffect(() => {
    onVisualErrorChange(visualError);
  }, [onVisualErrorChange, visualError]);

  useEffect(() => () => onVisualErrorChange(null), [onVisualErrorChange]);

  const reportError = useCallback(
    (key: string, error?: string) =>
      setVisualErrors((current) => updateManualVisualErrors(current, key, error)),
    [],
  );
  const commit = (spec: EditableBinSpec) => {
    onChange(serializeManualBinSpec(spec));
  };
  const spec = parsed.ok ? parsed.value : null;

  return (
    <div className="grid gap-3">
      <div className="rounded-lg border border-[var(--line-strong)] bg-[var(--paper)] p-3">
        <div className="mb-3">
          <h3 className="m-0">可视化调整规则</h3>
          <p className="mt-1 text-[11px] text-[var(--muted)]">
            {spec?.kind === "numeric"
              ? "逐项修改、添加或删除数值切点；切点必须严格递增。"
              : spec?.kind === "categorical"
                ? "按类别组增删取值；同一类别只能出现一次。"
                : "高级 JSON 暂时无法解析，修正后会恢复可视化编辑。"}
          </p>
        </div>
        {spec?.kind === "numeric" && (
          <NumericRuleEditor
            spec={spec}
            onCommit={commit}
            reportError={reportError}
            hasVisualErrors={Boolean(visualError)}
          />
        )}
        {spec?.kind === "categorical" && (
          <CategoricalRuleEditor
            spec={spec}
            onCommit={commit}
            reportError={reportError}
            hasVisualErrors={Boolean(visualError)}
          />
        )}
        {spec && (
          <div className="mt-3 border-t border-[var(--line)] pt-3">
            <BusinessExceptionField
              value={spec.business_exception}
              reportError={reportError}
              onCommit={(businessException) =>
                commit({
                  ...spec,
                  ...(businessException
                    ? { business_exception: businessException }
                    : { business_exception: undefined }),
                })
              }
            />
          </div>
        )}
      </div>

      <details open={jsonOpen} onToggle={(event) => setJsonOpen(event.currentTarget.open)}>
        <summary className="cursor-pointer text-[11px] font-semibold">
          高级：直接编辑 JSON 规则
        </summary>
        <div className="mt-2">
          <Textarea
            value={value}
            onChange={(event) => onChange(event.target.value)}
            spellCheck={false}
            rows={10}
            aria-invalid={Boolean(submitError)}
            aria-describedby={submitError ? "manual-bin-spec-error" : undefined}
          />
          {submitError && (
            <p
              id="manual-bin-spec-error"
              role="alert"
              className="mt-2 rounded-md border border-[var(--red-border)] bg-[var(--red-soft)] p-2 text-[11px] text-[var(--red-text)]"
            >
              {submitError.message}
            </p>
          )}
        </div>
      </details>
    </div>
  );
}
