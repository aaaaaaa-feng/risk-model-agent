import { useEffect, useRef, useState } from "react";
import { runsApi } from "../api/runsApi";
import {
  createManualBinDrafts,
  parseManualBinDrafts,
  parseManualBinSpec,
  updateManualBinDraft,
  type ManualBinSpecError,
} from "../lib/binning";
import { confirmLabel, decisionStageName, reviewLabel } from "../lib/labels";
import { Button } from "@/shared/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/shared/ui/dialog";
import { notify } from "@/shared/lib/notify";
import { errorMessage, translateError } from "@/shared/lib/errors";
import { Hint } from "@/shared/ui/hint";
import type { Decision, Run } from "../types";
import {
  BinningDecision,
  DataDecision,
  ModelDecision,
  ScreeningDecision,
  SplitDecision,
  TargetDecision,
} from "./DecisionSections";

interface Props {
  run: Run;
  decision: Decision;
  onResolved: () => void;
}

export function DecisionWorkbench({ run, decision, onResolved }: Props) {
  const details = decision.payload;
  const summary = details.summary;
  const [busy, setBusy] = useState(false);
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [manualColumn, setManualColumn] = useState("");
  const [manualDrafts, setManualDrafts] = useState<Record<string, string>>({});
  const [manualDirtyColumns, setManualDirtyColumns] = useState<string[]>([]);
  const [manualSpecError, setManualSpecError] = useState<ManualBinSpecError | null>(null);
  const [manualVisualError, setManualVisualError] = useState<string | null>(null);
  const initializedDecision = useRef("");

  useEffect(() => {
    const decisionKey = `${decision.id}:${decision.kind}`;
    if (initializedDecision.current === decisionKey) return;
    initializedDecision.current = decisionKey;
    setEdits({});
    if (decision.kind === "confirm_data") {
      const dataSummary = summary as import("../types").DataSummary;
      setEdits({
        accepted_action_ids: (dataSummary.actions || [])
          .filter((action) => action.recommended)
          .map((action) => action.id),
      });
    } else if (decision.kind === "confirm_split") {
      const splitSummary = summary as import("../types").SplitSummary;
      setEdits({ ...(splitSummary.plan || splitSummary) });
    } else if (decision.kind === "confirm_models") {
      const modelsSummary = summary as import("../types").ModelsSummary;
      setEdits({
        models: modelsSummary.plan?.models || [],
        score: modelsSummary.plan?.score || {},
        search_budget: modelsSummary.plan?.search_budget ?? 0,
      });
    } else if (decision.kind === "confirm_binning") {
      const binningSummary = summary as import("../types").BinningSummary;
      const specs = binningSummary.specs || {};
      const first = Object.keys(specs)[0] || "";
      setManualColumn(first);
      setManualDrafts(createManualBinDrafts(specs));
    }
    if (decision.kind !== "confirm_binning") {
      setManualColumn("");
      setManualDrafts({});
    }
    setManualDirtyColumns([]);
    setManualSpecError(null);
    setManualVisualError(null);
  }, [decision.id, decision.kind, summary]);

  const review: import("../types").Review = summary.review || decision.review || {};

  const confirm = async (approved: boolean) => {
    const payloadEdits = { ...edits };
    if (approved && decision.kind === "confirm_binning" && manualVisualError) {
      notify(manualVisualError, true);
      return;
    }
    if (approved && decision.kind === "confirm_binning" && manualDirtyColumns.length) {
      const parsed = parseManualBinDrafts(manualDrafts, manualDirtyColumns);
      if (!parsed.ok) {
        setManualColumn(parsed.column);
        setManualSpecError(parsed.error);
        notify(parsed.error.message, true);
        return;
      }
      payloadEdits.manual_specs = parsed.value;
    }

    setManualSpecError(null);
    setBusy(true);
    try {
      await runsApi.decide(run.id, decision.id, approved, payloadEdits);
      onResolved();
    } catch (error) {
      notify(errorMessage(error, { context: "decision" }), true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open>
      <DialogContent
        className="hitl-dialog"
        aria-describedby="hitl-confirmation-description"
        onEscapeKeyDown={(event) => event.preventDefault()}
        onPointerDownOutside={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
      >
        <div className="decision-workbench" data-testid="hitl-confirmation-dialog">
          <div className="stage-line">
            <div>
              <span className="eyebrow">HUMAN IN THE LOOP · {decision.stage}</span>
              <DialogTitle id="hitl-confirmation-title">
                {details.title || decisionStageName[decision.stage] || decision.stage}
                <Hint text="Reviewer 已先完成审核；你只需确认业务选择，不需要阅读长代码。" />
              </DialogTitle>
              <DialogDescription
                id="hitl-confirmation-description"
                className="hitl-dialog-description"
              >
                这是必须完成的阶段确认。请检查摘要与 Reviewer 证据，确认后 Agent 才会继续运行。
              </DialogDescription>
            </div>
            <div className="run-meta">
              RUN <b>{run.id.slice(-8)}</b>
              <br />
              CHECKPOINT <b>{run.node}</b>
            </div>
          </div>
          <div className={`review-banner ${review.status || "pass"}`}>
            <div>
              <span>AI REVIEW</span>
              <strong>{reviewLabel[review.status || ""] || "已完成预审"}</strong>
            </div>
            <p>
              {review.issues?.length
                ? `${review.issues.length} 条意见；展开下方可查看。`
                : "没有发现逻辑或安全阻断。"}
            </p>
          </div>
          {review.status === "fallback_pass" && (
            <p className="inline-warning agent-fallback-note">
              本阶段没有调用外部 LLM：当前使用本地确定性 Reviewer。若要启用
              LLM，请到「设置中心」打开“启用 LLM”，保存后重新创建 Run；本次 Run 不会回溯重试。
            </p>
          )}
          {decision.kind === "confirm_target" && (
            <TargetDecision summary={summary as import("../types").TargetSummary} />
          )}
          {decision.kind === "confirm_data" && (
            <DataDecision
              summary={summary as import("../types").DataSummary}
              edits={edits}
              setEdits={setEdits}
            />
          )}
          {decision.kind === "confirm_split" && (
            <SplitDecision
              summary={summary as import("../types").SplitSummary}
              edits={edits}
              setEdits={setEdits}
            />
          )}
          {decision.kind === "confirm_screening" && (
            <ScreeningDecision
              summary={summary as import("../types").ScreeningSummary}
              edits={edits}
              setEdits={setEdits}
            />
          )}
          {decision.kind === "confirm_binning" && (
            <BinningDecision
              summary={summary as import("../types").BinningSummary}
              manualColumn={manualColumn}
              dirtyColumns={manualDirtyColumns}
              setManualColumn={(column) => {
                setManualSpecError(null);
                setManualColumn(column);
              }}
              manualSpec={manualDrafts[manualColumn] || ""}
              onManualSpecChange={(value) => {
                setManualSpecError((current) => {
                  if (!current) return null;
                  const parsed = parseManualBinSpec(value);
                  return parsed.ok ? null : parsed.error;
                });
                setManualDirtyColumns((current) =>
                  current.includes(manualColumn) ? current : [...current, manualColumn],
                );
                setManualDrafts((current) => updateManualBinDraft(current, manualColumn, value));
              }}
              manualSpecError={manualSpecError}
              onManualVisualErrorChange={setManualVisualError}
            />
          )}
          {decision.kind === "confirm_models" && (
            <ModelDecision
              plan={
                (summary as import("../types").ModelsSummary).plan || {
                  models: [],
                  score: {},
                  search_budget: 0,
                }
              }
              edits={edits}
              setEdits={setEdits}
            />
          )}
          <details className="review-details">
            <summary>查看 Reviewer 结论与证据</summary>
            {review.issues?.length ? (
              <ul>
                {review.issues.map((issue, index) => (
                  <li key={index} title={issue.code ? `诊断码：${issue.code}` : undefined}>
                    <b>Reviewer 建议</b>
                    <span>
                      {
                        translateError(
                          { code: issue.code, message: issue.message },
                          { context: "review" },
                        ).text
                      }
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p>确定性检查和独立上下文 Reviewer 均未发现阻断。</p>
            )}
            <pre>{JSON.stringify(review.evidence || {}, null, 2)}</pre>
          </details>
          <div className="decision-actions">
            <Button variant="destructiveOutline" disabled={busy} onClick={() => confirm(false)}>
              不批准并停止本 Run
            </Button>
            <Button disabled={busy} onClick={() => confirm(true)}>
              {busy ? "提交中…" : confirmLabel[decision.kind] || "确认并继续"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
