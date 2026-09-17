from __future__ import annotations

import math
from collections.abc import Sequence


class ModelEvaluator:
    """Evaluates ML model probability predictions against ground truth per §13.3."""

    @staticmethod
    def evaluate(predictions: Sequence[float], truth: Sequence[bool | int]) -> dict[str, float]:
        if len(predictions) == 0 or len(truth) == 0 or len(predictions) != len(truth):
            return {
                "log_loss": 0.0,
                "brier_score": 0.0,
                "accuracy": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
            }

        n = len(truth)
        eps = 1e-15
        total_log_loss = 0.0
        total_brier = 0.0
        tp = 0
        fp = 0
        tn = 0
        fn = 0

        for p_raw, t_val in zip(predictions, truth, strict=True):
            y = 1.0 if bool(t_val) else 0.0
            p = max(eps, min(1.0 - eps, float(p_raw)))

            # Log loss
            total_log_loss += -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
            # Brier score
            total_brier += (p - y) ** 2

            pred_bool = p >= 0.5
            truth_bool = bool(t_val)
            if pred_bool and truth_bool:
                tp += 1
            elif pred_bool and not truth_bool:
                fp += 1
            elif not pred_bool and not truth_bool:
                tn += 1
            else:
                fn += 1

        accuracy = (tp + tn) / n
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

        return {
            "log_loss": round(total_log_loss / n, 6),
            "brier_score": round(total_brier / n, 6),
            "accuracy": round(accuracy, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
