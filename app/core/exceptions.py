"""
Exceções do projeto.
"""


class WorkerError(Exception):
    pass


class ValidationError(WorkerError):
    pass


class AuthenticationError(WorkerError):
    """HTTP 401: token ausente, inválido ou expirado."""


class ApiRequestError(WorkerError):
    pass


class ForbiddenError(ApiRequestError):
    """HTTP 403: o backend recusou a operação (não é problema de token)."""


class RunnerRegistrationError(WorkerError):
    pass


class BotInstallError(WorkerError):
    pass
