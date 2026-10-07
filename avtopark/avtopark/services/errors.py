class BusinessRuleError(ValueError):
    """An operation violates a business rule. The message is shown to the user.

    `field` names the form field the error belongs to, if any.
    """

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field
