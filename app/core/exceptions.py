"""
Exceções do projeto.
"""


class WorkerError(Exception):
    pass


class ValidationError(WorkerError):
    pass


class AuthenticationError(WorkerError):
    pass


class ApiRequestError(WorkerError):
    pass


class RunnerRegistrationError(WorkerError):
    pass


class BotInstallError(WorkerError):
    pass
