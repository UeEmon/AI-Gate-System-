"""Accuracy metrics for fixed, labelled gate evaluation sets."""
from __future__ import annotations

from dataclasses import dataclass


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, a in enumerate(left, 1):
        current = [row]
        for column, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (a != b)))
        previous = current
    return previous[-1]


@dataclass(slots=True)
class EvaluationMetrics:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    ocr_total: int = 0
    ocr_exact: int = 0
    ocr_edits: int = 0
    ocr_characters: int = 0

    def add_detection(self, *, matched: bool, predicted: bool, expected: bool) -> None:
        self.true_positive += int(matched and predicted and expected)
        self.false_positive += int(predicted and not matched)
        self.false_negative += int(expected and not matched)

    def add_ocr(self, truth: str, prediction: str) -> None:
        self.ocr_total += 1
        self.ocr_exact += int(truth == prediction)
        self.ocr_edits += edit_distance(truth, prediction)
        self.ocr_characters += len(truth)

    def report(self) -> dict:
        precision_denominator = self.true_positive + self.false_positive
        recall_denominator = self.true_positive + self.false_negative
        precision = self.true_positive / precision_denominator if precision_denominator else 0.0
        recall = self.true_positive / recall_denominator if recall_denominator else 0.0
        return {
            "detection_precision": precision,
            "detection_recall": recall,
            "detection_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "ocr_exact_match": self.ocr_exact / self.ocr_total if self.ocr_total else 0.0,
            "ocr_cer": self.ocr_edits / self.ocr_characters if self.ocr_characters else 0.0,
            "samples": self.ocr_total,
        }
