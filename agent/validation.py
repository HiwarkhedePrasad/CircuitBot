from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ValidationIssue:
    code: str
    severity: str
    stage: str
    message: str
    component: str = ""
    pin: str = ""
    net: str = ""
    fixable: bool = False
    suggested_fix: Optional[dict] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "stage": self.stage,
            "message": self.message,
            "component": self.component,
            "pin": self.pin,
            "net": self.net,
            "fixable": self.fixable,
            "suggested_fix": self.suggested_fix,
            "metadata": self.metadata,
        }

    def to_legacy_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "component": self.component,
        }
