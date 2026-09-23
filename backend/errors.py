# Error with a message that is safe to show to the user
class AppError(Exception):
    def __init__(self, message, raw=None):
        super().__init__(message)
        self.message = message
        self.raw = raw or message


# Ollama did not respond or returned an unusable result
class LlmError(AppError):
    pass


# SQL failed to run on PostgreSQL
class DbError(AppError):
    pass


# SQL failed the validation layer
class SqlUnsafeError(AppError):
    pass
