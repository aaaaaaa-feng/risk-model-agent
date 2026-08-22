export interface ModelCandidate {
  candidate: string;
  status: string;
  calibration?: string;
  test_metrics?: { roc_auc?: number; ks?: number };
  train_test_score_psi?: number;
  test_monotonicity?: { absolute?: boolean };
}

export interface ModelResult {
  champion?: string;
  candidates?: ModelCandidate[];
}
