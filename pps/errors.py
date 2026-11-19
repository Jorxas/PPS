class PPSError(Exception):
    def __init__(self, code, message, status=400, details=None, retryable=False):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.details, self.retryable = details or {}, retryable

    def payload(self):
        return {"code": self.code, "message": self.message,
                "details": self.details, "retryable": self.retryable}
