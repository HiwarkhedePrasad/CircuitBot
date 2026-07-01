class ExportValidationError(Exception):
    def __init__(self, message: str, issues: list | None = None):
        super().__init__(message)
        self.issues = issues or []
